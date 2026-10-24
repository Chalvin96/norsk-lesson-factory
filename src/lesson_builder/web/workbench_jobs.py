"""Entry point: `WorkbenchJobs` schedules slow authoring operations for HTTP polling."""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from queue import Queue
from threading import Condition
from threading import Lock
from threading import Thread
from threading import current_thread
from time import monotonic
from typing import Literal
from typing import cast
from uuid import uuid4

import httpx

from lesson_builder.application.operations.workbench_chat import WorkbenchChatBusy
from lesson_builder.application.operations.workbench_new_lesson import NewLessonFailure
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.codex.types import CodexAppServerError
from lesson_builder.clients.codex.types import CodexChatClient
from lesson_builder.clients.codex.types import CodexTurnInterrupted
from lesson_builder.clients.llm.exceptions import LlmException
from lesson_builder.clients.llm.failure_messages import format_provider_failure
from lesson_builder.clients.llm.invocation import provider_call_cancellation
from lesson_builder.web.settings import K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS
from lesson_builder.web.settings import K_WEB_WORKBENCH_WORKERS
from lesson_builder.web.workbench_wire import WorkbenchJobResponse

WorkbenchJobKind = Literal["provider", "cancelable_progress", "chat"]


@dataclass
class WorkbenchJob:
    """One process-local task; persisted authoring results live in the application store."""

    kind: WorkbenchJobKind
    status: Literal["queued", "running", "done", "failed", "cancelled"] = "queued"
    result: object = None
    error: str | None = None
    error_detail: str | None = None
    provider_name: str = "OpenCode"
    steps: list[str] = field(default_factory=list)
    check: dict[str, object] | None = None
    run_id: str | None = None
    chat_turn_id: str | None = None
    partial: str = ""
    cancellation: CancellationToken = field(default_factory=CancellationToken)


class ChatJobControl:
    """Thread-safe cancellation and partial-text channel for one persisted chat turn."""

    def __init__(
        self,
        cancellation: CancellationToken,
        on_partial: Callable[[str], None],
        on_run_linked: Callable[[str], None],
    ) -> None:
        self.cancellation = cancellation
        self._on_partial = on_partial
        self._on_run_linked = on_run_linked
        self._lock = Lock()
        self._interrupt: tuple[str, str] | None = None

    @property
    def is_cancelled(self) -> bool:
        return self.cancellation.is_cancelled

    def report_partial(self, partial: str) -> None:
        """Publish the currently decoded assistant reply prefix to job polling."""
        self._on_partial(partial)

    def link_run(self, run_id: str) -> None:
        """Expose a regeneration started by chat through the same polling job."""
        self._on_run_linked(run_id)

    def set_interrupt(self, thread_id: str, turn_id: str, interrupt: Callable[[str, str], None]) -> None:
        """Register the active app-server identifiers, honoring an earlier cancel request."""
        with self._lock:
            self._interrupt = (thread_id, turn_id)
        self.cancellation.set_interrupt_hook(lambda: self._interrupt_turn_async(thread_id, turn_id, interrupt))

    def clear_interrupt(self, thread_id: str, turn_id: str) -> None:
        """Forget a finished server turn so a late cancel cannot interrupt a later turn."""
        with self._lock:
            if self._interrupt == (thread_id, turn_id):
                self._interrupt = None
                self.cancellation.clear_interrupt_hook()

    def cancel(self) -> None:
        """Request cancellation and interrupt the active server turn when one exists."""
        self.cancellation.cancel("author", "Chat turn cancelled.")

    @staticmethod
    def _interrupt_turn_async(thread_id: str, turn_id: str, interrupt: Callable[[str, str], None]) -> None:
        Thread(
            target=ChatJobControl._interrupt_turn,
            args=(thread_id, turn_id, interrupt),
            name="workbench-chat-interrupt",
            daemon=True,
        ).start()

    @staticmethod
    def _interrupt_turn(thread_id: str, turn_id: str, interrupt: Callable[[str, str], None]) -> None:
        try:
            interrupt(thread_id, turn_id)
        except Exception:
            return


class WorkbenchJobs:
    """One bounded daemon worker pool and thread-safe polling registry per server process."""

    def __init__(self, codex_client: CodexChatClient) -> None:
        self._lock = Lock()
        self._condition = Condition(self._lock)
        self._jobs: dict[str, WorkbenchJob] = {}
        self._codex_client = codex_client
        self._chat_turns: dict[str, tuple[str, ChatJobControl]] = {}
        self._work_queue: Queue[tuple[str, Callable[[], None]] | None] = Queue()
        self._closing = False
        self._workers = tuple(
            Thread(target=self._worker_loop, name=f"workbench-{index + 1}", daemon=True)
            for index in range(K_WEB_WORKBENCH_WORKERS)
        )
        for worker in self._workers:
            worker.start()

    def submit(self, operation: Callable[[], object], *, provider_name: str = "OpenCode") -> str:
        """Queue one operation and return its id without waiting for a provider."""
        identity = uuid4().hex
        with self._lock:
            self._jobs[identity] = WorkbenchJob(kind="provider", provider_name=provider_name)
        self._submit_worker(identity, lambda: self._run(identity, operation))
        return identity

    def submit_progress(
        self,
        operation: Callable[[Callable[[str], None]], object],
        *,
        provider_name: str = "OpenCode",
    ) -> str:
        """Queue an operation that reports a thread-safe sequence of progress steps."""
        identity = uuid4().hex
        with self._lock:
            self._jobs[identity] = WorkbenchJob(kind="provider", provider_name=provider_name)
        self._submit_worker(
            identity, lambda: self._run(identity, lambda: operation(lambda step: self._progress(identity, step)))
        )
        return identity

    def submit_cancelable_progress(
        self,
        operation: Callable[[Callable[[str], None], CancellationToken], object],
        *,
        run_id: str | None = None,
        provider_name: str = "OpenCode",
    ) -> str:
        """Queue a progress-reporting operation that can stop at its own safe checkpoints."""
        identity = uuid4().hex
        cancellation = CancellationToken()
        with self._lock:
            self._jobs[identity] = WorkbenchJob(
                kind="cancelable_progress", run_id=run_id, cancellation=cancellation, provider_name=provider_name
            )
        self._submit_worker(
            identity,
            lambda: self._run_cancelable(
                identity,
                cancellation,
                lambda: operation(lambda step: self._progress(identity, step), cancellation),
            ),
        )
        return identity

    def find_active_job_for_run(self, run_id: str) -> str | None:
        """Find the cancelable process job currently executing one persisted run."""
        with self._lock:
            return next(
                (
                    identity
                    for identity, job in self._jobs.items()
                    if job.run_id == run_id and job.status in ("queued", "running")
                ),
                None,
            )

    def cancel_job(self, identity: str) -> bool:
        """Request cancellation only when the job kind has a safe stop mechanism."""
        control: ChatJobControl | None = None
        cancellation: CancellationToken | None = None
        with self._lock:
            job = self._jobs.get(identity)
            if job is None or job.status not in ("queued", "running"):
                return False
            if job.kind == "provider":
                raise ValueError("This job cannot be cancelled.")
            if job.kind == "chat":
                if job.chat_turn_id is None or (entry := self._chat_turns.get(job.chat_turn_id)) is None:
                    return False
                control = entry[1]
            else:
                cancellation = job.cancellation
        if control is not None:
            control.cancel()
        elif cancellation is not None:
            cancellation.cancel("author", "Job cancelled by author.")
        return True

    def submit_chat_turn(self, turn_id: str, operation: Callable[[ChatJobControl], object]) -> str:
        """Queue a persisted chat turn with partial updates and an app-server interrupt handle."""
        identity = uuid4().hex
        cancellation = CancellationToken()
        control = ChatJobControl(
            cancellation,
            lambda partial: self._partial(identity, partial),
            lambda run_id: self._link_run(identity, run_id),
        )
        with self._lock:
            self._jobs[identity] = WorkbenchJob(
                kind="chat", chat_turn_id=turn_id, cancellation=cancellation, provider_name="Codex"
            )
            self._chat_turns[turn_id] = (identity, control)
        self._submit_worker(identity, lambda: self._run_chat(identity, turn_id, control, operation))
        return identity

    def cancel_chat_turn(self, turn_id: str) -> bool:
        """Cancel a queued or active chat turn by its stable persisted id."""
        with self._lock:
            entry = self._chat_turns.get(turn_id)
            if entry is None:
                return False
            identity, control = entry
            job = self._jobs[identity]
            if job.status not in ("queued", "running"):
                return False
        control.cancel()
        return True

    @property
    def codex_client(self) -> CodexChatClient:
        """The one app-server client owned by this workbench server."""
        return self._codex_client

    def find(self, identity: str) -> WorkbenchJobResponse | None:
        """Return a stable snapshot, or None for an unknown job id."""
        with self._lock:
            job = self._jobs.get(identity)
            if job is None:
                return None
            return self._snapshot(job)

    def wait(self, job_id: str, timeout: float | None = None) -> WorkbenchJobResponse | None:
        """Wait for a job to finish or the timeout to elapse, then return its current snapshot."""
        deadline = None if timeout is None else monotonic() + max(timeout, 0.0)
        with self._condition:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            while job.status in ("queued", "running"):
                remaining = None if deadline is None else deadline - monotonic()
                if remaining is not None and remaining <= 0:
                    break
                self._condition.wait(remaining)
            return self._snapshot(job)

    def close(self) -> None:
        """Cancel supported work, close Codex, then join daemon workers to one deadline."""
        deadline = monotonic() + K_WEB_WORKBENCH_SHUTDOWN_JOIN_SECONDS
        with self._lock:
            if self._closing:
                return
            self._closing = True
            active_cancellations = self._signal_job_cancellation()
            for _worker in self._workers:
                self._work_queue.put(None)
        for cancellation in active_cancellations:
            cancellation.cancel("shutdown", "Workbench server is shutting down.")
        try:
            self._codex_client.close(timeout_seconds=max(0.0, deadline - monotonic()))
        finally:
            for worker in self._workers:
                if worker is not current_thread():
                    worker.join(max(0.0, deadline - monotonic()))

    def _signal_job_cancellation(self) -> list[CancellationToken]:
        active_cancellations = []
        for job in self._jobs.values():
            if job.status not in ("queued", "running"):
                continue
            if job.status == "running" and job.kind != "provider":
                active_cancellations.append(job.cancellation)
            if job.status == "queued":
                job.status = "cancelled"
                if job.chat_turn_id is not None:
                    self._chat_turns.pop(job.chat_turn_id, None)
                self._condition.notify_all()
        return active_cancellations

    def _submit_worker(self, identity: str, operation: Callable[[], None]) -> None:
        with self._lock:
            job = self._jobs[identity]
            if self._closing:
                job.status = "cancelled"
                if job.chat_turn_id is not None:
                    self._chat_turns.pop(job.chat_turn_id, None)
                self._condition.notify_all()
            else:
                self._work_queue.put((identity, operation))

    def _worker_loop(self) -> None:
        while True:
            task = self._work_queue.get()
            try:
                if task is None:
                    return
                identity, operation = task
                self._run_worker(identity, operation)
            finally:
                self._work_queue.task_done()

    def _run_worker(self, identity: str, operation: Callable[[], None]) -> None:
        with self._lock:
            job = self._jobs[identity]
            if self._closing or job.status != "queued":
                if job.status == "queued":
                    job.status = "cancelled"
                    self._condition.notify_all()
                return
            job.status = "running"
        operation()

    def _link_run(self, identity: str, run_id: str) -> None:
        with self._lock:
            self._jobs[identity].run_id = run_id

    def _progress(self, identity: str, step: str) -> None:
        with self._lock:
            self._jobs[identity].steps.append(step)

    def _partial(self, identity: str, partial: str) -> None:
        with self._lock:
            self._jobs[identity].partial = partial

    def _run_chat(
        self,
        identity: str,
        turn_id: str,
        control: ChatJobControl,
        operation: Callable[[ChatJobControl], object],
    ) -> None:
        with self._lock:
            self._jobs[identity].status = "running"
        try:
            with provider_call_cancellation(control.cancellation):
                result = operation(control)
        except Cancelled:
            with self._lock:
                job = self._jobs[identity]
                job.partial = ""
                job.status = "cancelled"
                self._chat_turns.pop(turn_id, None)
                self._condition.notify_all()
            return
        except Exception as exc:
            self._record_failure(identity, exc)
            with self._lock:
                self._chat_turns.pop(turn_id, None)
            return
        with self._lock:
            job = self._jobs[identity]
            job.result = result
            job.status = "done"
            self._chat_turns.pop(turn_id, None)
            self._condition.notify_all()

    def _run(self, identity: str, operation: Callable[[], object]) -> None:
        with self._lock:
            self._jobs[identity].status = "running"
        try:
            result = operation()
        except Exception as exc:
            self._record_failure(identity, exc)
            return
        with self._lock:
            job = self._jobs[identity]
            job.result = result
            job.status = "done"
            self._condition.notify_all()

    def _run_cancelable(self, identity: str, cancellation: CancellationToken, operation: Callable[[], object]) -> None:
        with self._lock:
            self._jobs[identity].status = "running"
        try:
            with provider_call_cancellation(cancellation):
                result = operation()
        except Cancelled:
            with self._lock:
                self._jobs[identity].status = "cancelled"
                self._condition.notify_all()
            return
        except Exception as exc:
            self._record_failure(identity, exc)
            return
        with self._lock:
            job = self._jobs[identity]
            job.result = result
            job.status = "done"
            self._condition.notify_all()

    def _record_failure(self, identity: str, exc: Exception) -> None:
        frames = traceback.extract_tb(exc.__traceback__)
        locations = "\n".join(f"  File {frame.filename}, line {frame.lineno}, in {frame.name}" for frame in frames)
        print(
            f"Workbench job {identity} failed: {type(exc).__name__}\nTraceback (most recent call last):\n{locations}",
            file=sys.stderr,
        )
        with self._lock:
            job = self._jobs[identity]
            job.error_detail = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, NewLessonFailure):
                job.error = str(exc) or "The workbench could not finish this job."
                job.check = exc.check.model_dump(mode="json")
                job.error_detail += "\n" + "\n".join(exc.check.errors)
            elif isinstance(exc, CodexTurnInterrupted):
                job.error = "The chat turn was interrupted."
            elif isinstance(exc, LlmException):
                job.error = format_provider_failure(exc, exc.provider_name or job.provider_name)
            elif isinstance(exc, CodexAppServerError) or (
                isinstance(exc, httpx.HTTPError) and job.provider_name == "Google Cloud Text-to-Speech"
            ):
                job.error = format_provider_failure(exc, job.provider_name)
            elif isinstance(exc, (ValueError, WorkbenchChatBusy)):
                job.error = str(exc) or "The workbench could not finish this job."
            else:
                job.error = "The workbench could not finish this job."
            job.status = "failed"
            self._condition.notify_all()

    def _snapshot(self, job: WorkbenchJob) -> WorkbenchJobResponse:
        can_cancel = job.kind != "provider" and job.status in ("queued", "running")
        if job.status == "done":
            snapshot: dict[str, object] = {"status": "done", "result": job.result, "can_cancel": False}
        elif job.status == "failed":
            snapshot = {
                "status": "failed",
                "error": job.error or "The workbench could not finish this job.",
                "error_detail": job.error_detail or "",
                "can_cancel": False,
            }
            if job.check:
                snapshot["check"] = job.check
        elif job.status == "cancelled":
            snapshot = {"status": "cancelled", "can_cancel": False}
        else:
            snapshot = {"status": job.status, "can_cancel": can_cancel}
            if job.chat_turn_id is not None:
                snapshot["turn_id"] = job.chat_turn_id
                snapshot["partial"] = job.partial
        if job.steps:
            snapshot["steps"] = list(job.steps)
        if job.run_id:
            snapshot["run_id"] = job.run_id
        return cast(WorkbenchJobResponse, snapshot)
