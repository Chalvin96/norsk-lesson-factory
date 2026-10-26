"""Entry point: scoped regeneration routing, repair and persistent owner-gate behavior tests."""

from __future__ import annotations

import hashlib
import json
import shutil
from _thread import RLock
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from threading import Lock
from threading import Thread
from types import SimpleNamespace
from typing import Any
from typing import Literal

import pytest
import yaml
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from lesson_builder.application.operations.author_preview import AuthorPreview
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_editing import write_workbench_file
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAligned
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseStale
from lesson_builder.application.operations.workbench_exercise_alignment import restore_pin
from lesson_builder.application.operations.workbench_proposals import decide_proposal
from lesson_builder.application.operations.workbench_proposals import digest_workbench_source
from lesson_builder.application.operations.workbench_proposals import read_open_proposal
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentLog
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentPin
from lesson_builder.application.operations.workbench_records import ExerciseAlignmentRecord
from lesson_builder.application.operations.workbench_records import Proposal
from lesson_builder.application.operations.workbench_records import ProposalCheck
from lesson_builder.application.operations.workbench_records import ProposalLog
from lesson_builder.application.operations.workbench_records import ReviewLog
from lesson_builder.application.operations.workbench_regeneration import capture_regeneration
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.cancellation import active_cancellation_token
from lesson_builder.formats.workbench_source import split_exercise_source
from lesson_builder.formats.workbench_source import split_lesson_source
from lesson_builder.workflow.workbench_regeneration.runner import execute_regeneration
from lesson_builder.workflow.workbench_regeneration.runner import read_regeneration
from lesson_builder.workflow.workbench_regeneration.runner import recoverable_regenerations
from lesson_builder.workflow.workbench_regeneration.runner import resume_regeneration
from lesson_builder.workflow.workbench_regeneration.runner import start_regeneration
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.workbench_store import read_workbench_record
from tests.application.fakes import FakeLlmClient
from tests.application.fakes import workbench_review_reply
from tests.settings import K_TEST_HANG_GUARD_SECONDS


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = Path(__file__).parents[2]
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(root / "content/lessons" / lesson_id, tmp_path / "content/lessons" / lesson_id)
    shutil.copy(root / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(root / ".opencode/agents", tmp_path / ".opencode/agents")
    source = read_workbench_source(tmp_path, lesson_id)
    write_workbench_file(tmp_path, lesson_id, "lesson.md", source.files["lesson.md"])
    lesson_path = tmp_path / "drafts" / lesson_id / "lesson.md"
    lesson_source = lesson_path.read_text(encoding="utf-8")
    for heading in (block for block in split_lesson_source(lesson_source).blocks if block.kind == "heading"):
        lesson_source = lesson_source.replace(heading.source, heading.source.replace(" {#", " local {#", 1), 1)
    lesson_path.write_text(lesson_source, encoding="utf-8")
    return tmp_path


def test_capture_regeneration_given_section_id_exercises_expect_scoped_selection(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    lesson = source.files["lesson.md"] + (
        "\n## Exercise overview {#exercises role=recap objectives=obj-greet-and-introduce-yourself-01}\n\n"
        "Review the examples from this lesson.\n"
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", lesson)

    captured = capture_regeneration(workspace, lesson_id, "exercises", mode="scope")

    assert captured["mode"] == "scope"
    assert captured["key"] == "exercises"
    assert captured["selected_keys"] == ["exercises", "exercises#0"]
    assert captured["exercise_keys"] == []


@pytest.mark.parametrize("scope", ["whole", "section", "item"])
@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_regeneration_given_scoped_generation_expect_checked_proposal_interrupt_and_restart_resume(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, scope: str, decision: str
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    key = "whole" if scope == "whole" else reading.key.split("#")[0] if scope == "section" else reading.key
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Regenerated",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    identity = start_regeneration(workspace, lesson_id, key, RLock())
    assert recoverable_regenerations(workspace, RLock()) == (identity,)
    result = execute_regeneration(workspace, identity, RLock())
    assert result["status"] == "waiting"
    assert result["steps"] == ["generate", "check", "review", "waiting for you"]
    assert "deferred" not in result
    assert read_workbench_source(workspace, lesson_id).files == source.files
    proposal = read_open_proposal(workspace, lesson_id, source)
    assert proposal is not None
    assert proposal.source == "regenerate"
    assert proposal.check.status == "passed", proposal.check.errors
    decided = decide_proposal(workspace, lesson_id, proposal.proposal_id, decision)
    resume_regeneration(workspace, decided, RLock())
    restarted = read_regeneration(workspace, identity, RLock())
    assert restarted["status"] == "done"
    assert restarted["decision"] == decision
    assert len(author.calls) == 1
    assert (workspace / "drafts" / lesson_id).exists()
    if decision == "reject":
        assert read_workbench_source(workspace, lesson_id).files == source.files


def test_regeneration_given_process_exit_after_review_checkpoint_before_interrupt_expect_recovery_and_decision(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Regenerated",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    from lesson_builder.workflow.workbench_regeneration.graph import RegenerationRunner

    owner_gate = RegenerationRunner.owner_gate

    def exit_before_interrupt(self, state):
        raise KeyboardInterrupt

    monkeypatch.setattr(RegenerationRunner, "owner_gate", exit_before_interrupt)
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())

    with pytest.raises(KeyboardInterrupt):
        execute_regeneration(workspace, identity, RLock())

    monkeypatch.setattr(RegenerationRunner, "owner_gate", owner_gate)
    assert recoverable_regenerations(workspace, RLock()) == (identity,)
    proposal = read_open_proposal(workspace, lesson_id, source)
    assert proposal is not None
    decided = decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")

    resume_regeneration(workspace, decided, RLock())

    result = read_regeneration(workspace, identity, RLock())
    assert result["status"] == "done"
    assert result["decision"] == "accept"
    assert (workspace / "drafts" / lesson_id).exists()


def test_regeneration_given_two_failed_checks_expect_one_fix_failed_proposal_and_owner_interrupt(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    client = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Replace",
                "edits": [{"key": reading.key, "after_source": "::: {.reading}\nUnclosed", "note": "invalid"}],
            }
        )
    )
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())
    result = execute_regeneration(workspace, identity, RLock())
    proposal = read_open_proposal(workspace, lesson_id, source)
    assert proposal is not None
    assert result["status"] == "waiting"
    assert result["steps"] == ["generate", "check", "fix", "review", "waiting for you"]
    assert proposal.check.status == "failed"
    assert len(client.calls) == 2
    with pytest.raises(ValueError, match="passed"):
        decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")
    assert read_workbench_source(workspace, lesson_id).files == source.files


def test_regeneration_given_repair_provider_failure_expect_first_candidate_proposal(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")

    def respond(prompt: str, _model: str | None) -> str:
        if "Repair once against ORIGINAL source" in prompt:
            raise ValueError("repair response was invalid")
        return json.dumps(
            {
                "reply": "Replace",
                "edits": [{"key": reading.key, "after_source": "::: {.reading}\nUnclosed", "note": "invalid"}],
            }
        )

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())

    result = execute_regeneration(workspace, identity, RLock())

    proposal = read_open_proposal(workspace, lesson_id, source)
    assert result["status"] == "waiting"
    assert result["steps"] == ["generate", "check", "fix", "review", "waiting for you"]
    assert proposal is not None
    assert proposal.blocks[0].after == "::: {.reading}\nUnclosed"
    assert proposal.check.status == "failed"
    assert proposal.check.errors
    assert proposal.blocks[0].check == proposal.check
    assert len(client.calls) == 2


def test_regeneration_given_provider_failure_expect_terminal_run_excluded_from_recovery(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    broken = FakeLlmClient.raising(ValueError("provider offline"))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: broken)
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())
    with pytest.raises(ValueError, match="offline"):
        execute_regeneration(workspace, identity, RLock())
    assert read_regeneration(workspace, identity, RLock())["status"] == "failed"
    assert recoverable_regenerations(workspace, RLock()) == ()
    good = FakeLlmClient.responding("unused because failed runs are terminal")
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: good)
    result = execute_regeneration(workspace, identity, RLock())
    assert result["status"] == "failed"
    assert good.calls == []


