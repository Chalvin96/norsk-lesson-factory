"""Entry points: pure curriculum policy called by materialization services.

`build_plan_slots`, `build_sequence_report`, `hash_catalog_content`,
`hash_sequence_content`, `normalize_catalog_kinds`, and
`require_complete_dependency_graph` are called by the application curriculum
materialization operation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any
from typing import cast

from lesson_builder.domain.catalog.models import DependencyEdge
from lesson_builder.domain.catalog.services.validate_dependency_graph import topological_order
from lesson_builder.domain.catalog.settings import K_CATALOG_PLANNER_CEFR_ORDER
from lesson_builder.domain.catalog.settings import K_CATALOG_PLANNER_KIND_ORDER
from lesson_builder.domain.curriculum.models import CatalogKind
from lesson_builder.domain.curriculum.models import CatalogOwnerScope
from lesson_builder.domain.curriculum.models import CatalogTeachingPoint
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.domain.curriculum.models import CurriculumSequence
from lesson_builder.domain.curriculum.models import CurriculumSequencePlacement
from lesson_builder.domain.curriculum.settings import K_CATALOG_PLANNER_DEFAULT_ACTIVE_KINDS
from lesson_builder.domain.curriculum.settings import K_CATALOG_PLANNER_EARLY_CEFR_TAGS
from lesson_builder.domain.curriculum.settings import K_CATALOG_PLANNER_EARLY_TECHNICAL_LABEL_BUDGET
from lesson_builder.domain.lesson.models.terminology import TerminologyRegistry
from lesson_builder.domain.lesson.validation.terminology_registry import resolve_concept_ids

K_CATALOG_PLANNER_COMPLETE_DEPENDENCY_SCHEMA_VERSION = 3


def build_plan_slots(
    entries: list[Any],
    *,
    registry: TerminologyRegistry | None,
    included_kinds: set[str],
    sequence: CurriculumSequence | None = None,
) -> tuple[list[CurriculumPlanSlot], dict[str, int]]:
    """Validate approved entries and return stable topological plan slots."""
    all_entry_by_id = _build_catalog_entries_by_id(entries)
    entry_by_id = {
        entry_id: raw for entry_id, raw in all_entry_by_id.items() if str(raw["catalog_kind"]) in included_kinds
    }
    dependency_edges = _build_catalog_dependency_edges(entry_by_id, all_entry_by_id)
    cefr_by_owner = {entry_id: _derive_cefr_floor(raw) for entry_id, raw in entry_by_id.items()}
    kind_by_owner = {entry_id: str(raw["catalog_kind"]) for entry_id, raw in entry_by_id.items()}
    preferred_rank = _validate_sequence(sequence, all_entry_by_id, entry_by_id) if sequence is not None else None
    ordered_ids = topological_order(
        entry_by_id,
        dependency_edges,
        cefr_by_owner=cefr_by_owner,
        kind_by_owner=kind_by_owner,
        kind_order=K_CATALOG_PLANNER_KIND_ORDER,
        preferred_rank=preferred_rank,
    )
    ordered = [(entry_id, entry_by_id[entry_id]) for entry_id in ordered_ids]
    slots = _build_curriculum_plan_slots(ordered, registry)
    return slots, {
        "required": sum(edge.kind == "required" for edge in dependency_edges),
        "helpful": sum(edge.kind == "helpful" for edge in dependency_edges),
    }


def hash_sequence_content(payload: Mapping[str, Any] | CurriculumSequence | None) -> str:
    """Hash normalized sequence input, including the explicit absent-file state."""
    if isinstance(payload, CurriculumSequence):
        normalized_payload: object = payload.model_dump(mode="json")
    elif payload is None:
        normalized_payload = {"schema_version": 0, "entries": []}
    else:
        normalized_payload = payload
    normalized = json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_sequence_report(
    entries: list[Any],
    *,
    sequence: CurriculumSequence | None,
    included_kinds: set[str],
) -> list[CurriculumSequencePlacement]:
    """Report requested, baseline, and dependency-safe positions for preferences."""
    if sequence is None:
        return []
    all_entry_by_id = _build_catalog_entries_by_id(entries)
    entry_by_id = {
        entry_id: raw for entry_id, raw in all_entry_by_id.items() if str(raw["catalog_kind"]) in included_kinds
    }
    dependency_edges = _build_catalog_dependency_edges(entry_by_id, all_entry_by_id)
    cefr_by_owner = {entry_id: _derive_cefr_floor(raw) for entry_id, raw in entry_by_id.items()}
    kind_by_owner = {entry_id: str(raw["catalog_kind"]) for entry_id, raw in entry_by_id.items()}
    baseline = topological_order(
        entry_by_id,
        dependency_edges,
        cefr_by_owner=cefr_by_owner,
        kind_by_owner=kind_by_owner,
        kind_order=K_CATALOG_PLANNER_KIND_ORDER,
    )
    preferred_rank = _validate_sequence(sequence, all_entry_by_id, entry_by_id)
    preferred = topological_order(
        entry_by_id,
        dependency_edges,
        cefr_by_owner=cefr_by_owner,
        kind_by_owner=kind_by_owner,
        kind_order=K_CATALOG_PLANNER_KIND_ORDER,
        preferred_rank=preferred_rank,
    )
    old_positions = {lesson_id: position for position, lesson_id in enumerate(baseline, start=1)}
    new_positions = {lesson_id: position for position, lesson_id in enumerate(preferred, start=1)}
    required_by_dependent: dict[str, list[str]] = {lesson_id: [] for lesson_id in preferred}
    helpful_by_dependent: dict[str, list[str]] = {lesson_id: [] for lesson_id in preferred}
    for edge in dependency_edges:
        target = required_by_dependent if edge.kind == "required" else helpful_by_dependent
        target[edge.dependent_id].append(edge.prerequisite_id)
    return [
        CurriculumSequencePlacement(
            lesson_id=entry.lesson_id,
            requested_position=position,
            old_position=old_positions[entry.lesson_id],
            new_position=new_positions[entry.lesson_id],
            status="moved" if new_positions[entry.lesson_id] != position else "placed",
            delayed_by_required_prerequisites=(
                required_by_dependent[entry.lesson_id]
                if new_positions[entry.lesson_id] > position and required_by_dependent[entry.lesson_id]
                else []
            ),
            helpful_prerequisites_later=[
                dependency
                for dependency in helpful_by_dependent[entry.lesson_id]
                if new_positions[dependency] > new_positions[entry.lesson_id]
            ],
        )
        for position, entry in enumerate(sequence.entries, start=1)
    ]


def normalize_catalog_kinds(included_kinds: Sequence[str] | None) -> tuple[CatalogKind, ...]:
    """Return a stable, validated generation scope for a curriculum plan."""
    requested = (
        tuple(K_CATALOG_PLANNER_DEFAULT_ACTIVE_KINDS)
        if included_kinds is None
        else tuple(dict.fromkeys(kind.strip() for kind in included_kinds if kind.strip()))
    )
    if not requested:
        raise ValueError("included_kinds must contain at least one catalog kind")
    unsupported = sorted(set(requested) - set(K_CATALOG_PLANNER_KIND_ORDER))
    if unsupported:
        raise ValueError(
            "unsupported catalog kind(s): "
            + ", ".join(unsupported)
            + "; choose from "
            + ", ".join(K_CATALOG_PLANNER_KIND_ORDER)
        )
    return tuple(cast(CatalogKind, kind) for kind in K_CATALOG_PLANNER_KIND_ORDER if kind in requested)


def require_complete_dependency_graph(payload: Mapping[str, Any]) -> None:
    """Reject schema-2 or pending catalogs before new curriculum planning."""
    try:
        schema_version = int(payload.get("schema_version", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("approved catalog has an invalid schema_version") from exc
    dependency_graph = payload.get("dependency_graph")
    if schema_version < K_CATALOG_PLANNER_COMPLETE_DEPENDENCY_SCHEMA_VERSION or not isinstance(
        dependency_graph, Mapping
    ):
        raise ValueError(
            "approved catalog requires schema_version 3 with a complete dependency_graph; "
            "run `lesson-data curriculum dependencies` and promote the reviewed result"
        )
    if dependency_graph.get("status") != "complete":
        raise ValueError(
            "approved catalog dependency_graph is not complete; resolve the dependency review before planning"
        )
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise TypeError("approved catalog entries must be a list")
    _validate_complete_catalog_entries(entries)


def hash_catalog_content(payload: Mapping[str, Any]) -> str:
    """Hash normalized source catalog content for plan compatibility checks."""
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _validate_sequence(
    sequence: CurriculumSequence,
    all_entry_by_id: dict[str, dict[str, Any]],
    entry_by_id: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Validate preference scope and return stable requested ranks."""
    if sequence.schema_version != 1:
        raise ValueError(f"unsupported curriculum sequence schema_version: {sequence.schema_version}; expected 1")
    ranks: dict[str, int] = {}
    for rank, preference in enumerate(sequence.entries):
        lesson_id = preference.lesson_id.strip()
        if lesson_id != preference.lesson_id:
            raise ValueError(
                f"curriculum sequence lesson ID must not have surrounding whitespace: {preference.lesson_id!r}"
            )
        if lesson_id in ranks:
            raise ValueError(f"curriculum sequence contains duplicate lesson ID: {lesson_id!r}")
        if lesson_id not in all_entry_by_id:
            raise ValueError(f"curriculum sequence references unknown catalog ID: {lesson_id!r}")
        raw = all_entry_by_id[lesson_id]
        if lesson_id not in entry_by_id:
            raise ValueError(
                f"curriculum sequence lesson {lesson_id!r} is deferred because catalog kind "
                f"{raw.get('catalog_kind')!r} is outside the active plan"
            )
        if str(raw.get("status", "active")).strip().lower() not in {"", "active", "approved"}:
            raise ValueError(f"curriculum sequence lesson {lesson_id!r} is not active")
        if _derive_cefr_floor(raw) != "A1":
            raise ValueError(f"curriculum sequence lesson {lesson_id!r} must be an A1 catalog entry")
        ranks[lesson_id] = rank
    return ranks


