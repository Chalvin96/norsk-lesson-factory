"""Not a check itself — restore and persist rich-authoring node context.

Each rich stage reconstructs its provider configuration from the checkpoint's
stable paths. Model clients and other resources never enter LangGraph state;
only JSON-compatible stage outputs and scratch paths do. The stage runner calls
the workflow operations directly; no pass-through stage facade is needed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from typing import Protocol

from lesson_builder.domain.lesson.models.rich_authoring import ExercisePackage
from lesson_builder.domain.lesson.models.rich_authoring import NormalizedPackage
from lesson_builder.workflow.lesson_generation.coverage import evaluate_required_coverage
from lesson_builder.workflow.lesson_generation.generation_log import write_generation_log as write_receipt
from lesson_builder.workflow.lesson_generation.rich_authoring import ExerciseAuthorResult
from lesson_builder.workflow.lesson_generation.rich_authoring import ExerciseVerificationResult
from lesson_builder.workflow.lesson_generation.rich_authoring import NormalizationResult
from lesson_builder.workflow.lesson_generation.rich_authoring import RichAuthoringRun
from lesson_builder.workflow.lesson_generation.rich_authoring import author_exercises
from lesson_builder.workflow.lesson_generation.rich_authoring import author_or_reuse_draft
from lesson_builder.workflow.lesson_generation.rich_authoring import closed_task_verifier_for
from lesson_builder.workflow.lesson_generation.rich_authoring import compile_generated_package
from lesson_builder.workflow.lesson_generation.rich_authoring import extract_intents
from lesson_builder.workflow.lesson_generation.rich_authoring import finish_rich_run
from lesson_builder.workflow.lesson_generation.rich_authoring import initialize_rich_run
from lesson_builder.workflow.lesson_generation.rich_authoring import normalize_rich_draft as normalize_draft
from lesson_builder.workflow.lesson_generation.rich_authoring import record_coverage
from lesson_builder.workflow.lesson_generation.rich_authoring import record_verifier_success
from lesson_builder.workflow.lesson_generation.rich_authoring import review_lesson_draft
from lesson_builder.workflow.lesson_generation.rich_authoring import review_normalization
from lesson_builder.workflow.lesson_generation.rich_authoring import split_checkpoint
from lesson_builder.workflow.lesson_generation.rich_authoring import standalone_review_verifier_for
from lesson_builder.workflow.lesson_generation.rich_authoring import validate_checkpoint_boundary
from lesson_builder.workflow.lesson_generation.rich_authoring import verify_rich_exercises as verify_exercises
from lesson_builder.workflow.lesson_generation.rich_authoring_content import normalize_compiler_unsafe_markdown
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_COMPLETE
from lesson_builder.workflow.lesson_generation.stage_attestations import StageAttestation
from lesson_builder.workflow.lesson_generation.stage_attestations import text_content_hash
from lesson_builder.workflow.lesson_generation.state import LessonGenerationState


class RichAuthoringStageRunner(Protocol):
    """Checkpointable rich-authoring stage collaborator."""

    def author(self, state: LessonGenerationState) -> dict[str, Any]:
        """Author or reuse the rich draft."""

    def review(self, state: LessonGenerationState) -> dict[str, Any]:
        """Review the authored draft."""

    def normalize(self, state: LessonGenerationState) -> dict[str, Any]:
        """Normalize the reviewed draft."""

    def preservation(self, state: LessonGenerationState) -> dict[str, Any]:
        """Review normalization preservation."""

    def intent_review(self, state: LessonGenerationState) -> dict[str, Any]:
        """Validate the exercise-request handoff."""

    def exercise_author(self, state: LessonGenerationState) -> dict[str, Any]:
        """Author exercises against the frozen lesson."""

    def exercise_compile(self, state: LessonGenerationState) -> dict[str, Any]:
        """Compile generated exercise source."""

    def exercise_verify(self, state: LessonGenerationState) -> dict[str, Any]:
        """Verify generated exercises."""

    def coverage(self, state: LessonGenerationState) -> dict[str, Any]:
        """Run the required-coverage gate and finish the scratch package."""


def restore_rich_run(state: Mapping[str, Any]) -> RichAuthoringRun:
    """Restore one rich-authoring context from checkpointed paths and receipt."""
    output_root = Path(state["output_root"])
    run = initialize_rich_run(
        plan_source=Path(state["fixture_source"]),
        output_root=output_root,
        repo_root=Path(state["repo_root"]),
        run_id=state["run_id"],
        job=state["generation_job"],
    )
    receipt_path = output_root / "llm_receipt.json"
    if receipt_path.is_file():
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            receipt = None
        if isinstance(receipt, dict):
            run.receipt = receipt
    return run


def persist_rich_run(run: RichAuthoringRun) -> str:
    """Persist the cumulative scratch receipt after one graph stage."""
    return str(write_receipt(run.root, run.receipt))


class RichAuthoringStages:
    """Default stage collaborator used by the lesson-generation graph.

    Keeping the stage boundary in one injected collaborator gives tests a
    small seam for failures while production retains the same checkpointed
    graph topology. No provider clients or mutable run objects enter graph
    state; this collaborator reconstructs them from the state paths.
    """

    def author(self, state: LessonGenerationState) -> dict[str, Any]:
        """Author or reuse the cache-bound rich lesson draft."""
        run = restore_rich_run(state)
        draft_path = run.draft_dir / "rich.md"
        draft_text = author_or_reuse_draft(run, draft_path)
        return _persist_stage(run, rich_draft_text=draft_text, generation_stage="draft_authored")

    def review(self, state: LessonGenerationState) -> dict[str, Any]:
        """Review the authored draft and checkpoint its attestation."""
        run = restore_rich_run(state)
        result = review_lesson_draft(
            author_config=run.author_config,
            reviewer_config=run.reviewer_config,
            root=run.root,
            review_dir=run.review_dir,
            draft_path=run.draft_dir / "rich.md",
            plan_text=run.plan_text,
            draft_prompt=run.draft_prompt,
            draft_text=state["rich_draft_text"],
            terminology_context=run.terminology_context,
            receipt=run.receipt,
        )
        return _persist_stage(
            run,
            rich_draft_text=result["draft_text"],
            rich_reviewed_draft_hash=result["attestation"].input_hash or text_content_hash(result["draft_text"]),
            rich_lesson_review_attestation=result["attestation"].model_dump(mode="json"),
            generation_stage="draft_reviewed",
        )

    def normalize(self, state: LessonGenerationState) -> dict[str, Any]:
        """Normalize reviewed prose into a typed package."""
        run = restore_rich_run(state)
        draft_text, intents_yaml = split_checkpoint(run, state["rich_draft_text"])
        normalized = normalize_draft(run, draft_text=draft_text, checkpoint_intents_yaml=intents_yaml)
        return _persist_stage(
            run,
            rich_draft_text=draft_text,
            rich_checkpoint_intents_yaml=intents_yaml,
            rich_normalization_prompt=normalized.normalization_prompt,
            rich_normalized_package=normalized.package.model_dump(mode="json"),
            rich_normalization_response_reused=normalized.response_reused,
            generation_stage="draft_normalized",
        )

    def preservation(self, state: LessonGenerationState) -> dict[str, Any]:
        """Review normalization preservation and checkpoint the package."""
        run = restore_rich_run(state)
        package = NormalizedPackage.model_validate(state["rich_normalized_package"])
        preservation = review_normalization(
            reviewer_config=run.reviewer_config,
            normalizer_config=run.normalizer_config,
            root=run.root,
            normalization_dir=run.normalization_dir,
            plan_text=run.plan_text,
            draft_text=state["rich_draft_text"],
            package=package,
            normalization_prompt=state["rich_normalization_prompt"],
            draft_input_hash=state.get("rich_reviewed_draft_hash"),
            receipt=run.receipt,
        )
        package = preservation["package"].model_copy(
            update={"lesson_md": normalize_compiler_unsafe_markdown(preservation["package"].lesson_md)}
        )
        validate_checkpoint_boundary(run, package, state["rich_checkpoint_intents_yaml"], phase="preserved")
        return _persist_stage(
            run,
            rich_normalized_package=package.model_dump(mode="json"),
            rich_normalization_attestation=preservation["attestation"].model_dump(mode="json"),
            rich_normalization_repaired=preservation["repaired"],
            generation_stage="normalization_preserved",
        )

    def intent_review(self, state: LessonGenerationState) -> dict[str, Any]:
        """Extract and validate the exercise-request handoff."""
        run = restore_rich_run(state)
        package, _requests, requests_hash, lesson_hash = extract_intents(
            run,
            NormalizedPackage.model_validate(state["rich_normalized_package"]),
        )
        return _persist_stage(
            run,
            rich_normalized_package=package.model_dump(mode="json"),
            rich_requests_hash=requests_hash,
            rich_lesson_hash=lesson_hash,
            generation_stage="intent_reviewed",
        )

    def exercise_author(self, state: LessonGenerationState) -> dict[str, Any]:
        """Author and align exercises for the frozen lesson."""
        run = restore_rich_run(state)
        author = author_exercises(
            run,
            package=NormalizedPackage.model_validate(state["rich_normalized_package"]),
            requests_hash=state["rich_requests_hash"],
            lesson_hash=state["rich_lesson_hash"],
        )
        return _persist_stage(
            run,
            rich_exercise_package=author.package.model_dump(mode="json"),
            rich_exercises_yaml=author.exercises_yaml,
            rich_exercise_prompt=author.exercise_prompt,
            rich_exercise_response_path=str(author.response_path),
            rich_exercise_input_path=str(author.input_path),
            rich_exercise_response_reused=author.response_reused,
            generation_stage="exercises_authored",
        )

    def exercise_compile(self, state: LessonGenerationState) -> dict[str, Any]:
        """Compile the normalized lesson and authored exercises."""
        run = restore_rich_run(state)
        internal_lesson = compile_generated_package(
            run,
            package=NormalizedPackage.model_validate(state["rich_normalized_package"]),
            exercises_yaml=state["rich_exercises_yaml"],
            lesson_hash=state["rich_lesson_hash"],
        )
        return _persist_stage(run, rich_internal_lesson=internal_lesson, generation_stage="exercises_compiled")

    def exercise_verify(self, state: LessonGenerationState) -> dict[str, Any]:
        """Verify exercises and apply the bounded repair policy."""
        run = restore_rich_run(state)
        author = _restore_exercise_author_result(state)
        verification = verify_exercises(
            run,
            package=NormalizedPackage.model_validate(state["rich_normalized_package"]),
            author=author,
            internal_lesson=state["rich_internal_lesson"],
            verifier=closed_task_verifier_for(run.repo_root),
            standalone=standalone_review_verifier_for(run.repo_root),
            lesson_hash=state["rich_lesson_hash"],
            requests_hash=state["rich_requests_hash"],
        )
        record_verifier_success(run, verification)
        return _persist_stage(
            run,
            rich_internal_lesson=verification.internal_lesson,
            rich_exercise_package=author.package.model_dump(mode="json"),
            rich_exercises_yaml=verification.exercises_yaml,
            rich_verification=verification.verification,
            rich_standalone_report=verification.standalone_report,
            rich_semantic_repairs=verification.semantic_repairs,
            generation_stage="exercises_verified",
        )

    def coverage(self, state: LessonGenerationState) -> dict[str, Any]:
        """Gate coverage and finish the checkpointed generated source package."""
        run = restore_rich_run(state)
        package = NormalizedPackage.model_validate(state["rich_normalized_package"])
        coverage = evaluate_required_coverage(
            plan_text=run.plan_text,
            lesson_md=package.lesson_md,
            exercises_yaml=state["rich_exercises_yaml"],
        )
        record_coverage(run, coverage)
        normalization = NormalizationResult(
            preservation={
                "attestation": StageAttestation.model_validate(state["rich_normalization_attestation"]),
                "repaired": state["rich_normalization_repaired"],
            },
            response_path=run.normalization_dir / "response.json",
            last_response=None,
            repaired=state["rich_normalization_repaired"],
            response_reused=state.get("rich_normalization_response_reused", False),
        )
        author = _restore_exercise_author_result(state)
        verification = ExerciseVerificationResult(
            internal_lesson=state["rich_internal_lesson"],
            exercises_yaml=state["rich_exercises_yaml"],
            verification=state["rich_verification"],
            standalone_report=state["rich_standalone_report"],
            semantic_repairs=state["rich_semantic_repairs"],
        )
        generated = finish_rich_run(
            run,
            lesson_review={"attestation": StageAttestation.model_validate(state["rich_lesson_review_attestation"])},
            normalization=normalization,
            package=package,
            author=author,
            verification=verification,
            lesson_hash=state["rich_lesson_hash"],
            requests_hash=state["rich_requests_hash"],
        )
        return _persist_stage(
            run,
            rich_coverage=coverage,
            generated_source_dir=str(generated.source_dir),
            fixture_source=str(generated.source_dir),
            generation_stage=K_LESSON_GENERATION_STAGE_COMPLETE,
        )


def _restore_exercise_author_result(state: Mapping[str, Any]) -> ExerciseAuthorResult:
    """Restore the exercise-author record needed by verification and coverage."""
    return ExerciseAuthorResult(
        package=ExercisePackage.model_validate(state["rich_exercise_package"]),
        exercises_yaml=state["rich_exercises_yaml"],
        exercise_prompt=state["rich_exercise_prompt"],
        response_path=Path(state["rich_exercise_response_path"]),
        input_path=Path(state["rich_exercise_input_path"]),
        last_response=None,
        lesson_hash=state["rich_lesson_hash"],
        response_reused=state.get("rich_exercise_response_reused", False),
    )


def _persist_stage(run: RichAuthoringRun, **values: object) -> dict[str, object]:
    """Persist stage receipt and return only JSON-compatible checkpoint values."""
    values["llm_receipt_path"] = persist_rich_run(run)
    return values


__all__ = ["RichAuthoringStageRunner", "RichAuthoringStages", "restore_rich_run", "persist_rich_run"]
