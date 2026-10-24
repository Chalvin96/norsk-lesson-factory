"""Entry points: `preview_curriculum_edits` and `collect_curriculum_dependency_findings`."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Literal

from lesson_builder.domain.curriculum.models import CurriculumAddEdit
from lesson_builder.domain.curriculum.models import CurriculumAffectedLesson
from lesson_builder.domain.curriculum.models import CurriculumDependencyFinding
from lesson_builder.domain.curriculum.models import CurriculumEdit
from lesson_builder.domain.curriculum.models import CurriculumEditPreview
from lesson_builder.domain.curriculum.models import CurriculumManualEntry
from lesson_builder.domain.curriculum.models import CurriculumMergeEdit
from lesson_builder.domain.curriculum.models import CurriculumMovedOutcome
from lesson_builder.domain.curriculum.models import CurriculumMovedPrerequisite
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.domain.curriculum.models import CurriculumRemoveEdit
from lesson_builder.domain.curriculum.models import CurriculumReorderEdit
from lesson_builder.domain.curriculum.models import CurriculumSplitEdit
from lesson_builder.domain.curriculum.models import CurriculumSplitPart
from lesson_builder.domain.curriculum.models import CurriculumUpdateEdit


def preview_curriculum_edits(plan: CurriculumPlan, edits: Iterable[CurriculumEdit]) -> CurriculumEditPreview:
    """Apply staged edits in memory and return the complete, ordered authoring impact."""
    current = plan.model_copy(deep=True)
    affected: dict[str, CurriculumAffectedLesson] = {}
    moved_outcomes: list[CurriculumMovedOutcome] = []
    moved_prerequisites: list[CurriculumMovedPrerequisite] = []
    for edit in edits:
        current, edit_affected, edit_outcomes, edit_prerequisites = _apply_edit(current, edit)
        affected.update({item.lesson_id: item for item in edit_affected})
        moved_outcomes.extend(edit_outcomes)
        moved_prerequisites.extend(edit_prerequisites)
    ordered = _ordered_slots(current)
    preview = CurriculumEditPreview(
        review_digest="",
        entries=ordered,
        affected_lessons=list(affected.values()),
        moved_outcomes=moved_outcomes,
        moved_prerequisites=moved_prerequisites,
        dependency_findings=list(collect_curriculum_dependency_findings(current)),
    )
    candidate = json.dumps(
        preview.model_dump(mode="json", exclude={"review_digest"}), sort_keys=True, ensure_ascii=False
    )
    return preview.model_copy(update={"review_digest": hashlib.sha256(candidate.encode()).hexdigest()})


def collect_curriculum_dependency_findings(plan: CurriculumPlan) -> tuple[CurriculumDependencyFinding, ...]:
    """Locate required prerequisites that are missing or appear at or after their dependent."""
    positions = {slot.catalog_id: slot.sequence_index for slot in plan.slots}
    findings = []
    for slot in _ordered_slots(plan):
        for prerequisite in slot.prerequisites:
            position = positions.get(prerequisite)
            if position is None:
                findings.append(
                    CurriculumDependencyFinding(
                        finding_id=f"dependency:{slot.catalog_id}:curriculum.prerequisite_missing:{prerequisite}",
                        lesson_id=slot.catalog_id,
                        prerequisite_id=prerequisite,
                        code="curriculum.prerequisite_missing",
                        explanation=f"{slot.title} requires missing curriculum entry {prerequisite}. Remove or re-link it.",
                    )
                )
            elif position >= slot.sequence_index:
                findings.append(
                    CurriculumDependencyFinding(
                        finding_id=f"dependency:{slot.catalog_id}:curriculum.prerequisite_order:{prerequisite}",
                        lesson_id=slot.catalog_id,
                        prerequisite_id=prerequisite,
                        code="curriculum.prerequisite_order",
                        explanation=f"{slot.title} precedes its prerequisite {prerequisite}. Move the prerequisite earlier.",
                    )
                )
    return tuple(findings)


def _apply_edit(
    plan: CurriculumPlan, edit: CurriculumEdit
) -> tuple[
    CurriculumPlan,
    tuple[CurriculumAffectedLesson, ...],
    tuple[CurriculumMovedOutcome, ...],
    tuple[CurriculumMovedPrerequisite, ...],
]:
    if isinstance(edit, CurriculumReorderEdit):
        return _reorder(plan, edit)
    if isinstance(edit, CurriculumAddEdit):
        return _add(plan, edit)
    if isinstance(edit, CurriculumRemoveEdit):
        return _remove(plan, edit)
    if isinstance(edit, CurriculumMergeEdit):
        return _merge(plan, edit)
    if isinstance(edit, CurriculumSplitEdit):
        return _split(plan, edit)
    if isinstance(edit, CurriculumUpdateEdit):
        return _update(plan, edit)
    raise TypeError(f"unsupported curriculum edit: {type(edit).__name__}")


def _reorder(
    plan: CurriculumPlan, edit: CurriculumReorderEdit
) -> tuple[
    CurriculumPlan,
    tuple[CurriculumAffectedLesson, ...],
    tuple[CurriculumMovedOutcome, ...],
    tuple[CurriculumMovedPrerequisite, ...],
]:
    ordered = _ordered_slots(plan)
    current_ids = [slot.catalog_id for slot in ordered]
    if (
        len(set(current_ids)) != len(current_ids)
        or len(edit.lesson_ids) != len(current_ids)
        or set(edit.lesson_ids) != set(current_ids)
    ):
        raise ValueError("lesson_ids must be a permutation of current curriculum slots")
    slots = {slot.catalog_id: slot for slot in ordered}
    result = _replace_slots(plan, [slots[identity] for identity in edit.lesson_ids])
    moved = tuple(
        CurriculumAffectedLesson(
            lesson_id=slots[identity].catalog_id,
            title=slots[identity].title,
            effect="order_changed",
            detail=f"Position {current_ids.index(identity) + 1} moves to {edit.lesson_ids.index(identity) + 1}.",
        )
        for identity in edit.lesson_ids
        if current_ids.index(identity) != edit.lesson_ids.index(identity)
    )
    return result, moved, (), ()


def _add(
    plan: CurriculumPlan, edit: CurriculumAddEdit
) -> tuple[
    CurriculumPlan,
    tuple[CurriculumAffectedLesson, ...],
    tuple[CurriculumMovedOutcome, ...],
    tuple[CurriculumMovedPrerequisite, ...],
]:
    ordered = _ordered_slots(plan)
    _require_available_id(ordered, edit.entry.catalog_id)
    position = _insertion_position(ordered, edit.after_lesson_id)
    new_slot = _slot_from_manual_entry(edit.entry)
    result = _replace_slots(plan, [*ordered[:position], new_slot, *ordered[position:]])
    return (
        result,
        (
            CurriculumAffectedLesson(
                lesson_id=new_slot.catalog_id,
                title=new_slot.title,
                effect="new_entry",
                detail="This entry starts without a lesson package.",
            ),
        ),
        (),
        (),
    )


def _remove(
    plan: CurriculumPlan, edit: CurriculumRemoveEdit
) -> tuple[
    CurriculumPlan,
    tuple[CurriculumAffectedLesson, ...],
    tuple[CurriculumMovedOutcome, ...],
    tuple[CurriculumMovedPrerequisite, ...],
]:
    ordered = _ordered_slots(plan)
    selected = _selected_slots(ordered, edit.lesson_ids, minimum=1)
    selected_ids = {slot.catalog_id for slot in selected}
    kept = [slot for slot in ordered if slot.catalog_id not in selected_ids]
    result = _replace_slots(plan, kept)
    affected = tuple(
        CurriculumAffectedLesson(
            lesson_id=slot.catalog_id,
            title=slot.title,
            effect="unlisted",
            detail="The entry leaves the curriculum; its lesson package stays in the workspace.",
        )
        for slot in selected
    )
    return result, affected, (), ()


def _merge(
    plan: CurriculumPlan, edit: CurriculumMergeEdit
) -> tuple[
    CurriculumPlan,
    tuple[CurriculumAffectedLesson, ...],
    tuple[CurriculumMovedOutcome, ...],
    tuple[CurriculumMovedPrerequisite, ...],
]:
    ordered = _ordered_slots(plan)
    selected = _selected_slots(ordered, edit.lesson_ids, minimum=2)
    selected_ids = {slot.catalog_id for slot in selected}
    kept = selected[0]
    required = _unique(
        prerequisite for slot in selected for prerequisite in slot.prerequisites if prerequisite not in selected_ids
    )
    helpful = _unique(
        prerequisite
        for slot in selected
        for prerequisite in slot.helpful_prerequisites
        if prerequisite not in selected_ids and prerequisite not in required
    )
    replaced = _validated_slot(
        kept,
        title=edit.title,
        learner_outcome=edit.learner_outcome,
        prerequisites=required,
        helpful_prerequisites=helpful,
    )
    moved_prerequisites: list[CurriculumMovedPrerequisite] = []
    affected = [
        CurriculumAffectedLesson(
            lesson_id=kept.catalog_id,
            title=edit.title,
            effect="needs_replan",
            detail="The merged entry changed; re-plan its lesson package.",
        )
    ]
    result_slots = []
    for slot in ordered:
        if slot.catalog_id == kept.catalog_id:
            result_slots.append(replaced)
        elif slot.catalog_id in selected_ids:
            affected.append(
                CurriculumAffectedLesson(
                    lesson_id=slot.catalog_id,
                    title=slot.title,
                    effect="unlisted",
                    detail="This entry is merged; its lesson package remains in the workspace.",
                )
            )
        else:
            result_slots.append(
                _relink_slot(
                    slot,
                    selected_ids - {kept.catalog_id},
                    kept.catalog_id,
                    moved_prerequisites,
                    affected,
                )
            )
    outcomes = tuple(
        CurriculumMovedOutcome(
            from_lesson_id=slot.catalog_id, to_lesson_id=kept.catalog_id, outcome=slot.learner_outcome
        )
        for slot in selected
        if slot.catalog_id != kept.catalog_id
    )
    return _replace_slots(plan, result_slots), tuple(affected), outcomes, tuple(moved_prerequisites)


def _split(
    plan: CurriculumPlan, edit: CurriculumSplitEdit
) -> tuple[
    CurriculumPlan,
    tuple[CurriculumAffectedLesson, ...],
    tuple[CurriculumMovedOutcome, ...],
    tuple[CurriculumMovedPrerequisite, ...],
]:
    ordered = _ordered_slots(plan)
    original = _require_slot(ordered, edit.lesson_id)
    _validate_split_parts(ordered, original, edit.parts)
    parts = _build_split_parts(original, edit.parts)
    previous_id = parts[-1].catalog_id

    moved_prerequisites: list[CurriculumMovedPrerequisite] = []
    affected = [
        CurriculumAffectedLesson(
            lesson_id=original.catalog_id,
            title=parts[0].title,
            effect="needs_replan",
            detail="The existing lesson becomes part one and needs re-planning.",
        )
    ]
    split_slots: list[CurriculumPlanSlot] = []
    for slot in ordered:
        if slot.catalog_id == original.catalog_id:
            split_slots.extend(parts)
        else:
            split_slots.append(_relink_slot(slot, {original.catalog_id}, previous_id, moved_prerequisites, affected))
    outcomes = tuple(
        CurriculumMovedOutcome(
            from_lesson_id=original.catalog_id, to_lesson_id=part.catalog_id, outcome=part.learner_outcome
        )
        for part in parts[1:]
        if part.learner_outcome == original.learner_outcome
    )
    affected.extend(
        CurriculumAffectedLesson(
            lesson_id=part.catalog_id,
            title=part.title,
            effect="new_entry",
            detail="This split part starts without a lesson package.",
        )
        for part in parts[1:]
    )
    return _replace_slots(plan, split_slots), tuple(affected), outcomes, tuple(moved_prerequisites)


def _validate_split_parts(
    ordered: list[CurriculumPlanSlot], original: CurriculumPlanSlot, parts: list[CurriculumSplitPart]
) -> None:
    part_ids = [part.catalog_id for part in parts]
    if part_ids[0] != original.catalog_id:
        raise ValueError("the first split part must keep the current entry ID")
    if len(set(part_ids)) != len(part_ids):
        raise ValueError("split part IDs must be unique")
    existing_ids = {slot.catalog_id for slot in ordered}
    if any(identity in existing_ids for identity in part_ids[1:]):
        raise ValueError("split part IDs must not already exist")


def _build_split_parts(original: CurriculumPlanSlot, edit_parts: list[CurriculumSplitPart]) -> list[CurriculumPlanSlot]:
    parts = []
    previous_id = original.catalog_id
    for index, part in enumerate(edit_parts):
        if index == 0:
            slot = _validated_slot(
                original,
                title=part.title,
                learner_outcome=part.learner_outcome,
                prerequisites=original.prerequisites,
                helpful_prerequisites=original.helpful_prerequisites,
            )
        else:
            slot = _validated_slot(
                original,
                catalog_id=part.catalog_id,
                sequence_index=original.sequence_index + index,
                title=part.title,
                learner_outcome=part.learner_outcome,
                prerequisites=[previous_id],
                helpful_prerequisites=[],
            )
        parts.append(slot)
        previous_id = part.catalog_id
    return parts


def _update(
    plan: CurriculumPlan, edit: CurriculumUpdateEdit
) -> tuple[
    CurriculumPlan,
    tuple[CurriculumAffectedLesson, ...],
    tuple[CurriculumMovedOutcome, ...],
    tuple[CurriculumMovedPrerequisite, ...],
]:
    ordered = _ordered_slots(plan)
    original = _require_slot(ordered, edit.lesson_id)
    updated = _validated_slot(
        original,
        title=edit.title,
        learner_outcome=edit.learner_outcome,
        cefr_level=edit.cefr_level,
        prerequisites=_unique(edit.prerequisites),
        helpful_prerequisites=_unique(edit.helpful_prerequisites),
    )
    result = _replace_slots(
        plan,
        [updated if slot.catalog_id == updated.catalog_id else slot for slot in ordered],
    )
    changed = updated != original
    affected = (
        (
            CurriculumAffectedLesson(
                lesson_id=updated.catalog_id,
                title=updated.title,
                effect="needs_replan",
                detail="The curriculum entry changed; re-plan its lesson package.",
            ),
        )
        if changed
        else ()
    )
    return result, affected, (), ()


def _relink_slot(
    slot: CurriculumPlanSlot,
    old_ids: set[str],
    new_id: str,
    moved: list[CurriculumMovedPrerequisite],
    affected: list[CurriculumAffectedLesson],
) -> CurriculumPlanSlot:
    required = _replace_dependencies(slot, slot.prerequisites, old_ids, new_id, "required", moved)
    helpful = _replace_dependencies(slot, slot.helpful_prerequisites, old_ids, new_id, "helpful", moved)
    if required == slot.prerequisites and helpful == slot.helpful_prerequisites:
        return slot
    affected.append(
        CurriculumAffectedLesson(
            lesson_id=slot.catalog_id,
            title=slot.title,
            effect="needs_replan",
            detail="A prerequisite now points to a changed curriculum entry; re-plan its lesson package.",
        )
    )
    return _validated_slot(slot, prerequisites=required, helpful_prerequisites=helpful)


def _replace_dependencies(
    slot: CurriculumPlanSlot,
    values: list[str],
    old_ids: set[str],
    new_id: str,
    kind: Literal["required", "helpful"],
    moved: list[CurriculumMovedPrerequisite],
) -> list[str]:
    result = []
    for identity in values:
        replacement = new_id if identity in old_ids else identity
        if replacement != identity:
            moved.append(
                CurriculumMovedPrerequisite(
                    lesson_id=slot.catalog_id,
                    from_lesson_id=identity,
                    to_lesson_id=replacement,
                    kind=kind,
                )
            )
        if replacement not in result:
            result.append(replacement)
    return result


def _slot_from_manual_entry(entry: CurriculumManualEntry) -> CurriculumPlanSlot:
    return CurriculumPlanSlot(
        sequence_index=0,
        catalog_id=entry.catalog_id,
        catalog_kind="communicative",
        title=entry.title,
        learner_outcome=entry.learner_outcome,
        cefr_level=entry.cefr_level,
        cefr_source="planner_default",
        prerequisites=_unique(entry.prerequisites),
        helpful_prerequisites=_unique(entry.helpful_prerequisites),
    )


def _insertion_position(ordered: list[CurriculumPlanSlot], after_lesson_id: str | None) -> int:
    if after_lesson_id is None:
        return 0
    for index, slot in enumerate(ordered):
        if slot.catalog_id == after_lesson_id:
            return index + 1
    raise ValueError("after_lesson_id is not a current curriculum entry")


def _selected_slots(
    ordered: list[CurriculumPlanSlot], lesson_ids: list[str], *, minimum: int
) -> list[CurriculumPlanSlot]:
    if len(lesson_ids) < minimum or len(set(lesson_ids)) != len(lesson_ids):
        raise ValueError(f"select at least {minimum} distinct curriculum entries")
    selected = [slot for slot in ordered if slot.catalog_id in set(lesson_ids)]
    if len(selected) != len(lesson_ids):
        raise ValueError("curriculum edit refers to an unknown entry")
    return selected


def _require_available_id(ordered: list[CurriculumPlanSlot], identity: str) -> None:
    if identity in {slot.catalog_id for slot in ordered}:
        raise ValueError(f"curriculum entry ID already exists: {identity}")


def _require_slot(ordered: list[CurriculumPlanSlot], identity: str) -> CurriculumPlanSlot:
    slot = next((item for item in ordered if item.catalog_id == identity), None)
    if slot is None:
        raise ValueError(f"curriculum entry not found: {identity}")
    return slot


def _validated_slot(slot: CurriculumPlanSlot, **updates: object) -> CurriculumPlanSlot:
    return CurriculumPlanSlot.model_validate(slot.model_dump() | updates)


def _replace_slots(plan: CurriculumPlan, ordered: list[CurriculumPlanSlot]) -> CurriculumPlan:
    first_position = min((slot.sequence_index for slot in plan.slots), default=0)
    slots = [_validated_slot(slot, sequence_index=first_position + index) for index, slot in enumerate(ordered)]
    summary = dict(plan.summary)
    if "approved_entries" in summary:
        summary["approved_entries"] = len(slots)
    return CurriculumPlan.model_validate(plan.model_dump() | {"slots": slots, "summary": summary})


def _ordered_slots(plan: CurriculumPlan) -> list[CurriculumPlanSlot]:
    return sorted(plan.slots, key=lambda slot: slot.sequence_index)


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
