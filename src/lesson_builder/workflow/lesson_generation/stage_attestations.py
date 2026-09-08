"""Entry points: `write_stage_attestations` and `verify_source_attestations`.

Typed, content-addressed stage receipts for the lesson production review
chain. Lesson review, normalization preservation, intent review, exercise
verification, deterministic coverage, and artifact compilation each record a
distinct attestation bound to the exact bytes they reviewed. Parking and
promotion fail closed unless every required attestation is fresh, passing, and
bound to the final source hash. Attestations travel beside the authored bytes
as scratch evidence only; they are never exported to the learner app.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXERCISES_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_LESSON_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_PLAN_FILE

K_STAGE_ATTESTATIONS_FILE = "stage_attestations.json"
K_STAGE_REQUESTS_FILE = "exercise_requests.yaml"
K_STAGE_LESSON_REVIEW = "lesson_review"
K_STAGE_NORMALIZATION_REVIEW = "normalization_review"
K_STAGE_INTENT_REVIEW = "intent_review"
K_STAGE_EXERCISE_VERIFICATION = "exercise_verification"
K_STAGE_COVERAGE = "coverage"
K_STAGE_ARTIFACT_COMPILATION = "artifact_compilation"
K_STAGE_REQUIRED: tuple[str, ...] = (
    K_STAGE_LESSON_REVIEW,
    K_STAGE_NORMALIZATION_REVIEW,
    K_STAGE_INTENT_REVIEW,
    K_STAGE_EXERCISE_VERIFICATION,
    K_STAGE_COVERAGE,
    K_STAGE_ARTIFACT_COMPILATION,
)
K_STAGE_PACKAGE_FILES: tuple[str, ...] = (
    K_LESSON_GENERATION_PLAN_FILE,
    K_LESSON_GENERATION_LESSON_FILE,
    K_LESSON_GENERATION_EXERCISES_FILE,
)
K_STAGE_STATUS_PASS: Literal["pass"] = "pass"
K_STAGE_STATUS_FAIL: Literal["fail"] = "fail"
K_STAGE_STATUS_UNAVAILABLE: Literal["unavailable"] = "unavailable"
K_STAGE_STATUS_INVALID: Literal["invalid"] = "invalid"
K_STAGE_STATUS_INVALIDATED: Literal["invalidated"] = "invalidated"
K_STAGE_STATUS_UNVERIFIED_OPEN: Literal["unverified_open"] = "unverified_open"
K_STAGE_SCHEMA_VERSION = "1"
K_LESSON_REVIEW_POLICY_VERSION = "1"
K_LESSON_REVIEW_PROMPT_VERSION = "9"
K_NORMALIZATION_REVIEW_POLICY_VERSION = "3"
K_NORMALIZATION_REVIEW_PROMPT_VERSION = "5"
K_INTENT_REVIEW_POLICY_VERSION = "2"
K_EXERCISE_VERIFICATION_POLICY_VERSION = "4"
K_COVERAGE_POLICY_VERSION = "1"
K_ARTIFACT_COMPILATION_POLICY_VERSION = "1"
K_STAGE_POLICY_VERSIONS: dict[str, str] = {
    K_STAGE_LESSON_REVIEW: K_LESSON_REVIEW_POLICY_VERSION,
    K_STAGE_NORMALIZATION_REVIEW: K_NORMALIZATION_REVIEW_POLICY_VERSION,
    K_STAGE_INTENT_REVIEW: K_INTENT_REVIEW_POLICY_VERSION,
    K_STAGE_EXERCISE_VERIFICATION: K_EXERCISE_VERIFICATION_POLICY_VERSION,
    K_STAGE_COVERAGE: K_COVERAGE_POLICY_VERSION,
    K_STAGE_ARTIFACT_COMPILATION: K_ARTIFACT_COMPILATION_POLICY_VERSION,
}
K_STAGE_PROMPT_VERSIONS: dict[str, str] = {
    K_STAGE_LESSON_REVIEW: K_LESSON_REVIEW_PROMPT_VERSION,
    K_STAGE_NORMALIZATION_REVIEW: K_NORMALIZATION_REVIEW_PROMPT_VERSION,
}

StageStatus = Literal[
    "pass",
    "fail",
    "unavailable",
    "invalid",
    "invalidated",
    "unverified_open",
]


class StageAttestation(BaseModel):
    """One typed stage result bound to the exact bytes it reviewed."""

    model_config = ConfigDict(extra="forbid")

    stage: str
    status: StageStatus
    policy_version: str = Field(min_length=1)
    prompt_hash: str | None = None
    prompt_version: str | None = None
    input_hash: str | None = None
    source_hash: str | None = None
    lesson_hash: str | None = None
    requests_hash: str | None = None
    attempts: int = Field(default=1, ge=1)
    details: dict[str, Any] = Field(default_factory=dict)


class StageAttestations(BaseModel):
    """The complete content-addressed evidence set for one package."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = K_STAGE_SCHEMA_VERSION
    stages: dict[str, StageAttestation]