def _build_catalog_entries_by_id(entries: list[Any]) -> dict[str, dict[str, Any]]:
    """Validate approved entries and index them by their stable ID."""
    indexed: dict[str, dict[str, Any]] = {}
    for raw in entries:
        if not isinstance(raw, dict):
            raise TypeError("approved catalog entries must be mappings")
        entry_id = str(raw.get("id", "")).strip()
        if not entry_id or entry_id in indexed:
            raise ValueError(f"duplicate or missing approved catalog ID: {entry_id!r}")
        kind = str(raw.get("catalog_kind", "")).strip()
        if kind not in K_CATALOG_PLANNER_KIND_ORDER:
            raise ValueError(f"unsupported catalog kind for {entry_id!r}: {kind!r}")
        indexed[entry_id] = raw
    return indexed


def _build_catalog_dependency_edges(
    entry_by_id: dict[str, dict[str, Any]], all_entry_by_id: dict[str, dict[str, Any]]
) -> list[DependencyEdge]:
    """Validate dependency references and build required/helpful edges."""
    dependency_edges: list[DependencyEdge] = []
    for entry_id, raw in entry_by_id.items():
        _validate_catalog_dependencies(entry_id, raw, entry_by_id, all_entry_by_id)
        dependency_edges.extend(_build_dependency_edges_for_entry(entry_id, raw, entry_by_id))
    return dependency_edges


