"""Entry points: `preview_workbench_curriculum_edits`, `save_workbench_curriculum_edits`, `check_curriculum`, and `read_curriculum`."""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from lesson_builder.application.operations.materialize_curriculum import validate_committed_curriculum_plan
from lesson_builder.application.operations.workbench_records import CurriculumBaseline
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_CURRICULUM_CACHE_SIZE
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_UNORDERED_PLAN_KEYS
from lesson_builder.domain.curriculum.models import CurriculumAddEdit
from lesson_builder.domain.curriculum.models import CurriculumEdit
from lesson_builder.domain.curriculum.models import CurriculumEditPreview
from lesson_builder.domain.curriculum.models import CurriculumFindingDecision
from lesson_builder.domain.curriculum.models import CurriculumMergeEdit
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.domain.curriculum.models import CurriculumRemoveEdit
from lesson_builder.domain.curriculum.models import CurriculumReorderEdit
from lesson_builder.domain.curriculum.models import CurriculumSplitEdit
from lesson_builder.domain.curriculum.models import CurriculumUpdateEdit
from lesson_builder.domain.curriculum.services.curriculum_edits import collect_curriculum_dependency_findings
from lesson_builder.domain.curriculum.services.curriculum_edits import preview_curriculum_edits as preview_edits
from lesson_builder.domain.lesson.validation.lesson_ids import validate_slug
from lesson_builder.formats.workbench_curriculum import rewrite_yaml_scalars
from lesson_builder.formats.workbench_curriculum import rewrite_yaml_sequence_items
from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_git import commit_draft
from lesson_builder.workspace.workbench_git import require_workbench_commit
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from lesson_builder.workspace.workbench_transaction import contained_workbench_path
from lesson_builder.workspace.workbench_transaction import workbench_transaction
from lesson_builder.workspace.workbench_transaction import workbench_tree_files


@dataclass(frozen=True)
class CurriculumFinding:
    """A plan finding, located on a slot or on the plan as a whole."""

    lesson_id: str | None
    code: str
    explanation: str
    artifact: str = "content/curriculum/plan.yaml"


