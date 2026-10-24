"""Entry points: curriculum edit preview, save, and chat proposal behavior tests."""

from __future__ import annotations

import json
import subprocess
from _thread import RLock
from pathlib import Path

import pytest
from pydantic import ValidationError

from lesson_builder.application.operations.release import run_release
from lesson_builder.application.operations.workbench_chat import begin_workbench_chat_turn
from lesson_builder.application.operations.workbench_chat import run_workbench_chat_turn
from lesson_builder.application.operations.workbench_curriculum import find_stale_curriculum_entries
from lesson_builder.application.operations.workbench_curriculum import preview_workbench_curriculum_edits
from lesson_builder.application.operations.workbench_curriculum import read_curriculum
from lesson_builder.application.operations.workbench_curriculum import save_workbench_curriculum_edits
from lesson_builder.application.operations.workbench_records import ChatTranscript
from lesson_builder.application.operations.workbench_records import CurriculumBaseline
from lesson_builder.domain.curriculum.models import CurriculumAddEdit
from lesson_builder.domain.curriculum.models import CurriculumEdit
from lesson_builder.domain.curriculum.models import CurriculumFindingDecision
from lesson_builder.domain.curriculum.models import CurriculumMergeEdit
from lesson_builder.domain.curriculum.models import CurriculumRemoveEdit
from lesson_builder.domain.curriculum.models import CurriculumReorderEdit
from lesson_builder.domain.curriculum.models import CurriculumSplitEdit
from lesson_builder.domain.curriculum.models import CurriculumSplitPart
from lesson_builder.domain.curriculum.models import CurriculumUpdateEdit
from lesson_builder.workspace.workbench_git import WorkbenchGitError
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.clients.codex.fakes import FakeCodexAppServer
from tests.clients.codex.fakes import FakeCodexTurn
from tests.workspace.builders import build_workbench_curriculum_repository


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return build_workbench_curriculum_repository(tmp_path)


def test_curriculum_reorder_preview_given_full_permutation_expect_authored_order(workspace: Path) -> None:
    plan = read_curriculum(workspace)
    preview = preview_workbench_curriculum_edits(
        workspace,
        [CurriculumReorderEdit(kind="reorder", lesson_ids=["second", "greet_and_introduce_yourself"])],
    )
    assert [slot.catalog_id for slot in preview.entries] == ["second", "greet_and_introduce_yourself"]
    assert len(preview.affected_lessons) == 2
    assert [slot.catalog_id for slot in plan.slots] == ["greet_and_introduce_yourself", "second"]


def test_curriculum_add_preview_given_manual_entry_expect_unplanned_slot_without_package(workspace: Path) -> None:
    preview = preview_workbench_curriculum_edits(
        workspace,
        [
            CurriculumAddEdit(
                kind="add",
                entry={
                    "catalog_id": "weather_talk",
                    "title": "Talk about weather",
                    "learner_outcome": "Describe the weather in a short exchange.",
                    "cefr_level": "A1",
                    "prerequisites": ["greet_and_introduce_yourself"],
                },
                after_lesson_id="greet_and_introduce_yourself",
            )
        ],
    )
    assert [slot.catalog_id for slot in preview.entries] == [
        "greet_and_introduce_yourself",
        "weather_talk",
        "second",
    ]
    assert preview.affected_lessons[-1].effect == "new_entry"
    assert preview.entries[1].cefr_source == "planner_default"


def test_curriculum_add_preview_given_null_position_expect_entry_at_beginning(workspace: Path) -> None:
    preview = preview_workbench_curriculum_edits(
        workspace,
        [
            CurriculumAddEdit(
                kind="add",
                entry={
                    "catalog_id": "weather_talk",
                    "title": "Talk about weather",
                    "learner_outcome": "Describe the weather in a short exchange.",
                },
                after_lesson_id=None,
            )
        ],
    )

    assert [slot.catalog_id for slot in preview.entries] == [
        "weather_talk",
        "greet_and_introduce_yourself",
        "second",
    ]