def _validate_catalog_dependencies(
    entry_id: str,
    raw: Mapping[str, Any],
    entry_by_id: dict[str, dict[str, Any]],
    all_entry_by_id: dict[str, dict[str, Any]],
) -> None:
    """Reject unknown or deferred required dependency references."""
    for field in ("prerequisites", "requires_grammar", "helpful_prerequisites", "helpful_grammar"):
        for dependency in _normalize_string_list(raw.get(field)):
            if dependency not in all_entry_by_id:
                raise ValueError(f"{entry_id!r} references unknown catalog ID {dependency!r}")
            if dependency not in entry_by_id and field in {"prerequisites", "requires_grammar"}:
                raise ValueError(
                    f"{entry_id!r} requires deferred catalog ID {dependency!r}; "
                    "include that kind in the plan or revise the dependency"
                )


def _build_dependency_edges_for_entry(
    entry_id: str, raw: Mapping[str, Any], entry_by_id: dict[str, dict[str, Any]]
) -> list[DependencyEdge]:
    """Build dependency edges for one included catalog entry."""
    edges: list[DependencyEdge] = []
    required_dependencies = _normalize_string_list(raw.get("prerequisites") or raw.get("requires_grammar"))
    helpful_dependencies = _normalize_string_list(raw.get("helpful_prerequisites") or raw.get("helpful_grammar"))
    for dependency in required_dependencies:
        if dependency in entry_by_id:
            edges.append(
                DependencyEdge(
                    prerequisite_id=dependency,
                    dependent_id=entry_id,
                    kind="required",
                    confidence=1.0,
                    rationale="canonical catalog dependency",
                )
            )
    for dependency in helpful_dependencies:
        if dependency in entry_by_id:
            edges.append(
                DependencyEdge(
                    prerequisite_id=dependency,
                    dependent_id=entry_id,
                    kind="helpful",
                    confidence=1.0,
                    rationale="canonical catalog helpful dependency",
                )
            )
    return edges


