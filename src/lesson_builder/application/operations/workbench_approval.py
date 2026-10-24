"""Entry point: `approve_workbench_lesson` writes owner approvals from current authoring and review evidence."""

from __future__ import annotations

import difflib
import hashlib
import json
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Literal
from typing import TypedDict

import yaml

from lesson_builder.application.operations.workbench_approval_readers import approval_records
from lesson_builder.application.operations.workbench_approval_readers import approved_snapshot
from lesson_builder.application.operations.workbench_approval_readers import legacy_baseline_files
from lesson_builder.application.operations.workbench_approval_readers import read_approval_yaml
from lesson_builder.application.operations.workbench_approval_readers import read_baselines
from lesson_builder.application.operations.workbench_curriculum import normalize_curriculum_slot
from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_lesson_gate import LessonGateDecision
from lesson_builder.application.operations.workbench_lesson_gate import LessonGateSource
from lesson_builder.application.operations.workbench_lesson_gate import LessonGateStage
from lesson_builder.application.operations.workbench_lesson_gate import approval
from lesson_builder.application.operations.workbench_lesson_gate import stage_source_keys
from lesson_builder.application.operations.workbench_lessons import build_workbench_preview
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_proposals import check_preview
from lesson_builder.application.operations.workbench_proposals import read_open_proposal
from lesson_builder.application.operations.workbench_records import CurriculumBaseline
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_records import ReviewLog
from lesson_builder.application.operations.workbench_records import StageApprovalRecord
from lesson_builder.application.operations.workbench_records import StageApprovalStatus
from lesson_builder.application.operations.workbench_records import StageReviewEvidence
from lesson_builder.application.operations.workbench_records import WorkbenchBaselines
from lesson_builder.application.operations.workbench_review import ReviewLedger
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_ACCEPTANCE_STATEMENT
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_APPROVAL_CACHE_SIZE
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_LEGACY_SLOT_INDEX_LIMIT
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_STAGE_DEPENDENCIES
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_STAGES
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_UNORDERED_PLAN_KEYS
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter
from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_git import commit_draft
from lesson_builder.workspace.workbench_git import require_workbench_commit
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from lesson_builder.workspace.workbench_transaction import contained_workbench_path
from lesson_builder.workspace.workbench_transaction import workbench_transaction
from lesson_builder.workspace.workbench_transaction import workbench_tree_files


class WorkbenchApprovalResult(TypedDict):
    """Commit identity and approved lesson stages returned by approval."""

    commit: str
    stages: list[str]


def derive_stage_statuses(
    root: Path,
    lesson_id: str,
    source: WorkbenchSource,
    *,
    plan: CurriculumPlan | None = None,
) -> tuple[StageApprovalStatus, ...]:
    """Derive file and named upstream approval status without reading review or proposal state."""
    paths = WorkspacePaths(root)
    baseline = _baseline_files(root, lesson_id)
    slot, slot_hash = _curriculum_slot_state(root, paths, lesson_id, plan)
    filenames = dict(K_APPLICATION_WORKBENCH_STAGES)
    dependencies = dict(K_APPLICATION_WORKBENCH_STAGE_DEPENDENCIES)
    current_hashes = {
        _stage_name(name): hashlib.sha256(source.files[filename].encode()).hexdigest()
        for name, filename in K_APPLICATION_WORKBENCH_STAGES
    }
    context = _StageStatusContext(
        root=root,
        lesson_id=lesson_id,
        source=source,
        paths=paths,
        baseline=baseline,
        filenames=filenames,
        dependencies=dependencies,
        current_hashes=current_hashes,
        slot_hash=slot_hash,
        slot=slot,
        stages_by_name={},
    )
    return tuple(_derive_stage_status(context, _stage_name(name)) for name, _ in K_APPLICATION_WORKBENCH_STAGES)


