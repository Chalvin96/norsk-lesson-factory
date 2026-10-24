"""Entry point: approval chain, evidence, promotion and rollback behavior tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from _thread import RLock
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

import lesson_builder.application.operations.workbench_approval as workbench_approval
from lesson_builder.application.operations.workbench_approval import approve_workbench_lesson
from lesson_builder.application.operations.workbench_approval import derive_stage_statuses
from lesson_builder.application.operations.workbench_audio import plan_workbench_audio
from lesson_builder.application.operations.workbench_audio import synthesize_workbench_audio
from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_editing import write_workbench_block
from lesson_builder.application.operations.workbench_editing import write_workbench_file
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAligned
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseStale
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseUnknown
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseUntracked
from lesson_builder.application.operations.workbench_promotion import CanonicalChanged
from lesson_builder.application.operations.workbench_promotion import canonical_source_digest
from lesson_builder.application.operations.workbench_promotion import discard_workbench_draft
from lesson_builder.application.operations.workbench_promotion import promote_workbench_lesson
from lesson_builder.application.operations.workbench_promotion import pull_canonical_proposal
from lesson_builder.application.operations.workbench_proposals import decide_proposal
from lesson_builder.application.operations.workbench_proposals import digest_workbench_source
from lesson_builder.application.operations.workbench_records import CurriculumBaseline
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentLog
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentRecord
from lesson_builder.application.operations.workbench_records import ReviewCoverage
from lesson_builder.application.operations.workbench_records import ReviewLog
from lesson_builder.application.operations.workbench_records import ReviewRun
from lesson_builder.application.operations.workbench_records import StageApprovalRecord
from lesson_builder.application.operations.workbench_review import block_content_hash
from lesson_builder.application.operations.workbench_review import load_workbench_reviewer_version
from lesson_builder.application.operations.workbench_review import review_workbench_lesson
from lesson_builder.application.operations.workbench_state import build_workbench_lesson_state
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_LEGACY_SLOT_INDEX_LIMIT
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_REVIEW_EXERCISE_PLACEMENT_TARGET
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_REVIEW_METADATA_TARGET
from lesson_builder.formats.workbench_source import split_lesson_source
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.application.factories import ReviewRunFactory
from tests.application.factories import StageApprovalRecordFactory
from tests.application.fakes import FakeTtsClient
from tests.application.fakes import fake_audio_for_text


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return _lesson_workspace(tmp_path, "greet_and_introduce_yourself")


@pytest.fixture
def modal_verbs_workspace(tmp_path: Path) -> Path:
    return _lesson_workspace(tmp_path, "modal_verbs_in_context")


def _lesson_workspace(tmp_path: Path, lesson_id: str) -> Path:
    """Copy one canonical lesson, its approvals and authoring support into a git workspace."""
    root = Path(__file__).parents[3]
    for relative in (
        f"content/lessons/{lesson_id}",
        f"content/approvals/lessons/{lesson_id}",
        "content/authoring",
        "content/terminology",
        "content/approvals/workbench",
        "content/catalog",
        ".opencode/agents",
    ):
        shutil.copytree(root / relative, tmp_path / relative)
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    plan = yaml.safe_load((root / "content/curriculum/plan.yaml").read_bytes())
    plan["slots"] = [slot for slot in plan["slots"] if slot["catalog_id"] == lesson_id]
    target = tmp_path / "content/curriculum/plan.yaml"
    target.parent.mkdir()
    target.write_text(yaml.safe_dump(plan, allow_unicode=True))
    _git(tmp_path, "init", "-b", "authoring")
    _git(tmp_path, "config", "user.name", "Test Author")
    _git(tmp_path, "config", "user.email", "author@example.test")
    _git(tmp_path, "add", "content", "config.yaml", ".opencode")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def test_approval_chain_given_baseline_then_lesson_edit_expect_lesson_needs_approval_and_exercises_stale(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    assert [stage.status for stage in derive_stage_statuses(modal_verbs_workspace, lesson_id, source)] == [
        "baseline"
    ] * 3
    write_workbench_file(
        modal_verbs_workspace,
        lesson_id,
        "lesson.md",
        source.files["lesson.md"].replace('translation="', 'translation="Hello: ', 1),
    )
    updated = read_workbench_source(modal_verbs_workspace, lesson_id)
    stages = derive_stage_statuses(modal_verbs_workspace, lesson_id, updated)
    assert [stage.status for stage in stages] == ["baseline", "needs_approval", "stale"]
    state = build_workbench_lesson_state(modal_verbs_workspace, lesson_id, updated, (), blocking_errors=False)
    assert {
        (blocker.kind, blocker.stage) for blocker in state.approve_blockers if blocker.kind == "review_coverage"
    } == {("review_coverage", "lesson"), ("review_coverage", "exercises")}
    assert "Hello:" in stages[2].upstream_diff


def test_approval_status_given_stage_configuration_reversed_expect_named_dependencies_remain_stable(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    expected = {item.stage: item for item in derive_stage_statuses(workspace, lesson_id, source)}
    monkeypatch.setattr(
        workbench_approval,
        "K_APPLICATION_WORKBENCH_STAGES",
        tuple(reversed(workbench_approval.K_APPLICATION_WORKBENCH_STAGES)),
    )

    actual = {item.stage: item for item in derive_stage_statuses(workspace, lesson_id, source)}

    assert actual == expected


def test_approval_state_given_canonical_source_without_draft_expect_review_coverage_blockers(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    state = build_workbench_lesson_state(modal_verbs_workspace, lesson_id, source, (), blocking_errors=False)

    assert not source.exists
    assert state.review_coverage.covered == 0
    assert state.review_coverage.total > 0
    assert {blocker.stage for blocker in state.approve_blockers if blocker.kind == "review_coverage"} == {
        "lesson",
        "exercises",
    }


def test_promotion_given_canonical_source_without_draft_expect_no_draft_blocker_and_refusal(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    state = build_workbench_lesson_state(workspace, lesson_id, source, (), blocking_errors=False)

    assert any(blocker.kind == "no_draft" for blocker in state.promotion_blockers)
    with pytest.raises(ValueError, match="Promotion requires a draft"):
        promote_workbench_lesson(workspace, lesson_id)


def _stale_exercise_keys(workspace: Path, lesson_id: str, source: WorkbenchSource | None = None) -> tuple[str, ...]:
    """Return only exercises whose stored section pins differ from current source."""
    source = source or read_workbench_source(workspace, lesson_id)
    return tuple(
        state.exercise_key
        for state in ExerciseAlignment(workspace, lesson_id).states(source)
        if isinstance(state, ExerciseStale)
    )


def _stale_section_summary(workspace: Path, lesson_id: str, exercise_key: str) -> str:
    """Return the first changed-section summary reported for one stale exercise."""
    state = next(
        item
        for item in ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))
        if isinstance(item, ExerciseStale) and item.exercise_key == exercise_key
    )
    return state.changed_sections[0].summary


def _summary_for_section_snapshot(workspace: Path, lesson_id: str, before: str, after: str) -> str:
    """Compare current source with one explicitly captured section version."""
    section_id = "a-weekend-in-telemark"
    section_before = _lesson_section_source(before, section_id)
    current_source = read_workbench_source(workspace, lesson_id)
    current_source.files["lesson.md"] = before
    alignment = ExerciseAlignment(workspace, lesson_id)
    pin = alignment.capture(current_source, ["review_distinguish_action_and_evidence_readings"])[
        "review_distinguish_action_and_evidence_readings"
    ]
    assert pin["section_sources"][section_id] == section_before
    write_workbench_file(workspace, lesson_id, "lesson.md", after)
    alignment.pin(pin, "kept", reason="still_matches")
    return _stale_section_summary(workspace, lesson_id, "review_distinguish_action_and_evidence_readings")


def _lesson_section_source(text: str, section_id: str) -> str:
    """Return one exact heading and body span from lesson source."""
    blocks = split_lesson_source(text).blocks
    start = next(index for index, block in enumerate(blocks) if block.kind == "heading" and block.key == section_id)
    end = next(
        (index for index in range(start + 1, len(blocks)) if blocks[index].kind == "heading"),
        len(blocks),
    )
    return "".join(block.source for block in blocks[start:end])


def test_stale_exercises_given_changed_derived_section_expect_matching_exercise_selected(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    original = read_workbench_source(workspace, lesson_id)
    changed_lesson = original.files["lesson.md"].replace(
        "Two new students arrive early", "Two students arrive early", 1
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))

    assert stale_keys == ("notice_name_question",)


def test_stale_exercises_given_unchanged_derived_section_expect_no_exercises_selected(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))

    assert stale_keys == ()


def test_exercise_staleness_given_repeated_source_expect_consistent_projection(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)

    first = ExerciseAlignment(workspace, lesson_id).states(source)
    repeated = ExerciseAlignment(workspace, lesson_id).states(source)

    assert first
    assert repeated == first


def test_stale_exercises_given_hand_edited_empty_derived_from_expect_original_alignment_remains_stale(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    original = read_workbench_source(workspace, lesson_id)
    changed_exercises = original.files["exercises.yaml"].replace(
        "  derived_from:\n  - section_id: a-first-meeting\n", "  derived_from: []\n", 1
    )
    changed_lesson = original.files["lesson.md"].replace(
        "Two new students arrive early", "Two students arrive early", 1
    )
    write_workbench_file(workspace, lesson_id, "exercises.yaml", changed_exercises)
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))

    assert stale_keys == ("notice_name_question",)


def test_exercise_alignment_given_empty_derived_from_without_section_change_expect_untracked(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    original = read_workbench_source(workspace, lesson_id)
    changed_exercises = original.files["exercises.yaml"].replace(
        "  derived_from:\n  - section_id: a-first-meeting\n", "  derived_from: []\n", 1
    )
    write_workbench_file(workspace, lesson_id, "exercises.yaml", changed_exercises)

    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))

    assert any(
        isinstance(state, ExerciseUntracked) and state.exercise_key == "notice_name_question" for state in states
    )


def test_exercise_alignment_history_given_multiple_decisions_expect_newest_first(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    older = ExerciseAlignmentRecord(
        exercise_key="notice_name_question",
        checked_against={"a-first-meeting": "older"},
        outcome="approved",
        note="Reviewed against the original wording.",
        at="2026-10-11T09:00:00+00:00",
    )
    newer = ExerciseAlignmentRecord(
        exercise_key="notice_name_question",
        checked_against={"a-first-meeting": "newer"},
        outcome="kept",
        note="The question still asks for the same fact.",
        at="2026-10-13T12:00:00+00:00",
    )
    write_workbench_record(workspace, "exercise_alignments", lesson_id, ExerciseAlignmentLog(records=[older, newer]))

    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))
    state = next(item for item in states if item.exercise_key == "notice_name_question")

    assert [item.at for item in state.history] == [newer.at, older.at]


def test_stale_exercises_given_removed_derived_section_expect_exercise_selected(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    original = read_workbench_source(workspace, lesson_id)
    changed_lesson = original.files["lesson.md"].replace("{#a-first-meeting ", "{#renamed-first-meeting ", 1)
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))

    assert stale_keys == ("notice_name_question",)


def test_stale_exercises_given_hand_edited_reference_block_expect_exercise_stays_stale(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    original = read_workbench_source(workspace, lesson_id)
    changed_exercises = original.files["exercises.yaml"].replace(
        "  - section_id: a-first-meeting\n", "  - section_id: new-section\n", 1
    )
    changed_lesson = (
        original.files["lesson.md"]
        + "\n## New section {#new-section role=recap objectives=obj-greet-and-introduce-yourself-01}\n\nNew context.\n"
    )
    write_workbench_file(workspace, lesson_id, "exercises.yaml", changed_exercises)
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))

    assert stale_keys == ("notice_name_question",)


def test_stale_exercises_given_no_approved_snapshot_expect_alignment_unknown(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    original = read_workbench_source(workspace, lesson_id)
    shutil.rmtree(workspace / "content/approvals/lessons" / lesson_id)
    shutil.rmtree(workspace / "content/approvals/workbench" / lesson_id, ignore_errors=True)
    changed_lesson = original.files["lesson.md"].replace(
        "Two new students arrive early", "Two students arrive early", 1
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)

    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))

    assert any(isinstance(state, ExerciseUnknown) for state in states)


def test_stale_exercises_given_unparseable_exercises_yaml_expect_unknown_with_parse_reason(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    package = workspace / "content/lessons" / lesson_id
    (package / "exercises.yaml").write_text("- id: broken\n  prompt_md: [\n", encoding="utf-8")

    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))

    unknown = next(state for state in states if isinstance(state, ExerciseUnknown))
    assert "exercises.yaml could not be parsed" in unknown.reason


def test_exercise_stage_approval_given_all_exercises_expect_complete_alignment_pins(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    record_path = next((workspace / "content/approvals/workbench" / lesson_id / "exercises").glob("*.approval.yaml"))
    record = StageApprovalRecord.model_validate(yaml.safe_load(record_path.read_bytes()))
    source = read_workbench_source(workspace, lesson_id)
    exercise_keys = {block.key for block in source.blocks if block.kind == "exercise"}

    assert set(record.exercise_alignments) == exercise_keys
    assert all(item.outcome == "approved" for item in record.exercise_alignments.values())
    assert record.exercise_alignments["notice_name_question"].checked_against
    assert record.exercise_alignments["match_line_to_job"].checked_against


def test_stale_exercises_given_workbench_lesson_approval_expect_only_changes_since_approval(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)

    approved_source = read_workbench_source(workspace, lesson_id)
    assert _stale_exercise_keys(workspace, lesson_id, approved_source) == ()

    changed_lesson = approved_source.files["lesson.md"].replace(
        "A first meeting can be short and still feel complete:",
        "This first meeting can be short and still feel complete:",
        1,
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))

    assert stale_keys == ("match_line_to_job",)


def test_stale_exercise_given_one_word_section_change_expect_concise_english_summary(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    source = read_workbench_source(workspace, lesson_id)
    changed_lesson = source.files["lesson.md"].replace("a small language school", "a language school", 1)
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)

    state = next(
        item
        for item in ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))
        if isinstance(item, ExerciseStale) and item.exercise_key == "notice_name_question"
    )

    assert state.changed_sections[0].summary == "Removed “small” from section a-first-meeting."


def test_stale_exercise_summary_given_dialogue_body_word_edit_expect_visible_word_pair_only(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    changed_lesson = source.files["lesson.md"].replace("er ganske gjørmete nå.", "er ganske bratt nå.")
    write_workbench_file(modal_verbs_workspace, lesson_id, "lesson.md", changed_lesson)

    summary = _stale_section_summary(
        modal_verbs_workspace, lesson_id, "review_distinguish_action_and_evidence_readings"
    )

    assert summary == "Changed “gjørmete” to “bratt” in section a-weekend-in-telemark."


def test_stale_exercise_summary_given_speaker_name_change_expect_name_change_summary(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    changed_lesson = source.files["lesson.md"].replace(
        'rather muddy right now." dialogue_id=office speaker_id=thomas speaker_name=Thomas}',
        'rather muddy right now." dialogue_id=office speaker_id=thomas speaker_name=Mari}',
    )
    write_workbench_file(modal_verbs_workspace, lesson_id, "lesson.md", changed_lesson)

    summary = _stale_section_summary(
        modal_verbs_workspace, lesson_id, "review_distinguish_action_and_evidence_readings"
    )

    assert summary == "Changed “Thomas” to “Mari” in section a-weekend-in-telemark."


def test_stale_exercise_summary_given_translation_gloss_edit_expect_word_change_summary(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    changed_lesson = source.files["lesson.md"].replace("rather muddy right now.", "rather muddy just now.")
    write_workbench_file(modal_verbs_workspace, lesson_id, "lesson.md", changed_lesson)

    summary = _stale_section_summary(
        modal_verbs_workspace, lesson_id, "review_distinguish_action_and_evidence_readings"
    )

    assert summary == "Changed “right” to “just” in section a-weekend-in-telemark."


def test_stale_exercise_summary_given_bracketed_language_span_change_expect_plain_word_pair(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    before = source.files["lesson.md"].replace("er ganske gjørmete nå.", "er [kan]{lang=nb} gjørmete nå.")
    after = before.replace("[kan]{lang=nb}", "[må]{lang=nb}")

    summary = _summary_for_section_snapshot(modal_verbs_workspace, lesson_id, before, after)

    assert summary == "Changed “kan” to “må” in section a-weekend-in-telemark."


@pytest.mark.parametrize("emphasis", ["**_ganske_**", "_**ganske**_"])
def test_stale_exercise_summary_given_nested_emphasis_change_expect_wording_unchanged(
    modal_verbs_workspace: Path, emphasis: str
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    before = source.files["lesson.md"]
    after = before.replace("er ganske gjørmete nå.", f"er {emphasis} gjørmete nå.")

    summary = _summary_for_section_snapshot(modal_verbs_workspace, lesson_id, before, after)

    assert summary == "Section a-weekend-in-telemark formatting changed; wording is unchanged."


def test_stale_exercises_given_unreachable_approval_commit_expect_stored_snapshot_selection(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    record_path = next((workspace / "content/approvals/workbench" / lesson_id / "lesson").glob("*.approval.yaml"))
    record = StageApprovalRecord.model_validate(yaml.safe_load(record_path.read_bytes()))
    invalidated = record.model_copy(update={"code_commit": "f" * len(record.code_commit)})
    record_path.write_text(yaml.safe_dump(invalidated.model_dump(mode="json"), allow_unicode=True, sort_keys=False))
    changed_lesson = (
        read_workbench_source(workspace, lesson_id)
        .files["lesson.md"]
        .replace(
            "A first meeting can be short and still feel complete:",
            "This first meeting can be short and still feel complete:",
            1,
        )
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))

    assert stale_keys == ("match_line_to_job",)


def test_stale_exercises_given_hand_edited_exercise_expect_stale_until_kept_with_note(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    source = read_workbench_source(workspace, lesson_id)
    changed_lesson = source.files["lesson.md"].replace(
        "A first meeting can be short and still feel complete:",
        "This first meeting can be short and still feel complete:",
        1,
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)
    changed_source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in changed_source.blocks if block.key == "match_line_to_job")
    item = yaml.safe_load(exercise.source)[0]
    item["prompt_md"] += " Use the new example."
    write_workbench_block(
        workspace,
        lesson_id,
        exercise.key,
        source=yaml.safe_dump([item], allow_unicode=True, sort_keys=False),
    )

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))
    assert stale_keys == ("match_line_to_job",)

    record = ExerciseAlignment(workspace, lesson_id).keep_current(
        read_workbench_source(workspace, lesson_id),
        "match_line_to_job",
        "The exercise still teaches the same line matching task.",
        reason="still_matches",
    )
    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))

    aligned = next(state for state in states if state.exercise_key == "match_line_to_job")
    assert record.outcome == "kept"
    assert record.note == "The exercise still teaches the same line matching task."
    assert aligned.status == "aligned"
    assert aligned.history[-1] == record


@pytest.mark.parametrize("comment", [None, "The exercise still fits.", "x" * 500])
def test_kept_reason_given_exercise_approval_expect_committed_reason_and_optional_comment(
    workspace: Path, comment: str | None
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    source = read_workbench_source(workspace, lesson_id)
    changed_lesson = source.files["lesson.md"].replace(
        "A first meeting can be short and still feel complete:",
        "This first meeting can be short and still feel complete:",
        1,
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)
    changed_source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in changed_source.blocks if block.key == "match_line_to_job")
    item = yaml.safe_load(exercise.source)[0]
    item["prompt_md"] += " Use the new example."
    write_workbench_block(
        workspace,
        lesson_id,
        exercise.key,
        source=yaml.safe_dump([item], allow_unicode=True, sort_keys=False),
    )

    stale_keys = _stale_exercise_keys(workspace, lesson_id, read_workbench_source(workspace, lesson_id))
    assert stale_keys == ("match_line_to_job",)

    record = ExerciseAlignment(workspace, lesson_id).keep_current(
        read_workbench_source(workspace, lesson_id),
        "match_line_to_job",
        "",
        reason="still_matches",
        comment=comment,
    )
    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))

    aligned = next(state for state in states if state.exercise_key == "match_line_to_job")
    assert record.outcome == "kept"
    assert record.reason == "still_matches"
    assert record.comment == comment
    assert aligned.status == "aligned"
    assert aligned.history[-1] == record

    with patch(
        "lesson_builder.application.operations.workbench_review_calls.workbench_runner_for_role", return_value=None
    ):
        review_workbench_lesson(workspace, lesson_id, "lesson", None, RLock())
    approve_workbench_lesson(workspace, lesson_id)
    records = list((workspace / "content/approvals/workbench" / lesson_id / "exercises").glob("*.approval.yaml"))
    latest = max(
        (StageApprovalRecord.model_validate(yaml.safe_load(path.read_bytes())) for path in records),
        key=lambda item: item.approved_at,
    )
    assert latest.exercise_alignments["match_line_to_job"].reason == "still_matches"
    assert latest.exercise_alignments["match_line_to_job"].comment == comment


def test_lesson_state_given_approved_snapshot_deleted_expect_unavailable_status_chip(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    record_path = next((workspace / "content/approvals/workbench" / lesson_id / "lesson").glob("*.approval.yaml"))
    record = StageApprovalRecord.model_validate(yaml.safe_load(record_path.read_bytes()))
    snapshot = workspace / "content/approvals/workbench" / lesson_id / "lesson" / f"{record.file_sha256}.md"
    snapshot.unlink(missing_ok=True)

    state = build_workbench_lesson_state(workspace, lesson_id, read_workbench_source(workspace, lesson_id), (), False)

    assert any(chip.label == "approved snapshot unavailable" for chip in state.status_chips)


def test_approval_given_plan_edit_expect_complete_cascade_and_no_plan_review_blocker(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    write_workbench_file(
        modal_verbs_workspace, lesson_id, "plan.md", source.files["plan.md"] + "\nOwner design note.\n"
    )
    stages = derive_stage_statuses(
        modal_verbs_workspace, lesson_id, read_workbench_source(modal_verbs_workspace, lesson_id)
    )
    assert [stage.status for stage in stages] == ["needs_approval", "stale", "stale"]
    state = build_workbench_lesson_state(
        modal_verbs_workspace, lesson_id, read_workbench_source(modal_verbs_workspace, lesson_id), (), False
    )
    assert not any(blocker.stage == "plan" for blocker in state.approve_blockers)


def test_approve_given_current_full_review_expect_hook_commit_versioned_records_and_deduplicated_evidence(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    code_commit = _git(workspace, "rev-parse", "HEAD").strip()
    hook = workspace / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nprintf ran >> hook-ran\n")
    hook.chmod(0o755)
    result = approve_workbench_lesson(workspace, lesson_id)
    records = [
        StageApprovalRecord.model_validate(yaml.safe_load(path.read_bytes()))
        for path in (workspace / "content/approvals/workbench" / lesson_id).glob("*/*.approval.yaml")
    ]
    assert result["stages"] == ["lesson", "exercises"]
    assert (workspace / "hook-ran").read_text() == "ran"
    assert {record.schema_version for record in records} == {2}
    assert {record.reviewer for record in records} == {"human"}
    evidence = list((workspace / "content/approvals/evidence").glob("*.json"))
    assert len(evidence) == 1
    assert evidence[0].stem == hashlib.sha256(evidence[0].read_bytes()).hexdigest()
    assert "Owner acceptance is not qualified Norwegian review." in evidence[0].read_text()
    lesson_record = next(record for record in records if record.stage == "lesson")
    assert lesson_record.code_commit == code_commit
    approval_files = _git(workspace, "show", "--format=", "--name-only", str(result["commit"]))
    assert f"drafts/{lesson_id}/lesson.md" in approval_files
    assert f"content/approvals/workbench/{lesson_id}/lesson/{lesson_record.file_sha256}.md" in approval_files
    assert f"content/approvals/workbench/{lesson_id}/exercises/" in approval_files
    assert "content/approvals/evidence/" in approval_files
    assert _git(workspace, "rev-list", "--count", "HEAD").strip() == "2"
    assert [
        stage.status
        for stage in derive_stage_statuses(workspace, lesson_id, read_workbench_source(workspace, lesson_id))
    ] == ["baseline", "approved", "approved"]


def test_approval_evidence_given_late_old_plan_review_expect_only_current_plan_review_recorded(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    current_run = _store_late_old_plan_review(workspace, lesson_id)

    approve_workbench_lesson(workspace, lesson_id)

    records = [
        StageApprovalRecord.model_validate(yaml.safe_load(path.read_bytes()))
        for path in (workspace / "content/approvals/workbench" / lesson_id).glob("*/*.approval.yaml")
    ]
    evidence = json.loads(next((workspace / "content/approvals/evidence").glob("*.json")).read_bytes())
    lesson_record = next(record for record in records if record.stage == "lesson")
    assert [run["run_id"] for run in evidence["review_runs"]] == [current_run.run_id]
    assert lesson_record.review is not None
    assert lesson_record.review.model == "current-reviewer"
    assert lesson_record.review.prompt_version == "current-prompt"


def test_approval_evidence_given_exercise_only_review_expect_exercise_model_recorded_for_exercises(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    exercise_run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        reviewer_version=load_workbench_reviewer_version(workspace),
        scope="item",
        key=exercise.key,
        model=None,
        exercise_model="exercise-only-reviewer",
        prompt_version="exercise-only-prompt",
        criteria_version="exercise-only-criteria",
        block_hashes={exercise.key: block_content_hash(exercise.source)},
    )
    log_path = workspace / "store/workbench/reviews" / f"{lesson_id}.json"
    log = ReviewLog.model_validate_json(log_path.read_bytes())
    log.runs[0].model = "lesson-reviewer"
    log.runs.append(exercise_run)
    write_workbench_record(workspace, "reviews", lesson_id, log)

    approve_workbench_lesson(workspace, lesson_id)

    records = [
        StageApprovalRecord.model_validate(yaml.safe_load(path.read_bytes()))
        for path in (workspace / "content/approvals/workbench" / lesson_id).glob("*/*.approval.yaml")
    ]
    lesson_record = next(record for record in records if record.stage == "lesson")
    exercise_record = next(record for record in records if record.stage == "exercises")
    assert lesson_record.review is not None
    assert exercise_record.review is not None
    assert lesson_record.review.model == "lesson-reviewer"
    assert exercise_record.review.model == "exercise-only-reviewer"


def _store_late_old_plan_review(root: Path, lesson_id: str) -> ReviewRun:
    old_source = read_workbench_source(root, lesson_id)
    old_run = _review_run_for_source(root, lesson_id, "old-reviewer", "old-prompt", "old-criteria")
    write_workbench_file(root, lesson_id, "plan.md", old_source.files["plan.md"] + "\nOwner design note.\n")
    current_source = read_workbench_source(root, lesson_id)
    current_run = _review_run_for_source(root, lesson_id, "current-reviewer", "current-prompt", "current-criteria")
    log = ReviewLog(
        runs=[current_run, old_run],
        coverage=_review_coverage_records(current_run, current_source) + _review_coverage_records(old_run, old_source),
    )
    write_workbench_record(root, "reviews", lesson_id, log)
    return current_run


def _review_run_for_source(
    root: Path, lesson_id: str, model: str, prompt_version: str, criteria_version: str
) -> ReviewRun:
    with patch(
        "lesson_builder.application.operations.workbench_review_calls.workbench_runner_for_role", return_value=None
    ):
        result = review_workbench_lesson(root, lesson_id, "lesson", None, RLock())
    return ReviewRun.model_validate(result).model_copy(
        update={"model": model, "prompt_version": prompt_version, "criteria_version": criteria_version}
    )


def _review_coverage_records(run: ReviewRun, source: WorkbenchSource) -> list[ReviewCoverage]:
    stages = {
        K_APPLICATION_WORKBENCH_REVIEW_METADATA_TARGET: "lesson",
        K_APPLICATION_WORKBENCH_REVIEW_EXERCISE_PLACEMENT_TARGET: "exercises",
        **{block.key: "exercises" if block.kind == "exercise" else "lesson" for block in source.blocks},
    }
    return [
        ReviewCoverage(
            stage=stages[block_id],
            block_id=block_id,
            block_hash=block_hash,
            review_run_id=run.run_id,
            reviewer=run.model or "workbench_reviewer",
            at=run.created_at,
        )
        for block_id, block_hash in run.block_hashes.items()
    ]


def test_approval_blockers_given_open_language_review_finding_expect_no_finding_blocker(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    source = read_workbench_source(workspace, lesson_id)
    log = ReviewLog.model_validate_json((workspace / "store/workbench/reviews" / f"{lesson_id}.json").read_bytes())
    key = next(block.key for block in source.blocks if block.kind == "reading")
    from lesson_builder.application.operations.workbench_review_calls import map_review_finding

    log.runs[-1].findings = [
        map_review_finding(
            source.blocks,
            "naturalness",
            next(block.source for block in source.blocks if block.key == key),
            "Fix phrasing",
        )
    ]
    write_workbench_record(workspace, "reviews", lesson_id, log)

    approved = approve_workbench_lesson(workspace, lesson_id)

    assert approved["stages"] == ["lesson", "exercises"]


def test_approval_blockers_given_deterministic_check_error_expect_lesson_stage_blocked(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    write_workbench_file(workspace, lesson_id, "exercises.yaml", "- invalid: true\n")
    state = build_workbench_lesson_state(workspace, lesson_id, read_workbench_source(workspace, lesson_id), (), True)

    assert any(blocker.kind == "check_errors" for blocker in state.approve_blockers)
    with pytest.raises(ValueError, match="Approval blocked by current lesson gate"):
        approve_workbench_lesson(workspace, lesson_id)


@pytest.mark.parametrize("has_placeholder", [False, True])
def test_approve_given_unassigned_family_expect_plan_approval_without_family_gate(
    modal_verbs_workspace: Path, has_placeholder: bool
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    _, frontmatter, body = source.files["plan.md"].split("---", 2)
    plan = yaml.safe_load(frontmatter)
    plan["family_id"] = ""
    write_workbench_file(modal_verbs_workspace, lesson_id, "plan.md", "---\n" + yaml.safe_dump(plan) + "---" + body)
    curriculum_path = modal_verbs_workspace / "content/curriculum/plan.yaml"
    curriculum = yaml.safe_load(curriculum_path.read_text())
    curriculum["slots"][0]["family_id"] = ""
    if has_placeholder:
        curriculum["slots"][0]["provisional_flags"] = [
            "Choose a family by learner outcome from catalog_families.yaml in the catalog registry before approving the plan."
        ]
    curriculum_path.write_text(yaml.safe_dump(curriculum))

    result = approve_workbench_lesson(modal_verbs_workspace, lesson_id, stages=["plan"])

    assert result["stages"] == ["plan"]
    stages = derive_stage_statuses(
        modal_verbs_workspace, lesson_id, read_workbench_source(modal_verbs_workspace, lesson_id)
    )
    assert next(stage.status for stage in stages if stage.stage == "plan") == "approved"


def test_approve_given_requested_plan_stage_only_expect_plan_record_while_content_stages_wait(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    write_workbench_file(
        modal_verbs_workspace, lesson_id, "plan.md", source.files["plan.md"] + "\nOwner design note.\n"
    )

    result = approve_workbench_lesson(modal_verbs_workspace, lesson_id, stages=["plan"])

    assert result["stages"] == ["plan"]
    records_root = modal_verbs_workspace / "content/approvals/workbench" / lesson_id
    assert [path.parent.name for path in records_root.glob("*/*.approval.yaml")] == ["plan"]
    assert not (modal_verbs_workspace / "content/approvals/evidence").exists()
    assert [
        stage.status
        for stage in derive_stage_statuses(
            modal_verbs_workspace, lesson_id, read_workbench_source(modal_verbs_workspace, lesson_id)
        )
    ] == ["approved", "stale", "stale"]


def test_approve_given_requested_stage_with_coverage_blocker_expect_refusal_and_no_commit(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    source = read_workbench_source(modal_verbs_workspace, lesson_id)
    write_workbench_file(
        modal_verbs_workspace, lesson_id, "plan.md", source.files["plan.md"] + "\nOwner design note.\n"
    )
    head_before = _git(modal_verbs_workspace, "rev-list", "--count", "HEAD").strip()

    with pytest.raises(ValueError, match="Approval blocked by current lesson gate"):
        approve_workbench_lesson(modal_verbs_workspace, lesson_id, stages=["lesson"])

    assert _git(modal_verbs_workspace, "rev-list", "--count", "HEAD").strip() == head_before
    assert not (modal_verbs_workspace / "content/approvals/workbench" / lesson_id).exists()


@pytest.mark.parametrize(
    ("stages", "message"),
    [
        ([], "Approval requires at least one stage"),
        (["plan", "chore"], "Unknown approval stage: chore"),
        (["plan"], "plan does not need approval"),
    ],
)
def test_approve_given_invalid_stage_request_expect_value_error(
    workspace: Path, stages: list[str], message: str
) -> None:
    lesson_id = "greet_and_introduce_yourself"

    with pytest.raises(ValueError, match=message):
        approve_workbench_lesson(workspace, lesson_id, stages=stages)  # type: ignore[arg-type]


def test_approve_given_requested_stage_without_draft_expect_draft_required_error(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    plan_path = modal_verbs_workspace / "content/curriculum/plan.yaml"
    raw = yaml.safe_load(plan_path.read_bytes())
    raw["slots"][0]["learner_outcome"] += " Updated owner intent."
    plan_path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="Approval requires a draft"):
        approve_workbench_lesson(modal_verbs_workspace, lesson_id, stages=["plan"])


def test_approve_given_hook_failure_expect_records_removed_draft_retained_and_index_restored(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    before = read_workbench_source(workspace, lesson_id).files
    records_before = sorted((workspace / "content/approvals/workbench" / lesson_id).glob("**/*"))
    hook = workspace / ".git/hooks/pre-commit"
    hook.write_text(f"#!/bin/sh\nsed -i 's/$/ /' drafts/{lesson_id}/lesson.md\nexit 1\n")
    hook.chmod(0o755)
    with pytest.raises(ValueError):
        approve_workbench_lesson(workspace, lesson_id)
    assert sorted((workspace / "content/approvals/workbench" / lesson_id).glob("**/*")) == records_before
    assert read_workbench_source(workspace, lesson_id).files == before
    assert _git(workspace, "diff", "--cached", "--name-only") == ""


@pytest.mark.parametrize("brand_new", [False, True])
def test_promote_given_fresh_approved_draft_expect_canonical_dist_commit_and_deleted_draft(
    workspace: Path, brand_new: bool
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    if brand_new:
        shutil.rmtree(workspace / "content/lessons" / lesson_id)
        metadata = workspace / "drafts" / lesson_id / "draft.yaml"
        raw = yaml.safe_load(metadata.read_bytes())
        raw["started_from_sha256"] = None
        metadata.write_text(yaml.safe_dump(raw))
    approve_workbench_lesson(workspace, lesson_id)
    _seed_complete_workbench_audio(workspace, lesson_id)
    expected = read_workbench_source(workspace, lesson_id).files["lesson.md"]
    result = promote_workbench_lesson(workspace, lesson_id)
    assert not (workspace / "drafts" / lesson_id).exists()
    assert (workspace / "content/lessons" / lesson_id / "lesson.md").read_text() == expected
    assert f"dist/lessons/{lesson_id}.json" in result["dist_files"]
    assert _git(workspace, "log", "-1", "--format=%s").strip() == f"workbench: promote {lesson_id}"
    assert "Hello:" in (workspace / "dist/lessons" / f"{lesson_id}.json").read_text()
    assert all(
        stage.status in ("baseline", "approved")
        for stage in derive_stage_statuses(workspace, lesson_id, read_workbench_source(workspace, lesson_id))
    )


def test_promote_given_another_planned_lesson_exists_only_as_draft_expect_only_canonical_lessons_exported(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    draft_only_id = "draft_only_lesson"
    _edit_and_review(workspace)
    plan_path = workspace / "content/curriculum/plan.yaml"
    plan = yaml.safe_load(plan_path.read_bytes())
    draft_only_slot = dict(plan["slots"][0])
    draft_only_slot["catalog_id"] = draft_only_id
    draft_only_slot["sequence_index"] = 1
    plan["slots"].append(draft_only_slot)
    plan_path.write_text(yaml.safe_dump(plan, allow_unicode=True))
    draft_only = workspace / "drafts" / draft_only_id
    shutil.copytree(workspace / "content/lessons" / lesson_id, draft_only)
    (draft_only / "draft.yaml").write_text(
        yaml.safe_dump(
            {"lesson_id": draft_only_id, "started_from_sha256": None, "created_at": "2026-01-01T00:00:00+00:00"}
        )
    )
    _git(workspace, "add", "content/curriculum/plan.yaml", f"drafts/{draft_only_id}")
    _git(workspace, "commit", "-m", "add another workbench draft")
    shutil.rmtree(workspace / "content/lessons" / lesson_id)
    metadata = workspace / "drafts" / lesson_id / "draft.yaml"
    raw = yaml.safe_load(metadata.read_bytes())
    raw["started_from_sha256"] = None
    metadata.write_text(yaml.safe_dump(raw))
    approve_workbench_lesson(workspace, lesson_id)
    _seed_complete_workbench_audio(workspace, lesson_id)

    result = promote_workbench_lesson(workspace, lesson_id)

    assert f"dist/lessons/{lesson_id}.json" in result["dist_files"]
    assert not (workspace / "dist/lessons" / f"{draft_only_id}.json").exists()
    catalog = json.loads((workspace / "dist/catalog.json").read_text())
    assert draft_only_id not in {entry["lesson_id"] for entry in catalog["lessons"]}


def test_promote_given_canonical_changed_expect_conflict_diff_and_pull_proposal(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    _seed_complete_workbench_audio(workspace, lesson_id)
    canonical = workspace / "content/lessons" / lesson_id / "lesson.md"
    canonical.write_text(canonical.read_text().replace('translation="', 'translation="Canonical: ', 1))
    before = canonical.read_bytes()
    state = build_workbench_lesson_state(workspace, lesson_id, read_workbench_source(workspace, lesson_id), (), False)
    assert any(blocker.kind == "canonical_changed" for blocker in state.promotion_blockers)
    with pytest.raises(CanonicalChanged, match="canonical_changed") as caught:
        promote_workbench_lesson(workspace, lesson_id)
    assert "Canonical:" in caught.value.diff
    proposal = pull_canonical_proposal(workspace, lesson_id)
    assert proposal.source == "canonical"
    assert read_workbench_source(workspace, lesson_id).files["lesson.md"] != canonical.read_text()
    decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")
    assert canonical.read_bytes() == before
    assert read_workbench_source(workspace, lesson_id).files["lesson.md"] == canonical.read_text()
    assert (
        derive_stage_statuses(workspace, lesson_id, read_workbench_source(workspace, lesson_id))[1].status
        == "needs_approval"
    )


def test_promote_given_missing_audio_expect_projected_blocker_and_action_refusal(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)

    from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
    from lesson_builder.web.workbench_api import build_lesson_payload

    payload = build_lesson_payload(workspace, resolve_lesson_package(workspace, lesson_id))
    audio_blocker = next(blocker for blocker in payload["state"]["promotionBlockers"] if blocker["kind"] == "audio")

    assert audio_blocker["missing"] > 0
    assert payload["state"]["promotable"] is False
    with pytest.raises(ValueError, match="Promotion blocked by current lesson gate"):
        promote_workbench_lesson(workspace, lesson_id)


def test_curriculum_approval_given_helpful_prerequisite_reorder_expect_baseline_retained_after_sequence_change(
    workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    repository = Path(__file__).parents[3]
    shutil.copytree(repository / "content/lessons" / lesson_id, workspace / "content/lessons" / lesson_id)
    shutil.copytree(
        repository / "content/approvals/lessons" / lesson_id,
        workspace / "content/approvals/lessons" / lesson_id,
    )
    plan_path = workspace / "content/curriculum/plan.yaml"
    workspace_plan = yaml.safe_load(plan_path.read_bytes())
    repository_plan = yaml.safe_load((repository / "content/curriculum/plan.yaml").read_bytes())
    workspace_plan["slots"].append(next(slot for slot in repository_plan["slots"] if slot["catalog_id"] == lesson_id))
    plan_path.write_text(yaml.safe_dump(workspace_plan, allow_unicode=True))
    plan = read_curriculum(workspace)
    slot = next(slot for slot in plan.slots if slot.catalog_id == lesson_id)
    write_workbench_record(
        workspace, "curriculum", "baseline", CurriculumBaseline(slots={lesson_id: slot.model_dump_json()})
    )
    source = read_workbench_source(workspace, lesson_id)
    reordered = slot.model_copy(update={"helpful_prerequisites": list(reversed(slot.helpful_prerequisites))})
    reordered_plan = plan.model_copy(
        update={"slots": [reordered if item.catalog_id == lesson_id else item for item in plan.slots]}
    )

    stages = derive_stage_statuses(workspace, lesson_id, source, plan=reordered_plan)

    assert [stage.status for stage in stages] == ["baseline"] * 3
    sequence_changed = reordered.model_copy(update={"sequence_index": reordered.sequence_index + 1})
    changed_plan = reordered_plan.model_copy(
        update={"slots": [sequence_changed if item.catalog_id == lesson_id else item for item in reordered_plan.slots]}
    )
    assert [stage.status for stage in derive_stage_statuses(workspace, lesson_id, source, plan=changed_plan)] == [
        "baseline"
    ] * 3


def test_pull_canonical_given_draft_matches_changed_canonical_expect_provenance_updated_without_proposal(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    source = read_workbench_source(workspace, lesson_id)
    canonical = workspace / "content/lessons" / lesson_id
    for name, text in source.files.items():
        (canonical / name).write_text(text)
    draft_metadata = workspace / "drafts" / lesson_id / "draft.yaml"
    started_from_before = yaml.safe_load(draft_metadata.read_bytes())["started_from_sha256"]

    with pytest.raises(CanonicalChanged, match="canonical_changed"):
        promote_workbench_lesson(workspace, lesson_id)
    result = pull_canonical_proposal(workspace, lesson_id)

    assert result is None
    assert yaml.safe_load(draft_metadata.read_bytes())["started_from_sha256"] == canonical_source_digest(
        workspace, lesson_id
    )
    assert yaml.safe_load(draft_metadata.read_bytes())["started_from_sha256"] != started_from_before
    assert not (workspace / "store/workbench/proposals" / f"{lesson_id}.json").exists()


def test_discard_given_confirmation_expect_one_deletion_commit_and_canonical_retained(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    before = (workspace / "content/lessons" / lesson_id / "lesson.md").read_bytes()
    with pytest.raises(ValueError, match="confirmation"):
        discard_workbench_draft(workspace, lesson_id, confirmed=False)
    commit = discard_workbench_draft(workspace, lesson_id, confirmed=True)
    assert not (workspace / "drafts" / lesson_id).exists()
    assert (workspace / "content/lessons" / lesson_id / "lesson.md").read_bytes() == before
    assert _git(workspace, "show", "--format=%s", "--no-patch", commit).strip() == f"workbench: discard {lesson_id}"


def test_discard_given_only_autosaved_draft_expect_deletion_commit_without_prior_save(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    before = _git(workspace, "rev-parse", "HEAD").strip()
    commit = discard_workbench_draft(workspace, lesson_id, confirmed=True)
    assert commit != before
    assert not (workspace / "drafts" / lesson_id).exists()
    assert (workspace / "content/lessons" / lesson_id).is_dir()


def test_promote_given_export_failure_expect_complete_file_and_index_rollback(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    before = read_workbench_source(workspace, lesson_id).files
    canonical = (workspace / "content/lessons" / lesson_id / "lesson.md").read_bytes()
    dist = workspace / "dist/marker.json"
    dist.parent.mkdir()
    dist.write_text('{"before": true}\n')
    _git(workspace, "add", "dist")
    _git(workspace, "commit", "-m", "distribution")
    head = _git(workspace, "rev-parse", "HEAD")
    _seed_complete_workbench_audio(workspace, lesson_id)

    def fail_export(root: Path, *, exclude_draft_only: bool = False) -> None:
        assert exclude_draft_only
        (root / "dist/marker.json").write_text("broken")
        raise ValueError("export rejected")

    monkeypatch.setattr("lesson_builder.application.operations.workbench_promotion.export_distribution", fail_export)
    with pytest.raises(ValueError, match="export rejected"):
        promote_workbench_lesson(workspace, lesson_id)
    assert read_workbench_source(workspace, lesson_id).files == before
    assert (workspace / "content/lessons" / lesson_id / "lesson.md").read_bytes() == canonical
    assert dist.read_text() == '{"before": true}\n'
    assert _git(workspace, "rev-parse", "HEAD") == head
    assert _git(workspace, "diff", "--cached", "--name-only") == ""


def _seed_complete_workbench_audio(root: Path, lesson_id: str) -> None:
    """Fill every candidate through the fake seam before promotion behavior tests."""
    plan = plan_workbench_audio(root, lesson_id, "missing_and_stale")
    client = FakeTtsClient.from_fn(fake_audio_for_text)
    synthesize_workbench_audio(root, plan.token, synthesis_client=client)


def test_stage_status_given_unmatched_slot_digest_expect_bounded_legacy_digest_search(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    record = StageApprovalRecordFactory.build(
        lesson_id=lesson_id,
        stage="plan",
        file_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        upstream_approved_sha256=None,
        curriculum_slot_sha256="0" * 64,
    )
    record_path = workspace / "content/approvals/workbench" / lesson_id / "plan" / f"{record.file_sha256}.approval.yaml"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(yaml.safe_dump(record.model_dump(mode="json"), allow_unicode=True), encoding="utf-8")
    with (
        patch.object(workbench_approval.hashlib, "sha256", wraps=hashlib.sha256) as digests,
        patch.object(workbench_approval.json, "dumps", wraps=json.dumps) as canonicalizations,
    ):
        stages = derive_stage_statuses(workspace, lesson_id, source)

    assert [stage.status for stage in stages] == ["stale"] * 3
    assert digests.call_count >= K_APPLICATION_WORKBENCH_LEGACY_SLOT_INDEX_LIMIT
    assert digests.call_count <= K_APPLICATION_WORKBENCH_LEGACY_SLOT_INDEX_LIMIT + 16
    assert canonicalizations.call_count <= 8


def test_baseline_given_direct_curriculum_reorder_and_lost_store_expect_fresh_chain(
    modal_verbs_workspace: Path,
) -> None:
    lesson_id = "modal_verbs_in_context"
    plan = modal_verbs_workspace / "content/curriculum/plan.yaml"
    raw = yaml.safe_load(plan.read_bytes())
    raw["slots"][0]["sequence_index"] += 10
    plan.write_text(yaml.safe_dump(raw))
    stages = derive_stage_statuses(
        modal_verbs_workspace, lesson_id, read_workbench_source(modal_verbs_workspace, lesson_id)
    )
    assert [stage.status for stage in stages] == ["baseline"] * 3
    assert not (modal_verbs_workspace / "store").exists()


def test_approve_given_clean_scoped_review_expect_history_retained_and_superseded_finding_excluded(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    source = read_workbench_source(workspace, lesson_id)
    log = ReviewLog.model_validate_json((workspace / "store/workbench/reviews" / f"{lesson_id}.json").read_bytes())
    from lesson_builder.application.operations.workbench_review_calls import map_review_finding

    reading = next(block for block in source.blocks if block.kind == "reading")
    original = map_review_finding(source.blocks, "naturalness", reading.source, "Improve wording")
    log.runs[0].findings = [original]
    log.runs.append(
        log.runs[0].model_copy(
            update={"scope": "item", "key": reading.key, "findings": [], "summary": "Scoped rerun cleared finding"}
        )
    )
    write_workbench_record(workspace, "reviews", lesson_id, log)
    approve_workbench_lesson(workspace, lesson_id)
    evidence = json.loads(next((workspace / "content/approvals/evidence").glob("*.json")).read_bytes())
    assert evidence["review_runs"][0]["findings"][0]["explanation"] == "Improve wording"
    assert any(run["summary"] == "Scoped rerun cleared finding" for run in evidence["review_runs"])
    assert evidence["owner_decisions"] == []


def test_accept_canonical_pull_given_provenance_write_failure_expect_source_and_proposal_restored_for_retry(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    canonical = workspace / "content/lessons" / lesson_id / "lesson.md"
    canonical.write_text(canonical.read_text().replace('translation="', 'translation="Canonical: ', 1))
    proposal = pull_canonical_proposal(workspace, lesson_id)
    draft_before = read_workbench_source(workspace, lesson_id).files
    draft_yaml = workspace / "drafts" / lesson_id / "draft.yaml"
    provenance_before = draft_yaml.read_bytes()

    def fail_provenance(path: Path, text: str) -> None:
        raise OSError("provenance write failed")

    monkeypatch.setattr(
        "lesson_builder.application.operations.workbench_proposals.write_text_atomically", fail_provenance
    )
    with pytest.raises(OSError, match="provenance write failed"):
        decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")
    assert read_workbench_source(workspace, lesson_id).files == draft_before
    assert draft_yaml.read_bytes() == provenance_before

    monkeypatch.undo()
    decided = decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")

    assert decided.status == "settled"
    assert read_workbench_source(workspace, lesson_id).files["lesson.md"] == canonical.read_text()
    assert yaml.safe_load(draft_yaml.read_bytes())["started_from_sha256"] == proposal.canonical_sha256


def test_approval_evidence_given_resolved_reason_expect_reason_and_comment_committed(workspace: Path) -> None:
    from lesson_builder.application.operations.workbench_review import resolve_workbench_findings
    from lesson_builder.application.operations.workbench_review_calls import map_review_finding

    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    source = read_workbench_source(workspace, lesson_id)
    block = next(item for item in source.blocks if item.kind == "reading")
    finding = map_review_finding(source.blocks, "naturalness", block.source, "Improve wording")
    log = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)
    log.runs[-1].findings = [finding]
    write_workbench_record(workspace, "reviews", lesson_id, log)
    resolve_workbench_findings(
        workspace, lesson_id, [finding.finding_id], reason="reviewer_wrong", comment="The wording is intentional."
    )

    approve_workbench_lesson(workspace, lesson_id)

    evidence = json.loads(next((workspace / "content/approvals/evidence").glob("*.json")).read_bytes())
    decision = evidence["owner_decisions"][0]
    assert decision["resolution_reason"] == "reviewer_wrong"
    assert decision["resolution_comment"] == "The wording is intentional."


def test_approval_record_given_legacy_keep_note_expect_comment_without_invented_reason(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_and_review(workspace)
    approve_workbench_lesson(workspace, lesson_id)
    path = next((workspace / "content/approvals/workbench" / lesson_id / "exercises").glob("*.approval.yaml"))
    raw = yaml.safe_load(path.read_bytes())
    pin = raw["exercise_alignments"]["match_line_to_job"]
    pin.pop("reason", None)
    pin.pop("comment", None)
    pin["note"] = "Still fits the section."
    path.write_text(yaml.safe_dump(raw))

    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))

    state = next(item for item in states if item.exercise_key == "match_line_to_job")
    assert state.alignment.comment == "Still fits the section."
    assert state.alignment.reason is None


def _edit_and_review(root: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(root, lesson_id)
    write_workbench_file(
        root, lesson_id, "lesson.md", source.files["lesson.md"].replace('translation="', 'translation="Hello: ', 1)
    )
    with patch(
        "lesson_builder.application.operations.workbench_review_calls.workbench_runner_for_role", return_value=None
    ):
        review_workbench_lesson(root, lesson_id, "lesson", None, RLock())


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


def test_alignment_state_given_captured_pin_then_section_edit_expect_stale_summary(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    alignment = ExerciseAlignment(workspace, lesson_id)
    pin = alignment.capture(source, ["notice_name_question"])["notice_name_question"]
    alignment.pin(pin, "kept", note="The exercise remains aligned to the captured section.", reason="still_matches")
    changed_lesson = source.files["lesson.md"].replace("Two new students arrive early", "Two students arrive early", 1)

    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)
    state = next(
        item
        for item in alignment.states(read_workbench_source(workspace, lesson_id))
        if item.exercise_key == "notice_name_question"
    )

    assert isinstance(state, ExerciseStale)
    assert state.changed_sections[0].summary == "Removed “new” from section a-first-meeting."


def test_alignment_state_given_captured_pin_is_persisted_expect_exercise_aligned(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    alignment = ExerciseAlignment(workspace, lesson_id)
    pin = alignment.capture(source, ["notice_name_question"])["notice_name_question"]

    alignment.pin(pin, "regenerated", note="Captured the exact section version.")
    state = next(
        item
        for item in alignment.states(read_workbench_source(workspace, lesson_id))
        if item.exercise_key == "notice_name_question"
    )

    assert isinstance(state, ExerciseAligned)
    assert state.alignment.checked_against == pin["checked_against"]


def test_alignment_history_given_duplicate_decision_id_expect_one_record(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    alignment = ExerciseAlignment(workspace, lesson_id)
    pin = alignment.capture(source, ["notice_name_question"])["notice_name_question"]

    alignment.pin(pin, "kept", note="First attempt.", decision_id="run-1:notice_name_question", reason="still_matches")
    alignment.pin(
        pin, "kept", note="Replayed decision.", decision_id="run-1:notice_name_question", reason="still_matches"
    )
    log = read_workbench_record(workspace, "exercise_alignments", lesson_id, ExerciseAlignmentLog)
    decisions = [item for item in log.records if item.decision_id == "run-1:notice_name_question"]

    assert len(decisions) == 1
    assert decisions[0].note == "Replayed decision."


def test_exercise_alignment_given_approved_snapshot_with_unloadable_operation_expect_pins_from_snapshot(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    shutil.rmtree(workspace / "content/approvals/workbench" / lesson_id)
    source = read_workbench_source(workspace, lesson_id)

    states = {state.exercise_key: state for state in ExerciseAlignment(workspace, lesson_id).states(source)}

    assert isinstance(states["begin_the_meeting"], ExerciseAligned)
    assert all(isinstance(state, ExerciseAligned) for state in states.values())


@pytest.mark.parametrize("boundary", ["pin", "keep_current"])
def test_alignment_decision_given_oversized_comment_expect_validation_error(workspace: Path, boundary: str) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    alignment = ExerciseAlignment(workspace, lesson_id)
    with pytest.raises(ValueError, match="Decision comment must be at most 500 characters"):
        if boundary == "pin":
            pin = alignment.capture(source, ["notice_name_question"])["notice_name_question"]
            alignment.pin(pin, "kept", reason="still_matches", comment="x" * 501)
        else:
            alignment.keep_current(source, "notice_name_question", reason="still_matches", comment="x" * 501)