def test_chat_triggered_regeneration_given_cancel_after_generation_expect_terminal_cancelled_run(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    cancellation = CancellationToken()

    def respond(prompt: str, model: str) -> str:
        if "ChatResponse" in prompt:
            cancellation.cancel("author", "Regeneration was cancelled.")
            return json.dumps(
                {
                    "reply": "Regenerated",
                    "edits": [
                        {
                            "key": reading.key,
                            "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                            "note": "translation",
                        }
                    ],
                }
            )
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())

    with pytest.raises(Cancelled, match="cancelled") as captured:
        execute_regeneration(workspace, identity, RLock(), cancellation=cancellation)
    assert captured.value.reason == "author"

    run = read_regeneration(workspace, identity, RLock())
    assert len(client.calls) == 1
    assert run["status"] == "cancelled"
    assert run["steps"] == []
    assert run["errors"] == ["Regeneration was cancelled."]
    assert run["error_kinds"] == ["cancelled"]
    assert recoverable_regenerations(workspace, RLock()) == ()
    assert read_open_proposal(workspace, lesson_id, source) is None

    resumed = execute_regeneration(workspace, identity, RLock())

    assert resumed["status"] == "cancelled"
    assert len(client.calls) == 1
    assert read_open_proposal(workspace, lesson_id, source) is None