def text_content_hash(value: str) -> str:
    """Hash one stage input such as a draft or request handoff."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def package_source_hash(
    *,
    plan_text: str,
    lesson_text: str,
    exercises_text: str,
) -> str:
    """Hash the exact authored package bytes in stable filename order."""
    hasher = hashlib.sha256()
    for name, text in (
        (K_LESSON_GENERATION_PLAN_FILE, plan_text),
        (K_LESSON_GENERATION_LESSON_FILE, lesson_text),
        (K_LESSON_GENERATION_EXERCISES_FILE, exercises_text),
    ):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(text.encode("utf-8"))
    return "sha256:" + hasher.hexdigest()


def source_dir_hashes(source_dir: Path) -> tuple[str, str, str | None]:
    """Return package, lesson, and request hashes for one source directory."""
    root = Path(source_dir)
    source_hash = package_source_hash(
        plan_text=(root / K_LESSON_GENERATION_PLAN_FILE).read_text(encoding="utf-8"),
        lesson_text=(root / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8"),
        exercises_text=(root / K_LESSON_GENERATION_EXERCISES_FILE).read_text(encoding="utf-8"),
    )
    lesson_hash = text_content_hash((root / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8"))
    requests_path = root / K_STAGE_REQUESTS_FILE
    requests_hash = text_content_hash(requests_path.read_text(encoding="utf-8")) if requests_path.is_file() else None
    return source_hash, lesson_hash, requests_hash


def write_stage_attestations(source_dir: Path, attestations: StageAttestations) -> Path:
    """Persist the evidence set beside the authored bytes it attests."""
    path = Path(source_dir) / K_STAGE_ATTESTATIONS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(attestations.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def read_stage_attestations(source_dir: Path) -> StageAttestations | None:
    """Load a traveling evidence set, or None when it is absent or unreadable."""
    path = Path(source_dir) / K_STAGE_ATTESTATIONS_FILE
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return StageAttestations.model_validate(payload)
    except (OSError, TypeError, ValueError, ValidationError):
        return None


def verify_source_attestations(source_dir: Path) -> list[str]:
    """Verify the traveling attestations against the exact current bytes."""
    root = Path(source_dir)
    if (root / K_STAGE_ATTESTATIONS_FILE).is_file() and read_stage_attestations(root) is None:
        return ["stage attestations are present but unreadable or invalid"]
    try:
        source_hash, lesson_hash, requests_hash = source_dir_hashes(root)
    except OSError as exc:
        return [f"authored source is incomplete: {exc}"]
    return verify_stage_attestations(
        read_stage_attestations(root),
        source_hash=source_hash,
        lesson_hash=lesson_hash,
        requests_hash=requests_hash,
    )


def verify_stage_attestations(
    attestations: StageAttestations | None,
    *,
    source_hash: str,
    lesson_hash: str,
    requests_hash: str | None,
) -> list[str]:
    """Return every reason the evidence set cannot admit this exact source.

    An empty list means every required stage has a fresh passing attestation
    bound to these bytes. Open write/speak tasks remain an explicit
    unverified_open result after the standalone attempt and open-rubric surfaces
    pass; they may cross this machine parking boundary because the artifact
    boundary is the human review responsibility. Arbitrary learner responses
    remain unverified, even when those structural surfaces pass. Closed verifier
    failures and reviewer outages remain blocking.
    """
    issues: list[str] = []
    if attestations is None:
        return ["stage attestations are missing for this source"]
    issues = _attestation_inventory_issues(attestations)
    if issues:
        return issues
    return _attestation_binding_issues(attestations, source_hash, lesson_hash, requests_hash)


def summary_statuses(attestations: StageAttestations | None) -> dict[str, str]:
    """Project stage statuses for receipts and batch summaries."""
    if attestations is None:
        return {}
    return {stage: item.status for stage, item in attestations.stages.items()}


def _attestation_inventory_issues(attestations: StageAttestations) -> list[str]:
    """Validate attestation keys, policies, statuses, and prompt metadata."""
    issues: list[str] = []
    if attestations.schema_version != K_STAGE_SCHEMA_VERSION:
        issues.append(f"attestation schema {attestations.schema_version!r} is not {K_STAGE_SCHEMA_VERSION!r}")
    unknown_stages = sorted(set(attestations.stages) - set(K_STAGE_REQUIRED))
    if unknown_stages:
        issues.append("attestation contains unknown stage(s): " + ", ".join(unknown_stages))
    for stage in K_STAGE_REQUIRED:
        attestation = attestations.stages.get(stage)
        if attestation is None:
            issues.append(f"required stage {stage!r} has no attestation")
            continue
        issues.extend(_attestation_stage_issues(stage, attestation))
    return issues


def _attestation_stage_issues(stage: str, attestation: StageAttestation) -> list[str]:
    """Validate one required stage's identity, policy, status, and prompt."""
    issues: list[str] = []
    if attestation.stage != stage:
        issues.append(f"attestation key {stage!r} contains stage {attestation.stage!r}")
    expected_version = K_STAGE_POLICY_VERSIONS[stage]
    if attestation.policy_version != expected_version:
        issues.append(
            f"stage {stage!r} policy version {attestation.policy_version!r} is stale; expected {expected_version!r}"
        )
    if not _stage_status_is_allowed(stage, attestation):
        issues.append(f"stage {stage!r} status is {attestation.status!r}")
        if stage == K_STAGE_EXERCISE_VERIFICATION and attestation.status == K_STAGE_STATUS_UNVERIFIED_OPEN:
            issues.extend(_unverified_open_details_issues(attestation.details))
    if stage not in (K_STAGE_LESSON_REVIEW, K_STAGE_NORMALIZATION_REVIEW):
        return issues
    if not attestation.prompt_hash:
        issues.append(f"stage {stage!r} is missing its prompt hash")
    expected_prompt_version = K_STAGE_PROMPT_VERSIONS[stage]
    if attestation.prompt_version != expected_prompt_version:
        issues.append(
            f"stage {stage!r} prompt version {attestation.prompt_version!r} is stale; "
            f"expected {expected_prompt_version!r}"
        )
    return issues