def find_approved_snapshot(root: Path, lesson_id: str, stage: Literal["lesson", "exercises"]) -> _ApprovalSnapshot:
    """Read a verified human snapshot or the legacy baseline when no human record exists."""
    snapshot = approved_snapshot(root, lesson_id, stage)
    return _ApprovalSnapshot(snapshot.content, snapshot.unavailable)


def curriculum_slot_digest(root: Path, lesson_id: str, *, plan: CurriculumPlan | None = None) -> str | None:
    """Pin all authored slot values except position; tolerate a missing plan."""
    if not WorkspacePaths(root).curriculum_plan.is_file():
        return None
    plan = plan if plan is not None else read_curriculum(root)
    slot = next((slot for slot in plan.slots if slot.catalog_id == lesson_id), None)
    if slot is None:
        return None
    return _digest_slot_values(normalize_curriculum_slot(slot, include_position=False))


def approve_workbench_lesson(
    root: Path, lesson_id: str, stages: Sequence[LessonGateStage] | None = None
) -> WorkbenchApprovalResult:
    """Commit the lesson package with source-bound approvals, snapshots and evidence.

    ``stages`` selects a subset of stages that currently need approval; each requested
    stage must clear its own gate blockers. ``None`` keeps the whole-stage behaviour
    that requires every stage to be ready at once.
    """
    source = read_workbench_source(root, lesson_id)
    reviews = read_workbench_record(root, "reviews", lesson_id, ReviewLog)
    ledger = ReviewLedger(source, reviews)
    view = build_workbench_preview(resolve_lesson_package(root, lesson_id))
    approvals = derive_stage_statuses(root, lesson_id, source)
    gate = approval(
        LessonGateSource(
            workbench_source=source,
            stages=approvals,
            review_ledger=ledger,
            proposal=read_open_proposal(root, lesson_id, source),
            blocking_errors=check_preview(view).status == "failed",
        )
    )
    all_stages = gate.stages
    needed = _require_approval_stages(stages, gate, source.exists)
    paths = WorkspacePaths(root)
    target = paths.workbench_approvals_root / lesson_id
    review_stages = [item.stage for item in needed if item.stage != "plan"]
    evidence = _review_evidence(root, lesson_id, review_stages, ledger, reviews) if review_stages else None
    evidence_path = (
        paths.approval_evidence_root / f"{next(iter(evidence[0].values())).evidence_sha256}.json" if evidence else None
    )
    package = resolve_lesson_package(root, lesson_id)
    records, snapshots, commit_paths = _approval_commit_paths(root, package.path, target, needed, evidence_path)
    head = require_workbench_commit(root, commit_paths)

    log = read_workbench_record(root, "proposals", lesson_id, ProposalLog)
    approval_history: Mapping[str, list[StageApprovalRecord]] = {
        item.stage: _approval_history(path) for item, path in zip(needed, records, strict=True)
    }
    with workbench_transaction(root, (package.path, target, *((evidence_path,) if evidence_path else ()))):
        _write_approval_files(
            root,
            lesson_id,
            source,
            all_stages,
            needed,
            records,
            snapshots,
            evidence,
            evidence_path,
            log,
            approval_history,
            head,
        )
        commit = commit_draft(
            root,
            lesson_id,
            commit_paths,
            hooks=True,
            expected_head=head,
            message=f"workbench: approve {lesson_id} ({', '.join(item.stage for item in needed)})",
        )
    return {"commit": commit, "stages": [item.stage for item in needed]}


@dataclass(frozen=True)
class _ApprovalSnapshot:
    """Verified approved source bytes, or an unavailable flag for a broken human snapshot."""

    content: bytes | None
    unavailable: bool = False


@dataclass
class _StageStatusContext:
    """Captured inputs and memoized named stages for one approval projection."""

    root: Path
    lesson_id: str
    source: WorkbenchSource
    paths: WorkspacePaths
    baseline: dict[str, str]
    filenames: dict[str, str]
    dependencies: dict[str, str | None]
    current_hashes: dict[Literal["plan", "lesson", "exercises"], str]
    slot_hash: str | None
    slot: CurriculumPlanSlot | None
    stages_by_name: dict[Literal["plan", "lesson", "exercises"], StageApprovalStatus]