def _build_curriculum_plan_slots(
    ordered: list[tuple[str, dict[str, Any]]], registry: TerminologyRegistry | None
) -> list[CurriculumPlanSlot]:
    """Build typed plan slots from topologically ordered entries."""
    return [
        _build_curriculum_plan_slot(index, entry_id, raw, registry) for index, (entry_id, raw) in enumerate(ordered)
    ]


def _build_curriculum_plan_slot(
    sequence_index: int,
    entry_id: str,
    raw: Mapping[str, Any],
    registry: TerminologyRegistry | None,
) -> CurriculumPlanSlot:
    """Validate one entry's terminology and build its plan slot."""
    tags = _normalize_string_list(raw.get("cefr_tags"))
    cefr = _derive_cefr_floor(raw)
    prerequisites = _normalize_string_list(raw.get("prerequisites") or raw.get("requires_grammar"))
    helpful = _normalize_string_list(raw.get("helpful_prerequisites") or raw.get("helpful_grammar"))
    family_id = str(raw.get("family_id", "") or "").strip()
    teaching_points = _build_teaching_points(raw.get("teaching_points"), entry_id=entry_id, raw=raw)
    terminology_ids = _normalize_terminology_ids(raw.get("terminology_ids"), entry_id=entry_id)
    if terminology_ids and registry is None:
        raise ValueError("terminology registry is required for approved terminology IDs")
    if registry is not None:
        terminology_ids = [concept.id for concept in resolve_concept_ids(registry, terminology_ids)]
    _validate_terminology_budget(entry_id=entry_id, target_cefr=cefr, terminology_ids=terminology_ids)
    return CurriculumPlanSlot(
        sequence_index=sequence_index,
        catalog_id=entry_id,
        catalog_kind=cast(CatalogKind, str(raw["catalog_kind"])),
        title=str(raw.get("title", entry_id)),
        learner_outcome=str(raw.get("learner_outcome", "Use the target language skill in context.")),
        cefr_level=cefr,
        cefr_source="catalog" if tags else "planner_default",
        family_id=family_id,
        cefr_tags=tags,
        teaching_points=teaching_points,
        terminology_ids=terminology_ids,
        prerequisites=prerequisites,
        helpful_prerequisites=helpful,
        provisional_flags=[],
        owner_scope=CatalogOwnerScope.model_validate(raw["owner_scope"])
        if raw.get("owner_scope") is not None
        else None,
    )


def _validate_complete_catalog_entries(entries: list[Any]) -> None:
    """Require reviewed dependency fields on every approved catalog entry."""
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError("approved catalog entries must be mappings")
        for field in ("prerequisites", "helpful_prerequisites"):
            if field not in entry or not isinstance(entry[field], list):
                raise ValueError(f"catalog entry {entry.get('id', '<unknown>')!r} requires {field}: []")
        if not _normalize_string_list(entry.get("cefr_tags")):
            raise ValueError(f"catalog entry {entry.get('id', '<unknown>')!r} requires reviewed cefr_tags")
        if entry.get("owner_scope") is not None:
            _require_atomic_owner_scope(entry)


