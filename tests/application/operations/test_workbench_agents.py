"""Entry point: checked proposal, structured chat and scoped review behavior tests."""

from __future__ import annotations

import hashlib
import json
import shutil
from _thread import RLock
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from threading import Barrier
from threading import Event
from threading import Lock
from typing import Literal

import pytest

from lesson_builder.application.operations import workbench_proposals
from lesson_builder.application.operations import workbench_review as workbench_review_operations
from lesson_builder.application.operations.review_judges import build_naturalness_prompt
from lesson_builder.application.operations.workbench_chat import begin_workbench_chat_turn
from lesson_builder.application.operations.workbench_chat import begin_workbench_explanation_turn
from lesson_builder.application.operations.workbench_chat import run_workbench_chat_turn
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_editing import write_workbench_block
from lesson_builder.application.operations.workbench_editing import write_workbench_file
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_proposals import allowed_decisions
from lesson_builder.application.operations.workbench_proposals import check_proposal
from lesson_builder.application.operations.workbench_proposals import create_proposal
from lesson_builder.application.operations.workbench_proposals import decide_proposal
from lesson_builder.application.operations.workbench_proposals import digest_workbench_source
from lesson_builder.application.operations.workbench_proposals import read_open_proposal
from lesson_builder.application.operations.workbench_proposals import store_proposal
from lesson_builder.application.operations.workbench_records import ChatResponse
from lesson_builder.application.operations.workbench_records import ChatTranscript
from lesson_builder.application.operations.workbench_records import ContextReference
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ProposalBlock
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_records import ReviewFinding
from lesson_builder.application.operations.workbench_records import ReviewLog
from lesson_builder.application.operations.workbench_records import ReviewRun
from lesson_builder.application.operations.workbench_review import ReviewLedger
from lesson_builder.application.operations.workbench_review import block_content_hash
from lesson_builder.application.operations.workbench_review import load_workbench_reviewer_version
from lesson_builder.application.operations.workbench_review import resolve_workbench_findings
from lesson_builder.application.operations.workbench_review import review_uncovered_blocks
from lesson_builder.application.operations.workbench_review import review_workbench_lesson
from lesson_builder.application.operations.workbench_review import select_review_blocks
from lesson_builder.application.operations.workbench_review_calls import map_review_finding
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettings
from lesson_builder.application.operations.workbench_settings_operations import default_workbench_settings
from lesson_builder.application.operations.workbench_settings_operations import write_workbench_settings
from lesson_builder.application.operations.workbench_state import BlockTarget
from lesson_builder.application.operations.workbench_state import WorkbenchFinding
from lesson_builder.application.operations.workbench_state import build_workbench_lesson_state
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.cancellation import active_cancellation_token
from lesson_builder.formats.workbench_source import SourceBlock
from lesson_builder.formats.workbench_source import split_lesson_source
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.application.factories import ReviewRunFactory
from tests.application.fakes import FakeLlmClient
from tests.application.fakes import workbench_review_reply
from tests.clients.codex.fakes import FakeCodexAppServer
from tests.clients.codex.fakes import FakeCodexTurn
from tests.settings import K_TEST_HANG_GUARD_SECONDS


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = Path(__file__).parents[3]
    shutil.copytree(
        root / "content" / "lessons" / "greet_and_introduce_yourself",
        tmp_path / "content" / "lessons" / "greet_and_introduce_yourself",
    )
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(root / ".opencode" / "agents", tmp_path / ".opencode" / "agents")
    return tmp_path


def test_chat_given_unknown_and_existing_keys_expect_validated_proposal_and_no_draft(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "reading")
    after = block.source.replace('translation="', 'translation="Hello: ', 1)
    client = FakeCodexAppServer(
        FakeCodexTurn(
            text=json.dumps(
                {
                    "reply": "More natural.",
                    "edits": [
                        {"key": block.key, "after_source": after, "note": "Changed translation"},
                        {"key": "unknown", "after_source": "text", "note": "x"},
                    ],
                }
            ),
        )
    )
    lock = RLock()
    turn_id = begin_workbench_chat_turn(
        workspace,
        lesson_id,
        "Make this natural",
        [ContextReference(kind="selection", label="Reading", key=block.key, detail="Hei!")],
        lock,
    )
    result = run_workbench_chat_turn(workspace, lesson_id, turn_id, lock, client)
    proposal = read_open_proposal(workspace, lesson_id, source)
    assert proposal is not None
    assert proposal.blocks[0].before == block.source
    assert proposal.blocks[0].after == after
    assert proposal.check.status == "passed"
    assert "unknown" in result["reply"]
    assert not (workspace / "drafts").exists()
    transcript = read_workbench_record(workspace, "chat", lesson_id, ChatTranscript)
    assert transcript.turns[0].chips[0].key == block.key
    assert transcript.turns[-1].tool_note == "proposed 1 block edits"
    assert transcript.turns[0].status == "completed"
    assert transcript.turns[-1].proposal_id == proposal.proposal_id
    assert json.dumps(block.source, ensure_ascii=False) in client.calls[0].prompt