def _derive_stage_status(
    context: _StageStatusContext, stage: Literal["plan", "lesson", "exercises"]
) -> StageApprovalStatus:
    """Derive one stage after resolving its named upstream dependency."""
    existing = context.stages_by_name.get(stage)
    if existing is not None:
        return existing
    dependency_name = context.dependencies[stage]
    previous = _derive_stage_status(context, _stage_name(dependency_name)) if dependency_name is not None else None
    upstream_hash = context.current_hashes[previous.stage] if previous is not None else None
    current = context.current_hashes[stage]
    record = _latest_record(
        context.root,
        context.lesson_id,
        stage,
        current if not context.source.exists else None,
        upstream=upstream_hash,
    )
    approved = record.file_sha256 if record else _baseline_digest(context.baseline, context.filenames[stage])
    reviewer: Literal["human", "baseline"] | None = "human" if record else "baseline" if approved else None
    status = _stage_status(
        context.root,
        context.lesson_id,
        stage,
        current,
        approved,
        record,
        context.baseline,
        previous,
        upstream_hash,
        context.slot_hash,
        context.slot,
    )
    upstream_diff = _upstream_diff(context.paths, context.lesson_id, context.source, stage) if status == "stale" else ""
    result = StageApprovalStatus(
        stage=stage,
        status=status,
        file_sha256=current,
        approved_sha256=approved,
        reviewer=reviewer,
        upstream_diff=upstream_diff,
    )
    context.stages_by_name[stage] = result
    return result


def _approval_commit_paths(
    root: Path,
    package_path: Path,
    target: Path,
    needed: list[StageApprovalStatus],
    evidence_path: Path | None,
) -> tuple[list[Path], dict[str, Path], tuple[str, ...]]:
    """Build explicit package, record, snapshot and evidence commit paths."""
    source_paths = workbench_tree_files(root, package_path)
    records = [target / item.stage / f"{item.file_sha256}.approval.yaml" for item in needed]
    snapshots: dict[str, Path] = {
        item.stage: target / item.stage / f"{item.file_sha256}.{'md' if item.stage == 'lesson' else 'yaml'}"
        for item in needed
        if item.stage != "plan"
    }
    approval_paths = (
        *(str(item.relative_to(root)) for item in records),
        *(str(item.relative_to(root)) for item in snapshots.values()),
        *((str(evidence_path.relative_to(root)),) if evidence_path else ()),
    )
    return records, snapshots, (*source_paths, *approval_paths)


def _write_approval_files(
    root: Path,
    lesson_id: str,
    source: WorkbenchSource,
    stages: tuple[StageApprovalStatus, ...],
    needed: list[StageApprovalStatus],
    records: list[Path],
    snapshots: dict[str, Path],
    evidence: tuple[dict[Literal["lesson", "exercises"], StageReviewEvidence], str] | None,
    evidence_path: Path | None,
    log: ProposalLog,
    approval_history: Mapping[str, list[StageApprovalRecord]],
    head: str,
) -> None:
    """Write stage records, approved source snapshots and review evidence."""
    for item, path in zip(needed, records, strict=True):
        _write_stage_approval(
            root,
            lesson_id,
            source,
            stages,
            item,
            path,
            snapshots.get(item.stage),
            evidence,
            log,
            approval_history[item.stage],
            head,
        )
    if evidence and evidence_path:
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(evidence_path, evidence[1])


