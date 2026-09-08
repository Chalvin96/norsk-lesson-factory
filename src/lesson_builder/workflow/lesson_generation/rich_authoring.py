"""Entry points: rich-authoring stage operations and ``resolve_plan_terminology_context``.

Workflow-owned rich-authoring stage implementation over the deterministic
authoring policy in ``rich_authoring_content.py``. This module owns the
scratch directory layout, stage sequencing, stage attestations, cache inputs,
and workflow receipt composition for the rich path; generation log serialization
and content hashes live in ``generation_log.py``. The configured author writes textbook
prose with typed checkpoint directives; a configured reviewer reviews those exact
draft bytes and may trigger one bounded exact-text edit; the workflow consumes
directives into learner markers and a frozen intent handoff before an edit-mode
normalizer converts the reviewed prose into the repository's Markdown/YAML lesson
source and routed exercise-request handoff; a preservation reviewer proves the
reviewed teaching content survived; a deterministic intent gate checks every
request before a third call fills exercise operations. The source compiler remains the only
authority for exportable structure, every stage records a distinct
content-addressed attestation, and every failure stays inspectable in
scratch.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import cast

import yaml
from pydantic import ValidationError

from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.application.operations.load_lesson import load_lesson
from lesson_builder.application.operations.load_terminology import load_terminology_registry
from lesson_builder.application.operations.repair_source import repair_exercise_source
from lesson_builder.application.operations.review_exercises import ExerciseReviewError
from lesson_builder.application.operations.review_exercises import verify_exercise_package
from lesson_builder.application.operations.review_standalone import K_STANDALONE_REVIEW_NEEDS_HUMAN
from lesson_builder.application.operations.review_standalone import StandaloneReviewError
from lesson_builder.application.operations.review_standalone import review_standalone_exercises
from lesson_builder.application.operations.rich_authoring_prompts import build_exercise_author_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_exercise_repair_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_lesson_repair_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_lesson_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_normalization_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_preservation_repair_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_preservation_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_rich_draft_prompt
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.clients.llm.base import extract_json_object
from lesson_builder.clients.llm.config import load_llm_job
from lesson_builder.clients.llm.exceptions import LlmException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.clients.llm.jobs import reviewer
from lesson_builder.clients.llm.jobs import runner_for_job
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.models.operations import render_evidence_route_guidance
from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.quality_review import NormalizationPreservationReview
from lesson_builder.domain.lesson.models.quality_review import QualityReviewValidation
from lesson_builder.domain.lesson.models.rich_authoring import ExercisePackage
from lesson_builder.domain.lesson.models.rich_authoring import LessonDraftEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizationEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizedPackage
from lesson_builder.domain.lesson.services.exercise_diagnostics import analyze_exercise_diagnostics
from lesson_builder.domain.lesson.services.quality_review import preservation_review_schema
from lesson_builder.domain.lesson.services.quality_review import quality_axes_for_kind
from lesson_builder.domain.lesson.services.quality_review import quality_review_schema
from lesson_builder.domain.lesson.services.quality_review import validate_preservation_review
from lesson_builder.domain.lesson.services.quality_review import validate_quality_review
from lesson_builder.domain.lesson.validation.exercise_envelope import validate_exercise_envelope
from lesson_builder.domain.lesson.validation.review_payloads import extract_answer_questions
from lesson_builder.domain.lesson.validation.terminology_registry import render_prompt_context
from lesson_builder.domain.lesson.validation.terminology_registry import resolve_concept_ids
from lesson_builder.formats.markdown.frontmatter import extract_frontmatter
from lesson_builder.formats.yaml import load_unique_yaml
from lesson_builder.formats.yaml import quote_unquoted_yaml_scalars
from lesson_builder.workflow.lesson_generation.checkpoints import assert_checkpoint_metadata_absent
from lesson_builder.workflow.lesson_generation.checkpoints import split_checkpoint_directives
from lesson_builder.workflow.lesson_generation.checkpoints import validate_routed_checkpoint_requests
from lesson_builder.workflow.lesson_generation.dependencies import ArtifactGenerationResult
from lesson_builder.workflow.lesson_generation.dependencies import approved_content_hash
from lesson_builder.workflow.lesson_generation.generation_log import (
    assert_file_matches_hash as _assert_file_matches_hash,
)
from lesson_builder.workflow.lesson_generation.generation_log import build_attempt_stage_log as _attempt_stage_log
from lesson_builder.workflow.lesson_generation.generation_log import build_failed_stage_log as _failed_stage_log
from lesson_builder.workflow.lesson_generation.generation_log import build_response_metadata as _build_response_metadata
from lesson_builder.workflow.lesson_generation.generation_log import build_stage_log as _stage_log
from lesson_builder.workflow.lesson_generation.generation_log import hash_config as _hash_config
from lesson_builder.workflow.lesson_generation.generation_log import hash_file as _hash_file
from lesson_builder.workflow.lesson_generation.generation_log import hash_package as _hash_package
from lesson_builder.workflow.lesson_generation.generation_log import hash_text as _hash_text
from lesson_builder.workflow.lesson_generation.generation_log import write_generation_log as _write_generation_log
from lesson_builder.workflow.lesson_generation.generation_log import write_stage_record as _write_stage_record
from lesson_builder.workflow.lesson_generation.rich_authoring_content import apply_lesson_draft_edits
from lesson_builder.workflow.lesson_generation.rich_authoring_content import apply_normalization_edits
from lesson_builder.workflow.lesson_generation.rich_authoring_content import complete_exercise_metadata
from lesson_builder.workflow.lesson_generation.rich_authoring_content import extract_exercise_requests
from lesson_builder.workflow.lesson_generation.rich_authoring_content import failed_exercise_handles
from lesson_builder.workflow.lesson_generation.rich_authoring_content import merge_failed_exercises
from lesson_builder.workflow.lesson_generation.rich_authoring_content import normalize_compiler_unsafe_markdown
from lesson_builder.workflow.lesson_generation.rich_authoring_content import unaffected_exercise_hashes
from lesson_builder.workflow.lesson_generation.rich_authoring_content import validate_exercise_alignment
from lesson_builder.workflow.lesson_generation.rich_authoring_content import validate_exercise_requests
from lesson_builder.workflow.lesson_generation.rich_authoring_content import validate_normalized_lesson_shape
from lesson_builder.workflow.lesson_generation.rich_authoring_exercise_repairs import repair_build_item
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXERCISE_AUTHOR_JOB
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXERCISES_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_LESSON_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_NORMALIZER_JOB
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_QUALITY_REVIEW_JOB
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
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_REQUESTS_FILE
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_STATUS_FAIL
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_STATUS_INVALID
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_STATUS_PASS
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_STATUS_UNAVAILABLE
from lesson_builder.workflow.lesson_generation.stage_attestations import StageAttestation
from lesson_builder.workflow.lesson_generation.stage_attestations import StageAttestations
from lesson_builder.workflow.lesson_generation.stage_attestations import StageStatus
from lesson_builder.workflow.lesson_generation.stage_attestations import package_source_hash
from lesson_builder.workflow.lesson_generation.stage_attestations import text_content_hash
from lesson_builder.workflow.lesson_generation.stage_attestations import write_stage_attestations
from lesson_builder.workspace.paths import WorkspacePaths


def resolve_plan_terminology_context(plan_text: str, *, repo_root: Path) -> str:
    """Load and render terminology selected by approved plan metadata."""
    metadata = _plan_metadata(plan_text)
    ids = metadata.get("terminology_ids", []) if isinstance(metadata, dict) else []
    ids = [value for value in ids if isinstance(value, str) and value.strip()] if isinstance(ids, list) else []
    if not ids:
        return ""
    registry = load_terminology_registry(WorkspacePaths(repo_root).terminology_registry)
    resolve_concept_ids(registry, ids)
    return render_prompt_context(registry, concept_ids=ids)


class ContentRemediationRequired(ValueError):
    """A content stage needs human remediation before artifact promotion."""

    def __init__(self, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.stage = stage


K_RICH_DRAFT_SUBDIR = "draft"
K_RICH_MAX_NORMALIZATION_ATTEMPTS = 2
K_RICH_REQUIREMENTS_REF = "plan.md"
K_RICH_DRAFT_FILE = "rich.md"
K_RICH_DRAFT_PRE_REPAIR_FILE = "rich.pre_repair.md"
K_RICH_DRAFT_PROMPT_FILE = "prompt.md"
K_RICH_DRAFT_INPUT_FILE = "stage_input.json"
K_RICH_DRAFT_PROSE_FILE = "prose.md"
K_RICH_LESSON_REPAIR_PROMPT_FILE = "repair_prompt.md"
K_RICH_NORMALIZATION_SUBDIR = "normalization"
K_RICH_NORMALIZATION_PROMPT_FILE = "prompt.md"
K_RICH_NORMALIZATION_RESPONSE_FILE = "response.json"
K_RICH_NORMALIZATION_INPUT_FILE = "stage_input.json"
K_RICH_CHECKPOINT_INTENTS_FILE = "checkpoint_intents.yaml"
K_RICH_NORMALIZATION_REPAIR_PROMPT_FILE = "repair_prompt.md"
K_RICH_PRESERVATION_PROMPT_FILE = "preservation_prompt.md"
K_RICH_PRESERVATION_REVIEW_FILE = "preservation_review.json"
K_RICH_REVIEW_SUBDIR = "review"
K_RICH_LESSON_REVIEW_PROMPT_FILE = "prompt.md"
K_RICH_LESSON_REVIEW_FILE = "lesson_review.json"
K_RICH_EXERCISE_SUBDIR = "exercise_author"
K_RICH_EXERCISE_PROMPT_FILE = "prompt.md"
K_RICH_EXERCISE_RESPONSE_FILE = "response.json"
K_RICH_EXERCISE_INPUT_FILE = "stage_input.json"
K_RICH_GENERATED_SUBDIR = "generated"
K_RICH_MAX_EXERCISE_ALIGNMENT_ATTEMPTS = 2
K_RICH_MAX_EXERCISE_SEMANTIC_REPAIRS = 1
K_RICH_MAX_LESSON_CONTENT_REPAIRS = 1
K_RICH_MAX_NORMALIZATION_REPAIRS = 1
K_RICH_MAX_REVIEW_REASKS = 1
K_RICH_MAX_EDIT_RESPONSE_ATTEMPTS = 2
K_RICH_DRAFT_POLICY_VERSION = "5"
K_RICH_NORMALIZATION_CACHE_POLICY_VERSION = "3"
K_RICH_EXERCISE_CACHE_POLICY_VERSION = "4"


@dataclass
class RichAuthoringRun:
    """Mutable shared inputs and receipts for one rich-authoring run."""

    plan_text: str
    root: Path
    repo_root: Path
    run_id: str
    job: str
    draft_dir: Path
    normalization_dir: Path
    review_dir: Path
    exercise_dir: Path
    generated_dir: Path
    author_config: dict[str, Any]
    normalizer_config: dict[str, Any]
    exercise_config: dict[str, Any]
    reviewer_config: dict[str, Any]
    terminology_context: str | None
    draft_prompt: str
    receipt: dict[str, Any]


@dataclass
class NormalizationResult:
    """Outputs retained from normalization and preservation review stages."""

    preservation: dict[str, Any]
    response_path: Path
    last_response: LlmResponse | None
    repaired: bool
    response_reused: bool = False


@dataclass
class NormalizedDraftResult:
    """Outputs retained after normalization before preservation review."""

    package: NormalizedPackage
    normalization_prompt: str
    response_reused: bool = False


@dataclass
class ExerciseAuthorResult:
    """Outputs retained from bounded exercise authoring and alignment."""

    package: ExercisePackage
    exercises_yaml: str
    exercise_prompt: str
    response_path: Path
    input_path: Path
    last_response: LlmResponse | None
    lesson_hash: str
    response_reused: bool = False


@dataclass
class ExerciseVerificationResult:
    """Outputs from strict exercise verification and its bounded repair."""

    internal_lesson: dict[str, Any]
    exercises_yaml: str
    verification: dict[str, Any]
    standalone_report: dict[str, Any] | None
    semantic_repairs: int


def complete_normalized_metadata(package: NormalizedPackage, plan_metadata: dict[str, Any]) -> NormalizedPackage:
    """Add compiler metadata and validate the operation-free exercise handoff."""
    lesson_md = normalize_compiler_unsafe_markdown(package.lesson_md)
    lesson_id = plan_metadata.get("lesson_id")
    if isinstance(lesson_id, str) and lesson_id:
        lesson_md, slug_count = re.subn(r"(?m)^slug:\s*.*$", f"slug: {lesson_id}", lesson_md, count=1)
        if slug_count != 1:
            raise ValueError("normalized lesson is missing slug metadata")
    frontmatter = extract_frontmatter(lesson_md)
    if not frontmatter:
        raise ValueError("normalized lesson is missing YAML front matter")
    if "grounding_mode:" not in frontmatter:
        lesson_md, grounding_count = re.subn(
            r"(?m)^(default_lang:\s*.*)$", r"\1\ngrounding_mode: grounded", lesson_md, count=1
        )
        if grounding_count != 1:
            raise ValueError("normalized lesson is missing default_lang metadata")
        frontmatter = extract_frontmatter(lesson_md)
    if "requirements_ref:" not in frontmatter:
        lesson_md, requirements_count = re.subn(
            r"(?m)^(grounding_mode:\s*.*)$",
            rf"\1\nrequirements_ref: {K_RICH_REQUIREMENTS_REF}",
            lesson_md,
            count=1,
        )
        if requirements_count != 1:
            raise ValueError("normalized lesson is missing grounding_mode metadata")
    requests_yaml = validate_exercise_requests(
        quote_unquoted_yaml_scalars(package.exercise_requests_yaml),
        _plan_objective_ids(plan_metadata),
        lesson_md,
    )
    return NormalizedPackage(lesson_md=lesson_md, exercise_requests_yaml=requests_yaml)


def repair_exercise_yaml_if_needed(package: ExercisePackage) -> ExercisePackage:
    """Repair transport-only YAML defects before domain validation."""
    candidate = package.exercises_yaml
    try:
        _validate_exercise_yaml(candidate)
    except yaml.YAMLError:
        repaired = quote_unquoted_yaml_scalars(candidate)
        if repaired == candidate:
            raise
        candidate = repaired
    raw = yaml.safe_load(candidate)
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                repair_build_item(item)
        candidate = yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)
    _validate_exercise_yaml(candidate)
    return package if candidate == package.exercises_yaml else package.model_copy(update={"exercises_yaml": candidate})


def initialize_rich_run(
    *,
    plan_source: Path,
    output_root: Path,
    repo_root: Path,
    run_id: str,
    job: str,
) -> RichAuthoringRun:
    """Load rich-authoring inputs, create stage directories, and seed the receipt."""
    plan_text = (Path(plan_source) / "plan.md").read_text(encoding="utf-8")
    root = Path(output_root)
    directories = {
        "draft_dir": root / K_RICH_DRAFT_SUBDIR,
        "normalization_dir": root / K_RICH_NORMALIZATION_SUBDIR,
        "review_dir": root / K_RICH_REVIEW_SUBDIR,
        "exercise_dir": root / K_RICH_EXERCISE_SUBDIR,
        "generated_dir": root / K_RICH_GENERATED_SUBDIR,
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    author_config = _load_job_config(job, repo_root)
    normalizer_config = _load_job_config(K_LESSON_GENERATION_NORMALIZER_JOB, repo_root)
    exercise_config = _load_job_config(K_LESSON_GENERATION_EXERCISE_AUTHOR_JOB, repo_root)
    reviewer_config = _load_job_config(K_LESSON_GENERATION_QUALITY_REVIEW_JOB, repo_root)
    terminology_context = resolve_plan_terminology_context(plan_text, repo_root=repo_root)
    draft_prompt = build_rich_draft_prompt(
        plan_text,
        plan_kind=_plan_kind(plan_text),
        terminology_context=terminology_context,
    )
    receipt: dict[str, Any] = {
        "mode": "rich_three_stage",
        "job": job,
        "job_name": author_config["job"],
        "model": author_config["model"],
        "variant": author_config["variant"],
        "agent_template": author_config["agent_template"],
        "normalizer_job": normalizer_config["job"],
        "normalizer_mode": "edit",
        "exercise_author_job": exercise_config["job"],
        "lesson_reviewer_job": reviewer_config["job"],
        "run_id": run_id,
        "stages": [],
        "stage_receipts": {},
    }
    (directories["draft_dir"] / K_RICH_DRAFT_PROMPT_FILE).write_text(draft_prompt, encoding="utf-8")
    return RichAuthoringRun(
        plan_text=plan_text,
        root=root,
        repo_root=Path(repo_root),
        run_id=run_id,
        job=job,
        **directories,
        author_config=author_config,
        normalizer_config=normalizer_config,
        exercise_config=exercise_config,
        reviewer_config=reviewer_config,
        terminology_context=terminology_context,
        draft_prompt=draft_prompt,
        receipt=receipt,
    )


def author_or_reuse_draft(run: RichAuthoringRun, draft_path: Path) -> str:
    """Reuse a cache-bound draft or invoke the configured draft author."""
    input_path = run.draft_dir / K_RICH_DRAFT_INPUT_FILE
    cache_inputs = {
        "prompt_hash": _hash_text(run.draft_prompt),
        "policy_version": K_RICH_DRAFT_POLICY_VERSION,
        "job_hash": _hash_config(run.author_config),
    }
    if (
        draft_path.is_file()
        and draft_path.read_text(encoding="utf-8").strip()
        and _stage_input_matches(input_path, **cache_inputs)
    ):
        draft_text = draft_path.read_text(encoding="utf-8").strip()
        run.receipt["stages"].append(
            {"name": "draft_author", "status": "reused", "response_hash": _hash_text(draft_text)}
        )
        return draft_text
    try:
        response = runner_for_job(
            run.author_config["job"],
            repo_root=Path(run.author_config["repo_root"]),
        ).invoke_response(run.draft_prompt)
        draft_text = response.text.strip()
        if not draft_text:
            raise LlmParseException("rich draft author returned empty text")
        draft_path.write_text(draft_text + "\n", encoding="utf-8")
        _write_stage_input(input_path, **cache_inputs)
        run.receipt["stages"].append(_stage_log("draft_author", run.draft_prompt, response))
        return draft_text
    except LlmException as exc:
        run.receipt["stages"].append(_failed_stage_log("draft_author", run.draft_prompt, exc))
        _write_generation_log(run.root, run.receipt)
        raise


def split_checkpoint(run: RichAuthoringRun, draft_text: str) -> tuple[str, str]:
    """Split typed checkpoint directives and persist the operation-free prose."""
    try:
        checkpoint_split = split_checkpoint_directives(draft_text)
    except ValueError as exc:
        error = f"typed checkpoint split rejected the reviewed draft: {exc}"
        run.receipt["stage_receipts"][K_STAGE_INTENT_REVIEW] = {"status": K_STAGE_STATUS_FAIL, "error": error}
        run.receipt["stages"].append({"name": "checkpoint_split", "status": "failed", "error": error})
        _write_generation_log(run.root, run.receipt)
        raise ContentRemediationRequired(error, stage=K_STAGE_INTENT_REVIEW) from exc
    prose = checkpoint_split.prose_md
    intents = checkpoint_split.intents_yaml
    (run.draft_dir / K_RICH_DRAFT_PROSE_FILE).write_text(prose, encoding="utf-8")
    (run.normalization_dir / K_RICH_CHECKPOINT_INTENTS_FILE).write_text(intents, encoding="utf-8")
    run.receipt["stages"].append(
        {
            "name": "checkpoint_split",
            "status": "valid",
            "request_count": len(yaml.safe_load(intents)),
            "intents_hash": _hash_text(intents),
        }
    )
    return prose, intents


def normalize_rich_draft(
    run: RichAuthoringRun,
    *,
    draft_text: str,
    checkpoint_intents_yaml: str,
) -> NormalizedDraftResult:
    """Normalize reviewed prose into a typed package before preservation review."""
    plan_metadata = _plan_metadata(run.plan_text)
    prompt = build_normalization_prompt(
        run.plan_text,
        draft_text,
        checkpoint_intents_yaml,
        plan_kind=_plan_kind(run.plan_text),
    )
    (run.normalization_dir / K_RICH_NORMALIZATION_PROMPT_FILE).write_text(prompt, encoding="utf-8")
    response_path = run.normalization_dir / K_RICH_NORMALIZATION_RESPONSE_FILE
    input_path = run.normalization_dir / K_RICH_NORMALIZATION_INPUT_FILE
    draft_hash = _hash_text(draft_text + "\0" + checkpoint_intents_yaml)
    package = _load_cached_normalized_package(
        response_path,
        run.plan_text,
        draft_hash=draft_hash,
        input_path=input_path,
        prompt_hash=_hash_text(prompt),
        job_hash=_hash_config(run.normalizer_config),
    )
    last_response: LlmResponse | None = None
    if package is not None:
        run.receipt["stages"].append(
            {
                "name": "source_normalizer",
                "status": "reused",
                "response_hash": _hash_text(response_path.read_text(encoding="utf-8")),
            }
        )
    else:
        try:
            package, attempts = _invoke_normalizer(
                job_config=run.normalizer_config,
                prompt=prompt,
                plan_metadata=plan_metadata,
            )
            try:
                package = complete_normalized_metadata(package, plan_metadata)
            except ValueError as exc:
                run.receipt["stages"].append(
                    {"name": "source_compile", "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                )
                _write_generation_log(run.root, run.receipt)
                raise
            last_response = attempts[-1][1]
            response_path.write_text(
                json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            _write_stage_input(
                input_path,
                draft_hash=draft_hash,
                plan_hash=_hash_text(run.plan_text),
                prompt_hash=_hash_text(prompt),
                policy_version=K_RICH_NORMALIZATION_CACHE_POLICY_VERSION,
                job_hash=_hash_config(run.normalizer_config),
            )
            run.receipt["stages"].append(_attempt_stage_log("source_normalizer", attempts))
        except LlmException as exc:
            run.receipt["stages"].append(_failed_stage_log("source_normalizer", prompt, exc))
            _write_generation_log(run.root, run.receipt)
            raise
    validate_checkpoint_boundary(run, package, checkpoint_intents_yaml, phase="normalized")
    return NormalizedDraftResult(
        package=package,
        normalization_prompt=prompt,
        response_reused=last_response is None,
    )


def validate_checkpoint_boundary(
    run: RichAuthoringRun,
    package: NormalizedPackage,
    checkpoint_intents_yaml: str,
    *,
    phase: str,
) -> None:
    """Require a normalized package to keep checkpoint directives at the boundary."""
    try:
        validate_routed_checkpoint_requests(package.exercise_requests_yaml, checkpoint_intents_yaml)
        assert_checkpoint_metadata_absent(package.lesson_md)
    except ValueError as exc:
        error = f"{phase} checkpoint boundary is invalid: {exc}"
        run.receipt["stages"].append({"name": "checkpoint_boundary", "status": "failed", "error": error})
        _write_generation_log(run.root, run.receipt)
        raise ContentRemediationRequired(error, stage=K_STAGE_INTENT_REVIEW) from exc


def extract_intents(
    run: RichAuthoringRun,
    package: NormalizedPackage,
) -> tuple[NormalizedPackage, str, str, str]:
    """Extract and gate operation-free exercise requests from the package."""
    try:
        package = package.model_copy(
            update={"exercise_requests_yaml": quote_unquoted_yaml_scalars(package.exercise_requests_yaml)}
        )
        extracted_requests, intent_findings = extract_exercise_requests(
            package,
            _plan_objective_ids(_plan_metadata(run.plan_text)),
        )
    except ValueError as exc:
        error = f"exercise intent extraction rejected the normalized handoff: {exc}"
        run.receipt["stage_receipts"][K_STAGE_INTENT_REVIEW] = {"status": K_STAGE_STATUS_FAIL, "error": error}
        run.receipt["stages"].append({"name": "intent_review", "status": "failed", "error": error})
        _write_generation_log(run.root, run.receipt)
        raise ContentRemediationRequired(error, stage=K_STAGE_INTENT_REVIEW) from exc
    package = package.model_copy(update={"exercise_requests_yaml": extracted_requests})
    requests_hash = _hash_text(extracted_requests)
    lesson_hash = _hash_text(package.lesson_md)
    if intent_findings:
        run.receipt["stage_receipts"][K_STAGE_INTENT_REVIEW] = {
            "status": K_STAGE_STATUS_FAIL,
            "findings": intent_findings,
            "requests_hash": requests_hash,
        }
        run.receipt["stages"].append({"name": "intent_review", "status": "failed", "error": "; ".join(intent_findings)})
        _write_generation_log(run.root, run.receipt)
        raise ContentRemediationRequired(
            f"exercise intent review rejected the handoff before exercise authoring: {'; '.join(intent_findings)}",
            stage=K_STAGE_INTENT_REVIEW,
        )
    run.receipt["stage_receipts"][K_STAGE_INTENT_REVIEW] = {
        "status": K_STAGE_STATUS_PASS,
        "requests_hash": requests_hash,
        "lesson_hash": lesson_hash,
    }
    run.receipt["stages"].append({"name": "intent_review", "status": "valid", "requests_hash": requests_hash})
    (run.normalization_dir / "exercise_requests.yaml").write_text(extracted_requests, encoding="utf-8")
    run.receipt["stages"].append(
        {
            "name": "intent_extraction",
            "status": "valid",
            "requests_hash": requests_hash,
            "request_count": len(yaml.safe_load(extracted_requests)),
        }
    )
    return package, extracted_requests, requests_hash, lesson_hash


def author_exercises(
    run: RichAuthoringRun,
    *,
    package: NormalizedPackage,
    requests_hash: str,
    lesson_hash: str,
) -> ExerciseAuthorResult:
    """Author exercises with a bounded cache-aware alignment retry."""
    prompt = build_exercise_author_prompt(
        plan_text=run.plan_text,
        lesson_md=package.lesson_md,
        exercise_requests_yaml=package.exercise_requests_yaml,
        terminology_context=run.terminology_context,
    )
    (run.exercise_dir / K_RICH_EXERCISE_PROMPT_FILE).write_text(prompt, encoding="utf-8")
    response_path = run.exercise_dir / K_RICH_EXERCISE_RESPONSE_FILE
    input_path = run.exercise_dir / K_RICH_EXERCISE_INPUT_FILE
    exercise_package = _load_cached_exercise_package(
        response_path,
        input_path=input_path,
        lesson_hash=lesson_hash,
        requests_hash=requests_hash,
        prompt_hash=_hash_text(prompt),
        job_hash=_hash_config(run.exercise_config),
    )
    last_response: LlmResponse | None = None
    exercises_yaml: str | None = None
    alignment_error: ValueError | None = None
    for alignment_attempt in range(K_RICH_MAX_EXERCISE_ALIGNMENT_ATTEMPTS):
        exercise_package, response = _load_or_author_exercise(
            run,
            package=exercise_package,
            response_path=response_path,
            prompt=prompt,
            alignment_error=alignment_error,
            attempt=alignment_attempt,
        )
        if response is not None:
            last_response = response
        try:
            exercises_yaml = _align_exercise_package(
                exercise_package,
                plan_text=run.plan_text,
                lesson_md=package.lesson_md,
                requests_yaml=package.exercise_requests_yaml,
            )
        except ValueError as exc:
            alignment_error = exc
            if alignment_attempt + 1 >= K_RICH_MAX_EXERCISE_ALIGNMENT_ATTEMPTS:
                run.receipt["stages"].append(
                    {"name": "exercise_alignment", "status": "failed", "error": f"{type(exc).__name__}: {exc}"}
                )
                _write_generation_log(run.root, run.receipt)
                raise ContentRemediationRequired(str(exc), stage=K_STAGE_EXERCISE_VERIFICATION) from exc
            exercise_package = None
            continue
        break
    if exercises_yaml is None or exercise_package is None:
        raise RuntimeError("unreachable")
    if last_response is not None:
        response_path.write_text(
            json.dumps(exercise_package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_stage_input(
            input_path,
            lesson_hash=lesson_hash,
            requests_hash=requests_hash,
            prompt_hash=_hash_text(prompt),
            policy_version=K_RICH_EXERCISE_CACHE_POLICY_VERSION,
            job_hash=_hash_config(run.exercise_config),
        )
    return ExerciseAuthorResult(
        package=exercise_package,
        exercises_yaml=exercises_yaml,
        exercise_prompt=prompt,
        response_path=response_path,
        input_path=input_path,
        last_response=last_response,
        response_reused=last_response is None,
        lesson_hash=lesson_hash,
    )


def compile_generated_package(
    run: RichAuthoringRun,
    *,
    package: NormalizedPackage,
    exercises_yaml: str,
    lesson_hash: str,
) -> dict[str, Any]:
    """Write and compile the generated package, recording its source stage."""
    (run.generated_dir / "plan.md").write_text(run.plan_text, encoding="utf-8")
    (run.generated_dir / K_LESSON_GENERATION_LESSON_FILE).write_text(package.lesson_md, encoding="utf-8")
    (run.generated_dir / K_LESSON_GENERATION_EXERCISES_FILE).write_text(exercises_yaml, encoding="utf-8")
    _assert_file_matches_hash(
        run.generated_dir / K_LESSON_GENERATION_LESSON_FILE,
        lesson_hash,
        "exercise author changed immutable lesson prose",
    )
    _refresh_approved_source_hash(run.generated_dir / "plan.md", run.generated_dir)
    try:
        lesson = _load_normalized_source(run.generated_dir)
    except (OSError, ValueError) as exc:
        run.receipt["stages"].append(
            {
                "name": "source_compile",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "source_hash": _hash_package(
                    NormalizedPackage(lesson_md=package.lesson_md, exercise_requests_yaml=exercises_yaml)
                ),
            }
        )
        _write_generation_log(run.root, run.receipt)
        raise
    run.receipt["stages"].append(
        {
            "name": "source_compile",
            "status": "valid",
            "source_hash": _hash_package(
                NormalizedPackage(lesson_md=package.lesson_md, exercise_requests_yaml=exercises_yaml)
            ),
        }
    )
    _record_exercise_diagnostics(lesson, run.receipt, root=run.root)
    return lesson.model_dump(mode="json")


def verify_rich_exercises(
    run: RichAuthoringRun,
    *,
    package: NormalizedPackage,
    author: ExerciseAuthorResult,
    internal_lesson: dict[str, Any],
    verifier: Callable[[dict[str, Any]], dict[str, Any]],
    standalone: Callable[[dict[str, Any]], dict[str, Any]],
    lesson_hash: str,
    requests_hash: str,
) -> ExerciseVerificationResult:
    """Run strict verifiers and apply at most one handle-scoped repair."""
    verification: dict[str, Any] | None = None
    standalone_report: dict[str, Any] | None = None
    semantic_repairs = 0
    while True:
        try:
            verification = verifier(internal_lesson)
            standalone_report = standalone(internal_lesson)
            break
        except (ExerciseReviewError, StandaloneReviewError) as exc:
            verification = _verification_from_failure(exc, run.receipt)
            failed_handles = failed_exercise_handles(verification)
            if not _is_exercise_repair_allowed(verification, failed_handles, semantic_repairs):
                _record_verifier_failure(run, verification, exc)
                raise ContentRemediationRequired(str(exc), stage=K_STAGE_EXERCISE_VERIFICATION) from exc
            internal_lesson, author.exercises_yaml = _repair_exercises_after_failure(
                run,
                package=package,
                author=author,
                internal_lesson=internal_lesson,
                exercises_yaml=author.exercises_yaml,
                failed_handles=failed_handles,
                verification=verification,
                lesson_hash=lesson_hash,
                requests_hash=requests_hash,
                semantic_repairs=semantic_repairs,
            )
            semantic_repairs += 1
    if verification is None:
        raise RuntimeError("unreachable")
    return ExerciseVerificationResult(
        internal_lesson=internal_lesson,
        exercises_yaml=author.exercises_yaml,
        verification=verification,
        standalone_report=standalone_report,
        semantic_repairs=semantic_repairs,
    )


def record_verifier_success(run: RichAuthoringRun, verification: ExerciseVerificationResult) -> None:
    """Record the successful exercise-verifier stage before coverage runs."""
    status = verification.verification.get("status", "needs_human")
    if verification.standalone_report is not None:
        run.receipt["standalone_exercise_review"] = verification.standalone_report
    run.receipt["exercise_verification"] = verification.verification
    run.receipt["stage_receipts"][K_STAGE_EXERCISE_VERIFICATION] = {
        "status": status,
        "lesson_hash": verification.verification.get("lesson_hash"),
        "exercise_count": verification.verification.get("total"),
        "semantic_repairs": verification.semantic_repairs,
        "standalone_status": (verification.standalone_report.get("status") if verification.standalone_report else None),
    }
    run.receipt["stages"].append(
        {
            "name": "exercise_verifier",
            "status": status,
            "lesson_hash": verification.verification.get("lesson_hash"),
            "exercise_count": verification.verification.get("total"),
        }
    )


def record_coverage(run: RichAuthoringRun, coverage: dict[str, Any]) -> None:
    """Persist coverage status and fail closed when required evidence is absent."""
    run.receipt["coverage"] = coverage
    coverage_status = K_STAGE_STATUS_PASS if coverage["status"] == K_STAGE_STATUS_PASS else K_STAGE_STATUS_FAIL
    run.receipt["stage_receipts"][K_STAGE_COVERAGE] = {
        "status": coverage_status,
        "findings": coverage["findings"],
    }
    if coverage["status"] != K_STAGE_STATUS_PASS:
        run.receipt["stages"].append({"name": "coverage", "status": "failed", "error": "; ".join(coverage["findings"])})
        _write_generation_log(run.root, run.receipt)
        raise ContentRemediationRequired(
            "deterministic required coverage failed: " + "; ".join(coverage["findings"]),
            stage=K_STAGE_COVERAGE,
        )
    run.receipt["stages"].append({"name": "coverage", "status": coverage["status"]})


def finish_rich_run(
    run: RichAuthoringRun,
    *,
    lesson_review: dict[str, Any],
    normalization: NormalizationResult,
    package: NormalizedPackage,
    author: ExerciseAuthorResult,
    verification: ExerciseVerificationResult,
    lesson_hash: str,
    requests_hash: str,
) -> ArtifactGenerationResult:
    """Write final attestations and receipt after every generation gate passes."""
    verification_status = verification.verification.get("status", "needs_human")
    (run.generated_dir / K_STAGE_REQUESTS_FILE).write_text(package.exercise_requests_yaml, encoding="utf-8")
    source_hash = package_source_hash(
        plan_text=(run.generated_dir / "plan.md").read_text(encoding="utf-8"),
        lesson_text=package.lesson_md,
        exercises_text=verification.exercises_yaml,
    )
    attestations = StageAttestations(
        stages={
            K_STAGE_LESSON_REVIEW: lesson_review["attestation"],
            K_STAGE_NORMALIZATION_REVIEW: normalization.preservation["attestation"].model_copy(
                update={"lesson_hash": lesson_hash, "requests_hash": requests_hash}
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
                status=cast(StageStatus, verification_status),
                policy_version=K_EXERCISE_VERIFICATION_POLICY_VERSION,
                source_hash=source_hash,
                lesson_hash=lesson_hash,
                details={
                    "total": verification.verification.get("total"),
                    "matches": verification.verification.get("matches"),
                    "open_handles": verification.verification.get("open_handles", []),
                    "attempt_surface_status": ((verification.verification.get("attempt_surface") or {}).get("status")),
                    "open_rubrics_status": ((verification.verification.get("open_rubrics") or {}).get("status")),
                    "standalone_status": (
                        verification.standalone_report.get("status") if verification.standalone_report else None
                    ),
                    "standalone_context_dependent": (
                        verification.standalone_report.get("context_dependent", [])
                        if verification.standalone_report
                        else []
                    ),
                },
            ),
            K_STAGE_COVERAGE: StageAttestation(
                stage=K_STAGE_COVERAGE,
                status=K_STAGE_STATUS_PASS,
                policy_version=K_COVERAGE_POLICY_VERSION,
                source_hash=source_hash,
                details={"findings": run.receipt["coverage"]["findings"]},
            ),
            K_STAGE_ARTIFACT_COMPILATION: StageAttestation(
                stage=K_STAGE_ARTIFACT_COMPILATION,
                status=K_STAGE_STATUS_PASS,
                policy_version=K_ARTIFACT_COMPILATION_POLICY_VERSION,
                source_hash=source_hash,
                lesson_hash=lesson_hash,
            ),
        }
    )
    write_stage_attestations(run.generated_dir, attestations)
    run.receipt["stage_attestations"] = {stage: item.status for stage, item in attestations.stages.items()}
    run.receipt["normalizer_response"] = (
        _build_response_metadata(normalization.last_response)
        if normalization.last_response is not None
        else {
            "status": "reused" if normalization.response_reused else "completed",
            "response_hash": _hash_text(normalization.response_path.read_text(encoding="utf-8")),
        }
    )
    run.receipt["exercise_author_response"] = (
        _build_response_metadata(author.last_response)
        if author.last_response is not None
        else {
            "status": "reused" if author.response_reused else "completed",
            "response_hash": _hash_text(author.response_path.read_text(encoding="utf-8")),
        }
    )
    lesson_file = run.generated_dir / K_LESSON_GENERATION_LESSON_FILE
    run.receipt["lesson_integrity"] = {
        "before_exercise_author": author.lesson_hash,
        "after_exercise_author": _hash_file(lesson_file),
        "unchanged": _hash_file(lesson_file) == author.lesson_hash,
    }
    run.receipt["normalization_repaired"] = normalization.repaired
    receipt_path = _write_generation_log(run.root, run.receipt)
    return ArtifactGenerationResult(
        source_dir=run.generated_dir,
        receipt_path=receipt_path,
        receipt=run.receipt,
    )


def review_lesson_draft(
    *,
    author_config: dict[str, Any],
    reviewer_config: dict[str, Any],
    root: Path,
    review_dir: Path,
    draft_path: Path,
    plan_text: str,
    draft_prompt: str,
    draft_text: str,
    terminology_context: str | None,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Review the exact draft bytes and allow one bounded draft-only repair.

    The reviewer judges the draft before normalization. An unavailable or
    contract-invalid review fails closed, and a `needs_repair` verdict triggers
    at most one exact-text edit by the original draft author followed by a
    re-review of the edited bytes. No post-review stage regenerates the draft;
    normalization never sees unreviewed prose.
    """
    kind = _plan_kind(plan_text)
    if kind is None:
        error = "approved plan has no catalog kind; the lesson review cannot select its rubric"
        receipt["stage_receipts"][K_STAGE_LESSON_REVIEW] = {
            "status": K_STAGE_STATUS_FAIL,
            "error": error,
        }
        receipt["stages"].append({"name": "lesson_review", "status": "failed", "error": error})
        _write_generation_log(root, receipt)
        raise ValueError(error)
    expected_axes = quality_axes_for_kind(kind)
    reviewer_agent = runner_for_job(
        reviewer_config["job"],
        repo_root=Path(reviewer_config["repo_root"]),
    )
    review_prompt = build_lesson_review_prompt(
        plan_text,
        draft_text,
        plan_kind=kind,
        terminology_context=terminology_context,
    )
    (review_dir / K_RICH_LESSON_REVIEW_PROMPT_FILE).write_text(review_prompt, encoding="utf-8")
    attempts: list[dict[str, Any]] = []
    try:
        review, validation, first_attempts = _invoke_lesson_review(
            reviewer_agent, review_prompt, expected_axes=expected_axes
        )
    except LlmParseException as exc:
        _fail_lesson_review(
            root,
            receipt,
            review_prompt,
            draft_text=draft_text,
            attempts=attempts,
            status=K_STAGE_STATUS_INVALID,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    except LlmException as exc:
        _fail_lesson_review(
            root,
            receipt,
            review_prompt,
            draft_text=draft_text,
            attempts=attempts,
            status=K_STAGE_STATUS_UNAVAILABLE,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    attempts.extend(first_attempts)
    reviewer_calls = len(first_attempts)
    repaired = False
    repaired = False
    if review.verdict == "needs_repair":
        draft_text, review, validation, review_prompt, repair_calls = _apply_lesson_review_repair(
            author_config=author_config,
            review_dir=review_dir,
            draft_path=draft_path,
            draft_prompt=draft_prompt,
            draft_text=draft_text,
            review=review,
            expected_axes=expected_axes,
            reviewer_agent=reviewer_agent,
            plan_text=plan_text,
            terminology_context=terminology_context,
            receipt=receipt,
            root=root,
            attempts=attempts,
        )
        reviewer_calls += repair_calls
        repaired = True
    input_hash = text_content_hash(draft_text)
    _write_stage_record(
        review_dir / K_RICH_LESSON_REVIEW_FILE,
        {
            "stage": K_STAGE_LESSON_REVIEW,
            "status": K_STAGE_STATUS_PASS,
            "policy_version": K_LESSON_REVIEW_POLICY_VERSION,
            "prompt_version": K_LESSON_REVIEW_PROMPT_VERSION,
            "input_hash": input_hash,
            "verdict": review.verdict,
            "review": review.model_dump(mode="json"),
            "validation": validation.model_dump(mode="json"),
            "attempts": attempts,
            "reviewer_calls": reviewer_calls,
            "repaired": repaired,
        },
    )
    receipt["stage_receipts"][K_STAGE_LESSON_REVIEW] = {
        "status": K_STAGE_STATUS_PASS,
        "input_hash": input_hash,
        "verdict": review.verdict,
        "total_score": validation.total_score,
        "reviewer_calls": reviewer_calls,
        "repaired": repaired,
    }
    receipt["stages"].append(
        {
            "name": "lesson_review",
            "status": "valid",
            "input_hash": input_hash,
            "reviewer_calls": reviewer_calls,
            "repaired": repaired,
        }
    )
    return {
        "draft_text": draft_text,
        "attestation": StageAttestation(
            stage=K_STAGE_LESSON_REVIEW,
            status=K_STAGE_STATUS_PASS,
            policy_version=K_LESSON_REVIEW_POLICY_VERSION,
            prompt_hash=_hash_text(review_prompt),
            prompt_version=K_LESSON_REVIEW_PROMPT_VERSION,
            input_hash=input_hash,
            attempts=reviewer_calls,
            details={
                "verdict": review.verdict,
                "total_score": validation.total_score,
                "findings": len(review.findings),
                "repaired": repaired,
            },
        ),
    }


def review_normalization(
    *,
    reviewer_config: dict[str, Any],
    normalizer_config: dict[str, Any],
    root: Path,
    normalization_dir: Path,
    plan_text: str,
    draft_text: str,
    package: NormalizedPackage,
    normalization_prompt: str,
    receipt: dict[str, Any],
    draft_input_hash: str | None = None,
) -> dict[str, Any]:
    """Review preservation of the reviewed draft inside the normalized bytes.

    The reviewer checks survival of reviewed teaching units; deterministic
    validators own compiler structure and handle identity. A `needs_repair`
    verdict triggers at most one representation-only repair by the normalizer
    followed by a re-review. Unresolved drift fails closed before exercise
    authoring.
    """
    if K_RICH_MAX_NORMALIZATION_REPAIRS < 1:
        raise ValueError("normalization repair limit must allow the configured bounded repair")
    reviewer_agent = runner_for_job(
        reviewer_config["job"],
        repo_root=Path(reviewer_config["repo_root"]),
    )
    input_hash = draft_input_hash or text_content_hash(draft_text)
    preservation_prompt = build_preservation_review_prompt(
        plan_text=plan_text,
        draft_text=draft_text,
        lesson_md=package.lesson_md,
        exercise_requests_yaml=package.exercise_requests_yaml,
    )
    (normalization_dir / K_RICH_PRESERVATION_PROMPT_FILE).write_text(preservation_prompt, encoding="utf-8")
    attempts: list[dict[str, Any]] = []
    try:
        review, first_attempts = _invoke_preservation_review(reviewer_agent, preservation_prompt)
    except LlmException as exc:
        _fail_preservation_review(
            root,
            receipt,
            normalization_dir,
            preservation_prompt,
            input_hash=input_hash,
            attempts=attempts,
            status=(K_STAGE_STATUS_INVALID if isinstance(exc, LlmParseException) else K_STAGE_STATUS_UNAVAILABLE),
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    attempts.extend(first_attempts)
    repaired = False
    if review.verdict == "needs_repair":
        try:
            package, repair_receipt_stage = _repair_normalization(
                normalizer_config=normalizer_config,
                normalization_dir=normalization_dir,
                plan_text=plan_text,
                package=package,
                normalization_prompt=normalization_prompt,
                review=review,
            )
        except (LlmException, ValueError) as exc:
            _fail_preservation_review(
                root,
                receipt,
                normalization_dir,
                preservation_prompt,
                input_hash=input_hash,
                attempts=attempts,
                status=K_STAGE_STATUS_FAIL,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        repaired = True
        receipt["stages"].append(repair_receipt_stage)
        preservation_prompt = build_preservation_review_prompt(
            plan_text=plan_text,
            draft_text=draft_text,
            lesson_md=package.lesson_md,
            exercise_requests_yaml=package.exercise_requests_yaml,
        )
        (normalization_dir / K_RICH_PRESERVATION_PROMPT_FILE).write_text(preservation_prompt, encoding="utf-8")
        try:
            review, re_attempts = _invoke_preservation_review(reviewer_agent, preservation_prompt)
        except LlmException as exc:
            _fail_preservation_review(
                root,
                receipt,
                normalization_dir,
                preservation_prompt,
                input_hash=input_hash,
                attempts=attempts,
                status=(K_STAGE_STATUS_INVALID if isinstance(exc, LlmParseException) else K_STAGE_STATUS_UNAVAILABLE),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        attempts.extend(re_attempts)
        if review.verdict == "needs_repair":
            error = (
                "normalization preservation review remained unresolved after one "
                "bounded representation repair: " + review.summary
            )
            _fail_preservation_review(
                root,
                receipt,
                normalization_dir,
                preservation_prompt,
                input_hash=input_hash,
                attempts=attempts,
                status=K_STAGE_STATUS_FAIL,
                error=error,
                review=review,
            )
            raise ContentRemediationRequired(error, stage=K_STAGE_NORMALIZATION_REVIEW)
        (normalization_dir / K_RICH_NORMALIZATION_RESPONSE_FILE).write_text(
            json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    _write_stage_record(
        normalization_dir / K_RICH_PRESERVATION_REVIEW_FILE,
        {
            "stage": K_STAGE_NORMALIZATION_REVIEW,
            "status": K_STAGE_STATUS_PASS,
            "policy_version": K_NORMALIZATION_REVIEW_POLICY_VERSION,
            "prompt_version": K_NORMALIZATION_REVIEW_PROMPT_VERSION,
            "input_hash": input_hash,
            "verdict": review.verdict,
            "review": review.model_dump(mode="json"),
            "attempts": attempts,
            "repaired": repaired,
        },
    )
    receipt["stage_receipts"][K_STAGE_NORMALIZATION_REVIEW] = {
        "status": K_STAGE_STATUS_PASS,
        "input_hash": input_hash,
        "verdict": review.verdict,
        "attempts": len(attempts),
        "repaired": repaired,
    }
    receipt["stages"].append(
        {
            "name": "normalization_review",
            "status": "valid",
            "input_hash": input_hash,
            "attempts": len(attempts),
            "repaired": repaired,
        }
    )
    return {
        "package": package,
        "repaired": repaired,
        "attestation": StageAttestation(
            stage=K_STAGE_NORMALIZATION_REVIEW,
            status=K_STAGE_STATUS_PASS,
            policy_version=K_NORMALIZATION_REVIEW_POLICY_VERSION,
            prompt_hash=_hash_text(preservation_prompt),
            prompt_version=K_NORMALIZATION_REVIEW_PROMPT_VERSION,
            input_hash=input_hash,
            attempts=len(attempts),
            details={
                "verdict": review.verdict,
                "repaired": repaired,
                "findings": len(review.findings),
            },
        ),
    }


def closed_task_verifier_for(
    repo_root: Path,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Run every strict exercise review surface used at the author boundary.

    The historical name is retained for the checkpoint contract, but author
    output must cross the same composite gate as an existing package: blinded
    closed-answer solving, standalone attempt visibility, and bidirectional
    open-write rubric coverage. The composite report still preserves
    ``unverified_open`` for open tasks because rubric review is not a closed
    answer oracle.
    """

    reviewer_agent = reviewer(repo_root=repo_root)

    def verifier(lesson: dict[str, Any]) -> dict[str, Any]:
        return verify_exercise_package(lesson, reviewer_agent=reviewer_agent, strict=True)

    return verifier


def standalone_review_verifier_for(
    repo_root: Path,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Run the strict standalone-quality review of the exact learner payload."""

    reviewer_agent = reviewer(repo_root=repo_root)

    def verifier(lesson: dict[str, Any]) -> dict[str, Any]:
        return review_standalone_exercises(lesson, reviewer_agent=reviewer_agent, strict=True)

    return verifier


def _validate_exercise_yaml(exercises_yaml: str) -> None:
    """Parse exercise YAML at the workflow boundary before domain validation."""
    validate_exercise_envelope(load_unique_yaml(exercises_yaml))


def _load_or_author_exercise(
    run: RichAuthoringRun,
    *,
    package: ExercisePackage | None,
    response_path: Path,
    prompt: str,
    alignment_error: ValueError | None,
    attempt: int,
) -> tuple[ExercisePackage, LlmResponse | None]:
    """Reuse a cached exercise package or author one bounded attempt."""
    if package is not None:
        run.receipt["stages"].append(
            {
                "name": "exercise_author",
                "status": "reused",
                "response_hash": _hash_text(response_path.read_text(encoding="utf-8")),
            }
        )
        return package, None
    attempt_prompt = _exercise_attempt_prompt(prompt, alignment_error)
    try:
        package, attempts = _invoke_exercise_author(
            job_config=run.exercise_config,
            prompt=attempt_prompt,
        )
    except LlmException as exc:
        run.receipt["stages"].append(_failed_stage_log("exercise_author", attempt_prompt, exc))
        _write_generation_log(run.root, run.receipt)
        raise
    _persist_exercise_author_response(run.exercise_dir, package, attempt_name=f"attempt-{attempt + 1}")
    run.receipt["stages"].append(_attempt_stage_log("exercise_author", attempts))
    return package, attempts[-1][1]


def _align_exercise_package(
    package: ExercisePackage,
    *,
    plan_text: str,
    lesson_md: str,
    requests_yaml: str,
) -> str:
    """Complete metadata and validate exercise alignment with the lesson."""
    exercises_yaml = complete_exercise_metadata(
        package.exercises_yaml,
        yaml.safe_load(extract_frontmatter(plan_text)),
    )
    validate_exercise_alignment(exercises_yaml, requests_yaml)
    return _validate_exercise_source_for_retry(lesson_md, exercises_yaml)


def _plan_metadata(plan_text: str) -> dict[str, Any]:
    """Parse one approved plan at the workflow format boundary."""
    metadata = yaml.safe_load(extract_frontmatter(plan_text))
    return metadata if isinstance(metadata, dict) else {}


def _plan_kind(plan_text: str) -> str | None:
    """Return the approved catalog kind, when present."""
    kind = _plan_metadata(plan_text).get("kind")
    return kind if isinstance(kind, str) else None


def _plan_objective_ids(plan_metadata: dict[str, Any]) -> set[str]:
    """Return declared objective identifiers from parsed plan metadata."""
    objectives = plan_metadata.get("objectives", [])
    if not isinstance(objectives, list):
        return set()
    return {str(item["id"]) for item in objectives if isinstance(item, dict) and isinstance(item.get("id"), str)}


def _invoke_normalizer(
    *, job_config: dict[str, Any], prompt: str, plan_metadata: dict[str, Any]
) -> tuple[NormalizedPackage, list[tuple[str, LlmResponse]]]:
    """Invoke the normalizer and allow one transport-only correction."""
    agent = runner_for_job(job_config["job"], repo_root=Path(job_config["repo_root"]))
    attempts: list[tuple[str, LlmResponse]] = []
    attempt_prompt = prompt
    last_error: LlmParseException | None = None
    for _attempt in range(K_RICH_MAX_NORMALIZATION_ATTEMPTS):
        response = agent.invoke_response(attempt_prompt)
        attempts.append((attempt_prompt, response))
        try:
            package = NormalizedPackage.model_validate(extract_json_object(response.text))
            validate_normalized_lesson_shape(package.lesson_md)
            validate_exercise_requests(
                quote_unquoted_yaml_scalars(package.exercise_requests_yaml),
                _plan_objective_ids(plan_metadata),
                package.lesson_md,
            )
            return package, attempts
        except ValidationError as exc:
            last_error = LlmParseException(f"normalizer schema validation failed: {exc}")
        except (TypeError, ValueError) as exc:
            last_error = LlmParseException(f"normalizer response was not valid JSON: {exc}")
        attempt_prompt = (
            f"{prompt}\n\nYour previous conversion was invalid: {last_error}. "
            "Return the complete JSON object again, with exactly lesson_md and "
            "exercise_requests_yaml, and no prose outside the object."
        )
    if last_error is None:
        raise RuntimeError("unreachable")
    raise last_error


def _invoke_exercise_author(
    *, job_config: dict[str, Any], prompt: str
) -> tuple[ExercisePackage, list[tuple[str, LlmResponse]]]:
    """Invoke the exercise author and retry only its transport envelope."""
    agent = runner_for_job(job_config["job"], repo_root=Path(job_config["repo_root"]))
    attempts: list[tuple[str, LlmResponse]] = []
    attempt_prompt = prompt
    last_error: LlmParseException | None = None
    for _attempt in range(K_RICH_MAX_NORMALIZATION_ATTEMPTS):
        response = agent.invoke_response(attempt_prompt)
        attempts.append((attempt_prompt, response))
        try:
            package = ExercisePackage.model_validate(extract_json_object(response.text))
            return repair_exercise_yaml_if_needed(package), attempts
        except (TypeError, ValueError, yaml.YAMLError) as exc:
            last_error = LlmParseException(f"exercise author output was invalid: {exc}")
        attempt_prompt = (
            f"{prompt}\n\nYour previous exercise package was invalid: {last_error}. "
            "Return the complete JSON object again with exactly exercises_yaml."
        )
    if last_error is None:
        raise RuntimeError("unreachable")
    raise last_error


def _exercise_attempt_prompt(prompt: str, alignment_error: ValueError | None) -> str:
    """Add one targeted alignment correction to an exercise-author prompt."""
    if alignment_error is None:
        return prompt
    return (
        f"{prompt}\n\nThe deterministic exercise-alignment gate rejected your previous package: {alignment_error}. "
        "Return the complete corrected package. The request's `evidence_route` is authoritative: use only the "
        "operation mapped to that route, even when another operation shares the Bloom level. Do not change the "
        "request handle, Bloom level, evidence route, or success criteria. Route guidance:\n"
        + render_evidence_route_guidance()
    )


def _verification_from_failure(
    exc: ExerciseReviewError | StandaloneReviewError,
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Normalize the two verifier exception reports into one receipt payload."""
    if isinstance(exc, StandaloneReviewError):
        report = _standalone_failure_verification(exc.report)
        receipt["standalone_exercise_review"] = dict(exc.report)
        return report
    return dict(exc.report)


def _is_exercise_repair_allowed(
    verification: dict[str, Any],
    failed_handles: list[str],
    semantic_repairs: int,
) -> bool:
    """Return whether a verifier report permits the single bounded repair."""
    return (
        verification.get("status") == "needs_human"
        and bool(failed_handles)
        and semantic_repairs < K_RICH_MAX_EXERCISE_SEMANTIC_REPAIRS
    )


def _record_verifier_failure(
    run: RichAuthoringRun,
    verification: dict[str, Any],
    exc: ExerciseReviewError | StandaloneReviewError,
) -> None:
    """Persist a blocking verifier failure before raising remediation."""
    run.receipt["stage_receipts"][K_STAGE_EXERCISE_VERIFICATION] = {
        "status": verification.get("status", "needs_human"),
        "error": str(exc),
        "lesson_hash": verification.get("lesson_hash"),
    }
    run.receipt["exercise_verification"] = verification
    run.receipt["stages"].append(
        {
            "name": "exercise_verifier",
            "status": "failed",
            "error": str(exc),
            "lesson_hash": verification.get("lesson_hash"),
        }
    )
    _write_generation_log(run.root, run.receipt)


def _repair_exercises_after_failure(
    run: RichAuthoringRun,
    *,
    package: NormalizedPackage,
    author: ExerciseAuthorResult,
    internal_lesson: dict[str, Any],
    exercises_yaml: str,
    failed_handles: list[str],
    verification: dict[str, Any],
    lesson_hash: str,
    requests_hash: str,
    semantic_repairs: int,
) -> tuple[dict[str, Any], str]:
    """Generate, merge, compile, and record one verifier-directed repair."""
    repair_prompt = build_exercise_repair_prompt(
        plan_text=run.plan_text,
        lesson_md=package.lesson_md,
        exercise_requests_yaml=package.exercise_requests_yaml,
        failed_handles=failed_handles,
        verification=verification,
        learner_visible_payload=[
            question for question in extract_answer_questions(internal_lesson) if question.get("id") in failed_handles
        ],
    )
    repaired_yaml = _generate_repaired_exercise_source(
        run,
        author=author,
        package=package,
        exercises_yaml=exercises_yaml,
        failed_handles=failed_handles,
        repair_prompt=repair_prompt,
        lesson_hash=lesson_hash,
        requests_hash=requests_hash,
        semantic_repairs=semantic_repairs,
        verification=verification,
    )
    (run.generated_dir / K_LESSON_GENERATION_EXERCISES_FILE).write_text(repaired_yaml, encoding="utf-8")
    _assert_file_matches_hash(
        run.generated_dir / K_LESSON_GENERATION_LESSON_FILE,
        lesson_hash,
        "exercise repair changed immutable lesson prose",
    )
    _refresh_approved_source_hash(run.generated_dir / "plan.md", run.generated_dir)
    try:
        repaired_lesson = _load_normalized_source(run.generated_dir)
    except (OSError, ValueError) as exc:
        run.receipt["stage_receipts"][K_STAGE_EXERCISE_VERIFICATION] = {
            "status": "needs_human",
            "repair_error": f"{type(exc).__name__}: {exc}",
        }
        run.receipt["exercise_verification"] = verification
        run.receipt["stages"].append(
            {
                "name": "exercise_verifier_repair_compile",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        _write_generation_log(run.root, run.receipt)
        if isinstance(exc, ValueError):
            raise ContentRemediationRequired(str(exc), stage=K_STAGE_EXERCISE_VERIFICATION) from exc
        raise
    _record_exercise_diagnostics(repaired_lesson, run.receipt, root=run.root)
    run.receipt["stages"].append(
        {
            "name": "exercise_verifier_repair",
            "status": "recompiled",
            "handles": failed_handles,
            "attempt": semantic_repairs + 1,
        }
    )
    return repaired_lesson.model_dump(mode="json"), repaired_yaml


def _generate_repaired_exercise_source(
    run: RichAuthoringRun,
    *,
    author: ExerciseAuthorResult,
    package: NormalizedPackage,
    exercises_yaml: str,
    failed_handles: list[str],
    repair_prompt: str,
    lesson_hash: str,
    requests_hash: str,
    semantic_repairs: int,
    verification: dict[str, Any],
) -> str:
    """Ask the exercise author for a handle-scoped replacement and merge it."""
    try:
        repair_package, attempts = _invoke_exercise_author(
            job_config=run.exercise_config,
            prompt=repair_prompt,
        )
        author.last_response = attempts[-1][1]
        _persist_exercise_author_response(
            run.exercise_dir,
            repair_package,
            attempt_name=f"repair-{semantic_repairs + 1}",
        )
        run.receipt["stages"].append(_attempt_stage_log("exercise_author", attempts))
        replacement_yaml = complete_exercise_metadata(
            repair_package.exercises_yaml,
            yaml.safe_load(extract_frontmatter(run.plan_text)),
        )
        unaffected_hashes = unaffected_exercise_hashes(exercises_yaml, failed_handles)
        repaired_yaml = merge_failed_exercises(exercises_yaml, replacement_yaml, failed_handles)
        validate_exercise_alignment(repaired_yaml, package.exercise_requests_yaml)
        # Keep the same deterministic transport boundary for model-produced repairs
        # as for the initial exercise package.
        repaired_yaml = _validate_exercise_source_for_retry(package.lesson_md, repaired_yaml)
        if unaffected_exercise_hashes(repaired_yaml, failed_handles) != unaffected_hashes:
            raise ValueError("exercise repair changed an unaffected exercise after normalization")
        author.package = ExercisePackage(exercises_yaml=repaired_yaml)
        author.response_path.write_text(
            json.dumps(author.package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_stage_input(
            author.input_path,
            lesson_hash=lesson_hash,
            requests_hash=requests_hash,
            prompt_hash=_hash_text(author.exercise_prompt),
            policy_version=K_RICH_EXERCISE_CACHE_POLICY_VERSION,
            job_hash=_hash_config(run.exercise_config),
        )
        return repaired_yaml
    except (LlmException, OSError, ValueError) as exc:
        run.receipt["stage_receipts"][K_STAGE_EXERCISE_VERIFICATION] = {
            "status": "needs_human",
            "repair_error": f"{type(exc).__name__}: {exc}",
        }
        run.receipt["exercise_verification"] = verification
        run.receipt["stages"].append(
            {
                "name": "exercise_verifier_repair",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        _write_generation_log(run.root, run.receipt)
        if isinstance(exc, ValueError):
            raise ContentRemediationRequired(str(exc), stage=K_STAGE_EXERCISE_VERIFICATION) from exc
        raise


def _apply_lesson_review_repair(
    *,
    author_config: dict[str, Any],
    review_dir: Path,
    draft_path: Path,
    draft_prompt: str,
    draft_text: str,
    review: LessonQualityReview,
    expected_axes: tuple[str, ...],
    reviewer_agent: JobRunner,
    plan_text: str,
    terminology_context: str | None,
    receipt: dict[str, Any],
    root: Path,
    attempts: list[dict[str, Any]],
) -> tuple[str, LessonQualityReview, QualityReviewValidation, str, int]:
    """Apply one bounded draft edit and re-review the resulting exact bytes."""
    repair_attempts: list[dict[str, Any]] = []
    try:
        draft_text = _repair_lesson_draft(
            author_config=author_config,
            review_dir=review_dir,
            draft_path=draft_path,
            draft_prompt=draft_prompt,
            draft_text=draft_text,
            review=review,
            receipt=receipt,
            repair_attempts=repair_attempts,
        )
    except LlmParseException as exc:
        attempts.extend(repair_attempts)
        _fail_lesson_review(
            root,
            receipt,
            build_lesson_review_prompt(
                plan_text,
                draft_text,
                plan_kind=_plan_kind(plan_text),
                terminology_context=terminology_context,
            ),
            draft_text=draft_text,
            attempts=attempts,
            status=K_STAGE_STATUS_INVALID,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    except LlmException as exc:
        attempts.extend(repair_attempts)
        _fail_lesson_review(
            root,
            receipt,
            build_lesson_review_prompt(
                plan_text,
                draft_text,
                plan_kind=_plan_kind(plan_text),
                terminology_context=terminology_context,
            ),
            draft_text=draft_text,
            attempts=attempts,
            status=K_STAGE_STATUS_UNAVAILABLE,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    except ValueError as exc:
        attempts.extend(repair_attempts)
        _fail_lesson_review(
            root,
            receipt,
            build_lesson_review_prompt(
                plan_text,
                draft_text,
                plan_kind=_plan_kind(plan_text),
                terminology_context=terminology_context,
            ),
            draft_text=draft_text,
            attempts=attempts,
            status=K_STAGE_STATUS_FAIL,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    attempts.extend(repair_attempts)
    review_prompt = build_lesson_review_prompt(
        plan_text,
        draft_text,
        plan_kind=_plan_kind(plan_text),
        terminology_context=terminology_context,
    )
    (review_dir / K_RICH_LESSON_REVIEW_PROMPT_FILE).write_text(review_prompt, encoding="utf-8")
    try:
        review, validation, re_attempts = _invoke_lesson_review(
            reviewer_agent,
            review_prompt,
            expected_axes=expected_axes,
        )
    except LlmParseException as exc:
        _fail_lesson_review(
            root,
            receipt,
            review_prompt,
            draft_text=draft_text,
            attempts=attempts,
            status=K_STAGE_STATUS_INVALID,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    except LlmException as exc:
        _fail_lesson_review(
            root,
            receipt,
            review_prompt,
            draft_text=draft_text,
            attempts=attempts,
            status=K_STAGE_STATUS_UNAVAILABLE,
            error=f"{type(exc).__name__}: {exc}",
        )
        raise
    attempts.extend(re_attempts)
    if review.verdict == "needs_repair":
        error = "lesson review remained unresolved after one bounded draft-only repair: " + review.summary
        _fail_lesson_review(
            root,
            receipt,
            review_prompt,
            draft_text=draft_text,
            attempts=attempts,
            status=K_STAGE_STATUS_FAIL,
            error=error,
            review=review,
            validation=validation,
        )
        raise ContentRemediationRequired(error, stage=K_STAGE_LESSON_REVIEW)
    return draft_text, review, validation, review_prompt, len(re_attempts)


def _repair_lesson_draft(
    *,
    author_config: dict[str, Any],
    review_dir: Path,
    draft_path: Path,
    draft_prompt: str,
    draft_text: str,
    review: LessonQualityReview,
    receipt: dict[str, Any],
    repair_attempts: list[dict[str, Any]],
) -> str:
    """Apply one bounded exact-edit repair through the original draft author."""
    if K_RICH_MAX_LESSON_CONTENT_REPAIRS < 1:
        raise ValueError("lesson review repair limit must allow the configured bounded repair")
    repair_prompt = build_lesson_repair_prompt(draft_prompt, draft_text, review)
    (review_dir / K_RICH_LESSON_REPAIR_PROMPT_FILE).write_text(repair_prompt, encoding="utf-8")
    attempt_prompt = repair_prompt
    for attempt_index in range(1, K_RICH_MAX_EDIT_RESPONSE_ATTEMPTS + 1):
        attempt_prompt_path = review_dir / (
            K_RICH_LESSON_REPAIR_PROMPT_FILE if attempt_index == 1 else f"repair_prompt-{attempt_index}.md"
        )
        attempt_prompt_path.write_text(attempt_prompt, encoding="utf-8")
        try:
            response = runner_for_job(
                author_config["job"],
                repo_root=Path(author_config["repo_root"]),
            ).invoke_response(attempt_prompt)
        except LlmException as exc:
            repair_attempts.append(
                {
                    "role": "draft_edit",
                    "attempt": attempt_index,
                    "status": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        response_path = review_dir / f"repair_response-{attempt_index}.txt"
        response_path.write_text(response.text, encoding="utf-8")
        try:
            edit_response = LessonDraftEditResponse.model_validate(extract_json_object(response.text))
            repaired_text = apply_lesson_draft_edits(
                draft_text,
                review,
                edit_response,
            )
        except (TypeError, ValueError) as exc:
            diagnostic = f"{type(exc).__name__}: {exc}"
            repair_attempts.append(
                {
                    "role": "draft_edit",
                    "attempt": attempt_index,
                    "status": "invalid",
                    "error": diagnostic,
                    "response": _build_response_metadata(response),
                    "response_path": str(response_path),
                }
            )
            if attempt_index < K_RICH_MAX_EDIT_RESPONSE_ATTEMPTS:
                attempt_prompt = (
                    f"{repair_prompt}\n\n"
                    "Your previous edit JSON was invalid or did not apply to the exact "
                    "draft. This is a transport correction, not a lesson rewrite. "
                    "Return only corrected JSON matching the schema. Copy every "
                    "old_text value literally from the draft above, use the exact "
                    "finding_ref values, and preserve coverage of every material "
                    "finding. You may split one finding across multiple exact edits "
                    "when Markdown formatting differs, or combine edits only when each "
                    "literal old_text is independently present. Every old_text must "
                    "be a unique anchor occurring exactly once; use a longer local "
                    "anchor when a shorter phrase repeats. "
                    "The complete previous response is included below so you can correct "
                    "it instead of guessing.\n\n"
                    "---BEGIN PREVIOUS INVALID RESPONSE---\n"
                    f"{response.text}\n"
                    "---END PREVIOUS INVALID RESPONSE---\n\n"
                    "---BEGIN VALIDATION DIAGNOSTICS---\n"
                    f"{diagnostic}\n"
                    "---END VALIDATION DIAGNOSTICS---"
                )
                continue
            raise LlmParseException(
                f"bounded lesson edit response remained invalid after one transport correction: {diagnostic}"
            ) from exc
        repair_attempts.append(
            {
                "role": "draft_edit",
                "attempt": attempt_index,
                "status": "valid",
                "response": _build_response_metadata(response),
                "response_path": str(response_path),
            }
        )
        (draft_path.parent / K_RICH_DRAFT_PRE_REPAIR_FILE).write_text(draft_text + "\n", encoding="utf-8")
        draft_path.write_text(repaired_text + "\n", encoding="utf-8")
        receipt["stages"].append(_stage_log("lesson_review_repair", attempt_prompt, response))
        receipt["stages"][-1]["edit_count"] = len(edit_response.edits)
        receipt["stages"][-1]["edit_finding_refs"] = [edit.finding_ref for edit in edit_response.edits]
        return repaired_text
    raise AssertionError("lesson edit response loop must return or raise")


def _fail_lesson_review(
    root: Path,
    receipt: dict[str, Any],
    review_prompt: str,
    *,
    draft_text: str,
    attempts: list[dict[str, Any]],
    status: str,
    error: str,
    review: LessonQualityReview | None = None,
    validation: QualityReviewValidation | None = None,
) -> None:
    """Record and persist a failed lesson review before failing closed."""
    receipt["stage_receipts"][K_STAGE_LESSON_REVIEW] = {
        "status": status,
        "input_hash": text_content_hash(draft_text),
        "attempts": len(attempts),
        "error": error,
    }
    receipt["stages"].append(
        {
            "name": "lesson_review",
            "status": "failed",
            "prompt_hash": _hash_text(review_prompt),
            "error": error,
        }
    )
    _write_stage_record(
        root / K_RICH_REVIEW_SUBDIR / K_RICH_LESSON_REVIEW_FILE,
        {
            "stage": K_STAGE_LESSON_REVIEW,
            "status": status,
            "policy_version": K_LESSON_REVIEW_POLICY_VERSION,
            "prompt_version": K_LESSON_REVIEW_PROMPT_VERSION,
            "input_hash": text_content_hash(draft_text),
            "review": review.model_dump(mode="json") if review is not None else None,
            "validation": validation.model_dump(mode="json") if validation is not None else None,
            "attempts": attempts,
            "reviewer_calls": sum(
                1 for record in attempts if record.get("role") != "draft_edit" and "attempt" in record
            ),
            "error": error,
        },
    )
    _write_generation_log(root, receipt)


def _invoke_lesson_review(
    agent: JobRunner,
    prompt: str,
    *,
    expected_axes: tuple[str, ...],
) -> tuple[LessonQualityReview, QualityReviewValidation, list[dict[str, Any]]]:
    """Invoke the lesson reviewer with one bounded response re-ask."""
    instruction = f"{prompt}\n\n{quality_review_schema(expected_axes)}"
    attempts: list[dict[str, Any]] = []
    last_error: str | None = None
    for attempt_index in range(1, K_RICH_MAX_REVIEW_REASKS + 2):
        response = agent.invoke_response(instruction)
        try:
            review = LessonQualityReview.model_validate(extract_json_object(response.text))
            validation = validate_quality_review(review, expected_axes)
            if validation.status != "valid":
                raise ValueError("; ".join(validation.errors))
        except (TypeError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append(
                {
                    "attempt": attempt_index,
                    "status": "invalid",
                    "error": last_error,
                    "response": _build_response_metadata(response),
                }
            )
            instruction = (
                f"{prompt}\n\n{quality_review_schema(expected_axes)}\n\n"
                "Your previous review response violated the response contract. "
                "Repair only the review JSON; do not request a lesson rewrite. "
                f"Diagnostic: {last_error}"
            )
            continue
        attempts.append(
            {
                "attempt": attempt_index,
                "status": "valid",
                "response": _build_response_metadata(response),
            }
        )
        return review, validation, attempts
    if last_error is None:
        raise RuntimeError("unreachable")
    raise LlmParseException(f"lesson review response remained invalid after one re-ask: {last_error}")


def _load_cached_normalized_package(
    response_path: Path,
    plan_text: str,
    *,
    draft_hash: str,
    input_path: Path,
    prompt_hash: str,
    job_hash: str,
) -> NormalizedPackage | None:
    """Reuse a cached conversion only for its exact recorded stage inputs.

    The cached package is bound to the reviewed draft and plan bytes through the
    stage-input record written beside it. A draft that changed after a bounded
    repair, a different plan, or a missing record makes the cache stale and
    forces a fresh normalization call.
    """
    if not _stage_input_matches(
        input_path,
        draft_hash=draft_hash,
        plan_hash=_hash_text(plan_text),
        prompt_hash=prompt_hash,
        policy_version=K_RICH_NORMALIZATION_CACHE_POLICY_VERSION,
        job_hash=job_hash,
    ):
        return None
    if not response_path.is_file():
        return None
    try:
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        package = NormalizedPackage.model_validate(payload)
        validate_normalized_lesson_shape(package.lesson_md)
        return complete_normalized_metadata(package, _plan_metadata(plan_text))
    except (OSError, TypeError, ValueError, ValidationError, yaml.YAMLError):
        return None


def _write_stage_input(input_path: Path, **inputs: str) -> None:
    """Bind a cached stage response to the exact input hashes it produced."""
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(inputs, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _stage_input_matches(input_path: Path, **inputs: str) -> bool:
    """Return whether a cached stage response was bound to these exact inputs."""
    try:
        recorded = json.loads(input_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(recorded, dict) and recorded == inputs


def _load_cached_exercise_package(
    response_path: Path,
    *,
    input_path: Path,
    lesson_hash: str,
    requests_hash: str,
    prompt_hash: str,
    job_hash: str,
) -> ExercisePackage | None:
    """Reuse only an exercise-author response bound to these exact inputs.

    The response is bound to the frozen lesson prose and the accepted request
    handoff, so a normalization repair or any other upstream change forces a
    fresh exercise-author call instead of replaying stale exercises.
    """
    if not _stage_input_matches(
        input_path,
        lesson_hash=lesson_hash,
        requests_hash=requests_hash,
        prompt_hash=prompt_hash,
        policy_version=K_RICH_EXERCISE_CACHE_POLICY_VERSION,
        job_hash=job_hash,
    ):
        return None
    if not response_path.is_file():
        return None
    try:
        payload = json.loads(response_path.read_text(encoding="utf-8"))
        package = ExercisePackage.model_validate(payload)
        return repair_exercise_yaml_if_needed(package)
    except (OSError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
        return None


def _repair_normalization(
    *,
    normalizer_config: dict[str, Any],
    normalization_dir: Path,
    plan_text: str,
    package: NormalizedPackage,
    normalization_prompt: str,
    review: NormalizationPreservationReview,
) -> tuple[NormalizedPackage, dict[str, Any]]:
    """Apply one representation-only exact-edit repair through the normalizer."""
    repair_prompt = build_preservation_repair_prompt(normalization_prompt, package, review)
    (normalization_dir / K_RICH_NORMALIZATION_REPAIR_PROMPT_FILE).write_text(repair_prompt, encoding="utf-8")
    attempt_prompt = repair_prompt
    attempts: list[dict[str, Any]] = []
    for attempt_index in range(1, K_RICH_MAX_EDIT_RESPONSE_ATTEMPTS + 1):
        attempt_prompt_path = normalization_dir / (
            K_RICH_NORMALIZATION_REPAIR_PROMPT_FILE
            if attempt_index == 1
            else f"normalization_repair_prompt-{attempt_index}.md"
        )
        attempt_prompt_path.write_text(attempt_prompt, encoding="utf-8")
        try:
            response = runner_for_job(
                normalizer_config["job"],
                repo_root=Path(normalizer_config["repo_root"]),
            ).invoke_response(attempt_prompt)
        except LlmException as exc:
            attempts.append(
                {
                    "attempt": attempt_index,
                    "status": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        response_path = normalization_dir / f"normalization_repair_response-{attempt_index}.txt"
        response_path.write_text(response.text, encoding="utf-8")
        try:
            edit_response = NormalizationEditResponse.model_validate(extract_json_object(response.text))
            repaired_package = apply_normalization_edits(package, review, edit_response)
        except (TypeError, ValueError) as exc:
            diagnostic = f"{type(exc).__name__}: {exc}"
            attempts.append(
                {
                    "attempt": attempt_index,
                    "status": "invalid",
                    "error": diagnostic,
                    "response": _build_response_metadata(response),
                    "response_path": str(response_path),
                }
            )
            if attempt_index < K_RICH_MAX_EDIT_RESPONSE_ATTEMPTS:
                attempt_prompt = (
                    f"{repair_prompt}\n\n"
                    "Your previous normalization edit JSON was invalid or did not "
                    "apply to the exact normalized fields. This is a transport "
                    "correction, not a content rewrite. Return only corrected JSON "
                    "matching the schema. Copy every old_text value literally from "
                    "the previous lesson or requests above, use the exact "
                    "finding_ref values, and address each finding exactly once. "
                    f"Diagnostic: {diagnostic}"
                )
                continue
            raise LlmParseException(
                f"bounded normalization edit response remained invalid after one transport correction: {diagnostic}"
            ) from exc
        attempts.append(
            {
                "attempt": attempt_index,
                "status": "valid",
                "response": _build_response_metadata(response),
                "response_path": str(response_path),
            }
        )
        repaired_package = complete_normalized_metadata(repaired_package, _plan_metadata(plan_text))
        return repaired_package, {
            "name": "normalization_review_repair",
            "status": "valid",
            "attempts": attempts,
            "edit_count": len(edit_response.edits),
            "edit_finding_refs": [edit.finding_ref for edit in edit_response.edits],
        }
    raise AssertionError("normalization edit response loop must return or raise")


def _fail_preservation_review(
    root: Path,
    receipt: dict[str, Any],
    normalization_dir: Path,
    preservation_prompt: str,
    *,
    input_hash: str,
    attempts: list[dict[str, Any]],
    status: str,
    error: str,
    review: NormalizationPreservationReview | None = None,
) -> None:
    """Record and persist a failed preservation review before failing closed."""
    receipt["stage_receipts"][K_STAGE_NORMALIZATION_REVIEW] = {
        "status": status,
        "input_hash": input_hash,
        "attempts": len(attempts),
        "error": error,
    }
    receipt["stages"].append(
        {
            "name": "normalization_review",
            "status": "failed",
            "prompt_hash": _hash_text(preservation_prompt),
            "error": error,
        }
    )
    _write_stage_record(
        normalization_dir / K_RICH_PRESERVATION_REVIEW_FILE,
        {
            "stage": K_STAGE_NORMALIZATION_REVIEW,
            "status": status,
            "policy_version": K_NORMALIZATION_REVIEW_POLICY_VERSION,
            "prompt_version": K_NORMALIZATION_REVIEW_PROMPT_VERSION,
            "input_hash": input_hash,
            "review": review.model_dump(mode="json") if review is not None else None,
            "attempts": attempts,
            "error": error,
        },
    )
    _write_generation_log(root, receipt)


def _invoke_preservation_review(
    agent: JobRunner,
    prompt: str,
) -> tuple[NormalizationPreservationReview, list[dict[str, Any]]]:
    """Invoke the preservation reviewer with one bounded response re-ask."""
    instruction = f"{prompt}\n\n{preservation_review_schema()}"
    attempts: list[dict[str, Any]] = []
    last_error: str | None = None
    for attempt_index in range(1, K_RICH_MAX_REVIEW_REASKS + 2):
        response = agent.invoke_response(instruction)
        try:
            review = NormalizationPreservationReview.model_validate(extract_json_object(response.text))
            errors = validate_preservation_review(review)
            if errors:
                raise ValueError("; ".join(errors))
        except (TypeError, ValueError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            attempts.append(
                {
                    "attempt": attempt_index,
                    "status": "invalid",
                    "error": last_error,
                    "response": _build_response_metadata(response),
                }
            )
            instruction = (
                f"{prompt}\n\n{preservation_review_schema()}\n\n"
                "Your previous review response violated the response contract. "
                "Repair only the review JSON; do not author lesson content. "
                f"Diagnostic: {last_error}"
            )
            continue
        attempts.append(
            {
                "attempt": attempt_index,
                "status": "valid",
                "response": _build_response_metadata(response),
            }
        )
        return review, attempts
    if last_error is None:
        raise RuntimeError("unreachable")
    raise LlmParseException(f"preservation review response remained invalid after one re-ask: {last_error}")


def _persist_exercise_author_response(
    exercise_dir: Path,
    package: ExercisePackage,
    *,
    attempt_name: str,
) -> None:
    """Persist each parsed exercise response before downstream validation.

    A malformed alignment or mechanical preflight result must leave the exact
    model payload available for debugging and a cache-only retry. The latest
    response keeps the historical cache contract; the attempt copy preserves
    earlier responses when the author is asked to retry.
    """
    payload = json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    exercise_dir.mkdir(parents=True, exist_ok=True)
    (exercise_dir / f"response-{attempt_name}.json").write_text(
        payload,
        encoding="utf-8",
    )
    (exercise_dir / K_RICH_EXERCISE_RESPONSE_FILE).write_text(
        payload,
        encoding="utf-8",
    )


def _load_normalized_source(source_dir: Path) -> Lesson:
    """Load normalized files into one typed lesson."""
    return load_lesson(source_dir, audit=audit_source_directory(source_dir))


def _record_exercise_diagnostics(lesson: Lesson, receipt: dict[str, Any], *, root: Path) -> None:
    """Record factory diagnostics and stop on concrete objective gaps."""
    diagnostics = analyze_exercise_diagnostics(lesson)
    payload = diagnostics.model_dump(mode="json")
    receipt["exercise_diagnostics"] = payload
    if diagnostics.findings:
        message = "; ".join(diagnostics.findings)
        receipt["stages"].append(
            {
                "name": "exercise_diagnostics",
                "status": "failed",
                "findings": diagnostics.findings,
            }
        )
        _write_generation_log(root, receipt)
        raise ContentRemediationRequired(
            "exercise package has concrete factory contract gaps: " + message,
            stage=K_STAGE_EXERCISE_VERIFICATION,
        )
    receipt["stages"].append(
        {
            "name": "exercise_diagnostics",
            "status": "valid",
            "observations": diagnostics.diagnostics,
        }
    )


def _validate_exercise_source_for_retry(lesson_md: str, exercises_yaml: str) -> str:
    """Repair safe transport defects, then return YAML that still passes audit."""
    repaired = repair_exercise_source(exercises_yaml, lesson_text=lesson_md)
    exercises_yaml = repaired.exercises_yaml
    audit = repaired.audit
    material = audit.material_findings
    if not material:
        return exercises_yaml
    details = "; ".join(
        f"{finding.code}@{finding.location} ({finding.severity}): {finding.explanation} Evidence: {finding.evidence}"
        for finding in material
    )
    raise ValueError(f"mechanical exercise source preflight rejected the package: {details}")


def _refresh_approved_source_hash(plan_path: Path, source_dir: Path) -> None:
    """Bind the generated Markdown/YAML package to its plan snapshot."""
    text = plan_path.read_text(encoding="utf-8")
    digest = approved_content_hash(source_dir)
    updated, count = re.subn(
        r"(?m)^approved_source_hash:\s*.*$",
        f"approved_source_hash: {digest}",
        text,
        count=1,
    )
    if count == 0:
        if not text.startswith("---\n"):
            raise ValueError("generated plan has no YAML front matter")
        closing = text.find("\n---", 4)
        if closing < 0:
            raise ValueError("generated plan has unterminated YAML front matter")
        insertion = f"\napproved_source_hash: {digest}"
        updated = text[:closing] + insertion + text[closing:]
    plan_path.write_text(updated, encoding="utf-8")


def _standalone_failure_verification(report: dict[str, Any]) -> dict[str, Any]:
    """Project a failed standalone report into the shared repair-decision shape."""
    context_dependent = [value for value in report.get("context_dependent", []) if isinstance(value, str)]
    diagnostics = report.get("deterministic_findings", [])
    diagnostic_handles: set[str] = set()
    for item in diagnostics:
        if not isinstance(item, dict):
            continue
        handle = item.get("id")
        if isinstance(handle, str):
            diagnostic_handles.add(handle)
    # A reviewer outage stays an outage: it is never repairable exercise content.
    status = report.get("status", K_STANDALONE_REVIEW_NEEDS_HUMAN)
    return {
        "status": status,
        "total": report.get("total", 0),
        "matches": 0,
        "context_dependent": sorted(set(context_dependent) | diagnostic_handles),
        "standalone": report,
        "error": (
            "standalone exercise review found exercises that are not understandable "
            "from their own learner-visible payload"
        ),
    }


def _load_job_config(job_name: str, repo_root: Path) -> dict[str, Any]:
    """Resolve one configured job into a cacheable stage descriptor."""
    job = load_llm_job(job_name, repo_root=repo_root)
    return {
        "job": job.name,
        "tier": job.tier.name,
        "model": job.tier.model,
        "variant": job.tier.variant,
        "agent_template": job.agent_template,
        "repo_root": str(repo_root),
    }


__all__ = [
    "ArtifactGenerationResult",
    "ExerciseAuthorResult",
    "ExerciseVerificationResult",
    "ContentRemediationRequired",
    "NormalizedDraftResult",
    "NormalizationResult",
    "RichAuthoringRun",
    "author_exercises",
    "author_or_reuse_draft",
    "closed_task_verifier_for",
    "compile_generated_package",
    "complete_normalized_metadata",
    "extract_intents",
    "finish_rich_run",
    "initialize_rich_run",
    "normalize_rich_draft",
    "record_coverage",
    "record_verifier_success",
    "repair_exercise_yaml_if_needed",
    "review_lesson_draft",
    "review_normalization",
    "split_checkpoint",
    "standalone_review_verifier_for",
    "validate_checkpoint_boundary",
    "verify_rich_exercises",
]
