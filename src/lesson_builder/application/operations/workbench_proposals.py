"""Entry point: `create_proposal`, `allowed_decisions` and `decide_proposal` manage proposal decisions."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from uuid import uuid4

import yaml

from lesson_builder.application.operations.author_preview import AuthorPreview
from lesson_builder.application.operations.author_preview import build_package_preview
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import WorkbenchSourceNotFound
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_editing import write_workbench_block
from lesson_builder.application.operations.workbench_editing import write_workbench_file
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_lessons import build_workbench_preview
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_records import ChatResponse
from lesson_builder.application.operations.workbench_records import KeepReason
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ProposalBlock
from lesson_builder.application.operations.workbench_records import ProposalCheck
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS
from lesson_builder.formats.workbench_source import replace_source_span
from lesson_builder.workspace.atomic import write_text_atomically
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from lesson_builder.workspace.workbench_transaction import contained_workbench_path
from lesson_builder.workspace.workbench_transaction import workbench_transaction

ProposalDecision = Literal["accept", "reject", "keep"]


def create_proposal(
    lesson_id: str,
    source: WorkbenchSource,
    response: ChatResponse,
    *,
    origin: Literal["chat", "review_fix", "regenerate", "canonical"],
) -> tuple[Proposal | None, str]:
    """Validate edit references against the captured context; report dropped keys."""
    by_key = {block.key: block for block in source.blocks}
    edits = {}
    dropped = []
    for edit in response.edits:
        if edit.key not in by_key:
            dropped.append(edit.key)
        else:
            edits[edit.key] = edit
    blocks = [
        ProposalBlock(
            key=key,
            before=by_key[key].source,
            after=edit.after_source,
            note=edit.note,
            change_summary=edit.change_summary,
            translations=edit.translations,
            answer_impact=edit.answer_impact,
            uncertainty=edit.uncertainty,
        )
        for key, edit in edits.items()
        if edit.after_source != by_key[key].source
    ]
    reply = response.reply
    if dropped:
        reply += " Dropped unknown block keys: " + ", ".join(dict.fromkeys(dropped)) + "."
    if not blocks:
        return None, reply
    return Proposal(
        proposal_id=uuid4().hex,
        lesson_id=lesson_id,
        source=origin,
        created_against_sha256=digest_workbench_source(source),
        note=reply,
        blocks=blocks,
    ), reply


def digest_workbench_source(source: WorkbenchSource) -> str:
    """Hash the exact lesson and exercises, separated by an unambiguous file boundary."""
    digest = hashlib.sha256()
    for name in ("lesson.md", "exercises.yaml"):
        text = source.files[name].encode("utf-8")
        digest.update(len(text).to_bytes(8, "big"))
        digest.update(text)
    return digest.hexdigest()


def check_proposal(root: Path, lesson_id: str, source: WorkbenchSource, proposal: Proposal) -> ProposalCheck:
    """Apply every edit to a disposable source copy and run the real deterministic check."""
    package = resolve_lesson_package(root, lesson_id)
    if any(item.is_symlink() for item in package.path.rglob("*")):
        raise ValueError("lesson package contains symlinks")
    with TemporaryDirectory(prefix="workbench-proposal-") as temporary:
        candidate = Path(temporary) / lesson_id
        shutil.copytree(package.path, candidate)
        for name, text in proposal_files(source, proposal.blocks).items():
            (candidate / name).write_text(text, encoding="utf-8")
        view = build_package_preview(candidate, source_label=f"proposal/{lesson_id}")
    return check_preview(view)


def check_preview(view: AuthorPreview) -> ProposalCheck:
    """Collect blocking compiler and audit errors without treating warnings as failures."""
    errors = list(view.errors)
    errors.extend(
        f"{finding.get('code', 'check')}: {finding.get('explanation', '')}"
        for finding in view.audit.get("findings", [])
        if finding.get("severity") in ("blocking", "blocker", "major", "error", "material")
    )
    return ProposalCheck(status="failed" if errors else "passed", errors=errors)


def proposal_files(source: WorkbenchSource, blocks: list[ProposalBlock]) -> dict[str, str]:
    """Replace original source spans from last to first so neighboring offsets remain exact."""
    files = dict(source.files)
    by_key = {item.key: item for item in source.blocks}
    for block in blocks:
        if block.key in files:
            files[block.key] = block.after
    for block in sorted(
        (item for item in blocks if item.key not in files), key=lambda item: by_key[item.key].start, reverse=True
    ):
        span = by_key[block.key]
        name = "exercises.yaml" if span.kind == "exercise" else "lesson.md"
        files[name] = replace_source_span(files[name], span, block.after)
    return files


def store_proposal(root: Path, lesson_id: str, proposal: Proposal) -> Proposal:
    """Retain history; replaying an id returns its stored state without changing it."""
    log = read_workbench_record(root, "proposals", lesson_id, ProposalLog)
    existing = next((item for item in log.proposals if item.proposal_id == proposal.proposal_id), None)
    if existing is not None:
        return existing
    for previous in log.proposals:
        if previous.status == "open":
            previous.status = "superseded"
    log.proposals.append(proposal)
    write_workbench_record(root, "proposals", lesson_id, log)
    return proposal


def read_open_proposal(root: Path, lesson_id: str, source: WorkbenchSource) -> Proposal | None:
    """Refresh conflicts against current source, preserving a conflict even after a later undo."""
    log = read_workbench_record(root, "proposals", lesson_id, ProposalLog)
    proposal = next((item for item in reversed(log.proposals) if item.status == "open"), None)
    if proposal is None:
        return None
    changed = _mark_conflicts(proposal, source)
    if changed:
        write_workbench_record(root, "proposals", lesson_id, log)
    return proposal


def allowed_decisions(
    proposal: Proposal, block: ProposalBlock, source: WorkbenchSource
) -> tuple[ProposalDecision, ...]:
    """Return decisions the projection and action may offer for one captured proposal block."""
    if block.status == "pending":
        decisions: list[ProposalDecision] = ["accept", "reject"]
        if proposal.source == "regenerate" and any(
            item.key == block.key and item.kind == "exercise" for item in source.blocks
        ):
            decisions.append("keep")
        return tuple(decisions)
    if block.status == "conflicted":
        return ("reject",)
    return ()


def decide_proposal(
    root: Path,
    lesson_id: str,
    proposal_id: str,
    decision: Literal["accept", "reject", "keep"],
    *,
    key: str | None = None,
    note: str | None = None,
    reason: KeepReason | None = None,
    comment: str | None = None,
) -> Proposal:
    """Accept/reject blocks or keep an exercise with an explicit owner reason."""
    _require_owner_decision(decision, reason, comment if comment is not None else note or "")
    source = read_workbench_source(root, lesson_id)
    log = read_workbench_record(root, "proposals", lesson_id, ProposalLog)
    proposal = next((item for item in log.proposals if item.proposal_id == proposal_id and item.status == "open"), None)
    if proposal is None:
        raise WorkbenchSourceNotFound("open proposal not found")
    if decision == "keep" and key is None:
        raise ValueError("Keeping an exercise requires its key")
    paths = WorkspacePaths(root)
    alignment = ExerciseAlignment(root, lesson_id)
    transaction_paths = [
        paths.drafts_root / lesson_id,
        paths.workbench_root / "proposals" / f"{lesson_id}.json",
    ]
    if (
        decision == "keep"
        or proposal.candidate_review is not None
        or (decision == "accept" and any(block.pin is not None for block in proposal.blocks))
    ):
        transaction_paths.extend(alignment.record_paths())
    if proposal.candidate_review:
        transaction_paths.append(paths.workbench_root / "reviews" / f"{lesson_id}.json")
    with workbench_transaction(
        root,
        tuple(transaction_paths),
    ):
        _mark_conflicts(proposal, source)
        unsettled = _select_unsettled_blocks(proposal, key)
        _require_proposal_decision(proposal, source, unsettled, decision)
        _apply_proposal_decision(root, lesson_id, source, proposal, unsettled, decision, key, note, reason, comment)
        if all(block.status in ("accepted", "rejected") for block in proposal.blocks):
            proposal.status = "settled"
        _store_accepted_candidate_review(root, lesson_id, proposal, decision)
        _record_pulled_canonical(root, lesson_id, proposal)
        write_workbench_record(root, "proposals", lesson_id, log)
    return proposal


def _require_owner_decision(decision: str, reason: KeepReason | None, comment: str) -> None:
    """Validate owner input before looking up or mutating the proposal."""
    if len(comment) > K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS:
        raise ValueError(
            f"Decision comment must be at most {K_APPLICATION_WORKBENCH_DECISION_COMMENT_MAX_CHARS} characters."
        )
    if decision == "keep" and reason is None:
        raise ValueError("Choose a reason for this owner decision.")


def _require_proposal_decision(
    proposal: Proposal,
    source: WorkbenchSource,
    unsettled: list[ProposalBlock],
    decision: ProposalDecision,
) -> None:
    """Reject decisions the shared policy does not offer for every selected block."""
    if not unsettled:
        raise ValueError("Proposal block has no available decision")
    if decision == "keep" and len(unsettled) != 1:
        raise WorkbenchSourceNotFound("exercise proposal block not found")
    if any(decision not in allowed_decisions(proposal, block, source) for block in unsettled):
        raise ValueError("Proposal decision is not allowed for the current block")


def _apply_proposal_decision(
    root: Path,
    lesson_id: str,
    source: WorkbenchSource,
    proposal: Proposal,
    unsettled: list[ProposalBlock],
    decision: Literal["accept", "reject", "keep"],
    key: str | None,
    note: str | None,
    reason: KeepReason | None,
    comment: str | None,
) -> None:
    """Apply accepted source, record alignment, or settle a keep/reject decision."""
    if decision == "accept":
        _accept_blocks(root, source, proposal, unsettled, all_blocks=key is None)
        for block in unsettled:
            if block.status == "accepted" and proposal.source == "regenerate" and block.pin is not None:
                alignment = ExerciseAlignment(root, lesson_id)
                alignment.pin(
                    block.pin,
                    "regenerated",
                    note=block.change_summary or block.note,
                    decision_id=f"{proposal.run_id or proposal.proposal_id}:{block.key}",
                )
    elif decision == "keep":
        _keep_proposal_exercise(root, lesson_id, source, proposal, unsettled[0], note, reason, comment)
    else:
        for block in unsettled:
            block.status = "rejected"


def _keep_proposal_exercise(
    root: Path,
    lesson_id: str,
    source: WorkbenchSource,
    proposal: Proposal,
    block: ProposalBlock,
    note: str | None,
    reason: KeepReason | None,
    comment: str | None,
) -> None:
    """Keep the current exercise, pin the sections considered, and reject its generated replacement."""
    if block.pin is not None:
        ExerciseAlignment(root, lesson_id).pin(
            block.pin,
            "kept",
            note=note.strip() if note else None,
            reason=reason,
            comment=(comment if comment is not None else note or "").strip() or None,
            decision_id=f"{proposal.run_id or proposal.proposal_id}:{block.key}",
        )
    else:
        ExerciseAlignment(root, lesson_id).keep_current(source, block.key, note or "", reason=reason, comment=comment)
    block.status = "rejected"


def _store_accepted_candidate_review(
    root: Path, lesson_id: str, proposal: Proposal, decision: Literal["accept", "reject", "keep"]
) -> None:
    """Record a paid candidate review only after every proposed block was accepted."""
    if decision != "accept" or proposal.candidate_review is None:
        return
    if all(block.status == "accepted" for block in proposal.blocks):
        from lesson_builder.application.operations.workbench_review import store_candidate_review_if_current

        store_candidate_review_if_current(root, lesson_id, proposal.candidate_review)


def _record_pulled_canonical(root: Path, lesson_id: str, proposal: Proposal) -> None:
    if (
        proposal.source != "canonical"
        or proposal.status != "settled"
        or any(block.status != "accepted" for block in proposal.blocks)
    ):
        return
    path = contained_workbench_path(root, WorkspacePaths(root).drafts_root / lesson_id / "draft.yaml")
    data = yaml.safe_load(path.read_bytes())
    data["started_from_sha256"] = proposal.canonical_sha256
    write_text_atomically(path, yaml.safe_dump(data, sort_keys=False))


def _mark_conflicts(proposal: Proposal, source: WorkbenchSource) -> bool:
    by_key = source.files | {block.key: block.source for block in source.blocks}
    changed = False
    for block in proposal.blocks:
        if block.status == "pending" and by_key.get(block.key) != block.before:
            block.status = "conflicted"
            changed = True
    return changed


def _select_unsettled_blocks(proposal: Proposal, key: str | None) -> list[ProposalBlock]:
    selected = [block for block in proposal.blocks if key is None or block.key == key]
    if not selected:
        raise WorkbenchSourceNotFound("proposal block not found")
    return [block for block in selected if block.status in ("pending", "conflicted")]


def _accept_blocks(
    root: Path, source: WorkbenchSource, proposal: Proposal, blocks: list[ProposalBlock], *, all_blocks: bool
) -> None:
    if all_blocks and proposal.check.status == "failed":
        raise ValueError("Accept all requires a passed proposal check")
    if not blocks or any(block.status == "conflicted" for block in blocks):
        return
    lesson_id = proposal.lesson_id
    if all_blocks:
        files = proposal_files(source, blocks)
        for name in ("plan.md", "lesson.md", "exercises.yaml"):
            if files[name] != source.files[name]:
                write_workbench_file(root, lesson_id, name, files[name])
        check = check_preview(build_workbench_preview(resolve_lesson_package(root, lesson_id)))
        for block in blocks:
            block.status = "accepted"
            block.check = check
        return
    for block in blocks:
        view = (
            write_workbench_file(root, lesson_id, block.key, block.after)
            if block.key in source.files
            else write_workbench_block(root, lesson_id, block.key, source=block.after)
        )
        block.status = "accepted"
        block.check = check_preview(view)