def _write_stage_approval(
    root: Path,
    lesson_id: str,
    source: WorkbenchSource,
    stages: tuple[StageApprovalStatus, ...],
    item: StageApprovalStatus,
    record_path: Path,
    snapshot_path: Path | None,
    evidence: tuple[dict[Literal["lesson", "exercises"], StageReviewEvidence], str] | None,
    log: ProposalLog,
    history: list[StageApprovalRecord],
    head: str,
) -> None:
    """Write one stage's record and, for lesson content, its exact source snapshot."""
    index = next(index for index, stage in enumerate(stages) if stage.stage == item.stage)
    if snapshot_path:
        filename = "lesson.md" if item.stage == "lesson" else "exercises.yaml"
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomically(snapshot_path, source.files[filename])
    approved_at = datetime.now(UTC).isoformat()
    record = StageApprovalRecord(
        lesson_id=lesson_id,
        stage=item.stage,
        file_sha256=item.file_sha256,
        upstream_approved_sha256=stages[index - 1].file_sha256 if index else None,
        curriculum_slot_sha256=curriculum_slot_digest(root, lesson_id) if index == 0 else None,
        approved_at=approved_at,
        code_commit=head,
        review=evidence[0].get(item.stage) if evidence and item.stage != "plan" else None,
        proposals_accepted=_proposal_count(root, lesson_id, item.stage, log, "accepted"),
        proposals_rejected=_proposal_count(root, lesson_id, item.stage, log, "rejected"),
        exercise_alignments=ExerciseAlignment(root, lesson_id).pin_all_approved(source, at=approved_at)
        if item.stage == "exercises"
        else {},
        acceptance_statement=K_APPLICATION_WORKBENCH_ACCEPTANCE_STATEMENT,
        prior_approvals=history,
    )
    record_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomically(
        record_path, yaml.safe_dump(record.model_dump(mode="json"), allow_unicode=True, sort_keys=False)
    )


def _curriculum_slot_state(
    root: Path, paths: WorkspacePaths, lesson_id: str, plan: CurriculumPlan | None
) -> tuple[CurriculumPlanSlot | None, str | None]:
    if plan is None and paths.curriculum_plan.is_file():
        plan = read_curriculum(root)
    if plan is None:
        return None, None
    slot = next((item for item in plan.slots if item.catalog_id == lesson_id), None)
    return slot, curriculum_slot_digest(root, lesson_id, plan=plan)