def test_regeneration_given_cancel_during_review_expect_each_review_call_cancelled(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    cancellation = CancellationToken()
    review_calls_started = Event()
    review_tokens: list[CancellationToken] = []
    review_tokens_lock = Lock()

    def respond_to_review(prompt: str, model: str | None) -> str:
        token = active_cancellation_token()
        assert token is not None
        with review_tokens_lock:
            review_tokens.append(token)
            if len(review_tokens) == 2:
                review_calls_started.set()
        token.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        token.raise_if_cancelled()
        return workbench_review_reply(prompt, model)

    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Regenerated",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(respond_to_review)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())
    errors: list[Exception] = []

    def execute() -> None:
        try:
            execute_regeneration(workspace, identity, RLock(), cancellation=cancellation)
        except Exception as error:
            errors.append(error)

    worker = Thread(target=execute)
    worker.start()
    try:
        assert review_calls_started.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        cancellation.cancel("author", "Regeneration was cancelled during review.")
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
        assert not worker.is_alive()
    finally:
        _stop_regeneration_review_calls_for_test(cancellation, review_tokens, worker)

    assert len(errors) == 1
    assert isinstance(errors[0], Cancelled)
    assert errors[0].reason == "author"
    assert len(reviewer.cancellation_signals) == 2
    assert all(token is cancellation and token.is_cancelled for token in reviewer.cancellation_signals)
    assert all(token is cancellation for token in review_tokens)


def test_regeneration_given_provider_wait_past_deadline_expect_clear_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lesson_builder.workflow.workbench_regeneration import graph as graph_module
    from lesson_builder.workflow.workbench_regeneration import runner as runner_module

    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    clock = [runner_module.time.time()]

    def respond(prompt: str, model: str | None) -> str:
        if "ChatResponse" in prompt:
            clock[0] = float("inf")
            return json.dumps(
                {
                    "reply": "Regenerated",
                    "edits": [
                        {
                            "key": reading.key,
                            "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                            "note": "translation",
                        }
                    ],
                }
            )
        return workbench_review_reply(prompt, model)

    author = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: author)
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())
    clock[0] = runner_module.time.time()
    fake_time = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(runner_module, "time", fake_time)
    monkeypatch.setattr(graph_module, "time", fake_time)

    result = execute_regeneration(workspace, identity, RLock())

    assert author.cancellation_signals[0] is not None
    assert author.cancellation_signals[0].is_cancelled is True

    assert result["status"] == "failed"
    assert result["errors"] == ["Regeneration exceeded its 20-minute run deadline."]
    assert result["steps"] == []
    assert read_open_proposal(workspace, lesson_id, source) is None

    resumed = execute_regeneration(workspace, identity, RLock())

    assert resumed["status"] == "failed"
    assert len(author.calls) == 1
    assert read_open_proposal(workspace, lesson_id, source) is None


def test_regeneration_given_late_queue_start_expect_full_deadline_budget(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lesson_builder.workflow.workbench_regeneration import graph as graph_module
    from lesson_builder.workflow.workbench_regeneration import runner as runner_module

    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Regenerated",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    clock = [100.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(runner_module, "time", fake_time)
    monkeypatch.setattr(graph_module, "time", fake_time)

    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())
    clock[0] += 20 * 60 + 1

    result = execute_regeneration(workspace, identity, RLock())

    assert result["status"] == "waiting"
    assert author.cancellation_signals[0] is not None
    assert author.cancellation_signals[0].is_cancelled is False


@pytest.mark.parametrize("signal_kind", ["cancel", "deadline"])
def test_exercise_regeneration_given_cancel_or_deadline_during_parallel_provider_calls_expect_each_signal_reaches_provider(
    workspace: Path, monkeypatch: pytest.MonkeyPatch, signal_kind: str
) -> None:
    from lesson_builder.workflow.workbench_regeneration import graph as graph_module
    from lesson_builder.workflow.workbench_regeneration import runner as runner_module

    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    selected_keys = [block.key for block in source.blocks if block.kind == "exercise"][:2]
    calls_started = 0
    calls_lock = Lock()
    both_started = Event()
    release_provider = Event()
    cancellation = CancellationToken()
    clock = [100.0]
    fake_time = SimpleNamespace(time=lambda: clock[0])
    monkeypatch.setattr(runner_module, "time", fake_time)
    monkeypatch.setattr(graph_module, "time", fake_time)

    def wait_for_release(prompt: str, model: str | None) -> str:
        nonlocal calls_started
        with calls_lock:
            calls_started += 1
            if calls_started == len(selected_keys):
                both_started.set()
        release_provider.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        return _exercise_author_reply(prompt, model)

    author = FakeLlmClient.from_fn(wait_for_release)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=selected_keys)
    errors: list[Exception] = []
    results: list[dict[str, object]] = []

    def execute() -> None:
        try:
            results.append(execute_regeneration(workspace, identity, RLock(), cancellation=cancellation))
        except Exception as exc:  # noqa: BLE001 - the assertion checks cancellation routing
            errors.append(exc)

    worker = Thread(target=execute)
    worker.start()
    assert both_started.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
    if signal_kind == "cancel":
        cancellation.cancel("author", "Regeneration was cancelled.")
    else:
        clock[0] += 20 * 60 + 1
    release_provider.set()
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert not worker.is_alive()
    if signal_kind == "cancel":
        assert len(errors) == 1
        assert isinstance(errors[0], Cancelled)
        assert errors[0].reason == "author"
    else:
        assert errors == []
        assert results[0]["status"] == "failed"
        assert results[0]["errors"] == ["Regeneration exceeded its 20-minute run deadline."]
    assert len(author.cancellation_signals) == len(selected_keys)
    assert all(signal is not None and signal.is_cancelled for signal in author.cancellation_signals)


