"""Entry points: persisted Codex chat turns stream, cancel, recover, retry and build prompt context safely."""

from __future__ import annotations

import json
import shutil
from _thread import RLock
from pathlib import Path
from threading import Thread

import pytest

from lesson_builder.application.operations.workbench_chat import begin_workbench_chat_turn
from lesson_builder.application.operations.workbench_chat import begin_workbench_explanation_turn
from lesson_builder.application.operations.workbench_chat import build_chat_prompt
from lesson_builder.application.operations.workbench_chat import recover_workbench_chat_turns
from lesson_builder.application.operations.workbench_chat import run_workbench_chat_turn
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_lessons import build_workbench_preview
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_records import ChatTranscript
from lesson_builder.application.operations.workbench_records import ContextReference
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_records import StoredChatTurn
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_CHAT_EXCERPT_LENGTH
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.clients.codex.fakes import FakeCodexAppServer
from tests.clients.codex.fakes import FakeCodexGate
from tests.clients.codex.fakes import FakeCodexTurn
from tests.settings import K_TEST_HANG_GUARD_SECONDS


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = Path(__file__).parents[3]
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(root / "content" / "lessons" / lesson_id, tmp_path / "content" / "lessons" / lesson_id)
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(root / ".opencode" / "agents", tmp_path / ".opencode" / "agents")
    return tmp_path


def _chat_prompt_context(prompt: str) -> dict[str, object]:
    """Parse the JSON context object carried by one chat prompt."""
    return json.loads(prompt.split("CONTEXT:\n", 1)[1].split("\nAUTHOR REQUEST:", 1)[0])


def test_begin_chat_turn_given_new_author_message_expect_pending_turn_persisted_before_dispatch(
    workspace: Path,
) -> None:
    turn_id = begin_workbench_chat_turn(
        workspace, "greet_and_introduce_yourself", "Shorten the first line", [], RLock()
    )

    transcript = read_workbench_record(workspace, "chat", "greet_and_introduce_yourself", ChatTranscript)
    assert transcript.turns[0].turn_id == turn_id
    assert transcript.turns[0].status == "pending"
    assert transcript.turns[0].text == "Shorten the first line"


