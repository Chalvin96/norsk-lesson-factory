"""Behavior tests for promotion, rollback and canonical exercise comparisons."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from lesson_builder.application.operations.workbench_audio import plan_workbench_audio
from lesson_builder.application.operations.workbench_audio import synthesize_workbench_audio
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
from lesson_builder.application.operations.workbench_state import build_workbench_lesson_state
from lesson_builder.formats.workbench_source import split_lesson_source
from tests.application.fakes import FakeTtsClient
from tests.application.fakes import fake_audio_for_text


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return _lesson_workspace(tmp_path, "greet_and_introduce_yourself")


@pytest.fixture
def modal_verbs_workspace(tmp_path: Path) -> Path:
    return _lesson_workspace(tmp_path, "modal_verbs_in_context")


def test_promotion_given_canonical_source_without_draft_expect_no_draft_blocker_and_refusal(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    state = build_workbench_lesson_state(workspace, lesson_id, source, (), blocking_errors=False)

    assert any(blocker.kind == "no_draft" for blocker in state.promotion_blockers)
    with pytest.raises(ValueError, match="Promotion requires a draft"):
        promote_workbench_lesson(workspace, lesson_id)


def test_exercise_alignment_given_new_draft_base_expect_old_local_decision_ignored(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    canonical = workspace / "content/lessons" / lesson_id / "lesson.md"
    original = canonical.read_text()
    changed = original.replace("a small language school", "a language school", 1)
    write_workbench_file(workspace, lesson_id, "lesson.md", changed)
    alignment = ExerciseAlignment(workspace, lesson_id)
    alignment.keep_current(read_workbench_source(workspace, lesson_id), "notice_name_question", reason="still_matches")
    shutil.rmtree(workspace / "drafts" / lesson_id)
    canonical.write_text(original.replace("a small language school", "a large language school", 1))
    write_workbench_file(workspace, lesson_id, "lesson.md", changed)

    states = alignment.states(read_workbench_source(workspace, lesson_id))

    exercise = next(state for state in states if state.exercise_key == "notice_name_question")
    assert isinstance(exercise, ExerciseStale)
    assert len(exercise.history) == 1
    assert exercise.history[0].reason == "still_matches"
    assert "large" in exercise.changed_sections[0].before_text


def test_exercise_alignment_given_unbound_legacy_decision_expect_history_without_reusing_pin(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    original = read_workbench_source(workspace, lesson_id)
    write_workbench_file(
        workspace,
        lesson_id,
        "lesson.md",
        original.files["lesson.md"].replace("a small language school", "a language school", 1),
    )
    alignment = ExerciseAlignment(workspace, lesson_id)
    source = read_workbench_source(workspace, lesson_id)
    alignment.keep_current(source, "notice_name_question", reason="still_matches", comment="The same question applies.")
    path = workspace / "store/workbench/exercise_alignments" / f"{lesson_id}.json"
    legacy = json.loads(path.read_text())
    legacy["records"][0].pop("canonical_sha256")
    path.write_text(json.dumps(legacy))

    states = alignment.states(source)

    exercise = next(state for state in states if state.exercise_key == "notice_name_question")
    assert isinstance(exercise, ExerciseStale)
    assert len(exercise.history) == 1
    assert exercise.history[0].comment == "The same question applies."
    assert exercise.alignment.outcome == "canonical"


@pytest.mark.parametrize("decision", ["current", "legacy", "recreated"])
def test_exercise_alignment_given_never_promoted_draft_expect_only_current_decision_applied(
    workspace: Path, decision: str
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_lesson(workspace)
    shutil.rmtree(workspace / "content/lessons" / lesson_id)
    metadata_path = workspace / "drafts" / lesson_id / "draft.yaml"
    metadata = yaml.safe_load(metadata_path.read_bytes())
    metadata.update(started_from_sha256=None, created_at="2026-01-01T00:00:00+00:00")
    metadata_path.write_text(yaml.safe_dump(metadata))
    alignment = ExerciseAlignment(workspace, lesson_id)
    captured = alignment.capture(read_workbench_source(workspace, lesson_id), ["notice_name_question"])
    alignment.pin(captured["notice_name_question"], "regenerated")
    if decision == "legacy":
        path = workspace / "store/workbench/exercise_alignments" / f"{lesson_id}.json"
        legacy = json.loads(path.read_text())
        legacy["records"][0].pop("canonical_sha256")
        legacy["records"][0].pop("draft_created_at", None)
        path.write_text(json.dumps(legacy))
    elif decision == "recreated":
        metadata["created_at"] = "2026-01-02T00:00:00+00:00"
        metadata_path.write_text(yaml.safe_dump(metadata))

    states = alignment.states(read_workbench_source(workspace, lesson_id))

    exercise = next(state for state in states if state.exercise_key == "notice_name_question")
    assert len(exercise.history) == 1
    assert isinstance(exercise, ExerciseAligned if decision == "current" else ExerciseUnknown)


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


def test_stale_exercises_given_unparseable_exercises_yaml_expect_unknown_with_parse_reason(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    package = workspace / "content/lessons" / lesson_id
    (package / "exercises.yaml").write_text("- id: broken\n  prompt_md: [\n", encoding="utf-8")

    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))

    unknown = next(state for state in states if isinstance(state, ExerciseUnknown))
    assert "exercises.yaml could not be parsed" in unknown.reason


def test_stale_exercise_given_one_word_section_change_expect_concise_english_summary(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
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


def test_stale_exercises_given_hand_edited_exercise_expect_stale_until_kept_with_note(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
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


@pytest.mark.parametrize("brand_new", [False, True])
def test_promote_given_valid_draft_without_review_expect_canonical_dist_commit_and_deleted_draft(
    workspace: Path, brand_new: bool
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_lesson(workspace)
    if brand_new:
        shutil.rmtree(workspace / "content/lessons" / lesson_id)
        metadata = workspace / "drafts" / lesson_id / "draft.yaml"
        raw = yaml.safe_load(metadata.read_bytes())
        raw["started_from_sha256"] = None
        metadata.write_text(yaml.safe_dump(raw))
    _seed_complete_workbench_audio(workspace, lesson_id)
    expected = read_workbench_source(workspace, lesson_id).files["lesson.md"]
    result = promote_workbench_lesson(workspace, lesson_id)
    assert not (workspace / "drafts" / lesson_id).exists()
    assert (workspace / "content/lessons" / lesson_id / "lesson.md").read_text() == expected
    assert f"dist/lessons/{lesson_id}.json" in result["dist_files"]
    assert _git(workspace, "log", "-1", "--format=%s").strip() == f"workbench: promote {lesson_id}"
    assert "Hello:" in (workspace / "dist/lessons" / f"{lesson_id}.json").read_text()


def test_promote_given_another_planned_lesson_exists_only_as_draft_expect_only_canonical_lessons_exported(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    draft_only_id = "draft_only_lesson"
    _edit_lesson(workspace)
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
    _seed_complete_workbench_audio(workspace, lesson_id)

    result = promote_workbench_lesson(workspace, lesson_id)

    assert f"dist/lessons/{lesson_id}.json" in result["dist_files"]
    assert not (workspace / "dist/lessons" / f"{draft_only_id}.json").exists()
    catalog = json.loads((workspace / "dist/catalog.json").read_text())
    assert draft_only_id not in {entry["lesson_id"] for entry in catalog["lessons"]}


def test_promote_given_canonical_changed_expect_conflict_diff_and_pull_proposal(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_lesson(workspace)
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


def test_promote_given_missing_audio_expect_projected_blocker_and_action_refusal(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_lesson(workspace)

    from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
    from lesson_builder.web.workbench_api import build_lesson_payload

    payload = build_lesson_payload(workspace, resolve_lesson_package(workspace, lesson_id))
    audio_blocker = next(blocker for blocker in payload["state"]["promotionBlockers"] if blocker["kind"] == "audio")

    assert audio_blocker["missing"] > 0
    assert payload["state"]["promotable"] is False
    with pytest.raises(ValueError, match="Promotion blocked by current lesson gate"):
        promote_workbench_lesson(workspace, lesson_id)


def test_pull_canonical_given_draft_matches_changed_canonical_expect_provenance_updated_without_proposal(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_lesson(workspace)
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
    _edit_lesson(workspace)
    before = (workspace / "content/lessons" / lesson_id / "lesson.md").read_bytes()
    with pytest.raises(ValueError, match="confirmation"):
        discard_workbench_draft(workspace, lesson_id, confirmed=False)
    commit = discard_workbench_draft(workspace, lesson_id, confirmed=True)
    assert not (workspace / "drafts" / lesson_id).exists()
    assert (workspace / "content/lessons" / lesson_id / "lesson.md").read_bytes() == before
    assert _git(workspace, "show", "--format=%s", "--no-patch", commit).strip() == f"workbench: discard {lesson_id}"


def test_discard_given_only_autosaved_draft_expect_deletion_commit_without_prior_save(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_lesson(workspace)
    before = _git(workspace, "rev-parse", "HEAD").strip()
    commit = discard_workbench_draft(workspace, lesson_id, confirmed=True)
    assert commit != before
    assert not (workspace / "drafts" / lesson_id).exists()
    assert (workspace / "content/lessons" / lesson_id).is_dir()


def test_promote_given_export_failure_expect_complete_file_and_index_rollback(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    _edit_lesson(workspace)
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


def _lesson_workspace(tmp_path: Path, lesson_id: str) -> Path:
    """Copy one canonical lesson, authoring support into a git workspace."""
    root = Path(__file__).parents[3]
    for relative in (
        f"content/lessons/{lesson_id}",
        "content/authoring",
        "content/terminology",
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


def _seed_complete_workbench_audio(root: Path, lesson_id: str) -> None:
    """Fill every candidate through the fake seam before promotion behavior tests."""
    plan = plan_workbench_audio(root, lesson_id, "missing_and_stale")
    client = FakeTtsClient.from_fn(fake_audio_for_text)
    synthesize_workbench_audio(root, plan.token, synthesis_client=client)


def _edit_lesson(root: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(root, lesson_id)
    write_workbench_file(
        root, lesson_id, "lesson.md", source.files["lesson.md"].replace('translation="', 'translation="Hello: ', 1)
    )


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout
