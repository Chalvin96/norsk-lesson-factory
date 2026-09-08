"""Entry point: ``FakeLessonPackageDeps``.

Typed, offline collaborators for catalog-package graph behavior tests. Exposes
call counters and configurable return values so routing and finalize behavior
can be asserted without touching the filesystem or the network.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.application.operations.review_standalone import StandaloneReviewError
from lesson_builder.clients.llm.base import AttemptOutcome
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_REQUIRED_FILES
from lesson_builder.workflow.lesson_generation.stage_attestations import K_ARTIFACT_COMPILATION_POLICY_VERSION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_COVERAGE_POLICY_VERSION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_EXERCISE_VERIFICATION_POLICY_VERSION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_INTENT_REVIEW_POLICY_VERSION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_LESSON_REVIEW_POLICY_VERSION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_LESSON_REVIEW_PROMPT_VERSION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_NORMALIZATION_REVIEW_POLICY_VERSION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_NORMALIZATION_REVIEW_PROMPT_VERSION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_ARTIFACT_COMPILATION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_COVERAGE
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_EXERCISE_VERIFICATION
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_INTENT_REVIEW
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_LESSON_REVIEW
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_NORMALIZATION_REVIEW
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_STATUS_PASS
from lesson_builder.workflow.lesson_generation.stage_attestations import StageAttestation
from lesson_builder.workflow.lesson_generation.stage_attestations import StageAttestations
from lesson_builder.workflow.lesson_generation.stage_attestations import source_dir_hashes
from lesson_builder.workflow.lesson_generation.stage_attestations import text_content_hash
from lesson_builder.workflow.lesson_generation.stage_attestations import write_stage_attestations
from tests.domain.lesson.models.fakes import needs_repair_quality_review
from tests.domain.lesson.models.fakes import valid_quality_review
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def write_passing_stage_attestations(source_dir: Path) -> None:
    """Write a complete content-addressed review set for one test package."""
    requests_text = "- handle: test-request\n  objective_ref: obj-question-order\n"
    (source_dir / "exercise_requests.yaml").write_text(requests_text, encoding="utf-8")
    source_hash, lesson_hash, requests_hash = source_dir_hashes(source_dir)
    assert requests_hash is not None
    draft_hash = text_content_hash("reviewed draft")
    attestations = StageAttestations(
        stages={
            K_STAGE_LESSON_REVIEW: StageAttestation(
                stage=K_STAGE_LESSON_REVIEW,
                status=K_STAGE_STATUS_PASS,
                policy_version=K_LESSON_REVIEW_POLICY_VERSION,
                prompt_hash=text_content_hash("lesson review prompt"),
                prompt_version=K_LESSON_REVIEW_PROMPT_VERSION,
                input_hash=draft_hash,
            ),
            K_STAGE_NORMALIZATION_REVIEW: StageAttestation(
                stage=K_STAGE_NORMALIZATION_REVIEW,
                status=K_STAGE_STATUS_PASS,
                policy_version=K_NORMALIZATION_REVIEW_POLICY_VERSION,
                prompt_hash=text_content_hash("normalization review prompt"),
                prompt_version=K_NORMALIZATION_REVIEW_PROMPT_VERSION,
                input_hash=draft_hash,
                lesson_hash=lesson_hash,
            ),
            K_STAGE_INTENT_REVIEW: StageAttestation(
                stage=K_STAGE_INTENT_REVIEW,
                status=K_STAGE_STATUS_PASS,
                policy_version=K_INTENT_REVIEW_POLICY_VERSION,
                lesson_hash=lesson_hash,
                requests_hash=requests_hash,
            ),
            K_STAGE_EXERCISE_VERIFICATION: StageAttestation(
                stage=K_STAGE_EXERCISE_VERIFICATION,
                status=K_STAGE_STATUS_PASS,
                policy_version=K_EXERCISE_VERIFICATION_POLICY_VERSION,
                source_hash=source_hash,
            ),
            K_STAGE_COVERAGE: StageAttestation(
                stage=K_STAGE_COVERAGE,
                status=K_STAGE_STATUS_PASS,
                policy_version=K_COVERAGE_POLICY_VERSION,
                source_hash=source_hash,
            ),
            K_STAGE_ARTIFACT_COMPILATION: StageAttestation(
                stage=K_STAGE_ARTIFACT_COMPILATION,
                status=K_STAGE_STATUS_PASS,
                policy_version=K_ARTIFACT_COMPILATION_POLICY_VERSION,
                source_hash=source_hash,
            ),
        }
    )
    write_stage_attestations(source_dir, attestations)


def needs_repair_preservation_review() -> dict[str, Any]:
    """Return one contract-valid preservation review that demands restoration."""
    return {
        "verdict": "needs_repair",
        "summary": "The conversion dropped one reviewed bilingual example.",
        "findings": [
            {
                "category": "dropped_example",
                "evidence": "The reviewed draft teaches 'Snakker du norsk?' with its translation.",
                "repair_instruction": "Restore the dropped example inside the model section.",
            }
        ],
    }


class FakeLessonPackageDeps:
    """Configurable fake collaborators for the catalog-package graph.

    ``copier`` writes real files into ``tmp_path`` (the behavior under test is
    the copy + hash), while ``compiler`` and ``ledger`` are fully in-memory so
    tests can assert call counts and return shapes without disk IO. Use
    ``LessonGenerationDeps()`` for real file-backed defaults.
    """

    def __init__(
        self,
        *,
        export_doc: dict[str, Any] | None = None,
        transcript_ids: list[str] | None = None,
    ) -> None:
        self.export_doc_override = export_doc
        self.transcript_ids = transcript_ids or ["t-fake-1", "t-fake-2"]
        self.copier_calls = 0
        self.compiler_calls = 0
        self.ledger_calls = 0
        self.ledger_entries: list[dict[str, Any]] = []

    def deps(self) -> LessonGenerationDeps:
        return LessonGenerationDeps(
            copier=self.copier,
            compiler=self.compiler,
            ledger=self.ledger,
        )

    def copier(self, *, fixture_source: Path, output_root: Path, run_id: str) -> tuple[Path, list[str], str]:
        """Real copy into tmp; the copy + hash IS the behavior under test."""
        self.copier_calls += 1
        from lesson_builder.workflow.lesson_generation.dependencies import default_source_copier

        return default_source_copier(fixture_source=fixture_source, output_root=output_root, run_id=run_id)

    def compiler(self, *, source_dir: Path, run_id: str) -> tuple[dict[str, Any], str]:
        self.compiler_calls += 1
        if self.export_doc_override is not None:
            doc = dict(self.export_doc_override)
        else:
            doc = {
                "course_id": "test-course",
                "run_id": run_id,
                "transcript_ids": list(self.transcript_ids),
                "lesson_md": "# fake lesson",
                "exercises": [],
                "transcript_blocks": [],
            }
        payload = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        import hashlib

        export_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        return doc, export_hash

    def ledger(self, *, ledger_path: Path, run_id: str, export_doc: dict[str, Any], source_hash: str) -> dict[str, Any]:
        self.ledger_calls += 1
        entry = {
            "run_id": run_id,
            "source_hash": source_hash,
            "export_hash": export_doc.get("export_hash", ""),
            "status": "accepted",
        }
        self.ledger_entries.append(entry)
        return entry


class FakeRichAuthoringStages:
    """Checkpoint-friendly rich stages for runner tests.

    The fake creates the same disposable generated package boundary as the
    production stages, while each method remains a separate graph call. A
    configured failure is raised once so tests can prove resume starts at the
    failed checkpoint rather than rerunning earlier stages.
    """

    def __init__(self, *, with_stage_attestations: bool = True, fail_stage: str | None = None) -> None:
        self.with_stage_attestations = with_stage_attestations
        self.fail_stage = fail_stage
        self.failed = False
        self.calls: list[str] = []

    def run(self, stage: str, state: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(stage)
        if stage == self.fail_stage and not self.failed:
            self.failed = True
            raise RuntimeError(f"injected {stage} failure")
        result: dict[str, Any] = {"generation_stage": stage}
        if stage == "draft_authored":
            generated_dir = Path(state["output_root"]) / "generated"
            generated_dir.mkdir(parents=True, exist_ok=True)
            fixture_source = Path(state["fixture_source"])
            for name in K_LESSON_GENERATION_REQUIRED_FILES:
                shutil.copy2(fixture_source / name, generated_dir / name)
            if self.with_stage_attestations:
                write_passing_stage_attestations(generated_dir)
            receipt_path = Path(state["output_root"]) / "llm_receipt.json"
            receipt_path.write_text(json.dumps({"mode": "fake"}), encoding="utf-8")
            result["llm_receipt_path"] = str(receipt_path)
        if stage == "complete":
            generated_dir = Path(state["output_root"]) / "generated"
            result["generated_source_dir"] = str(generated_dir)
            result["fixture_source"] = str(generated_dir)
        return result

    def author(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the draft stage."""
        return self.run("draft_authored", state)

    def review(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the lesson review stage."""
        return self.run("draft_reviewed", state)

    def normalize(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the normalization stage."""
        return self.run("draft_normalized", state)

    def preservation(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the preservation stage."""
        return self.run("normalization_preserved", state)

    def intent_review(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the intent-review stage."""
        return self.run("intent_reviewed", state)

    def exercise_author(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the exercise-author stage."""
        return self.run("exercises_authored", state)

    def exercise_compile(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the exercise-compile stage."""
        return self.run("exercises_compiled", state)

    def exercise_verify(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the exercise-verification stage."""
        return self.run("exercises_verified", state)

    def coverage(self, state: dict[str, Any]) -> dict[str, Any]:
        """Run the coverage stage."""
        if not self.with_stage_attestations:
            self.calls.append("coverage")
            raise ValueError("stage attestations are missing")
        return self.run("complete", state)


class FakeRichAuthorAgent:
    """Return prose, review, handoff, and final exercise fixture responses.

    Each stage is routed by the role sentence that opens its prompt, so guidance
    inside one prompt can never redirect another stage. Reviewer prompts consume
    payloads from ``review_responses`` when it is set (a dict is serialized, a
    str is returned verbatim to model a contract violation, an Exception is
    raised to model an outage), and otherwise return the default passing review
    for the fixture's grammar plan. Repair prompts get their own payloads so
    bounded-repair tests can observe the exact author call.
    """

    instances: list[FakeRichAuthorAgent] = []
    draft_text = """# A short contextual chapter

Mia: Kommer du i morgen?

This draft explains the question pattern with examples and practice.

{{checkpoint
handle: identify-question
objective_ref: obj-question-order
bloom: understand
evidence: Collect observable learner evidence for the choose target.
}}

{{checkpoint
handle: choose-infinitive
objective_ref: obj-question-order
bloom: understand
evidence: Collect observable learner evidence for the choose target.
}}

{{checkpoint
handle: judge-question-order
objective_ref: obj-question-order
bloom: understand
evidence: Collect observable learner evidence for the judge target.
}}

{{checkpoint
handle: find-fix-modal
objective_ref: obj-question-order
bloom: analyze
evidence: Collect observable learner evidence for the find_fix target.
}}

{{checkpoint
handle: judge-fronted-time
objective_ref: obj-question-order
bloom: understand
evidence: Collect observable learner evidence for the judge target.
}}

{{checkpoint
handle: build-fronted-time
objective_ref: obj-question-order
bloom: apply
evidence: Collect observable learner evidence for the build target.
}}

{{checkpoint
handle: recall-question
objective_ref: obj-question-order
bloom: apply
evidence: Collect observable learner evidence for the recall_fill target.
}}"""
    repair_draft_text: str | None = None
    repair_edit_response: dict[str, Any] | None = None
    repair_edit_responses: list[dict[str, Any] | str] | None = None
    normalized_payload: dict[str, str] | None = None
    normalized_repair_payload: dict[str, str] | None = None
    normalized_edit_response: dict[str, Any] | None = None
    normalized_edit_responses: list[dict[str, Any] | str] | None = None
    exercise_payload: dict[str, str] | None = None
    exercise_payloads: list[dict[str, str]] | None = None
    lesson_review_payload: dict[str, Any] | None = None
    preservation_payload: dict[str, Any] | None = None
    review_responses: list[dict[str, Any] | str | Exception] | None = None

    def __init__(self, *, name: str, client: str = "fake", model: str = "openai/test-model") -> None:
        self.name = name
        self.client = client
        self.model = model
        self.agent = None
        self.variant = None
        self.prompts: list[str] = []
        self.__class__.instances.append(self)

    def invoke_response(self, prompt: str) -> LlmResponse:
        self.prompts.append(prompt)
        payload = self._payload_for_prompt(prompt)
        return LlmResponse(
            text=payload if isinstance(payload, str) else json.dumps(payload),
            client=self.client,
            model=self.model,
            agent=self.agent,
            variant=self.variant,
            latency_ms=7,
            attempts=(
                AttemptOutcome(
                    self.client,
                    self.model,
                    "ok",
                    agent=self.agent,
                    variant=self.variant,
                ),
            ),
        )

    def _payload_for_prompt(self, prompt: str) -> dict[str, Any] | str:
        """Resolve a deterministic response for one configured prompt."""
        if "Bounded draft repair instructions" in prompt:
            return self._draft_repair_payload()
        if "Representation repair instructions" in prompt:
            return self._normalization_repair_payload()
        if prompt.startswith("You are the independent lesson-content reviewer"):
            return self._review_payload("lesson")
        if prompt.startswith("You are the normalization-preservation reviewer"):
            return self._review_payload("preservation")
        if prompt.startswith("You are the source-normalization node"):
            return self._normalization_payload()
        if prompt.startswith("You are the exercise-author node"):
            return self._exercise_payload()
        return self.__class__.draft_text

    def _draft_repair_payload(self) -> dict[str, Any] | str:
        """Return the next configured draft repair response."""
        responses = self.__class__.repair_edit_responses
        if responses:
            return responses.pop(0)
        return self.__class__.repair_edit_response or self.__class__.repair_draft_text or self.__class__.draft_text

    def _normalization_repair_payload(self) -> dict[str, Any]:
        """Return the next configured normalization repair response."""
        responses = self.__class__.normalized_edit_responses
        if responses:
            return responses.pop(0)
        return self.__class__.normalized_edit_response or {
            "edits": [
                {
                    "artifact": "lesson_md",
                    "finding_ref": "dropped_example:1",
                    "old_text": "The two Norwegian sentences contain the same main words.",
                    "new_text": (
                        "The two Norwegian sentences contain the same main words. "
                        "The restored example remains part of the model."
                    ),
                    "reason": "Restore the reviewed model explanation without rewriting the chapter.",
                }
            ]
        }

    def _normalization_payload(self) -> dict[str, str]:
        """Return the configured source-normalizer payload."""
        payload = self.__class__.normalized_payload or {
            "lesson_md": (K_CATALOG_PACKAGE_FIXTURE_ROOT / "lesson.md").read_text(encoding="utf-8"),
            "exercise_requests_yaml": fixture_exercise_requests(),
        }
        if "exercises_yaml" in payload and "exercise_requests_yaml" not in payload:
            return {
                "lesson_md": payload["lesson_md"],
                "exercise_requests_yaml": fixture_exercise_requests(),
            }
        return payload

    def _exercise_payload(self) -> dict[str, str]:
        """Return a legal exercise-author payload from configured or fixture data."""
        if self.__class__.exercise_payloads:
            payload = self.__class__.exercise_payloads.pop(0)
        elif self.__class__.exercise_payload is not None:
            payload = self.__class__.exercise_payload
        else:
            exercises = json.loads(
                json.dumps(
                    yaml.safe_load((K_CATALOG_PACKAGE_FIXTURE_ROOT / "exercises.yaml").read_text(encoding="utf-8"))
                )
            )
            for exercise in exercises:
                if exercise.get("op") == "judge":
                    exercise["bloom"] = "understand"
                exercise.pop("derived_from", None)
            payload = {"exercises_yaml": yaml.safe_dump(exercises, allow_unicode=True, sort_keys=False)}
        return self._normalize_exercise_payload(payload)

    def _normalize_exercise_payload(self, payload: dict[str, Any]) -> dict[str, str]:
        """Normalize fixture operation metadata to the current exercise contract."""
        if not isinstance(payload.get("exercises_yaml"), str):
            return payload
        exercises = yaml.safe_load(payload["exercises_yaml"])
        legal_bloom = {
            "choose": "understand",
            "recall_fill": "apply",
            "judge": "understand",
            "find_fix": "analyze",
            "build": "apply",
        }
        for exercise in exercises:
            if isinstance(exercise, dict) and exercise.get("op") in legal_bloom:
                exercise["bloom"] = legal_bloom[exercise["op"]]
            if isinstance(exercise, dict):
                exercise.pop("derived_from", None)
        return {"exercises_yaml": yaml.safe_dump(exercises, allow_unicode=True, sort_keys=False)}

    def _review_payload(self, kind: str) -> dict[str, Any] | str:
        """Resolve one explicit reviewer response for a reviewer prompt."""
        responses = self.__class__.review_responses
        if responses is not None:
            entry = responses.pop(0)
            if isinstance(entry, Exception):
                raise entry
            return entry
        if kind == "lesson":
            return self.__class__.lesson_review_payload or valid_quality_review("grammar")
        return self.__class__.preservation_payload or {
            "verdict": "pass",
            "summary": "Every reviewed teaching unit survived normalization.",
            "findings": [],
        }


def fixture_exercise_requests() -> str:
    """Build compact checkpoint requests from the compiler fixture for stage tests."""
    import yaml

    exercises = yaml.safe_load((K_CATALOG_PACKAGE_FIXTURE_ROOT / "exercises.yaml").read_text(encoding="utf-8"))
    bloom_by_operation = {
        "choose": "understand",
        "recall_fill": "apply",
        "judge": "understand",
        "find_fix": "analyze",
        "build": "apply",
        "match_pairs": "remember",
        "categorize": "analyze",
        "write": "apply",
        "speak": "apply",
    }
    route_by_operation = {
        "choose": "meaning_selection",
        "recall_fill": "bounded_retrieval",
        "judge": "sentence_judgement",
        "find_fix": "form_repair",
        "build": "sentence_construction",
        "match_pairs": "pair_matching",
        "categorize": "contrast_classification",
        "write": "open_production",
        "speak": "spoken_production",
    }
    requests = [
        {
            "handle": item["handle"],
            "objective_ref": item["objective"],
            "bloom": bloom_by_operation.get(item["op"], item.get("bloom", "apply")),
            "evidence_route": route_by_operation[item["op"]],
            "evidence": f"Collect observable learner evidence for the {item['op']} target.",
        }
        for item in exercises
    ]
    return yaml.safe_dump(requests, allow_unicode=True, sort_keys=False)


class FakeStandaloneVerifier:
    """Model the blocking standalone-quality gate with a configured report.

    The real gate reviews the exact learner-visible payload with no lesson
    context; tests use this fake to state that outcome explicitly and to
    observe the exact calls against the compiled lesson.
    """

    def __init__(self, report: dict[str, Any] | None = None) -> None:
        self.report = report or {
            "status": "pass",
            "total": 7,
            "context_dependent": [],
            "deterministic_findings": [],
        }
        self.calls: list[dict[str, Any]] = []

    def __call__(self, lesson: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(lesson)
        return dict(self.report)


class FakeClosedTaskVerifier:
    """Model the blinded closed-task reviewer boundary with a configured report.

    The real verifier solves the blinded projection independently; tests use
    this fake to state that outcome explicitly and to observe the exact call
    count against the compiled lesson.
    """

    def __init__(self, report: dict[str, Any] | None = None) -> None:
        self.report = report or {
            "status": "pass",
            "lesson_hash": "sha256:fake-closed-task-verifier",
            "total": 7,
            "matches": 7,
            "message": "fake closed-task reviewer matched every authored key",
        }
        self.calls: list[dict[str, Any]] = []

    def __call__(self, lesson: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(lesson)
        return dict(self.report)


class SequenceClosedTaskVerifier:
    """Return or raise a configured sequence of closed-review outcomes."""

    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, lesson: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(lesson)
        if not self.outcomes:
            raise AssertionError("closed-task verifier was called more times than configured")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)


class SequenceStandaloneVerifier:
    """Model the strict standalone gate with a sequence of configured outcomes.

    A non-passing report raises the same blocking error the strict production
    gate raises, so repair routing observes the real failure shape.
    """

    def __init__(self, outcomes: list[dict[str, Any] | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, lesson: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(lesson)
        if not self.outcomes:
            raise AssertionError("standalone reviewer was called more times than configured")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        if outcome.get("status") != "pass":
            raise StandaloneReviewError(dict(outcome))
        return dict(outcome)


__all__ = [
    "FakeRichAuthoringStages",
    "FakeLessonPackageDeps",
    "FakeClosedTaskVerifier",
    "FakeRichAuthorAgent",
    "FakeStandaloneVerifier",
    "SequenceClosedTaskVerifier",
    "SequenceStandaloneVerifier",
    "needs_repair_preservation_review",
    "needs_repair_quality_review",
    "valid_quality_review",
    "write_passing_stage_attestations",
]