def test_proposal_decisions_given_origin_block_status_and_decision_expect_projection_matches_action(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    initial_source = read_workbench_source(workspace, lesson_id)
    exercise_key = "notice_name_question"
    alignment = ExerciseAlignment(workspace, lesson_id)
    alignment.pin(
        alignment.capture(initial_source, [exercise_key])[exercise_key],
        "kept",
        "Initial baseline",
        reason="still_matches",
    )
    write_workbench_file(
        workspace,
        lesson_id,
        "lesson.md",
        initial_source.files["lesson.md"].replace("Two new students arrive early", "Two students arrive early", 1),
    )
    current_source = read_workbench_source(workspace, lesson_id)
    origins = ("chat", "review_fix", "regenerate", "canonical")
    block_kinds = ("exercise", "reading")
    block_statuses = ("pending", "conflicted", "accepted", "rejected")

    cases: list[Proposal] = []
    for origin, block_kind, block_status in product(origins, block_kinds, block_statuses):
        source_block = next(
            block
            for block in current_source.blocks
            if block.kind == block_kind and (block_kind != "exercise" or block.key == exercise_key)
        )
        cases.append(
            Proposal(
                proposal_id=f"{origin}-{block_kind}-{block_status}",
                lesson_id=lesson_id,
                source=origin,
                created_against_sha256=digest_workbench_source(current_source),
                note="Decision matrix case",
                blocks=[
                    ProposalBlock(
                        key=source_block.key,
                        before=source_block.source,
                        after=source_block.source,
                        note="No source change",
                        status=block_status,
                    )
                ],
            )
        )

    for origin in origins:
        origin_cases = [case for case in cases if case.source == origin]
        matrix = Proposal(
            proposal_id=f"{origin}-matrix",
            lesson_id=lesson_id,
            source=origin,
            created_against_sha256=digest_workbench_source(current_source),
            note="Decision matrix projection",
            blocks=[case.blocks[0].model_copy(deep=True) for case in origin_cases],
        )
        write_workbench_record(workspace, "proposals", lesson_id, ProposalLog(proposals=[matrix]))
        projected = build_workbench_lesson_state(workspace, lesson_id, current_source, (), False).proposals
        assert len(projected) == len(origin_cases)
        for projected_block, proposal in zip(projected, origin_cases, strict=True):
            assert projected_block.allowed_decisions == allowed_decisions(proposal, proposal.blocks[0], current_source)

    monkeypatch.setattr(workbench_proposals, "read_workbench_source", lambda *_args, **_kwargs: current_source)
    for proposal in cases:
        write_workbench_record(workspace, "proposals", lesson_id, ProposalLog(proposals=[proposal]))
        _assert_proposal_decision_case_matches_action(workspace, lesson_id, proposal, current_source)


def _assert_proposal_decision_case_matches_action(
    workspace: Path,
    lesson_id: str,
    proposal: Proposal,
    source: WorkbenchSource,
) -> None:
    proposal_id = proposal.proposal_id
    expected = allowed_decisions(proposal, proposal.blocks[0], source)
    for decision in ("accept", "reject", "keep"):
        write_workbench_record(
            workspace,
            "proposals",
            lesson_id,
            ProposalLog(proposals=[proposal.model_copy(deep=True)]),
        )
        _assert_proposal_decision_action_matches_policy(
            workspace, lesson_id, proposal_id, proposal.blocks[0].key, decision, expected
        )


def _assert_proposal_decision_action_matches_policy(
    workspace: Path,
    lesson_id: str,
    proposal_id: str,
    block_key: str,
    decision: Literal["accept", "reject", "keep"],
    expected: tuple[Literal["accept", "reject", "keep"], ...],
) -> None:
    note = "Keep current exercise" if decision == "keep" else None
    if decision in expected:
        decided = decide_proposal(
            workspace, lesson_id, proposal_id, decision, key=block_key, note=note, reason="still_matches"
        )
        assert decided.blocks[0].status == ("accepted" if decision == "accept" else "rejected")
        return
    with pytest.raises(ValueError):
        decide_proposal(workspace, lesson_id, proposal_id, decision, key=block_key, note=note, reason="still_matches")


def test_chat_turn_given_saved_codex_override_expect_model_and_effort_on_turn(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    settings = default_workbench_settings(workspace)
    updated = WorkbenchSettings.model_validate(
        {
            **settings.model_dump(mode="json"),
            "roles": {
                **settings.roles.model_dump(mode="json"),
                "chat": {"model": "gpt-6-luna", "effort": "low"},
            },
        }
    )
    write_workbench_settings(workspace, updated)
    client = FakeCodexAppServer(FakeCodexTurn(text=json.dumps({"reply": "Ready", "edits": [], "regenerate": None})))
    lock = RLock()
    turn_id = begin_workbench_chat_turn(workspace, lesson_id, "Explain this lesson", [], lock)

    run_workbench_chat_turn(workspace, lesson_id, turn_id, lock, client)

    assert client.calls[0].settings.model == "gpt-6-luna"
    assert client.calls[0].settings.effort == "low"


def test_explanation_given_current_dialogue_block_expect_cached_translations_without_proposal(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "reading")
    response = {
        "translations": [{"source": "Hei! Jeg heter Mina.", "english": "Hi! My name is Mina."}],
        "item_tested": "The dialogue practices greeting someone and giving a name.",
    }
    client = FakeCodexAppServer(FakeCodexTurn(text=json.dumps(response)))
    lock = RLock()
    turn_id = begin_workbench_explanation_turn(workspace, lesson_id, block.key, lock)

    run_workbench_chat_turn(workspace, lesson_id, turn_id, lock, client)

    transcript = read_workbench_record(workspace, "chat", lesson_id, ChatTranscript)
    cached = transcript.explanations[f"{block.key}:{block_content_hash(block.source)}"]
    state = build_workbench_lesson_state(workspace, lesson_id, source, (), False)
    assert cached.translations[0].english == "Hi! My name is Mina."
    assert state.explanations[block.key].item_tested == response["item_tested"]
    assert all(turn.mode == "chat" for turn in state.chat)
    assert read_open_proposal(workspace, lesson_id, source) is None
    assert "not a qualified Bokmål review" in client.calls[0].prompt


def test_review_coverage_given_two_scoped_runs_and_one_block_edit_expect_union_kept_for_unchanged_blocks(
    workspace: Path,
) -> None:
    source = read_workbench_source(workspace, "greet_and_introduce_yourself")
    reading = next(block for block in source.blocks if block.kind == "reading")
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    run_fields = {
        "digest": digest_workbench_source(source),
        "plan_sha256": hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        "reviewer_version": load_workbench_reviewer_version(workspace),
    }
    log = ReviewLog(
        runs=[
            ReviewRunFactory(
                **run_fields,
                scope="section",
                key=reading.key,
                block_hashes={
                    reading.key: block_content_hash(reading.source),
                },
            ),
            ReviewRunFactory(
                **run_fields,
                scope="item",
                key=exercise.key,
                block_hashes={
                    exercise.key: block_content_hash(exercise.source),
                },
            ),
        ]
    )

    coverage = ReviewLedger(source, log).coverage()

    assert coverage.covered == 2
    assert set(coverage.uncovered) == ({block.key for block in source.blocks} - {reading.key, exercise.key}) | {
        "@lesson-metadata",
        "@exercise-placement-order",
    }
    write_workbench_block(workspace, "greet_and_introduce_yourself", reading.key, fields={"body": "Updated dialogue."})
    updated = read_workbench_source(workspace, "greet_and_introduce_yourself")
    updated_coverage = ReviewLedger(updated, log).coverage()
    assert updated_coverage.covered == 1
    assert reading.key in updated_coverage.uncovered
    assert exercise.key not in updated_coverage.uncovered
    assert "@lesson-metadata" in updated_coverage.uncovered
    assert "@exercise-placement-order" in updated_coverage.uncovered


def test_review_coverage_given_metadata_and_exercise_order_change_expect_fresh_targets_uncovered(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    old_run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        block_hashes={block.key: block_content_hash(block.source) for block in source.blocks},
    )
    log = ReviewLog(runs=[old_run])
    lesson = source.files["lesson.md"]
    lesson = lesson.replace(
        'goal: "Complete a first-meeting exchange',
        'goal: "Complete a first-meeting conversation',
        1,
    )
    lesson = (
        lesson.replace("{{exercise: notice_name_question}}", "{{exercise: temporary-exercise}}", 1)
        .replace("{{exercise: match_line_to_job}}", "{{exercise: notice_name_question}}", 1)
        .replace("{{exercise: temporary-exercise}}", "{{exercise: match_line_to_job}}", 1)
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", lesson)

    current = read_workbench_source(workspace, lesson_id)
    coverage = ReviewLedger(current, log).coverage()

    assert set(coverage.uncovered) == {"@lesson-metadata", "@exercise-placement-order"}


def test_legacy_review_given_current_whole_lesson_run_expect_only_source_block_coverage_migrated(
    workspace: Path,
) -> None:
    source = read_workbench_source(workspace, "greet_and_introduce_yourself")
    legacy_run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
    )
    log = ReviewLog(runs=[legacy_run])

    ledger = ReviewLedger(source, log)

    assert len(log.coverage) == len(source.blocks)
    assert {item.block_id for item in log.coverage} == {block.key for block in source.blocks}
    assert set(ledger.coverage().uncovered) == {
        "@lesson-metadata",
        "@exercise-placement-order",
    }


@pytest.mark.parametrize("scope", ["lesson", "section", "item"])
@pytest.mark.parametrize("legacy", [True, False])
@pytest.mark.parametrize("synthetic_edit", ["none", "metadata", "placement"])
def test_review_ledger_views_given_scope_format_and_synthetic_edit_expect_coverage_findings_and_evidence_agree(
    workspace: Path,
    scope: Literal["lesson", "section", "item"],
    legacy: bool,
    synthetic_edit: Literal["none", "metadata", "placement"],
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    original = read_workbench_source(workspace, lesson_id)
    run, finding = _review_ledger_case(workspace, original, scope, legacy)
    log = ReviewLog(runs=[run])
    _apply_synthetic_review_edit(workspace, lesson_id, original.files["lesson.md"], synthetic_edit)
    current = read_workbench_source(workspace, lesson_id)
    ledger = ReviewLedger(current, log)
    expected_run_hashes = run.block_hashes if not legacy or run.digest == digest_workbench_source(current) else {}
    _assert_review_ledger_views_agree(ledger, current, expected_run_hashes, finding)


def _review_ledger_case(
    workspace: Path,
    source: WorkbenchSource,
    scope: Literal["lesson", "section", "item"],
    legacy: bool,
) -> tuple[ReviewRun, ReviewFinding]:
    reading = next(block for block in source.blocks if block.kind == "reading")
    key = None if scope == "lesson" else reading.key.partition("#")[0] if scope == "section" else reading.key
    selected = select_review_blocks(source, scope, key)
    finding_block = next(block for block in selected if block.kind != "exercise")
    hashes = {block.key: block_content_hash(block.source) for block in selected}
    if scope == "lesson":
        hashes.update(_synthetic_review_hashes(source.files["lesson.md"]))
    finding = ReviewFinding(
        finding_id="finding-current-block",
        key=finding_block.key,
        criterion="naturalness",
        excerpt=finding_block.source,
        explanation="Review finding",
        severity="minor",
        advisory=True,
        block_hash="" if legacy else block_content_hash(finding_block.source),
    )
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        scope=scope,
        key=key,
        block_hashes={} if legacy else hashes,
        findings=[finding],
    )
    return run, finding


def _apply_synthetic_review_edit(
    workspace: Path,
    lesson_id: str,
    source: str,
    edit: Literal["none", "metadata", "placement"],
) -> None:
    if edit == "none":
        return
    if edit == "metadata":
        source = source.replace(
            'goal: "Complete a first-meeting exchange',
            'goal: "Complete a first-meeting conversation',
            1,
        )
    else:
        source = (
            source.replace("{{exercise: notice_name_question}}", "{{exercise: temporary-exercise}}", 1)
            .replace("{{exercise: match_line_to_job}}", "{{exercise: notice_name_question}}", 1)
            .replace("{{exercise: temporary-exercise}}", "{{exercise: match_line_to_job}}", 1)
        )
    write_workbench_file(workspace, lesson_id, "lesson.md", source)


def _assert_review_ledger_views_agree(
    ledger: ReviewLedger,
    source: WorkbenchSource,
    run_hashes: dict[str, str],
    finding: ReviewFinding,
) -> None:
    targets = _expected_review_ledger_targets(source)
    covered = {key for key, (_, block_hash) in targets.items() if run_hashes.get(key) == block_hash}
    current_findings = {item.finding_id for item in ledger.current_findings()}
    coverage = ledger.coverage()
    assert coverage.covered == len(covered)
    assert set(coverage.uncovered) == set(targets) - covered
    assert current_findings == ({finding.finding_id} if finding.key in covered else set())
    assert bool(ledger.evidence_runs()) is bool(covered)
    _assert_stage_evidence_matches_coverage(ledger, targets, covered)


def _assert_stage_evidence_matches_coverage(
    ledger: ReviewLedger,
    targets: dict[str, tuple[Literal["lesson", "exercises"], str]],
    covered: set[str],
) -> None:
    for stage in ("lesson", "exercises"):
        stage_targets = {key for key, (target_stage, _) in targets.items() if target_stage == stage}
        stage_covered = stage_targets & covered
        stage_coverage = ledger.coverage(stage)
        assert stage_coverage.covered == len(stage_covered)
        assert set(stage_coverage.uncovered) == stage_targets - stage_covered
        assert bool(ledger.evidence_runs(stage)) is bool(stage_covered)


def _expected_review_ledger_targets(source: WorkbenchSource) -> dict[str, tuple[Literal["lesson", "exercises"], str]]:
    targets = {
        block.key: ("exercises" if block.kind == "exercise" else "lesson", block_content_hash(block.source))
        for block in source.blocks
    }
    targets.update(_synthetic_review_targets(source.files["lesson.md"]))
    return targets


def _synthetic_review_hashes(lesson_source: str) -> dict[str, str]:
    return {key: block_hash for key, (_, block_hash) in _synthetic_review_targets(lesson_source).items()}


def _synthetic_review_targets(lesson_source: str) -> dict[str, tuple[Literal["lesson", "exercises"], str]]:
    blocks = split_lesson_source(lesson_source)
    section_id: str | None = None
    placement: list[tuple[str, str | None, str | None]] = []
    for block in blocks.blocks:
        if block.kind == "heading":
            section_id = block.key
            placement.append(("section", block.key, section_id))
        elif block.kind == "exercise":
            placement.append(("exercise", block.key, section_id))
        else:
            placement.append(("content", None, section_id))
    metadata_hash = block_content_hash(blocks.frontmatter)
    placement_hash = block_content_hash(json.dumps(placement, ensure_ascii=False))
    return {
        "@lesson-metadata": ("lesson", metadata_hash),
        "@exercise-placement-order": ("exercises", placement_hash),
    }


def test_review_coverage_given_full_review_then_metadata_and_placement_edits_expect_target_hashes_refresh(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    client = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    review_workbench_lesson(workspace, lesson_id, "lesson", None, RLock())

    source = read_workbench_source(workspace, lesson_id)
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    assert ReviewLedger(source, log).coverage().uncovered == []
    assert any("AUTHORED LESSON FRONTMATTER" in prompt for prompt, _ in client.calls)
    assert any("EXERCISE PLACEMENT AND ORDER" in prompt for prompt, _ in client.calls)

    changed_metadata = source.files["lesson.md"].replace(
        'goal: "Complete a first-meeting exchange',
        'goal: "Complete a first-meeting conversation',
        1,
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_metadata)
    updated = read_workbench_source(workspace, lesson_id)
    assert ReviewLedger(updated, log).coverage().uncovered == ["@lesson-metadata"]
    assert ReviewLedger(updated, log).coverage("lesson").uncovered == ["@lesson-metadata"]
    assert ReviewLedger(updated, log).coverage("exercises").uncovered == []

    recovery = review_uncovered_blocks(workspace, lesson_id, RLock())
    assert recovery["results"][0]["scope"] == "lesson"
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    changed_placement = (
        updated.files["lesson.md"]
        .replace("{{exercise: notice_name_question}}", "{{exercise: temporary-exercise}}", 1)
        .replace("{{exercise: match_line_to_job}}", "{{exercise: notice_name_question}}", 1)
        .replace("{{exercise: temporary-exercise}}", "{{exercise: match_line_to_job}}", 1)
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_placement)
    updated = read_workbench_source(workspace, lesson_id)

    assert ReviewLedger(updated, log).coverage().uncovered == ["@exercise-placement-order"]
    assert ReviewLedger(updated, log).coverage("lesson").uncovered == []
    assert ReviewLedger(updated, log).coverage("exercises").uncovered == ["@exercise-placement-order"]
    recovery = review_uncovered_blocks(workspace, lesson_id, RLock())
    assert recovery["results"][0]["scope"] == "lesson"
    refreshed = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    assert ReviewLedger(read_workbench_source(workspace, lesson_id), refreshed).coverage().uncovered == []


def test_review_uncovered_given_cancel_before_start_expect_no_review_record(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    progress: list[str] = []
    cancellation = CancellationToken()
    cancellation.cancel("author", "Review cancelled.")

    with pytest.raises(Cancelled, match="cancelled"):
        review_uncovered_blocks(
            workspace,
            lesson_id,
            RLock(),
            progress=progress.append,
            cancellation=cancellation,
        )

    assert progress == []
    assert not (workspace / "store/workbench/reviews" / f"{lesson_id}.json").exists()


def test_proposals_given_accept_reject_and_new_turn_expect_draft_decisions_and_superseded_history(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    blocks = [item for item in source.blocks if item.kind == "reading"][:2]
    response = ChatResponse.model_validate(
        {
            "reply": "Edits",
            "edits": [
                {
                    "key": block.key,
                    "after_source": block.source.replace('translation="', 'translation="Hello: ', 1),
                    "note": "translation",
                }
                for block in blocks
            ],
        }
    )
    proposal, _ = create_proposal(lesson_id, source, response, origin="chat")
    assert proposal is not None
    proposal.check = check_proposal(workspace, lesson_id, source, proposal)
    store_proposal(workspace, lesson_id, proposal)
    accepted = decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept", key=blocks[0].key)
    assert accepted.blocks[0].status == "accepted"
    decide_proposal(workspace, lesson_id, proposal.proposal_id, "reject", key=blocks[1].key)
    updated = read_workbench_source(workspace, lesson_id)
    assert next(item.source for item in updated.blocks if item.key == blocks[1].key) == blocks[1].source
    assert "Hello: " in next(item.source for item in updated.blocks if item.key == blocks[0].key)
    assert read_open_proposal(workspace, lesson_id, updated) is None
    first, _ = create_proposal(lesson_id, updated, response, origin="chat")
    second, _ = create_proposal(lesson_id, updated, response, origin="review_fix")
    assert first is not None and second is not None
    store_proposal(workspace, lesson_id, first)
    store_proposal(workspace, lesson_id, second)
    log = read_workbench_record(workspace, "proposals", lesson_id, ProposalLog)
    assert [item.status for item in log.proposals] == ["settled", "superseded", "open"]
    assert log.proposals[-1].source == "review_fix"


def test_proposal_given_edited_source_expect_persistent_conflict_and_reject_only(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = source.blocks[0]
    response = ChatResponse.model_validate(
        {
            "reply": "Change title",
            "edits": [{"key": block.key, "after_source": block.source.replace("meeting", "greeting"), "note": "title"}],
        }
    )
    proposal, _ = create_proposal(lesson_id, source, response, origin="chat")
    assert proposal is not None
    store_proposal(workspace, lesson_id, proposal)
    write_workbench_block(workspace, lesson_id, block.key, fields={"title": "Author's title"})
    with pytest.raises(ValueError, match="not allowed"):
        decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept", key=block.key)
    conflict = read_open_proposal(workspace, lesson_id, read_workbench_source(workspace, lesson_id))
    assert conflict is not None and conflict.blocks[0].status == "conflicted"
    assert "Author's title" in read_workbench_source(workspace, lesson_id).files["lesson.md"]
    rejected = decide_proposal(workspace, lesson_id, proposal.proposal_id, "reject")
    assert rejected.blocks[0].status == "rejected"


def test_lesson_state_given_chat_and_regeneration_proposals_expect_block_origin_projected(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = source.blocks[0]
    response = ChatResponse.model_validate(
        {
            "reply": "Warmer gloss",
            "edits": [
                {
                    "key": block.key,
                    "after_source": block.source.replace("meeting", "greeting"),
                    "note": "Warmer gloss",
                    "change_summary": "Renders the greeting with a warmer English gloss.",
                }
            ],
        }
    )
    chat, _ = create_proposal(lesson_id, source, response, origin="chat")
    assert chat is not None
    store_proposal(workspace, lesson_id, chat)

    state = build_workbench_lesson_state(workspace, lesson_id, source, (), False)

    assert state.proposals[0].origin == "chat"
    assert state.proposals[0].change_summary == "Renders the greeting with a warmer English gloss."

    regenerated, _ = create_proposal(lesson_id, source, response, origin="regenerate")
    assert regenerated is not None
    store_proposal(workspace, lesson_id, regenerated)

    updated = build_workbench_lesson_state(workspace, lesson_id, source, (), False)

    assert updated.proposals[0].origin == "regenerate"


def test_store_proposal_given_replayed_settled_id_expect_one_settled_record(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = source.blocks[0]
    proposal, _ = create_proposal(
        lesson_id,
        source,
        ChatResponse.model_validate(
            {
                "reply": "Change title",
                "edits": [{"key": block.key, "after_source": block.source + " ", "note": "title"}],
            }
        ),
        origin="chat",
    )
    assert proposal is not None
    store_proposal(workspace, lesson_id, proposal)
    settled = decide_proposal(workspace, lesson_id, proposal.proposal_id, "reject")
    assert settled.status == "settled"

    stored = store_proposal(workspace, lesson_id, proposal.model_copy(deep=True))

    log = read_workbench_record(workspace, "proposals", lesson_id, ProposalLog)
    assert len(log.proposals) == 1
    assert stored.status == "settled"


def test_accept_proposal_given_second_file_write_failure_expect_draft_and_proposal_rolled_back(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    proposal, _ = create_proposal(
        lesson_id,
        source,
        ChatResponse.model_validate(
            {
                "reply": "Change two files",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Draft ', 1),
                        "note": "lesson",
                    },
                    {
                        "key": exercise.key,
                        "after_source": exercise.source.replace("handle: ", "handle: wp7-", 1),
                        "note": "exercises",
                    },
                ],
            }
        ),
        origin="chat",
    )
    assert proposal is not None
    store_proposal(workspace, lesson_id, proposal)
    original_write = workbench_proposals.write_workbench_file
    calls = 0

    def fail_on_second_file(root: Path, identity: str, name: str, text: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("second file write failed")
        return original_write(root, identity, name, text)

    monkeypatch.setattr(workbench_proposals, "write_workbench_file", fail_on_second_file)

    with pytest.raises(OSError, match="second file"):
        decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")

    log = read_workbench_record(workspace, "proposals", lesson_id, ProposalLog)
    assert calls == 2
    assert not (workspace / "drafts" / lesson_id).exists()
    assert log.proposals[0].status == "open"
    assert [block.status for block in log.proposals[0].blocks] == ["pending", "pending"]


@pytest.mark.parametrize("repair_passes", [True, False])
def test_chat_given_invalid_candidate_expect_one_check_retry_and_visible_result(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, repair_passes: bool
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "exercise")
    valid = block.source + "# clarified item\n"
    responses = iter(
        [
            json.dumps(
                {"reply": "First", "edits": [{"key": block.key, "after_source": "- [unterminated", "note": "bad"}]}
            ),
            json.dumps(
                {
                    "reply": "Repaired",
                    "edits": [
                        {
                            "key": block.key,
                            "after_source": valid if repair_passes else "- [unterminated",
                            "note": "repair",
                        }
                    ],
                }
            ),
        ]
    )
    client = FakeCodexAppServer(*(FakeCodexTurn(text=response) for response in responses))
    lock = RLock()
    turn_id = begin_workbench_chat_turn(workspace, lesson_id, "Improve exercise", [], lock)
    run_workbench_chat_turn(workspace, lesson_id, turn_id, lock, client)
    proposal = read_open_proposal(workspace, lesson_id, source)
    assert proposal is not None
    assert len(client.calls) == 2
    assert len(proposal.retries) == 1
    assert proposal.check.status == ("passed" if repair_passes else "failed")
    if not repair_passes:
        with pytest.raises(ValueError, match="passed"):
            decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")
        assert (
            decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept", key=block.key).blocks[0].status
            == "accepted"
        )


def test_review_mapping_given_excerpt_or_named_section_expect_source_row_and_top_fallback(workspace: Path) -> None:
    source = read_workbench_source(workspace, "greet_and_introduce_yourself")
    block = next(item for item in source.blocks if item.kind == "reading")
    excerpt = block.fields["body"]
    assert map_review_finding(source.blocks, "naturalness", excerpt, "Improve").key == block.key
    assert (
        map_review_finding(source.blocks, "naturalness", "", "Improve", unit=source.blocks[0].key).key
        == source.blocks[0].key
    )
    assert map_review_finding(source.blocks, "general", "", "Unlocated").key == source.blocks[0].key
    assert select_review_blocks(source, "item", block.key) == (block,)


def test_review_given_resolved_finding_and_rerun_expect_resolution_retained_and_scoped_review_not_full(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "reading")

    def respond(prompt: str, model: str | None) -> str:
        if '"PedagogyReview"' in prompt:
            return json.dumps(
                {
                    "scores": dict.fromkeys(
                        ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"], 4
                    ),
                    "summary": "One issue.",
                    "passes": [],
                    "issues": [
                        {
                            "severity": "P2",
                            "category": "bokmal",
                            "title": "Improve wording",
                            "evidence": block.fields["body"],
                            "fix": "Use natural wording",
                        }
                    ],
                }
            )
        return json.dumps(
            {
                "scores": {"idiomatic_phrasing": 4, "register_appropriateness": 4, "terminology_consistency": 4},
                "issues": [],
            }
        )

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    finding = log.runs[-1].findings[0]
    assert finding.key == block.key
    resolve_workbench_findings(workspace, lesson_id, [finding.finding_id], "Accepted wording", reason="intended")
    review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    assert log.runs[-1].findings[0].status == "resolved"
    assert log.runs[-1].findings[0].previously_resolved
    assert log.runs[-1].findings[0].resolution_note == "Accepted wording"
    coverage = ReviewLedger(source, log).coverage()
    assert coverage.covered == 1
    assert len(coverage.uncovered) == coverage.total - 1
    assert "SELECTED SOURCE" in client.calls[0][0]


def test_owner_decision_given_resolved_finding_expect_reason_persisted_and_prompt_advisory(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "reading")

    def respond(prompt: str, model: str | None) -> str:
        if '"PedagogyReview"' in prompt:
            return json.dumps(
                {
                    "scores": dict.fromkeys(
                        ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"], 4
                    ),
                    "summary": "One issue.",
                    "passes": [],
                    "issues": [
                        {
                            "severity": "P2",
                            "category": "bokmal",
                            "title": "Improve wording",
                            "evidence": block.fields["body"],
                            "fix": "Use natural wording",
                        }
                    ],
                }
            )
        return json.dumps(
            {
                "scores": {"idiomatic_phrasing": 4, "register_appropriateness": 4, "terminology_consistency": 4},
                "issues": [],
            }
        )

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    finding = log.runs[-1].findings[0]
    assert finding.key == block.key
    resolve_workbench_findings(workspace, lesson_id, [finding.finding_id], "Accepted wording", reason="intended")
    review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    assert log.runs[-1].findings[0].status == "resolved"
    assert log.runs[-1].findings[0].previously_resolved
    assert log.runs[-1].findings[0].resolution_note == "Accepted wording"
    coverage = ReviewLedger(source, log).coverage()
    assert coverage.covered == 1
    assert len(coverage.uncovered) == coverage.total - 1
    assert "SELECTED SOURCE" in client.calls[0][0]

    assert log.runs[-1].findings[0].resolution_reason == "intended"
    assert log.runs[-1].findings[0].resolution_comment == "Accepted wording"
    assert all("Owner already decided: Intended — Accepted wording" in prompt for prompt, _ in client.calls[2:])
    assert "genuinely new" in client.calls[-1][0]


def test_workbench_review_given_linguistic_finding_without_source_evidence_expect_advisory_without_approval_blocker(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "reading")

    def respond(prompt: str, model: str | None) -> str:
        if '"PedagogyReview"' in prompt:
            return json.dumps(
                {
                    "scores": dict.fromkeys(
                        ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"],
                        4,
                    ),
                    "summary": "Clear.",
                    "passes": [],
                    "issues": [],
                }
            )
        if '"NaturalnessReview"' in prompt:
            return json.dumps(
                {
                    "scores": {
                        "idiomatic_phrasing": 4,
                        "register_appropriateness": 4,
                        "terminology_consistency": 4,
                    },
                    "issues": [
                        {
                            "unit_id": block.key.partition("#")[0],
                            "severity": "P1",
                            "message": "This Bokmål phrase sounds unnatural.",
                        }
                    ],
                }
            )
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    review_workbench_lesson(workspace, lesson_id, "lesson", None, RLock())

    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    finding = next(item for item in log.runs[-1].findings if item.criterion == "naturalness")
    state = build_workbench_lesson_state(workspace, lesson_id, source, (), False)

    assert finding.advisory is True
    assert finding.status == "open"
    assert state.approve_blockers == ()


def test_review_mapping_given_two_phrases_in_one_section_expect_distinct_block_findings(workspace: Path) -> None:
    source = read_workbench_source(workspace, "greet_and_introduce_yourself")
    blocks = [block for block in source.blocks if block.kind == "reading"][:2]
    findings = [
        map_review_finding(
            source.blocks, "naturalness", "", f'Improve "{block.fields["body"]}".', unit=block.key.partition("#")[0]
        )
        for block in blocks
    ]
    assert [finding.key for finding in findings] == [block.key for block in blocks]
    assert len({finding.finding_id for finding in findings}) == 2
    assert [finding.excerpt for finding in findings] == [block.fields["body"] for block in blocks]


def test_scoped_review_given_bokmal_advisory_expect_no_approval_blocker(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    first, selected = [block for block in source.blocks if block.kind == "reading"][:2]
    pedagogy = {
        "scores": dict.fromkeys(
            ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"], 4
        ),
        "summary": "No issues.",
        "passes": [],
        "issues": [],
    }
    naturalness = {
        "scores": {"idiomatic_phrasing": 4, "register_appropriateness": 4, "terminology_consistency": 4},
        "issues": [],
    }

    def respond(prompt: str, model: str | None) -> str:
        if '"PedagogyReview"' in prompt:
            return json.dumps(pedagogy)
        if '"NaturalnessReview"' in prompt:
            return json.dumps(naturalness)
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    review_workbench_lesson(workspace, lesson_id, "lesson", None, RLock())
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    current_run = log.runs[-1]
    current_run.findings = [map_review_finding(source.blocks, "bokmal", first.fields["body"], "Current issue")]
    stale_run = current_run.model_copy(update={"digest": "stale-source", "block_hashes": {}, "findings": []})
    write_workbench_record(workspace, "reviews", lesson_id, ReviewLog(runs=[current_run, stale_run]))

    review_workbench_lesson(workspace, lesson_id, "item", selected.key, RLock())

    state = build_workbench_lesson_state(workspace, lesson_id, source, (), False)
    assert state.approve_blockers == ()
    assert state.findings[-1].advisory is True


def test_scoped_review_given_bokmal_and_naturalness_advisories_expect_no_approval_blocker(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    first, selected = [block for block in source.blocks if block.kind == "reading"][:2]
    previous = map_review_finding(source.blocks, "bokmal", first.fields["body"], "Earlier issue")

    def respond(prompt: str, model: str | None) -> str:
        if '"PedagogyReview"' in prompt:
            return json.dumps(
                {
                    "scores": dict.fromkeys(
                        ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"], 4
                    ),
                    "summary": "Outside issue should be excluded.",
                    "passes": [],
                    "issues": [
                        {
                            "category": "bokmal",
                            "severity": "P2",
                            "title": "Outside",
                            "evidence": first.fields["body"],
                            "fix": "Fix",
                        }
                    ],
                }
            )
        if '"NaturalnessReview"' in prompt:
            return json.dumps(
                {
                    "scores": {
                        "idiomatic_phrasing": 4,
                        "register_appropriateness": 4,
                        "terminology_consistency": 4,
                    },
                    "issues": [
                        {"unit_id": first.key, "severity": "P2", "message": "Outside scope"},
                        {
                            "unit_id": selected.key.partition("#")[0],
                            "severity": "P2",
                            "message": selected.fields["body"] + " should be clearer.",
                        },
                    ],
                }
            )
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    review_workbench_lesson(workspace, lesson_id, "lesson", None, RLock())
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    log.runs[-1].findings = [previous]
    write_workbench_record(workspace, "reviews", lesson_id, log)

    review_workbench_lesson(workspace, lesson_id, "item", selected.key, RLock())
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    assert {finding.key for finding in log.runs[-1].findings} == {first.key, selected.key}
    assert (
        next(finding for finding in log.runs[-1].findings if finding.key == first.key).finding_id == previous.finding_id
    )
    coverage = ReviewLedger(source, log).coverage()
    assert coverage.covered == coverage.total
    state = build_workbench_lesson_state(workspace, lesson_id, source, (), False)
    assert state.approve_blockers == ()
    assert all(finding.advisory for finding in state.findings)
    for finding in log.runs[-1].findings:
        resolve_workbench_findings(workspace, lesson_id, [finding.finding_id], reason="intended")

    advisory = WorkbenchFinding(
        finding_id="warning",
        target=BlockTarget(first.key.partition("#")[0]),
        origin="check",
        severity="warning",
        status="open",
        code="advisory",
        excerpt="",
        explanation="Advisory warning",
    )
    assert not build_workbench_lesson_state(workspace, lesson_id, source, (advisory,), False).approve_blockers
    write_workbench_block(workspace, lesson_id, selected.key, fields={"translation": "Updated translation"})
    updated = read_workbench_source(workspace, lesson_id)
    coverage = ReviewLedger(updated, log).coverage()
    assert coverage.covered == coverage.total - 1
    assert coverage.uncovered == [selected.key]
    assert any(
        blocker.kind == "review_coverage"
        and blocker.stage == "lesson"
        and blocker.count == 1
        and blocker.uncovered == (selected.key,)
        for blocker in build_workbench_lesson_state(workspace, lesson_id, updated, (), False).approve_blockers
    )


def test_scoped_review_given_naturalness_evidence_quotes_same_section_sibling_expect_issue_excluded(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    sibling, selected = [block for block in source.blocks if block.kind == "reading"][:2]
    assert sibling.key.partition("#")[0] == selected.key.partition("#")[0]

    def respond(prompt: str, model: str | None) -> str:
        if '"PedagogyReview"' in prompt:
            return json.dumps(
                {
                    "scores": dict.fromkeys(
                        ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"],
                        4,
                    ),
                    "summary": "Clear.",
                    "passes": [],
                    "issues": [],
                }
            )
        if '"NaturalnessReview"' in prompt:
            return json.dumps(
                {
                    "scores": {
                        "idiomatic_phrasing": 4,
                        "register_appropriateness": 4,
                        "terminology_consistency": 4,
                    },
                    "issues": [
                        {
                            "unit_id": selected.key.partition("#")[0],
                            "evidence": sibling.fields["body"],
                            "severity": "P1",
                            "message": "This sibling wording could be more natural.",
                        }
                    ],
                }
            )
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    review_workbench_lesson(workspace, lesson_id, "item", selected.key, RLock())

    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    assert not any(finding.criterion == "naturalness" for finding in log.runs[-1].findings)


@dataclass
class _OutOfOrderWorkbenchReviewResponder:
    block: SourceBlock
    calls_started: Barrier
    naturalness_barrier: Barrier
    naturalness_finished: Event
    second_pedagogy_finished: Event
    completion_order: list[str]

    def __call__(self, prompt: str, model: str | None) -> str:
        label = "second" if model == "anthropic/claude-sonnet-5-5" else "primary"
        self.calls_started.wait()
        if '"PedagogyReview"' in prompt:
            self._finish_pedagogy(label)
            return self._pedagogy_response(label)
        if '"NaturalnessReview"' in prompt:
            self.naturalness_barrier.wait()
            self.completion_order.append(f"{label}-naturalness")
            return self._naturalness_response(label)
        raise AssertionError("Unexpected reviewer surface")

    def _finish_pedagogy(self, label: str) -> None:
        prerequisite = self.second_pedagogy_finished if label == "primary" else self.naturalness_finished
        assert prerequisite.wait(timeout=2)
        self.completion_order.append(f"{label}-pedagogy")
        if label == "second":
            self.second_pedagogy_finished.set()

    def _pedagogy_response(self, label: str) -> str:
        return json.dumps(
            {
                "scores": dict.fromkeys(
                    ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"], 4
                ),
                "summary": f"{label.title()} summary.",
                "passes": [],
                "issues": [
                    {
                        "severity": "P2",
                        "category": "other",
                        "title": f"{label.title()} pedagogy finding.",
                        "evidence": self.block.source,
                        "fix": "Revise the example.",
                    }
                ],
            }
        )

    def _naturalness_response(self, label: str) -> str:
        return json.dumps(
            {
                "scores": {
                    "idiomatic_phrasing": 4,
                    "register_appropriateness": 4,
                    "terminology_consistency": 4,
                },
                "issues": [
                    {
                        "unit_id": self.block.key.partition("#")[0],
                        "severity": "P2",
                        "message": f"{label.title()} naturalness finding.",
                        "evidence": self.block.source,
                    }
                ],
            }
        )


def test_workbench_review_given_lesson_calls_finish_out_of_order_expect_stable_finding_order(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    block = next(item for item in read_workbench_source(workspace, lesson_id).blocks if item.kind == "reading")
    changed = WorkbenchSettings.model_validate(
        {
            **default_workbench_settings(workspace).model_dump(mode="json"),
            "second_opinion": {"enabled": True, "model": "anthropic/claude-sonnet-5-5"},
        }
    )
    write_workbench_settings(workspace, changed)
    all_calls_started = Barrier(4, timeout=2)
    both_naturalness_finished = Event()
    naturalness_barrier = Barrier(2, action=both_naturalness_finished.set, timeout=2)
    second_pedagogy_finished = Event()
    completion_order: list[str] = []
    responder = _OutOfOrderWorkbenchReviewResponder(
        block,
        all_calls_started,
        naturalness_barrier,
        both_naturalness_finished,
        second_pedagogy_finished,
        completion_order,
    )
    client = FakeLlmClient.from_fn(responder)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    result = review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())

    assert completion_order.index("second-pedagogy") < completion_order.index("primary-pedagogy")
    assert [finding["explanation"] for finding in result["findings"]] == [
        "Primary pedagogy finding.: Revise the example.",
        "Primary naturalness finding.",
        "Second pedagogy finding.: Revise the example.",
        "Second naturalness finding.",
    ]
    assert result["summary"] == "Primary summary. Second opinion: Second summary."


def test_workbench_review_given_lesson_and_exercise_blocks_expect_provider_calls_overlap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    lesson_and_exercise_calls = Barrier(6, timeout=2)
    reached: list[str] = []

    def respond(prompt: str, model: str | None) -> str:
        if '"PedagogyReview"' in prompt:
            reached.append("pedagogy")
            lesson_and_exercise_calls.wait()
        elif '"NaturalnessReview"' in prompt:
            reached.append("naturalness")
            lesson_and_exercise_calls.wait()
        elif '"ObjectiveAlignmentReview"' in prompt:
            reached.append("exercise alignment")
            lesson_and_exercise_calls.wait()
        elif '"AnswerReview"' in prompt:
            reached.append("exercise answers")
            lesson_and_exercise_calls.wait()
        elif '"AttemptReview"' in prompt:
            reached.append("exercise attempt")
            lesson_and_exercise_calls.wait()
        elif '"StandaloneExerciseReview"' in prompt:
            reached.append("exercise standalone")
            lesson_and_exercise_calls.wait()
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    result = review_workbench_lesson(workspace, lesson_id, "lesson", None, RLock())

    assert result["scope"] == "lesson"
    assert set(reached) == {
        "pedagogy",
        "naturalness",
        "exercise alignment",
        "exercise answers",
        "exercise attempt",
        "exercise standalone",
    }


def test_reviewer_version_given_changed_review_call_module_expect_new_version(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_module = Path(workbench_review_operations.__file__)
    reviewer_root = tmp_path / "reviewer"
    reviewer_root.mkdir()
    module_names = (
        "workbench_review.py",
        "workbench_review_calls.py",
        "review_judges.py",
        "review_exercises.py",
        "review_standalone.py",
    )
    for module_name in module_names:
        shutil.copy2(source_module.with_name(module_name), reviewer_root / module_name)
    monkeypatch.setattr(workbench_review_operations, "__file__", str(reviewer_root / "workbench_review.py"))

    before = load_workbench_reviewer_version(workspace)
    review_calls_path = reviewer_root / "workbench_review_calls.py"
    review_calls_path.write_bytes(review_calls_path.read_bytes() + b"\n")

    assert load_workbench_reviewer_version(workspace) != before


def test_workbench_review_given_second_opinion_expect_at_most_six_calls_in_flight(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    changed = WorkbenchSettings.model_validate(
        {
            **default_workbench_settings(workspace).model_dump(mode="json"),
            "second_opinion": {"enabled": True, "model": "anthropic/claude-sonnet-5-5"},
        }
    )
    write_workbench_settings(workspace, changed)
    calls_lock = Lock()
    six_calls_started = Event()
    seventh_call_started = Event()
    release_calls = Event()
    active_calls = 0
    maximum_active_calls = 0

    def respond(prompt: str, model: str | None) -> str:
        nonlocal active_calls, maximum_active_calls
        with calls_lock:
            active_calls += 1
            maximum_active_calls = max(maximum_active_calls, active_calls)
            if active_calls == 6:
                six_calls_started.set()
            if active_calls == 7:
                seventh_call_started.set()
        try:
            release_calls.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
            return workbench_review_reply(prompt, model)
        finally:
            with calls_lock:
                active_calls -= 1

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    with ThreadPoolExecutor(max_workers=1) as executor:
        review = executor.submit(review_workbench_lesson, workspace, lesson_id, "lesson", None, RLock())
        try:
            assert six_calls_started.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
            assert not seventh_call_started.wait(timeout=0.2)
        finally:
            release_calls.set()
        result = review.result(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert result["scope"] == "lesson"
    assert maximum_active_calls == 6


def test_workbench_review_given_cancellation_during_concurrent_calls_expect_each_provider_cancelled(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    block = next(item for item in read_workbench_source(workspace, lesson_id).blocks if item.kind == "reading")
    cancellation = CancellationToken()
    both_calls_started = Barrier(2, timeout=2)
    cancellation_reached_calls = Event()
    observed_tokens: list[CancellationToken | None] = []

    def respond(prompt: str, model: str | None) -> str:
        both_calls_started.wait()
        token = active_cancellation_token()
        observed_tokens.append(token)
        cancellation_reached_calls.set()
        assert token is not None
        assert token.wait(timeout=2)
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    with ThreadPoolExecutor(max_workers=1) as executor:
        review = executor.submit(
            review_workbench_lesson,
            workspace,
            lesson_id,
            "item",
            block.key,
            RLock(),
            cancellation=cancellation,
        )
        assert cancellation_reached_calls.wait(timeout=2)
        cancellation.cancel("author", "cancel concurrent review")
        with pytest.raises(Cancelled, match="cancel concurrent review"):
            review.result(timeout=3)

    assert len(observed_tokens) == 2
    assert all(token is cancellation for token in observed_tokens)
    assert client.cancellation_signals == [cancellation, cancellation]


def test_workbench_review_given_one_concurrent_call_fails_expect_other_call_cancelled(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    block = next(item for item in read_workbench_source(workspace, lesson_id).blocks if item.kind == "reading")
    cancellation = CancellationToken()
    both_calls_started = Barrier(2, timeout=2)
    other_call_cancelled = Event()

    def respond(prompt: str, model: str | None) -> str:
        both_calls_started.wait()
        token = active_cancellation_token()
        assert token is cancellation
        if '"PedagogyReview"' in prompt:
            raise ValueError("pedagogy provider failed")
        assert token.wait(timeout=2)
        other_call_cancelled.set()
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    with pytest.raises(ValueError, match="pedagogy provider failed"):
        review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock(), cancellation=cancellation)

    assert cancellation.is_cancelled
    assert other_call_cancelled.is_set()
    assert len(client.cancellation_signals) == 2
    assert all(token is cancellation for token in client.cancellation_signals)


def test_current_review_findings_given_naturalness_advisories_expect_pane_entries_without_approval_gate(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    first, second, third = [block for block in source.blocks if block.kind == "reading"][:3]
    write_workbench_block(workspace, lesson_id, first.key, fields={"translation": "Updated translation"})
    source = read_workbench_source(workspace, lesson_id)
    client = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    review_workbench_lesson(workspace, lesson_id, "lesson", None, RLock())
    full_review = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog).runs[-1]
    selected = next(block for block in source.blocks if block.key == first.key)
    findings = [
        map_review_finding(source.blocks, "naturalness", block.fields["body"], f"Review {block.key}")
        for block in (selected, second, third)
    ]
    current_run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        findings=findings,
        block_hashes=full_review.block_hashes,
    )
    item_run = current_run.model_copy(
        update={
            "scope": "item",
            "key": selected.key,
            "block_hashes": {selected.key: block_content_hash(selected.source)},
            "findings": findings[1:],
        }
    )
    stale_run = current_run.model_copy(update={"digest": "stale-source", "block_hashes": {}, "findings": []})
    write_workbench_record(workspace, "reviews", lesson_id, ReviewLog(runs=[current_run, item_run, stale_run]))

    state = build_workbench_lesson_state(workspace, lesson_id, source, (), False)

    current_findings = [finding for finding in state.findings if finding.origin == "ai"]
    assert {finding.finding_id for finding in current_findings} == {finding.finding_id for finding in findings[1:]}
    assert all(finding.advisory for finding in current_findings)
    assert state.approve_blockers == ()


def test_review_findings_given_clean_scoped_rerun_expect_reviewed_findings_superseded_outside_findings_kept(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    first, outside = [block for block in source.blocks if block.kind == "reading"][:2]
    plan_sha256 = hashlib.sha256(source.files["plan.md"].encode()).hexdigest()
    digest = digest_workbench_source(source)
    reviewer_version = load_workbench_reviewer_version(workspace)
    reviewed_finding = map_review_finding(source.blocks, "naturalness", first.source, "Earlier wording issue")
    outside_finding = map_review_finding(source.blocks, "naturalness", outside.source, "Outside wording issue")
    previous_item_review = ReviewRunFactory(
        digest=digest,
        plan_sha256=plan_sha256,
        reviewer_version=reviewer_version,
        scope="item",
        key=first.key,
        block_hashes={first.key: block_content_hash(first.source)},
        findings=[reviewed_finding],
    )
    outside_item_review = previous_item_review.model_copy(
        update={
            "run_id": "outside-scope-review",
            "key": outside.key,
            "block_hashes": {outside.key: block_content_hash(outside.source)},
            "findings": [outside_finding],
        }
    )
    clean_item_review = previous_item_review.model_copy(update={"run_id": "clean-rerun", "findings": []})
    log = ReviewLog(runs=[previous_item_review, outside_item_review, clean_item_review])

    current = ReviewLedger(source, log).current_findings()

    assert [finding.finding_id for finding in current] == [outside_finding.finding_id]


def test_review_given_resolved_match_at_another_block_expect_finding_remains_open(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    first, second = [block for block in source.blocks if block.kind == "reading"][:2]
    write_workbench_block(workspace, lesson_id, second.key, fields={"body": first.fields["body"]})
    source = read_workbench_source(workspace, lesson_id)
    first = next(block for block in source.blocks if block.key == first.key)
    second = next(block for block in source.blocks if block.key == second.key)
    previous = map_review_finding(source.blocks, "bokmal", first.fields["body"], "Change this wording")
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        findings=[previous],
    )
    write_workbench_record(workspace, "reviews", lesson_id, ReviewLog(runs=[run]))
    resolve_workbench_findings(workspace, lesson_id, [previous.finding_id], "Correct in this block", reason="intended")

    def respond(prompt: str, model: str | None) -> str:
        if '"PedagogyReview"' in prompt:
            return json.dumps(
                {
                    "scores": dict.fromkeys(
                        ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"], 4
                    ),
                    "summary": "One issue.",
                    "passes": [],
                    "issues": [
                        {
                            "severity": "P2",
                            "category": "bokmal",
                            "title": "Change wording",
                            "evidence": second.fields["body"],
                            "fix": "Use natural wording",
                        }
                    ],
                }
            )
        return json.dumps(
            {
                "scores": {"idiomatic_phrasing": 4, "register_appropriateness": 4, "terminology_consistency": 4},
                "issues": [],
            }
        )

    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: FakeLlmClient.from_fn(respond)
    )
    review_workbench_lesson(workspace, lesson_id, "item", second.key, RLock())

    findings = list(read_workbench_record(workspace, "reviews", lesson_id, ReviewLog).runs[-1].findings)
    reviewed = next(finding for finding in findings if finding.key == second.key)
    assert reviewed.excerpt == first.fields["body"]
    assert reviewed.status == "open"


def test_resolve_findings_given_current_open_ids_expect_one_note_on_every_selected_finding(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    first, second = [block for block in source.blocks if block.kind == "reading"][:2]
    findings = [
        ReviewFinding(
            finding_id=f"finding-{index}",
            key=block.key,
            criterion="naturalness",
            excerpt=block.fields["body"],
            explanation="Improve wording",
            severity="minor",
        )
        for index, block in enumerate((first, second))
    ]
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        findings=findings,
    )
    write_workbench_record(workspace, "reviews", lesson_id, ReviewLog(runs=[run]))

    resolve_workbench_findings(
        workspace, lesson_id, [item.finding_id for item in findings], "Handled together", reason="intended"
    )

    settled = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog).runs[0].findings
    assert [(item.status, item.resolution_note) for item in settled] == [
        ("resolved", "Handled together"),
        ("resolved", "Handled together"),
    ]


def test_resolve_findings_given_one_id_not_current_open_expect_no_finding_resolved(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "reading")
    finding = ReviewFinding(
        finding_id="finding-current",
        key=block.key,
        criterion="naturalness",
        excerpt=block.fields["body"],
        explanation="Improve wording",
        severity="minor",
    )
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        findings=[finding],
    )
    write_workbench_record(workspace, "reviews", lesson_id, ReviewLog(runs=[run]))

    with pytest.raises(ValueError, match="finding not found"):
        resolve_workbench_findings(
            workspace, lesson_id, [finding.finding_id, "stale-or-missing"], "Reviewed", reason="intended"
        )

    current = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog).runs[0].findings[0]
    assert (current.status, current.resolution_note) == ("open", "")


def test_review_mapping_given_repeated_quote_and_named_section_expect_matching_section_block() -> None:
    blocks = (
        SourceBlock("first#0", "reading", "Hei!", 0, 4, {"body": "Hei!"}),
        SourceBlock("second#0", "reading", "Hei!", 4, 8, {"body": "Hei!"}),
    )
    first = map_review_finding(blocks, "naturalness", "Hei!", "Change greeting", unit="first")
    second = map_review_finding(blocks, "naturalness", "Hei!", "Change greeting", unit="second")
    assert [first.key, second.key] == ["first#0", "second#0"]
    assert first.finding_id != second.finding_id


def test_reviewer_version_given_changed_active_terminology_expect_new_version_and_current_prompt(
    workspace: Path,
) -> None:
    root = Path(__file__).parents[3]
    path = workspace / "content" / "terminology" / "glossary.yaml"
    path.parent.mkdir(parents=True)
    shutil.copy(root / "content" / "terminology" / "glossary.yaml", path)
    before = load_workbench_reviewer_version(workspace)
    path.write_text(path.read_text().replace("preferred_label: gender", "preferred_label: grammatical gender", 1))
    assert load_workbench_reviewer_version(workspace) != before
    assert "grammatical gender" in build_naturalness_prompt({}, terminology_path=path)


def test_accept_all_given_markdown_and_exercise_edits_expect_exact_draft_replacements_and_canonical_unchanged(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    proposal, _ = create_proposal(
        lesson_id,
        source,
        ChatResponse.model_validate(
            {
                "reply": "Clarify",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    },
                    {
                        "key": exercise.key,
                        "after_source": exercise.source.replace("Choose what", "Select what"),
                        "note": "prompt",
                    },
                ],
            }
        ),
        origin="chat",
    )
    assert proposal is not None
    proposal.check = check_proposal(workspace, lesson_id, source, proposal)
    assert proposal.check.status == "passed"
    store_proposal(workspace, lesson_id, proposal)
    settled = decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")
    updated = read_workbench_source(workspace, lesson_id)
    assert settled.status == "settled"
    assert all(block.status == "accepted" and block.check.status == "passed" for block in settled.blocks)
    assert "Hello: " in updated.files["lesson.md"]
    assert "Select what" in updated.files["exercises.yaml"]
    for name in ("lesson.md", "exercises.yaml"):
        assert (workspace / "content" / "lessons" / lesson_id / name).read_text() == source.files[name]


def test_exercise_review_given_selected_choose_item_expect_existing_verifiers_and_standalone_finding(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    block = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "exercise")

    client = FakeLlmClient.from_fn(lambda prompt, model: _exercise_review_response(prompt, block.key))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    result = review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())
    findings = result["findings"]
    assert any(finding["criterion"] == "standalone" and finding["key"] == block.key for finding in findings)
    assert len(client.calls) == 4
    assert all(model == "openai/gpt-6.1-sol" for _, model in client.calls)
    answer_prompt = next(prompt for prompt, _ in client.calls if '"AnswerReview"' in prompt)
    standalone_prompt = next(prompt for prompt, _ in client.calls if '"StandaloneExerciseReview"' in prompt)
    assert '"lesson_context"' in answer_prompt
    assert "a-first-meeting#" not in standalone_prompt


@pytest.mark.parametrize("recovers", [True, False])
def test_review_given_exercise_output_invalid_twice_expect_one_additional_reask_or_bounded_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, recovers: bool
) -> None:
    from tests.application.fakes import workbench_review_reply

    attempts = []

    def respond(prompt: str, model: str | None) -> str:
        if "AnswerReview" in prompt:
            attempts.append(prompt)
            if len(attempts) <= 2 or not recovers:
                return "Invalid reviewer output"
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    if recovers:
        run = review_workbench_lesson(workspace, "greet_and_introduce_yourself", "lesson", None, RLock())
        assert run["scope"] == "lesson"
        assert read_workbench_record(workspace, "reviews", "greet_and_introduce_yourself", ReviewLog).runs
    else:
        with pytest.raises(ValueError, match="exercises reviewer returned no valid report"):
            review_workbench_lesson(workspace, "greet_and_introduce_yourself", "lesson", None, RLock())
        assert not read_workbench_record(workspace, "reviews", "greet_and_introduce_yourself", ReviewLog).runs
    assert len(attempts) == 3
    assert "Your previous response to this exact task was not valid" in attempts[-1]
    assert "no JSON in response" in attempts[-1]


@pytest.mark.parametrize("legacy", [False, True])
def test_exercise_review_given_owner_resolution_expect_identity_and_decision_in_all_prompts(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "exercise")
    client = FakeLlmClient.from_fn(lambda prompt, model: _exercise_review_response(prompt, block.key))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    resolve_workbench_findings(workspace, lesson_id, [log.runs[-1].findings[0].finding_id], reason="not_applicable")
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    finding = log.runs[-1].findings[0]
    finding.resolution_comment = "x" * 500 + "OVERFLOW_SENTINEL"
    if legacy:
        finding.resolution_reason = None
    write_workbench_record(workspace, "reviews", lesson_id, log)
    first_call_count = len(client.calls)

    review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())

    state = build_workbench_lesson_state(workspace, lesson_id, source, (), False)
    projected = next(item for item in state.findings if item.finding_id == finding.finding_id)
    assert projected.resolution_reason == (None if legacy else "not_applicable")
    prompts = [prompt for prompt, _ in client.calls[first_call_count:]]
    assert len(prompts) >= 4
    label = "Legacy decision" if legacy else "Not applicable"
    assert all(f"Owner already decided: {label}" in prompt for prompt in prompts)
    assert all("x" * 500 in prompt and "OVERFLOW_SENTINEL" not in prompt for prompt in prompts)
    assert all(block.key in prompt and "issue_identity" in prompt for prompt in prompts)
    assert all("genuinely new problems" in prompt for prompt in prompts)


def test_review_log_given_legacy_resolution_note_expect_comment_without_invented_reason(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "reading")
    finding = map_review_finding(source.blocks, "naturalness", block.source, "Improve wording")
    run = ReviewRunFactory(findings=[finding])
    raw = ReviewLog(runs=[run]).model_dump(mode="json")
    legacy = raw["runs"][0]["findings"][0]
    legacy.pop("resolution_reason")
    legacy.pop("resolution_comment")
    legacy.update(status="resolved", resolution_note="Accepted legacy wording")
    path = workspace / "store/workbench/reviews" / f"{lesson_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw))

    loaded = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)

    assert loaded.runs[0].findings[0].resolution_comment == "Accepted legacy wording"
    assert loaded.runs[0].findings[0].resolution_reason is None


def _exercise_review_response(prompt: str, key: str) -> str:
    if '"ObjectiveAlignmentReview"' in prompt:
        return '{"passed":true,"summary":"Aligned.","issues":[]}'
    if '"AnswerReview"' in prompt:
        questions = json.JSONDecoder().raw_decode(prompt.split("EXERCISES:\n", 1)[1])[0]
        answer = next(option["option_id"] for option in questions[0]["options"] if "person's name" in option["text"])
        return json.dumps({"answers": [{"id": key, "answer": answer}]})
    if '"AttemptReview"' in prompt:
        return json.dumps({"checks": [{"id": key, "status": "clear"}]})
    if '"StandaloneExerciseReview"' in prompt:
        return json.dumps({"results": [{"id": key, "verdict": "context_dependent", "reason": "Clarify this task."}]})
    raise AssertionError("Unexpected reviewer surface")


@pytest.mark.parametrize("comment_field", ["comment", "note"])
def test_resolve_findings_given_oversized_comment_expect_validation_error(workspace: Path, comment_field: str) -> None:
    with pytest.raises(ValueError, match="Decision comment must be at most 500 characters"):
        resolve_workbench_findings(
            workspace,
            "greet_and_introduce_yourself",
            ["missing"],
            reason="intended",
            **{comment_field: "x" * 501},
        )


def test_proposal_decision_given_oversized_comment_expect_validation_error(workspace: Path) -> None:
    with pytest.raises(ValueError, match="Decision comment must be at most 500 characters"):
        decide_proposal(
            workspace,
            "greet_and_introduce_yourself",
            "missing",
            "keep",
            key="notice_name_question",
            reason="still_matches",
            comment="x" * 501,
        )
