"""Entry point: workbench job cancellation and shutdown behavior tests."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from select import select
from shutil import copytree
from threading import Event
from threading import RLock
from threading import Thread
from time import monotonic

import httpx
import pytest

from lesson_builder.application.operations.workbench_review import review_workbench_lesson
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.codex.types import CodexTurnInterrupted
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.web import workbench_jobs
from lesson_builder.web.settings import K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS
from lesson_builder.web.workbench_jobs import ChatJobControl
from lesson_builder.web.workbench_jobs import WorkbenchJobs
from tests.clients.codex.fakes import FakeCodexAppServer
from tests.settings import K_TEST_HANG_GUARD_SECONDS
from tests.settings import K_TEST_SUBPROCESS_STARTUP_TIMEOUT_SECONDS
from tests.settings import K_TEST_WORKBENCH_SHUTDOWN_EXIT_MARGIN_SECONDS


def test_workbench_shutdown_given_active_chat_worker_expect_interrupt_close_then_bounded_join(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeCodexAppServer()
    jobs = WorkbenchJobs(client)
    worker_entered = Event()
    release_worker = Event()
    close_finished = Event()
    events: list[str] = []
    monkeypatch.setattr(workbench_jobs, "K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS", 0.05)
    monkeypatch.setattr(
        ChatJobControl, "_interrupt_turn_async", staticmethod(lambda *_args: events.append("interrupt"))
    )
    original_close = client.close

    def close_client(timeout_seconds: float | None = None) -> None:
        events.append("close")
        original_close(timeout_seconds=timeout_seconds)

    monkeypatch.setattr(client, "close", close_client)

    def operation(control: ChatJobControl) -> None:
        control.set_interrupt("thread-1", "turn-1", client.interrupt)
        worker_entered.set()
        release_worker.wait()
        control.cancellation.raise_if_cancelled()

    jobs.submit_chat_turn("turn-1", operation)
    assert worker_entered.wait(K_TEST_HANG_GUARD_SECONDS)

    def close_jobs() -> None:
        try:
            jobs.close()
        finally:
            close_finished.set()

    closer = Thread(target=close_jobs, daemon=True)
    closer.start()
    try:
        assert client.closed or close_finished.wait(K_TEST_HANG_GUARD_SECONDS)
        assert events[:2] == ["interrupt", "close"]
        assert close_finished.wait(K_TEST_HANG_GUARD_SECONDS)
    finally:
        release_worker.set()
        closer.join(K_TEST_HANG_GUARD_SECONDS)

    assert client.closed
    assert not closer.is_alive()


def test_cancelable_progress_job_given_cancel_request_expect_cancel_signal_and_cancelled_status() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())
    entered = Event()

    def operation(_progress: Callable[[str], None], cancellation: CancellationToken) -> None:
        entered.set()
        cancellation.wait(K_TEST_HANG_GUARD_SECONDS)
        cancellation.raise_if_cancelled()

    identity = jobs.submit_cancelable_progress(operation)
    assert entered.wait(K_TEST_HANG_GUARD_SECONDS)
    assert jobs.find(identity) == {"status": "running", "can_cancel": True}
    assert jobs.cancel_job(identity)
    result = jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
    jobs.close()

    assert result == {"status": "cancelled", "can_cancel": False}


def test_workbench_shutdown_given_running_cancelable_job_expect_job_observes_cancel() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())
    entered = Event()
    observed_cancel = Event()

    def operation(_progress: Callable[[str], None], cancellation: CancellationToken) -> None:
        entered.set()
        cancellation.wait(K_TEST_HANG_GUARD_SECONDS)
        observed_cancel.set()
        cancellation.raise_if_cancelled()

    identity = jobs.submit_cancelable_progress(operation)
    assert entered.wait(K_TEST_HANG_GUARD_SECONDS)
    try:
        jobs.close()
    finally:
        if not observed_cancel.is_set():
            jobs.cancel_job(identity)

    assert observed_cancel.is_set()
    assert jobs.find(identity) == {"status": "cancelled", "can_cancel": False}


def test_workbench_shutdown_given_queued_cancelable_job_expect_job_cancelled_without_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workbench_jobs, "K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS", 0.05)
    jobs = WorkbenchJobs(FakeCodexAppServer())
    release_workers = Event()
    workers_started = [Event(), Event()]
    queued_job_started = Event()

    def occupy(index: int) -> None:
        workers_started[index].set()
        release_workers.wait()

    for index in range(2):
        jobs.submit(lambda index=index: occupy(index))
    assert all(event.wait(K_TEST_HANG_GUARD_SECONDS) for event in workers_started)
    identity = jobs.submit_cancelable_progress(lambda _progress, _cancellation: queued_job_started.set())

    try:
        jobs.close()
    finally:
        release_workers.set()

    assert jobs.find(identity) == {"status": "cancelled", "can_cancel": False}


def test_cancelable_job_given_provider_raises_cancelled_expect_job_cancelled_not_failed() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())

    def cancel_from_provider(_progress: Callable[[str], None], _cancellation: CancellationToken) -> None:
        raise Cancelled("author", "provider cancelled")

    identity = jobs.submit_cancelable_progress(cancel_from_provider)

    try:
        result = jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
    finally:
        jobs.close()

    assert result == {"status": "cancelled", "can_cancel": False}


def test_workbench_jobs_wait_given_running_job_and_timeout_expect_current_snapshot() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())
    entered = Event()
    release = Event()

    def operation() -> None:
        entered.set()
        release.wait()

    job_id = jobs.submit(operation)
    try:
        assert entered.wait(K_TEST_HANG_GUARD_SECONDS)
        assert jobs.wait(job_id, timeout=0) == {"status": "running", "can_cancel": False}
        release.set()
        assert jobs.wait(job_id, timeout=K_TEST_HANG_GUARD_SECONDS) == {
            "status": "done",
            "result": None,
            "can_cancel": False,
        }
    finally:
        release.set()
        jobs.close()


def test_workbench_job_given_provider_failure_expect_friendly_and_raw_snapshot_fields() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())
    exception = BackendDownException("Upstream request failed: Insufficient account funds")
    exception.provider_name = "Z.AI"

    def fail() -> None:
        raise exception

    identity = jobs.submit(fail)
    try:
        result = jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
    finally:
        jobs.close()

    assert result == {
        "status": "failed",
        "error": "Z.AI has no credit left. Top up the account, then retry.",
        "error_detail": "BackendDownException: Upstream request failed: Insufficient account funds",
        "can_cancel": False,
    }


def test_workbench_job_given_llm_parse_failure_expect_unreadable_output_and_raw_detail() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())
    exception = LlmParseException("no JSON in response; response was: '<untrusted output>'")
    exception.provider_name = "OpenAI"

    def fail() -> None:
        raise exception

    identity = jobs.submit(fail)
    try:
        result = jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
    finally:
        jobs.close()

    assert result == {
        "status": "failed",
        "error": "The model returned output the workbench could not read; try again.",
        "error_detail": "LlmParseException: no JSON in response; response was: '<untrusted output>'",
        "can_cancel": False,
    }


def test_workbench_job_given_review_of_uncompilable_lesson_expect_actionable_error_and_detail(tmp_path: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = Path(__file__).parents[2] / "content" / "lessons" / lesson_id
    copytree(source, tmp_path / "content" / "lessons" / lesson_id)
    (tmp_path / "content" / "lessons" / lesson_id / "lesson.md").write_text(
        "::: {.reading}\nUnclosed", encoding="utf-8"
    )
    jobs = WorkbenchJobs(FakeCodexAppServer())
    identity = jobs.submit(lambda: review_workbench_lesson(tmp_path, lesson_id, "lesson", None, RLock()))

    try:
        result = jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
    finally:
        jobs.close()

    assert result == {
        "status": "failed",
        "error": "AI review requires a compilable lesson; fix check errors first",
        "error_detail": "ValueError: AI review requires a compilable lesson; fix check errors first",
        "can_cancel": False,
    }


def test_workbench_job_given_unexpected_exception_expect_neutral_error_and_raw_detail() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())

    def fail() -> None:
        raise RuntimeError("internal storage path /srv/private/workbench")

    identity = jobs.submit(fail)
    try:
        result = jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
    finally:
        jobs.close()

    assert result == {
        "status": "failed",
        "error": "The workbench could not finish this job.",
        "error_detail": "RuntimeError: internal storage path /srv/private/workbench",
        "can_cancel": False,
    }


def test_workbench_job_given_audio_provider_transport_error_expect_provider_message_and_raw_detail() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())

    def fail() -> None:
        raise httpx.ConnectError("audio transport failed")

    identity = jobs.submit(fail, provider_name="Google Cloud Text-to-Speech")
    try:
        result = jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
    finally:
        jobs.close()

    assert result == {
        "status": "failed",
        "error": "The Google Cloud Text-to-Speech call failed.",
        "error_detail": "ConnectError: audio transport failed",
        "can_cancel": False,
    }


def test_workbench_job_given_interrupted_codex_turn_expect_interruption_message_and_raw_detail() -> None:
    jobs = WorkbenchJobs(FakeCodexAppServer())

    def fail() -> None:
        raise CodexTurnInterrupted("app-server reported an interrupted turn")

    identity = jobs.submit(fail, provider_name="Codex")
    try:
        result = jobs.wait(identity, timeout=K_TEST_HANG_GUARD_SECONDS)
    finally:
        jobs.close()

    assert result == {
        "status": "failed",
        "error": "The chat turn was interrupted.",
        "error_detail": "CodexTurnInterrupted: app-server reported an interrupted turn",
        "can_cancel": False,
    }


def test_workbench_shutdown_given_non_cancelable_long_job_expect_close_within_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workbench_jobs, "K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS", 0.05)
    jobs = WorkbenchJobs(FakeCodexAppServer())
    entered = Event()
    release = Event()
    worker_finished = Event()
    close_finished = Event()

    def operation() -> None:
        entered.set()
        release.wait()
        worker_finished.set()

    jobs.submit(operation)
    assert entered.wait(K_TEST_HANG_GUARD_SECONDS)

    def close_jobs() -> None:
        try:
            jobs.close()
        finally:
            close_finished.set()

    closer = Thread(target=close_jobs, daemon=True)
    closer.start()
    try:
        assert close_finished.wait(K_TEST_HANG_GUARD_SECONDS)
        assert not worker_finished.is_set()
    finally:
        release.set()
        closer.join(K_TEST_HANG_GUARD_SECONDS)

    assert not closer.is_alive()


def test_workbench_shutdown_given_remaining_deadline_expect_codex_close_receives_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(workbench_jobs, "K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS", 0.05)
    client = FakeCodexAppServer()
    jobs = WorkbenchJobs(client)

    jobs.close()

    assert client.close_timeout_seconds is not None
    assert 0 <= client.close_timeout_seconds <= 0.05


def test_workbench_process_given_long_non_cancelable_job_expect_exit_after_close_deadline() -> None:
    script = """
