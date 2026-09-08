"""Entry point: ``LessonGenerationDeps`` and its default collaborators.

The collaborator protocols isolate filesystem work from the LangGraph nodes:
copying an approved authoring package, compiling Markdown/YAML, and appending
acceptance evidence. The transcript is derived after content compilation by
the pure projection in ``lesson_builder.domain.lesson.services.transcript``, so it is a
disposable derived artifact, not another authored input; this module only
projects audio declarations and writes the derived ``transcript.yaml``. Audio
is never synthesized here; the distribution exporter handles external media
providers. The workflow never mutates canonical
JSON authoring data; JSON remains a disposable export.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import Protocol

import yaml

if TYPE_CHECKING:
    from lesson_builder.workflow.lesson_generation.nodes.stages import RichAuthoringStageRunner

from lesson_builder.application.operations.load_lesson import load_plan_metadata
from lesson_builder.application.operations.load_terminology import load_terminology_bans
from lesson_builder.application.operations.prepare_source_package import hash_json
from lesson_builder.application.operations.prepare_source_package import prepare_source_package
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_ALLOWED_KINDS
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_COURSE_ID
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DECISION_ACCEPT
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DEFAULT_REVIEWER
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXERCISE_DIAGNOSTICS_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_EXERCISES_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_HASH_ALGO
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_HASH_PREFIX
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_LESSON_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_PLAN_FILE
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_REQUIRED_FILES
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_SCHEDULE_SLOT_MODULUS
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_SOURCE_SUBDIR
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_STAGE_ACCEPTED
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_TRANSCRIPT_FILE
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_ATTESTATIONS_FILE
from lesson_builder.workflow.lesson_generation.stage_attestations import K_STAGE_REQUESTS_FILE


class SourceCopier(Protocol):
    """Copy an approved source package into a disposable run directory."""

    def __call__(self, *, fixture_source: Path, output_root: Path, run_id: str) -> tuple[Path, list[str], str]:
        """Return copied source directory, relative file names, and source hash."""


class ExportCompiler(Protocol):
    """Compile authored content and derive its transcript into an export document."""

    def __call__(self, *, source_dir: Path, run_id: str) -> tuple[dict[str, Any], str]:
        """Return the export document and its deterministic export hash."""


class LedgerWriter(Protocol):
    """Record one human decision and its provenance hashes."""

    def __call__(
        self, *, ledger_path: Path, run_id: str, export_doc: dict[str, Any], source_hash: str
    ) -> dict[str, Any]:
        """Return the appended or already-existing ledger entry."""


@dataclass(frozen=True)
class ArtifactGenerationResult:
    """Paths and provenance produced by the authoring stages."""

    source_dir: Path
    receipt_path: Path
    receipt: dict[str, Any]


@dataclass
class LessonGenerationDeps:
    """Injected collaborators for the checkpointable lesson-generation graph."""

    copier: SourceCopier = field(default_factory=lambda: default_source_copier)
    proposal_copier: SourceCopier = field(default_factory=lambda: default_proposal_source_copier)
    compiler: ExportCompiler = field(default_factory=lambda: default_export_compiler)
    ledger: LedgerWriter = field(default_factory=lambda: default_ledger_writer)
    rich_stages: RichAuthoringStageRunner = field(default_factory=lambda: _default_rich_stage_runner())


def default_source_copier(*, fixture_source: Path, output_root: Path, run_id: str) -> tuple[Path, list[str], str]:
    """Validate approval and copy exactly the three authoring files.

    ``run_id`` is accepted by the protocol for future namespacing; the copied
    bytes and source hash intentionally do not depend on it.
    """
    del run_id
    fixture_source = Path(fixture_source)
    validate_approved_source(fixture_source)
    source_dir = Path(output_root) / K_LESSON_GENERATION_SOURCE_SUBDIR
    source_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    hasher = hashlib.new(K_LESSON_GENERATION_HASH_ALGO)
    for name in K_LESSON_GENERATION_REQUIRED_FILES:
        content = (fixture_source / name).read_bytes()
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(content)
        (source_dir / name).write_bytes(content)
        copied.append(name)
    return source_dir, copied, _prefixed_hash(hasher.digest())


def default_proposal_source_copier(
    *, fixture_source: Path, output_root: Path, run_id: str
) -> tuple[Path, list[str], str]:
    """Copy a plan-approved model proposal without claiming package approval."""
    del run_id
    fixture_source = Path(fixture_source)
    validate_approved_plan(fixture_source)
    return _copy_source_files(fixture_source=fixture_source, output_root=output_root)


def default_export_compiler(
    *,
    source_dir: Path,
    run_id: str,
    terminology_bans: TerminologyBans | None = None,
) -> tuple[dict[str, Any], str]:
    """Load the Markdown/YAML package through the application source loader.

    The JSON-shaped value returned here is still in memory. Only the finalize
    node writes it as disposable export output after human approval.
    """
    del run_id
    prepared = prepare_source_package(
        source_dir,
        terminology_bans=terminology_bans if terminology_bans is not None else load_terminology_bans(),
    )
    source_dir = Path(source_dir)
    (source_dir / K_LESSON_GENERATION_EXERCISE_DIAGNOSTICS_FILE).write_text(
        json.dumps(prepared.exercise_diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (source_dir / K_LESSON_GENERATION_TRANSCRIPT_FILE).write_text(
        yaml.safe_dump(prepared.transcript_document, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return prepared.export_doc, prepared.export_hash


def default_ledger_writer(
    *, ledger_path: Path, run_id: str, export_doc: dict[str, Any], source_hash: str
) -> dict[str, Any]:
    """Append one idempotent acceptance record with required provenance hashes."""
    ledger_path = Path(ledger_path)
    artifact_id = str(export_doc.get("artifact_id") or f"lesson:{run_id}")
    for existing in _read_ledger_entries(ledger_path):
        if (
            existing.get("artifact_id") == artifact_id
            and existing.get("run_id") == run_id
            and existing.get("decision") == K_LESSON_GENERATION_DECISION_ACCEPT
            and existing.get("source_hash") == source_hash
            and existing.get("semantic_hash") == export_doc.get("semantic_hash", "")
            and existing.get("dependency_hash") == export_doc.get("dependency_hash", "")
            and existing.get("export_hash") == export_doc.get("export_hash", "")
        ):
            return existing
    entry: dict[str, Any] = {
        "artifact_id": artifact_id,
        "run_id": run_id,
        "course_id": export_doc.get("course_id", K_LESSON_GENERATION_COURSE_ID),
        "source_hash": source_hash,
        "semantic_hash": export_doc.get("semantic_hash", ""),
        "dependency_hash": export_doc.get("dependency_hash", ""),
        "export_hash": export_doc.get("export_hash", ""),
        "status": K_LESSON_GENERATION_STAGE_ACCEPTED,
        "decision": K_LESSON_GENERATION_DECISION_ACCEPT,
        "reviewer": K_LESSON_GENERATION_DEFAULT_REVIEWER,
        "ts": datetime.now(UTC).isoformat(),
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return entry


def validate_approved_source(fixture_source: Path) -> dict[str, Any]:
    """Validate authoring shape, approval, identity, and protected source hash."""
    fixture_source = Path(fixture_source)
    missing = [name for name in K_LESSON_GENERATION_REQUIRED_FILES if not (fixture_source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"lesson-package fixture missing required file(s): {', '.join(missing)}")
    plan = load_plan_metadata(fixture_source / K_LESSON_GENERATION_PLAN_FILE)
    if plan.get("approved") is not True:
        raise ValueError("lesson-package fixture plan.md must contain approved: true")
    kind = plan.get("kind")
    if kind not in K_LESSON_GENERATION_ALLOWED_KINDS:
        raise ValueError(f"plan.md kind must be one of {list(K_LESSON_GENERATION_ALLOWED_KINDS)!r}")
    lesson_id = plan.get("lesson_id")
    if not isinstance(lesson_id, str) or not lesson_id:
        raise ValueError("plan.md must contain a non-empty lesson_id")
    expected_hash = approved_content_hash(fixture_source)
    if plan.get("approved_source_hash") != expected_hash:
        raise ValueError(
            "plan.md approved_source_hash does not match lesson.md, exercises.yaml, "
            f"and plan.md content (expected {expected_hash})"
        )
    lesson_frontmatter, _ = parse_source_frontmatter(
        (fixture_source / K_LESSON_GENERATION_LESSON_FILE).read_text(encoding="utf-8")
    )
    if lesson_frontmatter.get("slug") != lesson_id:
        raise ValueError("plan.md lesson_id must match lesson.md slug")
    return plan


def source_package_hash(source_dir: Path) -> str:
    """Hash the current authoring bytes exactly as the source copier does."""
    root = Path(source_dir)
    hasher = hashlib.new(K_LESSON_GENERATION_HASH_ALGO)
    for name in K_LESSON_GENERATION_REQUIRED_FILES:
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((root / name).read_bytes())
    return _prefixed_hash(hasher.digest())


def validate_approved_plan(fixture_source: Path) -> dict[str, Any]:
    """Validate the approved plan boundary used before model generation."""
    fixture_source = Path(fixture_source)
    missing = [name for name in K_LESSON_GENERATION_REQUIRED_FILES if not (fixture_source / name).is_file()]
    if missing:
        raise FileNotFoundError(f"lesson-package proposal missing required file(s): {', '.join(missing)}")
    plan = load_plan_metadata(fixture_source / K_LESSON_GENERATION_PLAN_FILE)
    if plan.get("approved") is not True:
        raise ValueError("lesson-package plan.md must contain approved: true")
    kind = plan.get("kind")
    if kind not in K_LESSON_GENERATION_ALLOWED_KINDS:
        raise ValueError(f"plan.md kind must be one of {list(K_LESSON_GENERATION_ALLOWED_KINDS)!r}")
    lesson_id = plan.get("lesson_id")
    if not isinstance(lesson_id, str) or not lesson_id:
        raise ValueError("plan.md must contain a non-empty lesson_id")
    return plan


def derive_scheduled_slot(*, artifact_id: str, source_hash: str) -> dict[str, Any]:
    """Derive a stable slot from artifact identity and protected source hash."""
    digest = hashlib.sha256(f"{artifact_id}|{source_hash}".encode()).hexdigest()
    return {
        "course_id": K_LESSON_GENERATION_COURSE_ID,
        "artifact_id": artifact_id,
        "slot_index": int(digest[:8], 16) % K_LESSON_GENERATION_SCHEDULE_SLOT_MODULUS,
        "ordering_key": digest[:16],
    }


def approved_content_hash(source_dir: Path) -> str:
    """Return the protected package hash with its self-hash field normalized."""
    hasher = hashlib.new(K_LESSON_GENERATION_HASH_ALGO)
    plan_metadata, plan_body = parse_source_frontmatter(
        (Path(source_dir) / K_LESSON_GENERATION_PLAN_FILE).read_text(encoding="utf-8")
    )
    plan_body = plan_body.lstrip("\n")
    plan_metadata.pop("approved_source_hash", None)
    normalized_plan = yaml.safe_dump(plan_metadata, allow_unicode=True, sort_keys=True) + plan_body
    hasher.update(K_LESSON_GENERATION_PLAN_FILE.encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(normalized_plan.encode("utf-8"))
    for name in (K_LESSON_GENERATION_LESSON_FILE, K_LESSON_GENERATION_EXERCISES_FILE):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update((Path(source_dir) / name).read_bytes())
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _default_rich_stage_runner() -> RichAuthoringStageRunner:
    """Construct the production rich-stage collaborator lazily."""
    from lesson_builder.workflow.lesson_generation.nodes.stages import RichAuthoringStages

    return RichAuthoringStages()


def _plan_objective_ids(plan: dict[str, Any]) -> set[str]:
    """Return non-empty objective identifiers declared by plan metadata."""
    objectives = plan.get("objectives", [])
    if not isinstance(objectives, list):
        return set()
    return {
        item["id"] for item in objectives if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"]
    }


def _copy_source_files(*, fixture_source: Path, output_root: Path) -> tuple[Path, list[str], str]:
    """Copy the three authoring source files and hash their exact bytes."""
    source_dir = Path(output_root) / K_LESSON_GENERATION_SOURCE_SUBDIR
    source_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    hasher = hashlib.new(K_LESSON_GENERATION_HASH_ALGO)
    for name in K_LESSON_GENERATION_REQUIRED_FILES:
        content = (fixture_source / name).read_bytes()
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(content)
        (source_dir / name).write_bytes(content)
        copied.append(name)
    for name in (K_STAGE_ATTESTATIONS_FILE, K_STAGE_REQUESTS_FILE):
        optional = fixture_source / name
        if optional.is_file():
            (source_dir / name).write_bytes(optional.read_bytes())
            copied.append(name)
    return source_dir, copied, _prefixed_hash(hasher.digest())


def _prefixed_hash(raw_digest: bytes) -> str:
    """Format a digest returned by hashlib.new."""
    return f"{K_LESSON_GENERATION_HASH_PREFIX}{raw_digest.hex()}"


def _read_ledger_entries(ledger_path: Path) -> list[dict[str, Any]]:
    """Read JSONL entries, tolerating an incomplete trailing line."""
    if not ledger_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    lines = ledger_path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                continue
            raise
        if isinstance(value, dict):
            entries.append(value)
    return entries


__all__ = [
    "SourceCopier",
    "ExportCompiler",
    "LedgerWriter",
    "ArtifactGenerationResult",
    "LessonGenerationDeps",
    "default_source_copier",
    "default_proposal_source_copier",
    "default_export_compiler",
    "default_ledger_writer",
    "validate_approved_source",
    "validate_approved_plan",
    "source_package_hash",
    "derive_scheduled_slot",
    "hash_json",
]
