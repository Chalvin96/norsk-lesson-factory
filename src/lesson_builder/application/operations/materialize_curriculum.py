"""Entry point: `materialize_curriculum_plan` sequences approved catalog plans."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from typing import cast

import yaml

from lesson_builder.application.operations.load_terminology import load_terminology_registry
from lesson_builder.domain.catalog.settings import K_CATALOG_PLANNER_KIND_ORDER
from lesson_builder.domain.curriculum.models import CatalogKind
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanResult
from lesson_builder.domain.curriculum.models import CurriculumSequence
from lesson_builder.domain.curriculum.services.plan_policy import build_plan_slots
from lesson_builder.domain.curriculum.services.plan_policy import build_sequence_report
from lesson_builder.domain.curriculum.services.plan_policy import hash_catalog_content
from lesson_builder.domain.curriculum.services.plan_policy import hash_sequence_content
from lesson_builder.domain.curriculum.services.plan_policy import normalize_catalog_kinds
from lesson_builder.domain.curriculum.services.plan_policy import require_complete_dependency_graph
from lesson_builder.domain.curriculum.settings import K_CATALOG_PLANNER_SCHEMA_VERSION
from lesson_builder.domain.lesson.models.terminology import TerminologyRegistry
from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths

K_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def materialize_curriculum_plan(
    *,
    repo_root: Path,
    run_id: str | None = None,
    catalog_path: Path | None = None,
    included_kinds: Sequence[str] | None = None,
    auto_approve: bool = False,
    commit: bool = False,
) -> CurriculumPlanResult:
    """Write a deterministic course plan from approved YAML.

    The planner never reads scratch proposals or canonical JSON. It requires a
    human-promoted schema-3 dependency graph, validates its owner references,
    and orders approved owners with a stable topological sort. By default, the
    MVP scope plans grammar, phraseology, and communicative owners; pronunciation
    and writing remain approved catalog inventory but are explicitly deferred
    until their category contracts are ready. ``commit`` materializes the
    explicitly auto-approved plan while retaining the scratch run.
    """
    root = Path(repo_root)
    if commit and not auto_approve:
        raise ValueError("--commit requires --auto-approve")
    chosen_run_id = _validated_run_id(run_id)
    selected_kinds = normalize_catalog_kinds(included_kinds)
    paths = WorkspacePaths(root)
    source_path = root / catalog_path if catalog_path else paths.catalog_file
    payload = _load_yaml_mapping(source_path)
    catalog_status = str(payload.get("catalog_status", "")).strip()
    if catalog_status != "complete":
        raise ValueError(
            "approved catalog is not schedulable: "
            f"catalog_status={catalog_status!r}; resolve approved catalog status before planning"
        )
    require_complete_dependency_graph(payload)
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"approved catalog has no entries: {source_path}")

    sequence_path = paths.curriculum_sequence
    sequence = _load_sequence(sequence_path)
    registry = _required_terminology_registry(entries, root)
    slots, dependency_edge_counts = build_plan_slots(
        entries,
        registry=registry,
        included_kinds=set(selected_kinds),
        sequence=sequence,
    )
    plan, excluded_kinds = _build_curriculum_plan(
        entries=entries,
        payload=payload,
        source_path=source_path,
        root=root,
        selected_kinds=selected_kinds,
        sequence=sequence,
        sequence_path=sequence_path,
        registry=registry,
        slots=slots,
        dependency_edge_counts=dependency_edge_counts,
        chosen_run_id=chosen_run_id,
        auto_approve=auto_approve,
    )
    output_dir = paths.curriculum_scratch_root / chosen_run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    plan_path = output_dir / "plan.yaml"
    serialized_plan = yaml.safe_dump(plan.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
    plan_path.write_text(serialized_plan, encoding="utf-8")
    (output_dir / "README.md").write_text(
        "# Scratch catalog curriculum plan\n\n"
        "This plan is derived from the approved Markdown/YAML catalog. It is "
        f"{('auto-approved for generation' if auto_approve else 'parked for curriculum review')}; "
        "it does not modify canonical JSON or accept lesson human gates.\n\n"
        f"Included kinds: {', '.join(selected_kinds)}\n"
        f"Deferred kinds: {', '.join(excluded_kinds) or 'none'}\n"
        "Deferred prerequisites remain references only; they are not generated "
        "as part of this plan.\n",
        encoding="utf-8",
    )
    committed_plan_path: str | None = None
    if commit:
        canonical_path = paths.curriculum_plan
        write_text_atomically(canonical_path, serialized_plan, create_parent=True, suffix=".tmp")
        committed_plan_path = str(canonical_path.relative_to(root))
    return CurriculumPlanResult(
        status="ready_for_generation" if auto_approve else "parked_for_review",
        run_id=chosen_run_id,
        plan_path=str(plan_path.relative_to(root)),
        plan=plan,
        committed_plan_path=committed_plan_path,
    )


def validate_committed_curriculum_plan(*, repo_root: Path) -> CurriculumPlan:
    """Validate that the committed plan still matches its approved catalog."""
    root = Path(repo_root)
    paths = WorkspacePaths(root)
    plan_path = paths.curriculum_plan
    if not plan_path.exists():
        raise FileNotFoundError(f"committed curriculum plan not found: {plan_path}")
    plan = CurriculumPlan.model_validate(_load_yaml_mapping(plan_path))
    catalog_path = root / plan.source_catalog
    current_hash = hash_catalog_content(_load_yaml_mapping(catalog_path))
    sequence_path = root / plan.source_sequence if plan.source_sequence else paths.curriculum_sequence
    current_sequence = _load_sequence(sequence_path)
    current_sequence_hash = hash_sequence_content(current_sequence)
    sequence_is_stale = (current_sequence is not None and current_sequence_hash != plan.source_sequence_hash) or (
        current_sequence is None and plan.source_sequence_hash not in {"", current_sequence_hash}
    )
    if current_hash != plan.source_catalog_hash or sequence_is_stale:
        raise ValueError(
            "committed curriculum plan is stale: "
            f"catalog plan={plan.source_catalog_hash}, current={current_hash}; "
            f"sequence plan={plan.source_sequence_hash}, current={current_sequence_hash}; "
            "rerun `lesson-data curriculum catalog --auto-approve --commit`"
        )
    return plan


def _build_curriculum_plan(
    *,
    entries: list[Any],
    payload: Mapping[str, Any],
    source_path: Path,
    root: Path,
    selected_kinds: tuple[CatalogKind, ...],
    sequence: CurriculumSequence | None,
    sequence_path: Path,
    registry: TerminologyRegistry | None,
    slots: list[Any],
    dependency_edge_counts: dict[str, int],
    chosen_run_id: str,
    auto_approve: bool,
) -> tuple[CurriculumPlan, tuple[CatalogKind, ...]]:
    """Build one reviewable plan and its deferred catalog kinds."""
    catalog_hash = hash_catalog_content(payload)
    sequence_hash = hash_sequence_content(sequence)
    excluded_kinds: tuple[CatalogKind, ...] = tuple(
        cast(CatalogKind, kind) for kind in K_CATALOG_PLANNER_KIND_ORDER if kind not in selected_kinds
    )
    deferred_helpful_references = sum(
        dependency not in {slot.catalog_id for slot in slots}
        for slot in slots
        for dependency in slot.helpful_prerequisites
    )
    summary = {
        "approved_entries": len(slots),
        "excluded_entries": len(entries) - len(slots),
        "deferred_entries": sum(str(raw.get("catalog_kind", "")) in excluded_kinds for raw in entries),
        "deferred_helpful_prerequisite_references": deferred_helpful_references,
        "owner_scope_missing": sum(slot.owner_scope is None for slot in slots),
        "provisional_cefr_entries": sum(bool(slot.provisional_flags) for slot in slots),
        "preferred_sequence_entries": len(sequence.entries) if sequence is not None else 0,
    }
    return (
        CurriculumPlan(
            schema_version=K_CATALOG_PLANNER_SCHEMA_VERSION,
            run_id=chosen_run_id,
            source_catalog=str(source_path.relative_to(root)),
            source_catalog_hash=catalog_hash,
            source_sequence=str(sequence_path.relative_to(root)) if sequence is not None else "",
            source_sequence_hash=sequence_hash,
            source_snapshot=str(payload.get("snapshot", "")),
            dependency_graph_status="complete",
            dependency_edge_counts=dependency_edge_counts,
            curriculum_approval="auto_approved" if auto_approve else "pending_human",
            included_catalog_kinds=list(selected_kinds),
            excluded_catalog_kinds=list(excluded_kinds),
            summary=summary,
            sequence_report=build_sequence_report(entries, sequence=sequence, included_kinds=set(selected_kinds)),
            slots=slots,
        ),
        excluded_kinds,
    )


def _load_sequence(path: Path) -> CurriculumSequence | None:
    """Load the optional editorial sequence file without changing old fallback order."""
    if not path.exists():
        return None
    payload = _load_yaml_mapping(path)
    try:
        return CurriculumSequence.model_validate(payload)
    except ValueError as exc:
        raise ValueError(f"invalid curriculum sequence {path}: {exc}") from exc


def _required_terminology_registry(entries: list[Any], root: Path) -> TerminologyRegistry | None:
    """Load the registry only when an approved entry binds terminology IDs."""
    for raw in entries:
        if isinstance(raw, Mapping) and isinstance(raw.get("terminology_ids"), list) and raw["terminology_ids"]:
            return load_terminology_registry(WorkspacePaths(root).terminology_registry)
    return None


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    """Load one YAML mapping with an actionable error."""
    if not path.exists():
        raise FileNotFoundError(f"catalog source not found: {path}")
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"catalog source must be a YAML mapping: {path}")
    return payload


def _validated_run_id(run_id: str | None) -> str:
    """Return a safe unique scratch run identifier."""
    value = run_id or uuid.uuid4().hex[:12]
    if not K_RUN_ID_RE.fullmatch(value):
        raise ValueError("run_id must be a single safe identifier of at most 64 characters")
    return value


__all__ = ["materialize_curriculum_plan", "validate_committed_curriculum_plan"]