def _stage_status_is_allowed(stage: str, attestation: StageAttestation) -> bool:
    """Allow open status only after every required surface has passed."""
    if attestation.status == K_STAGE_STATUS_PASS:
        return True
    if stage != K_STAGE_EXERCISE_VERIFICATION or attestation.status != K_STAGE_STATUS_UNVERIFIED_OPEN:
        return False
    return not _unverified_open_details_issues(attestation.details)


def _unverified_open_details_issues(details: dict[str, Any]) -> list[str]:
    """Require every open-task surface to pass before admitting mixed content."""
    issues: list[str] = []
    handles = details.get("open_handles")
    if (
        not isinstance(handles, list)
        or not handles
        or not all(isinstance(handle, str) and handle for handle in handles)
    ):
        issues.append("unverified_open exercise stage is missing non-empty open_handles")
    for field in ("attempt_surface_status", "open_rubrics_status", "standalone_status"):
        status = details.get(field)
        if status not in {"pass", "not_applicable"}:
            issues.append(f"unverified_open exercise stage requires {field}=pass or not_applicable")
    return issues


def _attestation_binding_issues(
    attestations: StageAttestations,
    source_hash: str,
    lesson_hash: str,
    requests_hash: str | None,
) -> list[str]:
    """Validate attestation hashes against the current authored package."""
    issues: list[str] = []
    lesson_review = attestations.stages[K_STAGE_LESSON_REVIEW]
    normalization = attestations.stages[K_STAGE_NORMALIZATION_REVIEW]
    intent = attestations.stages[K_STAGE_INTENT_REVIEW]
    if lesson_review.input_hash is None:
        issues.append("lesson review attestation is missing its draft input hash")
    if normalization.input_hash is None or normalization.input_hash != lesson_review.input_hash:
        issues.append("normalization review was not bound to the reviewed draft bytes")
    if normalization.lesson_hash != lesson_hash:
        issues.append("normalization review attestation is stale for the current lesson bytes")
    if intent.lesson_hash != lesson_hash:
        issues.append("intent review attestation is stale for the current lesson bytes")
    if requests_hash is None:
        issues.append("exercise request handoff is missing beside the attestations")
    elif intent.requests_hash != requests_hash:
        issues.append("intent review attestation is stale for the current request handoff")
    for stage in (K_STAGE_EXERCISE_VERIFICATION, K_STAGE_COVERAGE, K_STAGE_ARTIFACT_COMPILATION):
        if attestations.stages[stage].source_hash != source_hash:
            issues.append(f"stage {stage!r} attestation is stale for the current source bytes")
    return issues