def test_regeneration_given_provider_error_mentions_unchanged_phrase_expect_terminal_run_excluded_from_recovery(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    reading = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "reading")
    broken = FakeLlmClient.raising(ValueError("Regeneration returned unchanged source for exercise from provider"))
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: broken)
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())

    with pytest.raises(ValueError, match="unchanged source"):
        execute_regeneration(workspace, identity, RLock())

    assert recoverable_regenerations(workspace, RLock()) == ()


def test_recoverable_regenerations_given_non_run_and_unreadable_files_expect_logged_skip(
    workspace: Path, caplog: pytest.LogCaptureFixture
) -> None:
    run_directory = WorkspacePaths(workspace).workbench_runs_root
    run_directory.mkdir(parents=True)
    (run_directory / "notes.sqlite").write_bytes(b"not a regeneration run")
    unreadable_id = "b" * 32
    (run_directory / f"{unreadable_id}.sqlite").write_bytes(b"not a sqlite database")

    with caplog.at_level("WARNING"):
        recoverable = recoverable_regenerations(workspace, RLock())

    assert recoverable == ()
    assert "invalid run id" in caplog.text
    assert "unreadable workbench regeneration run" in caplog.text


def test_recoverable_regenerations_given_empty_run_file_expect_failed_terminal_run(
    workspace: Path,
) -> None:
    identity = "a" * 32
    run_directory = WorkspacePaths(workspace).workbench_runs_root
    run_directory.mkdir(parents=True)
    (run_directory / f"{identity}.sqlite").touch()

    assert recoverable_regenerations(workspace, RLock()) == ()
    run = read_regeneration(workspace, identity, RLock())

    assert run["status"] == "failed"
    assert run["errors"] == ["Regeneration run has no saved checkpoint."]
    assert recoverable_regenerations(workspace, RLock()) == ()