def test_curriculum_remove_preview_given_required_dependent_expect_located_finding(workspace: Path) -> None:
    preview = preview_workbench_curriculum_edits(
        workspace,
        [CurriculumRemoveEdit(kind="remove", lesson_ids=["greet_and_introduce_yourself"])],
    )
    assert [slot.catalog_id for slot in preview.entries] == ["second"]
    assert preview.affected_lessons[0].effect == "unlisted"
    assert preview.dependency_findings[0].lesson_id == "second"
    assert preview.dependency_findings[0].code == "curriculum.prerequisite_missing"


def test_curriculum_remove_preview_given_draft_deletion_expect_review_discloses_package_removal(
    workspace: Path,
) -> None:
    draft = workspace / "drafts" / "second"
    draft.mkdir(parents=True)
    (draft / "plan.md").write_text("# Draft second\n", encoding="utf-8")
    _git(workspace, "add", "drafts/second/plan.md")
    _git(workspace, "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "-m", "add second draft")

    preview = preview_workbench_curriculum_edits(
        workspace,
        [CurriculumRemoveEdit(kind="remove", lesson_ids=["second"], delete_never_promoted_drafts=True)],
    )

    affected = next(item for item in preview.affected_lessons if item.lesson_id == "second")
    assert "draft package will be deleted" in affected.detail


def test_curriculum_save_given_removed_then_readded_draft_expect_package_preserved(workspace: Path) -> None:
    draft = workspace / "drafts" / "second"
    draft.mkdir(parents=True)
    draft_file = draft / "plan.md"
    draft_file.write_text("# Draft second\n", encoding="utf-8")
    _git(workspace, "add", "drafts/second/plan.md")
    _git(workspace, "-c", "core.hooksPath=/dev/null", "commit", "--no-verify", "-m", "add second draft")
    edits: list[CurriculumEdit] = [
        CurriculumRemoveEdit(kind="remove", lesson_ids=["second"], delete_never_promoted_drafts=True),
        CurriculumAddEdit(
            kind="add",
            entry={"catalog_id": "second", "title": "Second", "learner_outcome": "Use the skill."},
            after_lesson_id="greet_and_introduce_yourself",
        ),
    ]
    preview = preview_workbench_curriculum_edits(workspace, edits)

    save_workbench_curriculum_edits(workspace, edits, review_digest=preview.review_digest)

    assert draft_file.read_text(encoding="utf-8") == "# Draft second\n"


def test_curriculum_merge_preview_given_dependent_selected_entries_expect_unified_outcome_and_edges(
    workspace: Path,
) -> None:
    preview = preview_workbench_curriculum_edits(
        workspace,
        [
            CurriculumMergeEdit(
                kind="merge",
                lesson_ids=["second", "greet_and_introduce_yourself"],
                title="Merged",
                learner_outcome="Use both skills.",
            )
        ],
    )
    assert len(preview.entries) == 1
    assert preview.entries[0].catalog_id == "greet_and_introduce_yourself"
    assert preview.entries[0].title == "Merged"
    assert preview.entries[0].prerequisites == []
    assert preview.moved_outcomes[0].from_lesson_id == "second"
    assert preview.moved_outcomes[0].to_lesson_id == "greet_and_introduce_yourself"
    assert preview.affected_lessons[0].effect == "needs_replan"


def test_curriculum_split_preview_given_two_parts_expect_ordered_parts_and_relinked_dependents(workspace: Path) -> None:
    preview = preview_workbench_curriculum_edits(
        workspace,
        [
            CurriculumSplitEdit(
                kind="split",
                lesson_id="greet_and_introduce_yourself",
                parts=[
                    CurriculumSplitPart(
                        catalog_id="greet_and_introduce_yourself", title="Greetings", learner_outcome="Greet someone."
                    ),
                    CurriculumSplitPart(
                        catalog_id="introduce_yourself",
                        title="Introductions",
                        learner_outcome="Use the taught form in a short exchange.",
                    ),
                ],
            )
        ],
    )
    assert [slot.catalog_id for slot in preview.entries] == [
        "greet_and_introduce_yourself",
        "introduce_yourself",
        "second",
    ]
    assert preview.entries[1].prerequisites == ["greet_and_introduce_yourself"]
    assert preview.entries[2].prerequisites == ["introduce_yourself"]
    assert preview.moved_prerequisites[0].to_lesson_id == "introduce_yourself"
    assert preview.moved_outcomes[0].outcome == "Use the taught form in a short exchange."
    assert preview.moved_outcomes[0].to_lesson_id == "introduce_yourself"
    assert [lesson.effect for lesson in preview.affected_lessons] == ["needs_replan", "needs_replan", "new_entry"]


def test_curriculum_update_preview_given_outcome_and_prerequisite_changes_expect_replan_and_dependency_review(
    workspace: Path,
) -> None:
    preview = preview_workbench_curriculum_edits(
        workspace,
        [
            CurriculumUpdateEdit(
                kind="update",
                lesson_id="second",
                title="Updated second",
                learner_outcome="Use the skill in context.",
                cefr_level="A2",
                prerequisites=[],
                helpful_prerequisites=["greet_and_introduce_yourself"],
            )
        ],
    )
    assert preview.entries[1].learner_outcome == "Use the skill in context."
    assert preview.entries[1].cefr_level == "A2"
    assert preview.entries[1].helpful_prerequisites == ["greet_and_introduce_yourself"]
    assert preview.affected_lessons[0].effect == "needs_replan"
    assert preview.moved_prerequisites == []


def test_curriculum_save_given_repaired_remove_edit_expect_one_plan_commit_and_baseline(workspace: Path) -> None:
    edits: list[CurriculumEdit] = [
        CurriculumUpdateEdit(
            kind="update",
            lesson_id="second",
            title="Second",
            learner_outcome="Use the skill.",
            cefr_level="A1",
            prerequisites=[],
            helpful_prerequisites=[],
        ),
        CurriculumRemoveEdit(kind="remove", lesson_ids=["greet_and_introduce_yourself"]),
    ]
    preview = preview_workbench_curriculum_edits(workspace, edits)
    commit, findings = save_workbench_curriculum_edits(workspace, edits, review_digest=preview.review_digest)
    assert [slot.catalog_id for slot in read_curriculum(workspace).slots] == ["second"]
    assert read_curriculum(workspace).curriculum_approval == "human_approved"
    assert not [finding for finding in findings if finding.code == "curriculum.prerequisite_missing"]
    assert _git(workspace, "show", "--format=", "--name-only", commit).strip() == "content/curriculum/plan.yaml"
    assert _git(workspace, "log", "-1", "--format=%s").strip() == "workbench: edit curriculum"
    assert set(read_workbench_record(workspace, "curriculum", "baseline", CurriculumBaseline).slots) == {
        "greet_and_introduce_yourself",
        "second",
    }


def test_curriculum_save_given_missing_required_dependency_expect_no_write_or_commit(workspace: Path) -> None:
    before = (workspace / "content" / "curriculum" / "plan.yaml").read_bytes()
    with pytest.raises(ValueError, match="fix missing prerequisites"):
        edits = [CurriculumRemoveEdit(kind="remove", lesson_ids=["greet_and_introduce_yourself"])]
        preview = preview_workbench_curriculum_edits(workspace, edits)
        save_workbench_curriculum_edits(
            workspace,
            edits,
            review_digest=preview.review_digest,
            finding_decisions=[
                CurriculumFindingDecision(finding_id=preview.dependency_findings[0].finding_id, decision="revise")
            ],
        )
    assert (workspace / "content" / "curriculum" / "plan.yaml").read_bytes() == before
    assert _git(workspace, "status", "--porcelain") == ""


def test_curriculum_save_given_protected_branch_expect_original_plan_restored(workspace: Path) -> None:
    _git(workspace, "branch", "-m", "main")
    with pytest.raises(WorkbenchGitError, match="protected"):
        edits = [CurriculumReorderEdit(kind="reorder", lesson_ids=["second", "greet_and_introduce_yourself"])]
        preview = preview_workbench_curriculum_edits(workspace, edits)
        save_workbench_curriculum_edits(
            workspace,
            edits,
            review_digest=preview.review_digest,
            finding_decisions=[
                CurriculumFindingDecision(finding_id=item.finding_id, decision="accept")
                for item in preview.dependency_findings
            ],
        )
    assert _git(workspace, "status", "--porcelain") == ""


@pytest.mark.parametrize(
    "edit",
    [
        CurriculumReorderEdit(kind="reorder", lesson_ids=["second", "greet_and_introduce_yourself"]),
        CurriculumAddEdit(
            kind="add",
            entry={"catalog_id": "weather_talk", "title": "Weather talk", "learner_outcome": "Describe weather."},
            after_lesson_id="greet_and_introduce_yourself",
        ),
        CurriculumRemoveEdit(kind="remove", lesson_ids=["second"]),
        CurriculumMergeEdit(
            kind="merge",
            lesson_ids=["greet_and_introduce_yourself", "second"],
            title="Merged",
            learner_outcome="Use both skills.",
        ),
        CurriculumSplitEdit(
            kind="split",
            lesson_id="greet_and_introduce_yourself",
            parts=[
                CurriculumSplitPart(
                    catalog_id="greet_and_introduce_yourself", title="Greetings", learner_outcome="Greet someone."
                ),
                CurriculumSplitPart(
                    catalog_id="introduce_yourself", title="Introductions", learner_outcome="Introduce yourself."
                ),
            ],
        ),
        CurriculumUpdateEdit(
            kind="update",
            lesson_id="second",
            title="Updated second",
            learner_outcome="Use the skill in context.",
            cefr_level="A2",
            prerequisites=["greet_and_introduce_yourself"],
            helpful_prerequisites=[],
        ),
    ],
    ids=["reorder", "add", "remove", "merge", "split", "update"],
)
def test_curriculum_edit_apply_given_reviewed_edit_expect_committed_plan(workspace: Path, edit: CurriculumEdit) -> None:
    preview = preview_workbench_curriculum_edits(workspace, [edit])
    decisions = [
        CurriculumFindingDecision(finding_id=item.finding_id, decision="accept")
        for item in preview.dependency_findings
        if item.code == "curriculum.prerequisite_order"
    ]
    commit, _findings = save_workbench_curriculum_edits(
        workspace, [edit], review_digest=preview.review_digest, finding_decisions=decisions
    )
    plan = read_curriculum(workspace)
    assert len(commit) == 40
    assert plan.curriculum_approval == "human_approved"
    assert [slot.catalog_id for slot in plan.slots] == [entry.catalog_id for entry in preview.entries]


def test_curriculum_save_given_unreviewed_force_edit_expect_pending_plan_and_release_block(workspace: Path) -> None:
    edit = CurriculumRemoveEdit(kind="remove", lesson_ids=["greet_and_introduce_yourself"])
    commit, _findings = save_workbench_curriculum_edits(workspace, [edit], force=True)
    assert len(commit) == 40
    assert read_curriculum(workspace).curriculum_approval == "pending_human"
    with pytest.raises(ValueError, match="curriculum plan: review required"):
        run_release(repo_root=workspace)


def test_curriculum_save_given_pending_plan_reviewed_without_edits_expect_release_approval(workspace: Path) -> None:
    force_edit = CurriculumUpdateEdit(
        kind="update",
        lesson_id="greet_and_introduce_yourself",
        title="Updated greetings",
        learner_outcome="Greet someone.",
        cefr_level="A1",
        prerequisites=[],
        helpful_prerequisites=[],
    )
    save_workbench_curriculum_edits(workspace, [force_edit], force=True)
    order = [slot.catalog_id for slot in read_curriculum(workspace).slots]
    edits = [CurriculumReorderEdit(kind="reorder", lesson_ids=order)]
    preview = preview_workbench_curriculum_edits(workspace, edits)
    decisions = [
        CurriculumFindingDecision(finding_id=item.finding_id, decision="accept") for item in preview.dependency_findings
    ]

    save_workbench_curriculum_edits(
        workspace,
        edits,
        review_digest=preview.review_digest,
        finding_decisions=decisions,
    )

    assert read_curriculum(workspace).curriculum_approval == "human_approved"
    with pytest.raises(ValueError) as error:
        run_release(repo_root=workspace)
    assert "curriculum plan: review required" not in str(error.value)


def test_curriculum_save_given_missing_review_decision_expect_no_write(workspace: Path) -> None:
    edits: list[CurriculumEdit] = [
        CurriculumReorderEdit(kind="reorder", lesson_ids=["second", "greet_and_introduce_yourself"])
    ]
    before = (workspace / "content" / "curriculum" / "plan.yaml").read_bytes()
    preview = preview_workbench_curriculum_edits(workspace, edits)
    with pytest.raises(ValueError, match="review every current curriculum finding"):
        save_workbench_curriculum_edits(workspace, edits, review_digest=preview.review_digest)
    assert (workspace / "content" / "curriculum" / "plan.yaml").read_bytes() == before
    assert _git(workspace, "status", "--porcelain") == ""


def test_curriculum_remove_given_all_entries_expect_empty_plan_saved_in_source_order(workspace: Path) -> None:
    edits = [CurriculumRemoveEdit(kind="remove", lesson_ids=["greet_and_introduce_yourself", "second"])]
    preview = preview_workbench_curriculum_edits(workspace, edits)

    commit, _findings = save_workbench_curriculum_edits(workspace, edits, review_digest=preview.review_digest)

    plan = read_curriculum(workspace)
    assert len(commit) == 40
    assert plan.slots == []


def test_curriculum_save_given_already_approved_identical_edit_expect_existing_commit_returned(workspace: Path) -> None:
    slot = read_curriculum(workspace).slots[0]
    edit = CurriculumUpdateEdit(
        kind="update",
        lesson_id=slot.catalog_id,
        title=slot.title,
        learner_outcome=slot.learner_outcome,
        cefr_level=slot.cefr_level,
        prerequisites=slot.prerequisites,
        helpful_prerequisites=slot.helpful_prerequisites,
    )
    first_preview = preview_workbench_curriculum_edits(workspace, [edit])
    first_commit, _findings = save_workbench_curriculum_edits(
        workspace, [edit], review_digest=first_preview.review_digest
    )
    second_preview = preview_workbench_curriculum_edits(workspace, [edit])

    second_commit, _findings = save_workbench_curriculum_edits(
        workspace, [edit], review_digest=second_preview.review_digest
    )

    assert second_commit == first_commit
    assert _git(workspace, "rev-parse", "HEAD").strip() == first_commit


@pytest.mark.parametrize(
    "edit",
    [
        CurriculumReorderEdit(kind="reorder", lesson_ids=["second"]),
        CurriculumMergeEdit(kind="merge", lesson_ids=["second", "second"], title="Merged", learner_outcome="Outcome."),
    ],
)
def test_curriculum_preview_given_invalid_edit_expect_validation_error(workspace: Path, edit: CurriculumEdit) -> None:
    with pytest.raises(ValueError):
        preview_workbench_curriculum_edits(workspace, [edit])


def test_curriculum_baseline_given_helpful_prerequisite_reorder_and_position_change_expect_fresh(
    workspace: Path,
) -> None:
    plan = read_curriculum(workspace)
    slot = plan.slots[1]
    original = slot.model_copy(update={"helpful_prerequisites": ["gamma", "alpha"]})
    write_workbench_record(
        workspace,
        "curriculum",
        "baseline",
        CurriculumBaseline(slots={slot.catalog_id: original.model_dump_json()}),
    )
    reordered = original.model_copy(update={"helpful_prerequisites": list(reversed(original.helpful_prerequisites))})
    reordered_plan = plan.model_copy(
        update={"slots": [reordered if item.catalog_id == slot.catalog_id else item for item in plan.slots]}
    )
    assert find_stale_curriculum_entries(workspace, reordered_plan) == set()
    sequence_changed = reordered.model_copy(update={"sequence_index": reordered.sequence_index + 1})
    changed_plan = reordered_plan.model_copy(
        update={"slots": [sequence_changed if item.catalog_id == slot.catalog_id else item for item in plan.slots]}
    )
    assert find_stale_curriculum_entries(workspace, changed_plan) == set()


def test_curriculum_chat_given_typed_merge_proposal_expect_validated_persisted_edit_only(workspace: Path) -> None:
    before = (workspace / "content" / "curriculum" / "plan.yaml").read_bytes()
    client = FakeCodexAppServer(
        FakeCodexTurn(
            text=json.dumps(
                {
                    "reply": "These entries share an outcome.",
                    "proposed_edit": {
                        "kind": "merge",
                        "lesson_ids": ["greet_and_introduce_yourself", "second"],
                        "title": "Greetings and introductions",
                        "learner_outcome": "Greet and introduce yourself.",
                    },
                }
            )
        )
    )
    lock = RLock()
    turn_id = begin_workbench_chat_turn(workspace, "curriculum", "Merge these entries", [], lock, kind="curriculum")
    result = run_workbench_chat_turn(workspace, "curriculum", turn_id, lock, client)
    transcript = read_workbench_record(workspace, "chat", "curriculum", ChatTranscript)
    assert result["proposed_edit"]["kind"] == "merge"
    assert transcript.turns[-1].proposed_edit is not None
    assert transcript.turns[-1].proposed_edit.kind == "merge"
    assert (workspace / "content" / "curriculum" / "plan.yaml").read_bytes() == before


def test_curriculum_chat_given_invalid_edit_proposal_expect_no_proposal_saved(workspace: Path) -> None:
    client = FakeCodexAppServer(
        FakeCodexTurn(
            text=json.dumps(
                {"reply": "I cannot apply that edit.", "proposed_edit": {"kind": "remove", "lesson_ids": ["unknown"]}}
            )
        )
    )
    lock = RLock()
    turn_id = begin_workbench_chat_turn(workspace, "curriculum", "Remove unknown", [], lock, kind="curriculum")
    result = run_workbench_chat_turn(workspace, "curriculum", turn_id, lock, client)
    assert result["proposed_edit"] is None
    assert read_workbench_record(workspace, "chat", "curriculum", ChatTranscript).turns[-1].proposed_edit is None


def test_curriculum_read_given_rewrite_and_mutated_return_expect_current_isolated_plan(workspace: Path) -> None:
    first = read_curriculum(workspace)
    title = first.slots[0].title
    first.slots[0].title = "Mutated by caller"
    assert read_curriculum(workspace).slots[0].title == title
    path = workspace / "content" / "curriculum" / "plan.yaml"
    path.write_text(path.read_text().replace(title, "Changed on disk"))
    assert read_curriculum(workspace).slots[0].title == "Changed on disk"


def test_curriculum_manual_add_given_invalid_level_expect_validation_error() -> None:
    with pytest.raises(ValidationError):
        CurriculumAddEdit.model_validate(
            {
                "kind": "add",
                "entry": {
                    "catalog_id": "weather_talk",
                    "title": "Weather",
                    "learner_outcome": "Talk.",
                    "cefr_level": "C2",
                },
            }
        )


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True).stdout
