"""Entry point: `review_existing_package` records advisory findings for Promptfoo evaluations."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from evals.promptfoo.lesson_generation.dependencies import approved_content_hash
from evals.promptfoo.lesson_generation.dependencies import validate_approved_plan
from evals.promptfoo.lesson_generation.dependencies import validate_approved_source
from evals.promptfoo.lesson_generation.generation_log import build_response_metadata
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_EXERCISES_FILE
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_LESSON_FILE
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_PLAN_FILE
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_REVIEW_EDIT_JOB
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_REVIEW_EDIT_MAX_REASKS
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_REVIEW_EDIT_REQUIRED_FILES
from evals.promptfoo.lesson_generation.settings import K_LESSON_GENERATION_REVIEW_EDIT_SUBDIR
from evals.promptfoo.lesson_generation.source_repair import repair_exercise_source
from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.application.operations.compile_lesson_package import compile_lesson_package
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.clients.llm.base import extract_json_object
from lesson_builder.clients.llm.exceptions import LlmException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.clients.llm.invocation import build_structured_json_prompt
from lesson_builder.clients.llm.role_jobs import runner_for_job
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter


class ReviewEditFinding(BaseModel):
    """One naturalness or structure defect found in the frozen package."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    severity: Literal["major", "minor"]
    artifact: Literal["lesson.md", "exercises.yaml"]
    location: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class ReviewEditPackage(BaseModel):
    """Structured advisory report; never a source replacement."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1)
    audited_handles: list[str] = Field(default_factory=list)
    findings: list[ReviewEditFinding] = Field(default_factory=list)


@dataclass(frozen=True)
class ReviewEditResult:
    """Paths and provenance produced by advisory review and deterministic repairs."""

    status: str
    source_dir: Path
    review_path: Path
    receipt_path: Path
    review: ReviewEditPackage | None
    mechanical_audit: MechanicalAudit


def review_existing_package(
    *,
    source_dir: Path,
    output_root: Path,
    repo_root: Path,
    run_id: str,
    reviewer_job: str = K_LESSON_GENERATION_REVIEW_EDIT_JOB,
    review_context: str | None = None,
) -> ReviewEditResult:
    """Record advisory review findings and deterministic repairs on a copy."""
    source = Path(source_dir)
    review_root = Path(output_root) / K_LESSON_GENERATION_REVIEW_EDIT_SUBDIR
    review_root.mkdir(parents=True, exist_ok=True)
    copied_source = review_root / "source"
    _copy_authoring_source(source, copied_source)
    original_hash = _package_hash(source)
    identity_repairs = _repair_lesson_identity(copied_source)
    repair_result = repair_exercise_source(
        (copied_source / K_LESSON_GENERATION_EXERCISES_FILE).read_text(encoding="utf-8"),
        lesson_text=(copied_source / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8"),
    )
    (copied_source / K_LESSON_GENERATION_EXERCISES_FILE).write_text(repair_result.exercises_yaml, encoding="utf-8")
    _refresh_approved_source_hash(copied_source / K_LESSON_GENERATION_PLAN_FILE, copied_source)
    (review_root / "mechanical-repairs.yaml").write_text(
        yaml.safe_dump(
            {
                "identity_repairs": identity_repairs,
                "repairs": [item.model_dump(mode="json") for item in repair_result.repairs],
                "preflight": repair_result.audit.model_dump(mode="json"),
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    mechanical_audit = audit_source_directory(copied_source)
    (review_root / "mechanical.yaml").write_text(
        yaml.safe_dump(mechanical_audit.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    receipt: dict[str, Any] = {
        "mode": "review_edit",
        "run_id": run_id,
        "reviewer_job": reviewer_job,
        "original_source_hash": original_hash,
        "deterministic_repairs": {
            "identity": identity_repairs,
            "exercises": [item.model_dump(mode="json") for item in repair_result.repairs],
        },
        "mechanical_preflight": mechanical_audit.model_dump(mode="json"),
        "stages": [],
    }
    if mechanical_audit.status == "invalid" or mechanical_audit.material_findings:
        receipt["status"] = "needs_human"
        receipt["error"] = "deterministic preflight findings remain unresolved"
        (review_root / "review.yaml").write_text(
            yaml.safe_dump({"status": "not_run", "reason": receipt["error"]}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        _write_receipt(review_root, receipt)
        return ReviewEditResult(
            status="needs_human",
            source_dir=copied_source,
            review_path=review_root / "review.yaml",
            receipt_path=review_root / "receipt.json",
            review=None,
            mechanical_audit=mechanical_audit,
        )

    prompt = build_review_edit_prompt(
        lesson_md=(copied_source / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8"),
        exercises_yaml=(copied_source / K_LESSON_GENERATION_EXERCISES_FILE).read_text(encoding="utf-8"),
        mechanical_audit=mechanical_audit,
        review_context=review_context,
    )
    (review_root / "prompt.md").write_text(prompt, encoding="utf-8")
    try:
        review, responses = _invoke_review(
            prompt=prompt,
            repo_root=Path(repo_root),
            reviewer_job=reviewer_job,
            review_root=review_root,
            expected_handles=mechanical_audit.exercise_handles,
        )
    except LlmException as exc:
        receipt["status"] = "reviewer_unavailable"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        _write_receipt(review_root, receipt)
        raise
    except (ValueError, TypeError) as exc:
        receipt["status"] = "reviewer_invalid"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        _write_receipt(review_root, receipt)
        raise LlmParseException(f"review response was invalid: {exc}") from exc

    receipt["review"] = review.model_dump(mode="json")
    receipt["review_response"] = [build_response_metadata(response) for response in responses]
    receipt["advisory_findings"] = len(review.findings)
    (review_root / "review.yaml").write_text(
        yaml.safe_dump(review.model_dump(mode="json"), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    try:
        _refresh_approved_source_hash(copied_source / K_LESSON_GENERATION_PLAN_FILE, copied_source)
        validate_approved_source(copied_source)
        compile_lesson_package(copied_source)
    except (OSError, ValueError) as exc:
        receipt["status"] = "deterministic_repair_failed"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        _write_receipt(review_root, receipt)
        raise

    final_hash = _package_hash(copied_source)
    status = "edited" if final_hash != original_hash else "unchanged"
    receipt["status"] = status
    receipt["source_hash"] = final_hash
    receipt["source_changed_by_deterministic_repairs"] = final_hash != original_hash
    _write_receipt(review_root, receipt)
    return ReviewEditResult(
        status=status,
        source_dir=copied_source,
        review_path=review_root / "review.yaml",
        receipt_path=review_root / "receipt.json",
        review=review,
        mechanical_audit=mechanical_audit,
    )


def build_review_edit_prompt(
    *,
    lesson_md: str,
    exercises_yaml: str,
    mechanical_audit: MechanicalAudit,
    review_context: str | None = None,
) -> str:
    """Build a source-grounded advisory review prompt."""
    audit_json = json.dumps(mechanical_audit.model_dump(mode="json"), ensure_ascii=False, indent=2)
    context_block = (
        "\nOTHER AI REVIEW NOTES (unverified; use only as inspection context)\n"
        "---BEGIN REVIEW NOTES---\n"
        f"{review_context}\n"
        "---END REVIEW NOTES---\n"
        if review_context
        else ""
    )
    return f"""You review a frozen Norwegian lesson package for possible language-quality, translation, answer, feedback, and learner-task concerns.