def _digest_slot_values(values: dict[str, object]) -> str:
    text = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def _is_slot_digest_current(recorded: str | None, current: str | None, slot: CurriculumPlanSlot | None) -> bool:
    if recorded == current:
        return True
    if recorded is None or slot is None:
        return False
    content = json.dumps(
        normalize_curriculum_slot(slot, include_position=False),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _is_legacy_slot_digest_current(recorded, content)


@lru_cache(maxsize=K_APPLICATION_WORKBENCH_APPROVAL_CACHE_SIZE)
def _is_legacy_slot_digest_current(recorded: str, content: str) -> bool:
    values = json.loads(content)
    values["sequence_index"] = 0
    canonical = json.dumps(values, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    prefix, marker, suffix = canonical.partition(b'"sequence_index":0')
    prefix += marker.removesuffix(b"0")
    for index in range(K_APPLICATION_WORKBENCH_LEGACY_SLOT_INDEX_LIMIT):
        if hashlib.sha256(prefix + str(index).encode() + suffix).hexdigest() == recorded:
            return True
    return False


def _stage_status(
    root: Path,
    lesson_id: str,
    stage: Literal["plan", "lesson", "exercises"],
    current: str,
    approved: str | None,
    record: StageApprovalRecord | None,
    baseline: dict[str, str],
    previous: StageApprovalStatus | None,
    upstream: str | None,
    slot_hash: str | None,
    slot: CurriculumPlanSlot | None,
) -> Literal["needs_approval", "stale", "baseline", "approved"]:
    if approved != current:
        return "needs_approval"
    if record and record.upstream_approved_sha256 != upstream:
        return "stale"
    if stage == "plan" and record and not _is_slot_digest_current(record.curriculum_slot_sha256, slot_hash, slot):
        return "stale"
    if previous and previous.status in ("needs_approval", "stale"):
        return "stale"
    if not record and baseline and _baseline_stage_stale(root, lesson_id, stage, baseline, previous, slot, slot_hash):
        return "stale"
    return "approved" if record else "baseline"


def _baseline_stage_stale(
    root: Path,
    lesson_id: str,
    stage: str,
    baseline: dict[str, str],
    previous: StageApprovalStatus | None,
    slot: CurriculumPlanSlot | None,
    slot_hash: str | None,
) -> bool:
    if stage == "plan":
        return not _baseline_contract_current(root, lesson_id, baseline, slot, slot_hash)
    if previous is None:
        return False
    return previous.file_sha256 != _baseline_digest(baseline, dict(K_APPLICATION_WORKBENCH_STAGES)[previous.stage])


def _stage_name(value: str) -> Literal["plan", "lesson", "exercises"]:
    if value == "plan":
        return "plan"
    if value == "lesson":
        return "lesson"
    return "exercises"


def _require_requested_stages(stages: Sequence[LessonGateStage], gate: LessonGateDecision) -> frozenset[str]:
    """Validate an explicit per-stage approval request against current gate state."""
    if not stages:
        raise ValueError("Approval requires at least one stage")
    unknown = [name for name in stages if name not in ("plan", "lesson", "exercises")]
    if unknown:
        raise ValueError(f"Unknown approval stage: {', '.join(unknown)}")
    required = {stage.stage for stage in gate.required_stages}
    not_needed = [name for name in stages if name not in required]
    if not_needed:
        raise ValueError(f"{', '.join(not_needed)} does not need approval")
    return frozenset(stages)


def _require_approval_stages(
    stages: Sequence[LessonGateStage] | None, gate: LessonGateDecision, source_exists: bool
) -> list[StageApprovalStatus]:
    """Resolve the stages one approval run may record, honouring the gate for each request."""
    if stages is None:
        if not gate.allowed:
            if gate.blockers:
                raise ValueError("Approval blocked by current lesson gate")
            if not source_exists:
                raise ValueError("Approval requires a draft")
            raise ValueError("All stages already have owner acceptance and are fresh")
        return list(gate.required_stages)
    requested = _require_requested_stages(stages, gate)
    if any(blocker.stage is None or blocker.stage in requested for blocker in gate.blockers):
        raise ValueError("Approval blocked by current lesson gate")
    if not source_exists:
        raise ValueError("Approval requires a draft")
    return [item for item in gate.required_stages if item.stage in requested]


def _latest_record(
    root: Path, lesson_id: str, stage: str, current: str | None = None, *, upstream: str | None = None
) -> StageApprovalRecord | None:
    records = approval_records(root, lesson_id, stage)
    matches = [record for record in records if record.file_sha256 == current] if current else records
    if current:
        chained = [record for record in matches if record.upstream_approved_sha256 == upstream]
        matches = chained or matches
    return max(matches, key=lambda record: record.approved_at, default=None)


def _baseline_files(root: Path, lesson_id: str) -> dict[str, str]:
    return legacy_baseline_files(root, lesson_id)


def _baseline_digest(baseline: dict[str, str], filename: str) -> str | None:
    return hashlib.sha256(baseline[filename].encode()).hexdigest() if filename in baseline else None


def _baseline_contract_current(
    root: Path, lesson_id: str, baseline: dict[str, str], slot: CurriculumPlanSlot | None, slot_hash: str | None
) -> bool:
    if not WorkspacePaths(root).curriculum_plan.is_file():
        return True
    if slot is None:
        return False
    pin = _read_baselines(root).lessons.get(lesson_id)
    if pin and not _is_slot_digest_current(pin.curriculum_slot_sha256, slot_hash, slot):
        return False
    original = read_workbench_record(root, "curriculum", "baseline", CurriculumBaseline)
    original_slot = original.slots.get(lesson_id)
    if original_slot and normalize_curriculum_slot(
        CurriculumPlanSlot.model_validate_json(original_slot), include_position=False
    ) != normalize_curriculum_slot(slot, include_position=False):
        return False
    metadata, _ = parse_source_frontmatter(baseline["plan.md"])
    for key in K_APPLICATION_WORKBENCH_UNORDERED_PLAN_KEYS:
        value = metadata.get(key)
        if isinstance(value, list):
            metadata[key] = sorted(value)
    expected = normalize_curriculum_slot(slot, include_position=False)
    expected["kind"] = expected.pop("catalog_kind")
    return all(metadata[key] == value for key, value in expected.items() if key in metadata)


def _upstream_diff(paths: WorkspacePaths, lesson_id: str, source: WorkbenchSource, stage: str) -> str:
    filename = "plan.md" if stage == "lesson" else "lesson.md" if stage == "exercises" else "plan.md"
    canonical = contained_workbench_path(paths.root, paths.lessons_root / lesson_id / filename)
    before = canonical.read_text() if canonical.is_file() else ""
    return (
        "".join(
            difflib.unified_diff(
                before.splitlines(True), source.files[filename].splitlines(True), fromfile="canonical", tofile="draft"
            )
        )
        or "Upstream approval or curriculum slot changed; stage source is unchanged."
    )


def _review_evidence(
    root: Path,
    lesson_id: str,
    stages: list[Literal["lesson", "exercises"]],
    ledger: ReviewLedger,
    log: ReviewLog,
) -> tuple[dict[Literal["lesson", "exercises"], StageReviewEvidence], str]:
    current_runs = ledger.evidence_runs()
    if not current_runs:
        raise ValueError("AI review coverage evidence is unavailable")
    stage_runs = {stage: ledger.evidence_runs(stage) for stage in stages}
    if any(not runs for runs in stage_runs.values()):
        raise ValueError("AI review coverage evidence is unavailable")
    write_workbench_record(root, "reviews", lesson_id, log)
    decisions = ledger.current_findings()
    text = (
        json.dumps(
            {
                "review_runs": [item.model_dump(mode="json") for item in current_runs],
                "owner_decisions": [item.model_dump(mode="json") for item in decisions],
                "acceptance_statement": K_APPLICATION_WORKBENCH_ACCEPTANCE_STATEMENT,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    )
    evidence_sha256 = hashlib.sha256(text.encode()).hexdigest()
    return {
        stage: StageReviewEvidence(
            job=run.job,
            model=run.model if stage == "lesson" else run.exercise_model,
            prompt_version=run.prompt_version or run.reviewer_version,
            criteria_version=run.criteria_version or run.reviewer_version,
            evidence_sha256=evidence_sha256,
            findings=decisions,
        )
        for stage, runs in stage_runs.items()
        for run in (runs[-1],)
    }, text


def _proposal_count(
    root: Path, lesson_id: str, stage: Literal["plan", "lesson", "exercises"], log: ProposalLog, decision: str
) -> int:
    previous = _latest_record(root, lesson_id, stage)
    source = read_workbench_source(root, lesson_id)
    keys = stage_source_keys(source, stage)
    return sum(
        block.status == decision and block.key in keys
        for proposal in log.proposals
        if previous is None or proposal.created_at > previous.approved_at
        for block in proposal.blocks
    )


def _approval_history(path: Path) -> list[StageApprovalRecord]:
    legacy_path = path.with_name(path.name.removesuffix(".approval.yaml") + ".yaml")
    for candidate in (path, legacy_path):
        if not candidate.is_file():
            continue
        metadata = _approval_yaml(candidate)
        if isinstance(metadata, dict):
            previous = StageApprovalRecord.model_validate(metadata)
            return [*previous.prior_approvals, previous.model_copy(update={"prior_approvals": []})]
    return []


def _read_baselines(root: Path) -> WorkbenchBaselines:
    return read_baselines(root)


def _approval_yaml(path: Path) -> object:
    return read_approval_yaml(path)