def test_chat_turn_given_streamed_delta_expect_running_partial_then_completed_reply(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    lock = RLock()
    turn_id = begin_workbench_chat_turn(workspace, lesson_id, "Explain", [], lock)
    gate = FakeCodexGate.create()
    client = FakeCodexAppServer(
        FakeCodexTurn(
            text='{"reply":"Draft reply","edits":[],"regenerate":null}',
            deltas=('{"reply":"Draft',),
            gate=gate,
        )
    )
    failures: list[Exception] = []

    def run() -> None:
        try:
            run_workbench_chat_turn(workspace, lesson_id, turn_id, lock, client)
        except Exception as exc:
            failures.append(exc)

    worker = Thread(target=run)
    worker.start()
    assert gate.entered.wait(K_TEST_HANG_GUARD_SECONDS)
    streaming = read_workbench_record(workspace, "chat", lesson_id, ChatTranscript).turns[0]
    assert streaming.status == "running"
    assert streaming.partial == "Draft"
    gate.release.set()
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    transcript = read_workbench_record(workspace, "chat", lesson_id, ChatTranscript)
    assert failures == []
    assert transcript.turns[0].status == "completed"
    assert transcript.turns[0].partial == ""
    assert transcript.turns[-1].text == "Draft reply"


def test_chat_turn_given_interrupt_before_completion_expect_cancelled_author_turn_only(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    lock = RLock()
    turn_id = begin_workbench_chat_turn(workspace, lesson_id, "Explain", [], lock)
    gate = FakeCodexGate.create()
    client = FakeCodexAppServer(
        FakeCodexTurn(text='{"reply":"Should not commit","edits":[],"regenerate":null}', gate=gate)
    )
    cancellation = CancellationToken()
    failures: list[Exception] = []

    def run() -> None:
        try:
            run_workbench_chat_turn(workspace, lesson_id, turn_id, lock, client, cancellation=cancellation)
        except Exception as exc:
            failures.append(exc)

    worker = Thread(target=run)
    worker.start()
    assert gate.entered.wait(K_TEST_HANG_GUARD_SECONDS)
    cancellation.cancel("author", "Chat turn cancelled.")
    client.interrupt("fake-thread-1", "fake-turn-1")
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    transcript = read_workbench_record(workspace, "chat", lesson_id, ChatTranscript)
    assert failures and isinstance(failures[0], Cancelled)
    assert failures[0].reason == "author"
    assert transcript.turns[0].status == "cancelled"
    assert transcript.turns[0].error is None
    assert [turn for turn in transcript.turns if turn.role == "assistant"] == []


def test_chat_restart_given_pending_or_running_turns_expect_failed_interrupted_turns(workspace: Path) -> None:
    transcript = ChatTranscript(
        turns=[
            StoredChatTurn(role="author", text="Pending", turn_id="pending-1", status="pending"),
            StoredChatTurn(role="author", text="Running", turn_id="running-1", status="running", partial="draft"),
        ]
    )
    write_workbench_record(workspace, "chat", "recovery", transcript)

    recover_workbench_chat_turns(workspace, RLock())

    recovered = read_workbench_record(workspace, "chat", "recovery", ChatTranscript)
    assert [(turn.status, turn.error) for turn in recovered.turns] == [
        ("failed", "interrupted"),
        ("failed", "interrupted"),
    ]


def test_chat_turn_given_provider_failure_expect_retry_same_author_turn_id(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    lock = RLock()
    turn_id = begin_workbench_chat_turn(workspace, lesson_id, "Explain", [], lock)
    failed_client = FakeCodexAppServer(FakeCodexTurn(error=RuntimeError("temporary server error")))

    with pytest.raises(RuntimeError, match="temporary"):
        run_workbench_chat_turn(workspace, lesson_id, turn_id, lock, failed_client)

    failed = read_workbench_record(workspace, "chat", lesson_id, ChatTranscript).turns[0]
    assert failed.status == "failed"
    assert failed.error == "temporary server error"
    retried_id = begin_workbench_chat_turn(workspace, lesson_id, failed.text, failed.chips, lock, retry_turn_id=turn_id)
    success_client = FakeCodexAppServer(
        FakeCodexTurn(text=json.dumps({"reply": "Recovered", "edits": [], "regenerate": None}))
    )

    run_workbench_chat_turn(workspace, lesson_id, retried_id, lock, success_client)

    transcript = read_workbench_record(workspace, "chat", lesson_id, ChatTranscript)
    assert retried_id == turn_id
    assert transcript.turns[0].status == "completed"
    assert transcript.turns[-1].text == "Recovered"


def test_explain_only_turn_given_following_chat_expect_source_snapshot_preserved_for_chat(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    lock = RLock()
    source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    client = FakeCodexAppServer(
        FakeCodexTurn(
            text=json.dumps({"translations": [{"source": "Hei!", "english": "Hi!"}], "item_tested": "greeting"})
        ),
        FakeCodexTurn(text='{"reply":"Here is a suggestion.","edits":[],"regenerate":null}'),
    )
    explanation_turn = begin_workbench_explanation_turn(workspace, lesson_id, exercise.key, lock)

    run_workbench_chat_turn(workspace, lesson_id, explanation_turn, lock, client)
    after_explanation = read_workbench_record(workspace, "chat", lesson_id, ChatTranscript)
    ordinary_turn = begin_workbench_chat_turn(workspace, lesson_id, "How can I improve this lesson?", [], lock)
    run_workbench_chat_turn(workspace, lesson_id, ordinary_turn, lock, client)

    prompt_context = client.calls[1].prompt.split("CONTEXT:\n", 1)[1].split("\nAUTHOR REQUEST:", 1)[0]
    context = json.loads(prompt_context)
    assert after_explanation.block_snapshot == {}
    assert {block["key"] for block in context["changed_or_selected_blocks"]} == {block.key for block in source.blocks}


def test_build_chat_prompt_given_follow_up_turn_expect_thread_memory_instruction_and_unchanged_block_excerpt(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    view = build_workbench_preview(resolve_lesson_package(workspace, lesson_id))
    selected = next(block for block in source.blocks if block.key == "a-first-meeting#2")

    prompt = build_chat_prompt(
        source,
        view,
        "Please simplify all three",
        [ContextReference(kind="selection", label=selected.key, detail="Jonas's reply", key=selected.key)],
        [],
        ProposalLog(),
        {block.key: block.source for block in source.blocks},
    )

    context = _chat_prompt_context(prompt)
    available = {block["key"]: block for block in context["available_blocks"]}
    assert available["a-first-meeting#1"]["excerpt"] == "Hei! Jeg heter Mina. Hva heter du?"
    assert available["a-first-meeting#0"]["excerpt"] == "It is Monday morning at a small language school in Bergen. T"
    assert len(available["a-first-meeting#0"]["excerpt"]) == K_APPLICATION_WORKBENCH_CHAT_EXCERPT_LENGTH
    assert [block["key"] for block in context["changed_or_selected_blocks"]] == [selected.key]
    assert context["changed_or_selected_blocks"][0]["source"] == selected.source
    assert "was sent in full in an earlier turn of this same thread" in prompt
    assert "never ask the author for internal block keys" in prompt
    assert "Ask for a selection when the requested block is not present" not in prompt


def test_build_chat_prompt_given_first_turn_without_snapshot_expect_every_block_full_source(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    view = build_workbench_preview(resolve_lesson_package(workspace, lesson_id))

    prompt = build_chat_prompt(
        source, view, "Simplify Jonas's two replies in the opening dialogue", [], [], ProposalLog(), {}
    )

    context = _chat_prompt_context(prompt)
    assert {block["key"] for block in context["changed_or_selected_blocks"]} == {block.key for block in source.blocks}
    assert [(block["key"], block["source"]) for block in context["changed_or_selected_blocks"]] == [
        (block.key, block.source) for block in source.blocks
    ]


def test_chat_turn_given_second_turn_on_same_thread_expect_excerpt_context_without_lesson_resend(
    workspace: Path,
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    lock = RLock()
    client = FakeCodexAppServer(
        FakeCodexTurn(text='{"reply":"Which two replies do you mean?","edits":[],"regenerate":null}'),
        FakeCodexTurn(text='{"reply":"Simplified all three replies.","edits":[],"regenerate":null}'),
    )
    first = begin_workbench_chat_turn(workspace, lesson_id, "Simplify Jonas's two replies", [], lock)

    run_workbench_chat_turn(workspace, lesson_id, first, lock, client)
    second = begin_workbench_chat_turn(workspace, lesson_id, "Please simplify all three", [], lock)
    run_workbench_chat_turn(workspace, lesson_id, second, lock, client)

    assert client.calls[1].thread_id == "fake-thread-1"
    first_context = _chat_prompt_context(client.calls[0].prompt)
    second_context = _chat_prompt_context(client.calls[1].prompt)
    assert {block["key"] for block in first_context["changed_or_selected_blocks"]} == {
        block.key for block in read_workbench_source(workspace, lesson_id).blocks
    }
    assert second_context["changed_or_selected_blocks"] == []
    available = {block["key"]: block for block in second_context["available_blocks"]}
    assert available["a-first-meeting#2"]["excerpt"] == "Hei, Mina. Jeg heter Jonas."
    assert "was sent in full in an earlier turn of this same thread" in client.calls[1].prompt
    assert "Ask for a selection when the requested block is not present" not in client.calls[1].prompt


def test_chat_turn_given_plan_chip_and_changed_plan_expect_plan_memory_and_resend(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    lock = RLock()
    plan_path = workspace / "content" / "lessons" / lesson_id / "plan.md"
    selected_plan = (
        plan_path.read_text(encoding="utf-8") + "\nScope sentinel: include introductions and exclude farewells.\n"
    )
    plan_path.write_text(selected_plan, encoding="utf-8")
    client = FakeCodexAppServer(
        FakeCodexTurn(text='{"reply":"The plan defines introduction scope.","edits":[],"regenerate":null}'),
        FakeCodexTurn(text='{"reply":"The plan is unchanged.","edits":[],"regenerate":null}'),
        FakeCodexTurn(text='{"reply":"The plan scope changed.","edits":[],"regenerate":null}'),
    )
    selected_turn = begin_workbench_chat_turn(
        workspace,
        lesson_id,
        "What scope constraints are in the plan?",
        [ContextReference(kind="file", label="plan.md")],
        lock,
    )

    run_workbench_chat_turn(workspace, lesson_id, selected_turn, lock, client)

    first_context = _chat_prompt_context(client.calls[0].prompt)
    assert first_context["plan_source"] == selected_plan

    unchanged_turn = begin_workbench_chat_turn(workspace, lesson_id, "Summarize that plan again.", [], lock)
    run_workbench_chat_turn(workspace, lesson_id, unchanged_turn, lock, client)
    unchanged_context = _chat_prompt_context(client.calls[1].prompt)
    assert "plan_source" not in unchanged_context
    assert (
        "if plan_source is omitted, the unchanged plan was sent in full earlier in this same thread"
        in client.calls[1].prompt
    )

    changed_plan = selected_plan + "\nUpdate sentinel: pronunciation practice is now included.\n"
    plan_path.write_text(changed_plan, encoding="utf-8")
    changed_turn = begin_workbench_chat_turn(workspace, lesson_id, "What changed in the plan?", [], lock)
    run_workbench_chat_turn(workspace, lesson_id, changed_turn, lock, client)

    changed_context = _chat_prompt_context(client.calls[2].prompt)
    assert changed_context["plan_source"] == changed_plan