Every finding is an unverified AI suggestion. Findings never authorize source edits, require the author to apply them, or block approval or release. Give each finding a location and rationale so an owner can inspect it. Do not treat another AI review as authoritative. Do not make deterministic claims already settled by the preflight.

Return a summary, one audited_handles entry for every exercise handle, and findings. Use an empty findings list when you have no concern. Do not include edits or replacement source.

MECHANICAL PREFLIGHT
---BEGIN PREFLIGHT---
{audit_json}
---END PREFLIGHT---
{context_block}
LESSON.MD
---BEGIN LESSON---
{lesson_md}
---END LESSON---

EXERCISES.YAML
---BEGIN EXERCISES---
{exercises_yaml}
---END EXERCISES---
"""


def _invoke_review(
    *,
    prompt: str,
    repo_root: Path,
    reviewer_job: str,
    review_root: Path,
    expected_handles: list[str],
) -> tuple[ReviewEditPackage, list[LlmResponse]]:
    """Invoke the configured reviewer with one bounded schema re-ask."""
    agent = runner_for_job(reviewer_job, repo_root=repo_root)
    instruction = build_structured_json_prompt(prompt, ReviewEditPackage)
    schema_json = json.dumps(ReviewEditPackage.model_json_schema(), ensure_ascii=False)
    responses: list[LlmResponse] = []
    last_error: Exception | None = None
    for attempt in range(K_LESSON_GENERATION_REVIEW_EDIT_MAX_REASKS + 1):
        response = agent.invoke_response(instruction)
        responses.append(response)
        (review_root / f"response-{attempt + 1}.txt").write_text(response.text, encoding="utf-8")
        try:
            review = ReviewEditPackage.model_validate(extract_json_object(response.text))
            _validate_review_handles(review, expected_handles)
            return review, responses
        except (ValueError, TypeError) as exc:
            last_error = exc
            instruction = (
                f"{prompt}\n\nYour previous response was invalid: {exc}. "
                "Return only a valid advisory report matching this schema. Do not return source edits.\n"
                f"{schema_json}"
            )
    raise LlmParseException(f"review/edit response remained invalid: {last_error}")


def _validate_review_handles(review: ReviewEditPackage, expected_handles: list[str]) -> None:
    """Require one semantic-review acknowledgement for every exercise handle."""
    actual = review.audited_handles
    if len(actual) != len(set(actual)):
        raise ValueError("audited_handles must not contain duplicates")
    if set(actual) != set(expected_handles) or len(actual) != len(expected_handles):
        missing = [handle for handle in expected_handles if handle not in actual]
        extra = [handle for handle in actual if handle not in expected_handles]
        raise ValueError(f"audited_handles mismatch: missing={missing!r}; extra={extra!r}")


def _copy_authoring_source(source_dir: Path, destination: Path) -> None:
    """Copy only the three authored files; never copy derived artifacts."""
    validate_approved_plan(Path(source_dir))
    destination.mkdir(parents=True, exist_ok=False)
    for name in K_LESSON_GENERATION_REVIEW_EDIT_REQUIRED_FILES:
        shutil.copy2(Path(source_dir) / name, destination / name)


def _repair_lesson_identity(source_dir: Path) -> list[dict[str, str]]:
    """Align a copied lesson slug with the approved plan's lesson identity."""
    plan_path = Path(source_dir) / K_LESSON_GENERATION_PLAN_FILE
    lesson_path = Path(source_dir) / K_LESSON_GENERATION_LESSON_FILE
    plan_metadata, _ = parse_source_frontmatter(plan_path.read_text(encoding="utf-8"))
    lesson_text = lesson_path.read_text(encoding="utf-8")
    lesson_metadata, lesson_body = parse_source_frontmatter(lesson_text)
    lesson_body = lesson_body.lstrip("\n")
    lesson_id = plan_metadata.get("lesson_id")
    slug = lesson_metadata.get("slug")
    if not isinstance(lesson_id, str) or not lesson_id or slug == lesson_id:
        return []
    if not isinstance(slug, str) or not slug:
        raise ValueError("lesson.md must contain a non-empty slug")
    lesson_metadata["slug"] = lesson_id
    updated = (
        "---\n"
        + yaml.safe_dump(lesson_metadata, allow_unicode=True, sort_keys=False).rstrip()
        + "\n---\n"
        + lesson_body
    )
    lesson_path.write_text(updated, encoding="utf-8")
    return [{"code": "lesson-slug-align", "from": slug, "to": lesson_id}]


def _refresh_approved_source_hash(plan_path: Path, source_dir: Path) -> None:
    """Refresh the plan's protected hash after an approved local edit."""
    text = Path(plan_path).read_text(encoding="utf-8")
    metadata, body = parse_source_frontmatter(text)
    content_hash = approved_content_hash(source_dir)
    if metadata.get("approved_source_hash") == content_hash:
        return
    metadata["approved_source_hash"] = content_hash
    updated = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip() + "\n---" + body
    Path(plan_path).write_text(updated, encoding="utf-8")


def _package_hash(source_dir: Path) -> str:
    """Hash the exact authored package before or after review edits."""
    hasher = hashlib.sha256()
    for name in K_LESSON_GENERATION_REVIEW_EDIT_REQUIRED_FILES:
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((Path(source_dir) / name).read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def _write_receipt(review_root: Path, receipt: dict[str, Any]) -> Path:
    """Persist the review/edit receipt atomically enough for scratch use."""
    path = Path(review_root) / "receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


__all__ = [
    "ReviewEditFinding",
    "ReviewEditPackage",
    "ReviewEditResult",
    "build_review_edit_prompt",
    "review_existing_package",
]