__all__ = [
    "K_ARTIFACT_COMPILATION_POLICY_VERSION",
    "K_COVERAGE_POLICY_VERSION",
    "K_EXERCISE_VERIFICATION_POLICY_VERSION",
    "K_INTENT_REVIEW_POLICY_VERSION",
    "K_LESSON_REVIEW_POLICY_VERSION",
    "K_LESSON_REVIEW_PROMPT_VERSION",
    "K_NORMALIZATION_REVIEW_POLICY_VERSION",
    "K_NORMALIZATION_REVIEW_PROMPT_VERSION",
    "K_STAGE_ARTIFACT_COMPILATION",
    "K_STAGE_ATTESTATIONS_FILE",
    "K_STAGE_COVERAGE",
    "K_STAGE_EXERCISE_VERIFICATION",
    "K_STAGE_INTENT_REVIEW",
    "K_STAGE_LESSON_REVIEW",
    "K_STAGE_NORMALIZATION_REVIEW",
    "K_STAGE_PACKAGE_FILES",
    "K_STAGE_POLICY_VERSIONS",
    "K_STAGE_PROMPT_VERSIONS",
    "K_STAGE_REQUESTS_FILE",
    "K_STAGE_REQUIRED",
    "K_STAGE_SCHEMA_VERSION",
    "K_STAGE_STATUS_FAIL",
    "K_STAGE_STATUS_INVALID",
    "K_STAGE_STATUS_INVALIDATED",
    "K_STAGE_STATUS_PASS",
    "K_STAGE_STATUS_UNAVAILABLE",
    "K_STAGE_STATUS_UNVERIFIED_OPEN",
    "StageAttestation",
    "StageAttestations",
    "StageStatus",
    "package_source_hash",
    "read_stage_attestations",
    "source_dir_hashes",
    "summary_statuses",
    "text_content_hash",
    "verify_source_attestations",
    "verify_stage_attestations",
    "write_stage_attestations",
]