def test_regeneration_given_proposal_settled_before_interrupt_expect_completed_owner_decision(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Changed",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    from lesson_builder.workflow.workbench_regeneration.graph import RegenerationRunner

    original = RegenerationRunner.owner_gate

    def decide_before_interrupt(self, state):
        pending = read_open_proposal(workspace, lesson_id, source)
        if pending is not None:
            decided = decide_proposal(workspace, lesson_id, state["run_id"], "reject")
            resume_regeneration(workspace, decided, RLock())
        return original(self, state)

    monkeypatch.setattr(RegenerationRunner, "owner_gate", decide_before_interrupt)
    identity = start_regeneration(workspace, lesson_id, reading.key, RLock())
    result = execute_regeneration(workspace, identity, RLock())
    assert result["status"] == "done"
    assert result["decision"] == "reject"


def test_regeneration_given_superseding_run_expect_previous_interrupt_completed_as_superseded(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Changed",
                "edits": [
                    {
                        "key": reading.key,
                        "after_source": reading.source.replace('translation="', 'translation="Hello: ', 1),
                        "note": "translation",
                    }
                ],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    first = start_regeneration(workspace, lesson_id, reading.key, RLock())
    assert execute_regeneration(workspace, first, RLock())["status"] == "waiting"
    second = start_regeneration(workspace, lesson_id, reading.key, RLock())
    assert execute_regeneration(workspace, second, RLock())["status"] == "waiting"
    result = read_regeneration(workspace, first, RLock())
    assert result["status"] == "done"
    assert result["decision"] == "superseded"


def test_exercise_regeneration_given_stale_selected_items_expect_one_checked_proposal_and_item_decisions(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _run_selected_exercise_regeneration(workspace, monkeypatch)
    _assert_selected_exercise_generation(run, workspace)
    _assert_selected_exercise_replay(run, workspace)
    _assert_selected_exercise_decisions(run, workspace)


def test_scoped_regeneration_given_exercise_edit_without_decision_support_expect_generation_failure(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    after_source = exercise.source.replace("«Hei!", "«God dag!", 1)
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Updated the exercise.",
                "edits": [{"key": exercise.key, "after_source": after_source, "note": "refresh"}],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    identity = start_regeneration(workspace, lesson_id, exercise.key, RLock())

    with pytest.raises(ValueError, match="explain the change in English"):
        execute_regeneration(workspace, identity, RLock())


def test_scoped_regeneration_given_supported_stale_exercise_edit_expect_acceptance_clears_staleness(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    stale = next(
        state
        for state in ExerciseAlignment(workspace, lesson_id).states(source)
        if isinstance(state, ExerciseStale) and state.exercise_key == exercise.key
    )
    assert stale.changed_sections
    after_source = exercise.source.replace("«Hei!", "«God dag!", 1)
    after_item = yaml.safe_load(after_source)[0]
    changed_text = after_item["stimulus"][0]["turns"][0]["text_md"]
    author = FakeLlmClient.responding(
        json.dumps(
            {
                "reply": "Updated the exercise.",
                "edits": [
                    {
                        "key": exercise.key,
                        "after_source": after_source,
                        "note": "refresh",
                        "change_summary": "The dialogue greeting now matches the revised lesson section.",
                        "translations": [
                            {
                                "norwegian": changed_text,
                                "english": "Good day! My name is Mina. What is your name?",
                            }
                        ],
                        "answer_impact": "The correct answer remains the other person's name.",
                    }
                ],
            }
        )
    )
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    identity = start_regeneration(workspace, lesson_id, exercise.key, RLock())

    result = execute_regeneration(workspace, identity, RLock())
    proposal = read_open_proposal(workspace, lesson_id, source)

    assert result["status"] == "waiting"
    assert proposal is not None
    assert proposal.check.status == "passed", proposal.check.errors
    block = proposal.blocks[0]
    assert block.change_summary
    assert block.translations
    assert block.pin is not None
    assert block.pin["checked_against"] == {
        section_id: hashlib.sha256(section_source.encode()).hexdigest()
        for section_id, section_source in block.pin["section_sources"].items()
    }
    assert block.pin["section_sources"]
    assert block.pin["changed_sections"]

    decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")

    updated_source = read_workbench_source(workspace, lesson_id)
    updated = next(
        state
        for state in ExerciseAlignment(workspace, lesson_id).states(updated_source)
        if state.exercise_key == exercise.key
    )
    assert isinstance(updated, ExerciseAligned)


def test_regeneration_checkpoint_given_captured_pin_expect_pin_round_trips(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    changed_lesson = source.files["lesson.md"].replace("Two new students arrive early", "Two students arrive early", 1)
    write_workbench_file(workspace, lesson_id, "lesson.md", changed_lesson)
    captured = capture_regeneration(
        workspace,
        lesson_id,
        None,
        exercise_keys=["notice_name_question"],
        mode="exercises",
    )
    pin = captured["exercise_context"]["notice_name_question"]
    serializer = JsonPlusSerializer()

    checkpoint = serializer.dumps_typed(captured)
    restored = serializer.loads_typed(checkpoint)
    restored_pin = restore_pin("notice_name_question", restored["exercise_context"]["notice_name_question"])

    assert restored_pin == pin


def test_exercise_regeneration_given_exact_candidate_accepted_expect_review_reused(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    exercise = next(block for block in read_workbench_source(workspace, lesson_id).blocks if block.kind == "exercise")
    author = FakeLlmClient.from_fn(_exercise_author_reply)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=[exercise.key])
    assert execute_regeneration(workspace, identity, RLock())["status"] == "waiting"
    proposal = read_open_proposal(workspace, lesson_id, read_workbench_source(workspace, lesson_id))
    assert proposal is not None
    assert proposal.candidate_review is not None

    decided = decide_proposal(workspace, lesson_id, proposal.proposal_id, "accept")
    current = read_workbench_source(workspace, lesson_id)
    reviews = read_workbench_record(workspace, "reviews", lesson_id, ReviewLog)

    assert decided.blocks[0].status == "accepted"
    assert digest_workbench_source(current) == proposal.candidate_review.digest
    assert any(run.digest == proposal.candidate_review.digest for run in reviews.runs)


def test_exercise_regeneration_proposal_given_keep_note_expect_alignment_cleared_without_source_change(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    before = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in before.blocks if block.kind == "exercise")
    author = FakeLlmClient.from_fn(_exercise_author_reply)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=[exercise.key])
    assert execute_regeneration(workspace, identity, RLock())["status"] == "waiting"
    proposal = read_open_proposal(workspace, lesson_id, before)
    assert proposal is not None
    note = "The exercise still asks learners to match the same lines."

    decided = decide_proposal(
        workspace, lesson_id, proposal.proposal_id, "keep", key=exercise.key, note=note, reason="still_matches"
    )
    after = read_workbench_source(workspace, lesson_id)
    alignment = next(
        state
        for state in ExerciseAlignment(workspace, lesson_id).states(after)
        if isinstance(state, ExerciseAligned) and state.exercise_key == exercise.key
    )

    assert after.files["exercises.yaml"] == before.files["exercises.yaml"]
    assert decided.blocks[0].status == "rejected"
    assert alignment.history[-1].outcome == "kept"
    assert alignment.history[-1].note == note


def test_keep_proposal_given_reason_without_comment_expect_alignment_decision(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    before = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in before.blocks if block.kind == "exercise")
    author = FakeLlmClient.from_fn(_exercise_author_reply)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=[exercise.key])
    assert execute_regeneration(workspace, identity, RLock())["status"] == "waiting"
    proposal = read_open_proposal(workspace, lesson_id, before)
    assert proposal is not None

    decided = decide_proposal(workspace, lesson_id, proposal.proposal_id, "keep", key=exercise.key, reason="unaffected")
    after = read_workbench_source(workspace, lesson_id)
    alignment = next(
        state
        for state in ExerciseAlignment(workspace, lesson_id).states(after)
        if isinstance(state, ExerciseAligned) and state.exercise_key == exercise.key
    )

    assert after.files["exercises.yaml"] == before.files["exercises.yaml"]
    assert decided.blocks[0].status == "rejected"
    assert alignment.history[-1].outcome == "kept"
    assert alignment.history[-1].reason == "unaffected"
    assert alignment.history[-1].comment is None


def test_exercise_regeneration_given_unchanged_item_expect_successful_kept_outcome(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    author = FakeLlmClient.from_fn(_unchanged_exercise_author_reply)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: author)
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=[exercise.key])

    result = execute_regeneration(workspace, identity, RLock())

    alignments = read_workbench_record(workspace, "exercise_alignments", lesson_id, ExerciseAlignmentLog)
    assert result["status"] == "done"
    assert result["decision"] == "kept unchanged by regeneration"
    assert result["kept_exercises"] == [exercise.key]
    assert len(author.calls) == 1
    assert alignments.records[-1].exercise_key == exercise.key
    assert alignments.records[-1].outcome == "kept"
    assert alignments.records[-1].note == "Kept unchanged by regeneration."
    prompt = author.calls[0][0]
    assert "Make the smallest necessary change" in prompt
    assert "Keep every field that is not affected by the section change" in prompt
    assert "If the exercise's purpose and answers are unaffected, return its current YAML unchanged" in prompt
    assert "EXACT BASELINE VS CURRENT SECTION DIFF" in prompt
    assert "CURRENT EXERCISE YAML" in prompt
    assert read_workbench_record(workspace, "proposals", lesson_id, ProposalLog).proposals == []


def test_older_unchanged_regeneration_given_newer_keep_decision_expect_newer_alignment_remains_pinned(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    first_change = source.files["lesson.md"].replace(
        "A first meeting can be short and still feel complete:",
        "A first meeting can be brief and still feel complete:",
        1,
    )
    write_workbench_file(workspace, lesson_id, "lesson.md", first_change)
    author = FakeLlmClient.from_fn(_unchanged_exercise_author_reply)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: author)
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=[exercise.key])

    second_change = first_change.replace("brief and still feel complete:", "brief and still feel warm:", 1)
    write_workbench_file(workspace, lesson_id, "lesson.md", second_change)
    kept = ExerciseAlignment(workspace, lesson_id).keep_current(
        read_workbench_source(workspace, lesson_id),
        exercise.key,
        "The exercise remains suitable.",
        reason="still_matches",
    )

    execute_regeneration(workspace, identity, RLock())

    states = ExerciseAlignment(workspace, lesson_id).states(read_workbench_source(workspace, lesson_id))
    alignment = next(state for state in states if state.exercise_key == exercise.key)
    alignments = read_workbench_record(workspace, "exercise_alignments", lesson_id, ExerciseAlignmentLog)
    assert isinstance(alignment, ExerciseAligned)
    assert alignment.history[-1].decision_id == kept.decision_id
    assert alignments.records[-1].note == "The exercise remains suitable."


def test_unchanged_regeneration_given_overlapping_alignment_mutation_expect_shared_lock_serializes_writes(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lesson_builder.workflow.workbench_regeneration import graph as graph_module

    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    exercise = next(block for block in source.blocks if block.kind == "exercise")
    author = FakeLlmClient.from_fn(_unchanged_exercise_author_reply)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: author)
    lock = RLock()
    identity = start_regeneration(workspace, lesson_id, None, lock, mode="exercises", exercise_keys=[exercise.key])
    alignment_started = Event()
    allow_alignment = Event()
    mutation_acquired = Event()
    result: dict[str, Any] = {}
    errors: list[Exception] = []
    original_pin = ExerciseAlignment.pin

    def pause_alignment(
        alignment: ExerciseAlignment,
        pin: ExerciseAlignmentPin,
        outcome: Literal["regenerated", "kept"],
        note: str | None = None,
        decision_id: str | None = None,
    ) -> ExerciseAlignmentRecord:
        alignment_started.set()
        if not allow_alignment.wait(K_TEST_HANG_GUARD_SECONDS):
            raise TimeoutError("alignment test was not released")
        return original_pin(alignment, pin, outcome, note, decision_id)

    def run_regeneration() -> None:
        try:
            result.update(execute_regeneration(workspace, identity, lock))
        except Exception as exc:
            errors.append(exc)

    def acquire_mutation_lock() -> None:
        with lock:
            mutation_acquired.set()

    monkeypatch.setattr(graph_module.ExerciseAlignment, "pin", pause_alignment)
    worker = Thread(target=run_regeneration, daemon=True)
    worker.start()
    assert alignment_started.wait(K_TEST_HANG_GUARD_SECONDS)

    mutation = Thread(target=acquire_mutation_lock, daemon=True)
    mutation.start()
    mutation_lock_available = lock.acquire(blocking=False)
    if mutation_lock_available:
        lock.release()
    try:
        assert not mutation_lock_available
        assert not mutation_acquired.is_set()
    finally:
        allow_alignment.set()
        worker.join(K_TEST_HANG_GUARD_SECONDS)
        mutation.join(K_TEST_HANG_GUARD_SECONDS)

    assert not worker.is_alive()
    assert not mutation.is_alive()
    assert errors == []
    assert result["status"] == "done"
    assert mutation_acquired.is_set()


def test_exercise_staleness_given_manual_exercise_edit_expect_section_pin_remains_stale(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    before = read_workbench_source(workspace, lesson_id)
    stale = next(
        state for state in ExerciseAlignment(workspace, lesson_id).states(before) if isinstance(state, ExerciseStale)
    )
    exercise = next(block for block in before.blocks if block.key == stale.exercise_key)
    edited_record = yaml.safe_load(exercise.source)[0]
    edited_record["prompt_md"] += " Keep this answer concise."
    edited_block = yaml.safe_dump([edited_record], allow_unicode=True, sort_keys=False).rstrip("\n")
    if exercise.source.endswith("\n"):
        edited_block += "\n"
    edited_source = before.files["exercises.yaml"].replace(exercise.source, edited_block, 1)
    write_workbench_file(workspace, lesson_id, "exercises.yaml", edited_source)

    after = read_workbench_source(workspace, lesson_id)

    assert after.files["exercises.yaml"] != before.files["exercises.yaml"]
    assert any(
        isinstance(state, ExerciseStale) and state.exercise_key == stale.exercise_key
        for state in ExerciseAlignment(workspace, lesson_id).states(after)
    )


def test_exercise_regeneration_given_two_failed_set_checks_expect_one_repair_and_failed_proposal(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    selected_keys = [block.key for block in source.blocks if block.kind == "exercise"][:2]
    author = FakeLlmClient.from_fn(_exercise_author_reply)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    from lesson_builder.workflow.workbench_regeneration import graph as graph_module

    monkeypatch.setattr(
        graph_module,
        "check_regeneration",
        lambda root, captured, proposal: ProposalCheck(status="failed", errors=["set check failed"]),
    )
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=selected_keys)

    result = execute_regeneration(workspace, identity, RLock())

    proposals = read_workbench_record(workspace, "proposals", lesson_id, ProposalLog).proposals
    assert result["status"] == "waiting"
    assert result["steps"] == ["generate", "check", "fix", "check", "review", "waiting for you"]
    assert len(author.calls) == 2 * len(selected_keys)
    assert len(proposals) == 1
    assert proposals[0].proposal_id == identity
    assert [block.key for block in proposals[0].blocks] == selected_keys
    assert len(proposals[0].retries) == 1
    assert proposals[0].check.status == "failed"


def test_exercise_regeneration_given_unchanged_repair_item_expect_item_dropped_and_marked_kept(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    selected_keys = [block.key for block in source.blocks if block.kind == "exercise"][:2]

    def repair_unchanged(prompt: str, model: str | None) -> str:
        if "Repair once against ORIGINAL source" in prompt and f"EXERCISE ITEM KEY: {selected_keys[0]}" in prompt:
            return _unchanged_exercise_author_reply(prompt, model)
        return _exercise_author_reply(prompt, model)

    author = FakeLlmClient.from_fn(repair_unchanged)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    from lesson_builder.workflow.workbench_regeneration import graph as graph_module

    monkeypatch.setattr(
        graph_module,
        "check_regeneration",
        lambda root, captured, proposal: ProposalCheck(status="failed", errors=["set check failed"]),
    )
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=selected_keys)

    result = execute_regeneration(workspace, identity, RLock())

    proposal = read_open_proposal(workspace, lesson_id, source)
    alignments = read_workbench_record(workspace, "exercise_alignments", lesson_id, ExerciseAlignmentLog)
    assert result["status"] == "waiting"
    assert result["steps"] == ["generate", "check", "fix", "check", "review", "waiting for you"]
    assert proposal is not None
    assert [block.key for block in proposal.blocks] == [selected_keys[1]]
    assert result["kept_exercises"] == [selected_keys[0]]
    assert alignments.records[-1].exercise_key == selected_keys[0]
    assert proposal.blocks[0].check.status == "failed"
    assert proposal.blocks[0].check.errors == ["set check failed"]


def _exercise_author_reply(prompt: str, _model: str | None) -> str:
    key = prompt.split("EXERCISE ITEM KEY: ", 1)[1].splitlines()[0]
    current_source = prompt.split("CURRENT EXERCISE YAML:\n", 1)[1].split(
        "\nEXACT BASELINE VS CURRENT SECTION DIFF", 1
    )[0]
    item = yaml.safe_load(current_source)[0]
    item["prompt_md"] = f"Regenerated: {key.replace('_', ' ')}"
    translations = [{"norwegian": item["prompt_md"], "english": item["prompt_md"]}]
    return json.dumps(
        {
            "reply": "Regenerated selected exercise",
            "edits": [
                {
                    "key": key,
                    "after_source": yaml.safe_dump([item], allow_unicode=True, sort_keys=False),
                    "note": "refresh",
                    "change_summary": "The prompt is shortened to match the revised section.",
                    "translations": translations,
                    "answer_impact": "none",
                }
            ],
        }
    )


def _unchanged_exercise_author_reply(prompt: str, _model: str | None) -> str:
    key = prompt.split("EXERCISE ITEM KEY: ", 1)[1].splitlines()[0]
    current_source = prompt.split("CURRENT EXERCISE YAML:\n", 1)[1].split(
        "\nEXACT BASELINE VS CURRENT SECTION DIFF", 1
    )[0]
    return json.dumps(
        {
            "reply": "No change",
            "edits": [{"key": key, "after_source": current_source, "note": "unchanged"}],
        }
    )


@dataclass(frozen=True)
class _SelectedExerciseRun:
    source: WorkbenchSource
    original_by_key: dict[str, str]
    selected_keys: list[str]
    unaffected_key: str
    author: FakeLlmClient
    checked_keys: list[list[str]]
    checked_candidate_sets: list[list[str]]
    identity: str
    result: dict[str, Any]
    proposal: Proposal


def _run_selected_exercise_regeneration(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> _SelectedExerciseRun:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    original_by_key = {block.key: block.source for block in source.blocks if block.kind == "exercise"}
    selected_keys = list(original_by_key)[:2]
    unaffected_key = next(key for key in original_by_key if key not in selected_keys)
    author = FakeLlmClient.from_fn(_exercise_author_reply)
    reviewer = FakeLlmClient.from_fn(workbench_review_reply)
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda job, **kwargs: author if job.name == "workbench_author" else reviewer,
    )
    checked_keys, checked_candidate_sets = _watch_exercise_regeneration_checks(monkeypatch)
    identity = start_regeneration(workspace, lesson_id, None, RLock(), mode="exercises", exercise_keys=selected_keys)
    result = execute_regeneration(workspace, identity, RLock())
    proposal = read_open_proposal(workspace, lesson_id, source)
    assert proposal is not None
    return _SelectedExerciseRun(
        source,
        original_by_key,
        selected_keys,
        unaffected_key,
        author,
        checked_keys,
        checked_candidate_sets,
        identity,
        result,
        proposal,
    )


def _watch_exercise_regeneration_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[list[str]], list[list[str]]]:
    from lesson_builder.application.operations import workbench_proposals as proposals_module
    from lesson_builder.workflow.workbench_regeneration import graph as graph_module

    original_check = graph_module.check_regeneration
    checked_keys: list[list[str]] = []

    def check_whole_set(root: Path, captured: dict[str, Any], proposal: Proposal) -> ProposalCheck:
        checked_keys.append([block.key for block in proposal.blocks])
        return original_check(root, captured, proposal)

    original_preview_check = proposals_module.check_preview
    checked_candidate_sets: list[list[str]] = []

    def check_candidate_set(view: AuthorPreview) -> ProposalCheck:
        checked_candidate_sets.append([block.key for block in split_exercise_source(view.exercise_source)])
        return original_preview_check(view)

    monkeypatch.setattr(graph_module, "check_regeneration", check_whole_set)
    monkeypatch.setattr(proposals_module, "check_preview", check_candidate_set)
    return checked_keys, checked_candidate_sets


def _assert_selected_exercise_generation(run: _SelectedExerciseRun, workspace: Path) -> None:
    assert run.result["status"] == "waiting"
    assert run.result["steps"] == ["generate", "check", "review", "waiting for you"]
    assert [block.key for block in run.proposal.blocks] == run.selected_keys
    assert run.proposal.check.status == "passed", run.proposal.check.errors
    assert [block.check.status for block in run.proposal.blocks] == ["passed", "passed"]
    assert all(block.change_summary for block in run.proposal.blocks)
    assert all(block.answer_impact == "none" for block in run.proposal.blocks)
    assert all(block.pin is not None and block.pin["changed_sections"] for block in run.proposal.blocks)
    assert run.checked_keys == [run.selected_keys]
    assert run.checked_candidate_sets == [list(run.original_by_key)]
    assert len(run.author.calls) == len(run.selected_keys)
    current = read_workbench_source(workspace, "greet_and_introduce_yourself")
    assert {block.key: block.source for block in current.blocks if block.kind == "exercise"} == run.original_by_key


def _assert_selected_exercise_replay(run: _SelectedExerciseRun, workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    replay = execute_regeneration(workspace, run.identity, RLock())
    log = read_workbench_record(workspace, "proposals", lesson_id, ProposalLog)
    assert replay["status"] == "waiting"
    assert len(log.proposals) == 1
    assert len(run.author.calls) == len(run.selected_keys)
    assert run.checked_keys == [run.selected_keys]


def _assert_selected_exercise_decisions(run: _SelectedExerciseRun, workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    decide_proposal(workspace, lesson_id, run.proposal.proposal_id, "accept", key=run.selected_keys[0])
    decided = decide_proposal(workspace, lesson_id, run.proposal.proposal_id, "reject", key=run.selected_keys[1])
    after = read_workbench_source(workspace, lesson_id)
    after_by_key = {block.key: block.source for block in after.blocks if block.kind == "exercise"}
    assert decided.status == "settled"
    assert [block.status for block in decided.blocks] == ["accepted", "rejected"]
    assert "Regenerated:" in after_by_key[run.selected_keys[0]]
    assert after_by_key[run.selected_keys[1]] == run.original_by_key[run.selected_keys[1]]
    assert after_by_key[run.unaffected_key] == run.original_by_key[run.unaffected_key]


def _stop_regeneration_review_calls_for_test(
    cancellation: CancellationToken,
    review_tokens: list[CancellationToken],
    worker: Thread,
) -> None:
    cancellation.cancel("author", "Regeneration test cleanup.")
    for token in review_tokens:
        token.cancel("shutdown", "Regeneration test cleanup.")
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