def preview_workbench_curriculum_edits(root: Path, edits: Iterable[CurriculumEdit]) -> CurriculumEditPreview:
    """Return a pure preview of staged edits against the current curriculum plan."""
    edit_list = list(edits)
    _validate_edit_ids(edit_list)
    plan_path = _plan_path(root)
    source = plan_path.read_bytes()
    preview = preview_edits(read_curriculum(root), edit_list)
    _draft_targets, _commit_paths, deleted_ids = _draft_deletion_targets(
        root, edit_list, {slot.catalog_id for slot in preview.entries}
    )
    affected_lessons = [
        item.model_copy(
            update={"detail": "The entry leaves the curriculum; its never-promoted draft package will be deleted."}
        )
        if item.lesson_id in deleted_ids and item.effect == "unlisted"
        else item
        for item in preview.affected_lessons
    ]
    preview = preview.model_copy(update={"affected_lessons": affected_lessons})
    digest_source = json.dumps(
        {
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "preview": preview.model_dump(mode="json", exclude={"review_digest"}),
            "deleted_draft_ids": sorted(deleted_ids),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(digest_source.encode()).hexdigest()
    return preview.model_copy(update={"review_digest": digest})


def save_workbench_curriculum_edits(
    root: Path,
    edits: Iterable[CurriculumEdit],
    *,
    review_digest: str | None = None,
    finding_decisions: Iterable[CurriculumFindingDecision] = (),
    force: bool = False,
) -> tuple[str, tuple[CurriculumFinding, ...]]:
    """Review or force-save staged edits with any eligible draft removals."""
    edit_list = list(edits)
    if not edit_list:
        raise ValueError("at least one curriculum edit is required")
    _validate_edit_ids(edit_list)
    original = read_curriculum(root)
    preview = preview_edits(original, edit_list)
    plan_path = _plan_path(root)
    approval = (
        "pending_human"
        if force
        else _require_reviewed_curriculum(root, edit_list, preview, review_digest, finding_decisions)
    )

    baseline = _capture_baseline(root, original)
    before = plan_path.read_text(encoding="utf-8")
    after = _rewrite_plan_source(before, original, preview.entries, approval)
    draft_targets, draft_commit_paths, _deleted_ids = _draft_deletion_targets(
        root, edit_list, {slot.catalog_id for slot in preview.entries}
    )
    targets = (plan_path, *draft_targets)
    commit_paths = (plan_path.relative_to(root).as_posix(), *draft_commit_paths)
    head = require_workbench_commit(root, commit_paths)
    if after == before and not draft_targets:
        write_workbench_record(root, "curriculum", "baseline", baseline)
        return head, check_curriculum(root)
    with workbench_transaction(root, targets):
        write_text_atomically(plan_path, after)
        for draft in draft_targets:
            if draft.exists():
                shutil.rmtree(draft)
        commit = commit_draft(
            root,
            "curriculum",
            commit_paths,
            message="workbench: edit curriculum",
            expected_head=head,
        )
    write_workbench_record(root, "curriculum", "baseline", baseline)
    return commit, check_curriculum(root)


def check_curriculum(root: Path, *, plan: CurriculumPlan | None = None) -> tuple[CurriculumFinding, ...]:
    """Run the same curriculum check as lesson-data check and locate prerequisite edges."""
    plan = plan if plan is not None else read_curriculum(root)
    findings = []
    try:
        validate_committed_curriculum_plan(repo_root=root, plan=plan)
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        findings.append(CurriculumFinding(None, "curriculum.plan_invalid", str(exc)))
    findings.extend(
        CurriculumFinding(finding.lesson_id, finding.code, finding.explanation)
        for finding in collect_curriculum_dependency_findings(plan)
    )
    return tuple(findings)


def read_curriculum(root: Path) -> CurriculumPlan:
    """Read current authored slots without requiring a matching catalog hash."""
    path = _plan_path(root)
    metadata = path.stat()
    return _cached_curriculum(path, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size).model_copy(deep=True)


def find_stale_curriculum_entries(root: Path, plan: CurriculumPlan) -> set[str]:
    """Identify slots changed since the first local Save without rewriting any lesson."""
    baseline = read_workbench_record(root, "curriculum", "baseline", CurriculumBaseline)
    return {
        slot.catalog_id
        for slot in plan.slots
        if slot.catalog_id in baseline.slots
        and normalize_curriculum_slot(
            CurriculumPlanSlot.model_validate_json(baseline.slots[slot.catalog_id]), include_position=False
        )
        != normalize_curriculum_slot(slot, include_position=False)
    }


def normalize_curriculum_slot(slot: CurriculumPlanSlot, *, include_position: bool = True) -> dict[str, object]:
    """Normalize slot values, optionally omitting position for approval comparisons."""
    values = slot.model_dump(mode="json")
    if not include_position:
        values.pop("sequence_index")
    for key in K_APPLICATION_WORKBENCH_UNORDERED_PLAN_KEYS:
        value = values.get(key)
        if isinstance(value, list):
            values[key] = sorted(value)
    return values


def _require_reviewed_curriculum(
    root: Path,
    edits: list[CurriculumEdit],
    preview: CurriculumEditPreview,
    review_digest: str | None,
    finding_decisions: Iterable[CurriculumFindingDecision],
) -> str:
    current_review = preview_workbench_curriculum_edits(root, edits)
    if review_digest != current_review.review_digest:
        raise ValueError("curriculum changed since review; review the current content before saving")
    decisions = list(finding_decisions)
    decision_map = {item.finding_id: item.decision for item in decisions}
    if len(decision_map) != len(decisions):
        raise ValueError("curriculum review contains duplicate finding decisions")
    expected_ids = {item.finding_id for item in preview.dependency_findings}
    if decision_map.keys() != expected_ids:
        raise ValueError("review every current curriculum finding before saving")
    missing = [item for item in preview.dependency_findings if item.code == "curriculum.prerequisite_missing"]
    if missing:
        raise ValueError(
            "fix missing prerequisites before reviewed save: " + "; ".join(item.explanation for item in missing)
        )
    if any(value != "accept" for value in decision_map.values()):
        raise ValueError("revise curriculum findings before saving")
    return "human_approved"


def _capture_baseline(root: Path, plan: CurriculumPlan) -> CurriculumBaseline:
    baseline = read_workbench_record(root, "curriculum", "baseline", CurriculumBaseline)
    for slot in plan.slots:
        baseline.slots.setdefault(slot.catalog_id, slot.model_dump_json())
    return baseline


def _rewrite_plan_source(
    text: str, original: CurriculumPlan, final_entries: list[CurriculumPlanSlot], approval: str
) -> str:
    updates: dict[tuple[str | int, ...], str | int] = {}
    if "approved_entries" in original.summary and original.summary["approved_entries"] != len(final_entries):
        updates[("summary", "approved_entries")] = len(final_entries)
    if original.curriculum_approval != approval:
        updates[("curriculum_approval",)] = approval
    if updates:
        text = rewrite_yaml_scalars(text, updates)
    result = rewrite_yaml_sequence_items(
        text,
        "slots",
        [slot.model_dump(mode="json") for slot in final_entries],
        "catalog_id",
    )
    CurriculumPlan.model_validate(yaml.safe_load(result))
    return result


def _draft_deletion_targets(
    root: Path, edits: list[CurriculumEdit], retained_ids: set[str]
) -> tuple[tuple[Path, ...], tuple[str, ...], frozenset[str]]:
    paths = WorkspacePaths(root)
    targets: list[Path] = []
    commit_paths: list[str] = []
    deleted_ids: set[str] = set()
    for edit in edits:
        if not isinstance(edit, CurriculumRemoveEdit) or not edit.delete_never_promoted_drafts:
            continue
        for lesson_id in edit.lesson_ids:
            if lesson_id in retained_ids:
                continue
            draft, files = _draft_deletion_target(root, paths, lesson_id)
            if draft is not None:
                targets.append(draft)
                commit_paths.extend(files)
                deleted_ids.add(lesson_id)
    return tuple(targets), tuple(dict.fromkeys(commit_paths)), frozenset(deleted_ids)


def _draft_deletion_target(root: Path, paths: WorkspacePaths, lesson_id: str) -> tuple[Path | None, tuple[str, ...]]:
    canonical = contained_workbench_path(root, paths.lessons_root / lesson_id)
    draft = contained_workbench_path(root, paths.drafts_root / lesson_id)
    if canonical.exists():
        raise ValueError(f"cannot delete {lesson_id}: a canonical lesson package exists")
    if not draft.exists():
        return None, ()
    if not draft.is_dir():
        raise ValueError(f"cannot delete {lesson_id}: draft package is not a directory")
    return draft, workbench_tree_files(root, draft)


def _validate_edit_ids(edits: list[CurriculumEdit]) -> None:
    for edit in edits:
        for identity in _edit_ids(edit):
            validate_slug(identity)


def _edit_ids(edit: CurriculumEdit) -> tuple[str, ...]:
    if isinstance(edit, CurriculumReorderEdit | CurriculumRemoveEdit | CurriculumMergeEdit):
        return tuple(edit.lesson_ids)
    if isinstance(edit, CurriculumAddEdit):
        return (
            (edit.entry.catalog_id, edit.after_lesson_id, *edit.entry.prerequisites, *edit.entry.helpful_prerequisites)
            if edit.after_lesson_id
            else (edit.entry.catalog_id, *edit.entry.prerequisites, *edit.entry.helpful_prerequisites)
        )
    if isinstance(edit, CurriculumSplitEdit):
        return (edit.lesson_id, *(part.catalog_id for part in edit.parts))
    if isinstance(edit, CurriculumUpdateEdit):
        return (edit.lesson_id, *edit.prerequisites, *edit.helpful_prerequisites)
    return ()


def _plan_path(root: Path) -> Path:
    path = WorkspacePaths(root).curriculum_plan
    if any(parent.is_symlink() for parent in (path, *path.parents)) or not path.resolve().is_relative_to(
        root.resolve()
    ):
        raise ValueError("curriculum path escapes workspace")
    return path


@lru_cache(maxsize=K_APPLICATION_WORKBENCH_CURRICULUM_CACHE_SIZE)
def _cached_curriculum(path: Path, modified: int, changed: int, size: int) -> CurriculumPlan:
    return CurriculumPlan.model_validate(yaml.load(path.read_bytes(), Loader=yaml.CSafeLoader))


__all__ = [
    # Findings and preview results.
    "CurriculumFinding",
    "preview_workbench_curriculum_edits",
    # Curriculums and save operations.
    "check_curriculum",
    "find_stale_curriculum_entries",
    "normalize_curriculum_slot",
    "read_curriculum",
    "save_workbench_curriculum_edits",
]