from threading import Event
from lesson_builder.web import workbench_jobs
from lesson_builder.web.workbench_jobs import WorkbenchJobs
from tests.settings import K_TEST_HANG_GUARD_SECONDS

workbench_jobs.K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS = 0.05
class Client:
    def close(self, timeout_seconds=None):
        pass

jobs = WorkbenchJobs(Client())
started = Event()
def operation():
    started.set()
    Event().wait(K_TEST_HANG_GUARD_SECONDS * 10)

jobs.submit(operation)
assert started.wait(K_TEST_HANG_GUARD_SECONDS)
jobs.close()
print("WORKBENCH_JOBS_CLOSED", flush=True)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    exit_bound_seconds = K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS + K_TEST_WORKBENCH_SHUTDOWN_EXIT_MARGIN_SECONDS

    try:
        startup_deadline = monotonic() + K_TEST_SUBPROCESS_STARTUP_TIMEOUT_SECONDS
        marker_seen = False
        while monotonic() < startup_deadline:
            readable, _, _ = select([process.stdout], [], [], max(0.0, startup_deadline - monotonic()))
            if not readable:
                break
            line = process.stdout.readline()
            if not line:
                break
            if line.rstrip("\n") == "WORKBENCH_JOBS_CLOSED":
                marker_seen = True
                break

        assert marker_seen, "subprocess did not reach WorkbenchJobs.close() within startup budget"
        exit_started = monotonic()
        return_code = process.wait(timeout=exit_bound_seconds)
        exit_elapsed = monotonic() - exit_started
        stderr = process.stderr.read() if process.stderr is not None else ""
        assert return_code == 0, stderr
        assert exit_elapsed < exit_bound_seconds
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