def _require_atomic_owner_scope(entry: Mapping[str, Any]) -> None:
    """Fail closed when an approved owner lacks human-reviewed atomic scope."""
    entry_id = str(entry.get("id", "<unknown>"))
    scope = entry.get("owner_scope")
    if not isinstance(scope, Mapping):
        raise TypeError(
            f"catalog entry {entry_id!r} requires owner_scope with status=atomic and review_status=approved"
        )
    if scope.get("status") != "atomic" or scope.get("review_status") != "approved":
        raise ValueError(f"catalog entry {entry_id!r} is not schedulable: owner_scope must be human-approved atomic")
    if not str(scope.get("learner_decision", "")).strip():
        raise ValueError(f"catalog entry {entry_id!r} owner_scope requires learner_decision")
    if not str(scope.get("assessment_operation", "")).strip():
        raise ValueError(f"catalog entry {entry_id!r} owner_scope requires assessment_operation")
    if not str(scope.get("reviewed_by", "")).strip():
        raise ValueError(f"catalog entry {entry_id!r} owner_scope requires reviewed_by")


def _derive_cefr_floor(raw: Mapping[str, Any]) -> str:
    """Return the earliest declared CEFR tag for one entry."""
    tags = _normalize_string_list(raw.get("cefr_tags"))
    known = [tag for tag in tags if tag in K_CATALOG_PLANNER_CEFR_ORDER]
    if not known:
        raise ValueError(f"catalog entry {raw.get('id', '<unknown>')!r} has no reviewed cefr_tags")
    return min(known, key=K_CATALOG_PLANNER_CEFR_ORDER.index)


def _normalize_terminology_ids(value: object, *, entry_id: str) -> list[str]:
    """Validate and de-duplicate optional approved glossary IDs in source order."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"catalog entry {entry_id!r} terminology_ids must be a list")
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_id in value:
        if not isinstance(raw_id, str) or not raw_id.strip():
            raise ValueError(f"catalog entry {entry_id!r} terminology_ids must contain non-empty strings")
        concept_id = raw_id.strip()
        if concept_id not in seen:
            normalized.append(concept_id)
            seen.add(concept_id)
    return normalized


def _validate_terminology_budget(*, entry_id: str, target_cefr: str, terminology_ids: list[str]) -> None:
    """Reject approved early-CEFR plans that cannot satisfy the lesson budget."""
    if (
        target_cefr in K_CATALOG_PLANNER_EARLY_CEFR_TAGS
        and len(terminology_ids) > K_CATALOG_PLANNER_EARLY_TECHNICAL_LABEL_BUDGET
    ):
        raise ValueError(
            f"catalog entry {entry_id!r} has {len(terminology_ids)} terminology_ids, "
            f"which exceeds the {target_cefr} technical-label budget "
            f"({K_CATALOG_PLANNER_EARLY_TECHNICAL_LABEL_BUDGET})"
        )


def _build_teaching_points(
    raw_teaching_points: object,
    *,
    entry_id: str,
    raw: Mapping[str, Any],
) -> list[CatalogTeachingPoint]:
    """Preserve reviewed teaching points while deriving a legacy fallback."""
    if raw_teaching_points is None:
        learner_outcome = str(raw.get("learner_outcome") or "Use the target language skill in context.").strip()
        return [
            CatalogTeachingPoint(id=f"{entry_id}:learner_outcome", source_owner=entry_id, statement=learner_outcome)
        ]
    if not isinstance(raw_teaching_points, list):
        raise TypeError(f"catalog entry {entry_id!r} teaching_points must be a list")
    points: list[CatalogTeachingPoint] = []
    for value in raw_teaching_points:
        if not isinstance(value, Mapping):
            raise TypeError(f"catalog entry {entry_id!r} has a non-mapping teaching point")
        point_id = str(value.get("id") or "").strip()
        statement = str(value.get("statement") or "").strip()
        if not point_id or not statement:
            raise ValueError(f"catalog entry {entry_id!r} has an incomplete teaching_point (id, statement)")
        source_owner = value.get("source_owner")
        points.append(
            CatalogTeachingPoint(
                id=point_id,
                source_owner=str(source_owner).strip() if isinstance(source_owner, str) else None,
                statement=statement,
            )
        )
    return points


def _normalize_string_list(value: object) -> list[str]:
    """Normalize optional YAML list fields without accepting scalar IDs."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError("catalog dependency fields must be lists")
    return [str(item).strip() for item in value if str(item).strip()]


__all__ = [
    "build_plan_slots",
    "hash_catalog_content",
    "hash_sequence_content",
    "build_sequence_report",
    "normalize_catalog_kinds",
    "require_complete_dependency_graph",
]
