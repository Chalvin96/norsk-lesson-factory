"""Not a check itself — typed behavior fakes for LLM client tests.

``FakeOpencodeProcess`` is a ``Popen`` stand-in with controllable termination;
``FakeLlmClient`` is the canonical LLM fake (AGENTS.md "Test data and behavior
fakes"), driven by an ``fn(prompt, model)`` closure with ``responding`` /
``raising`` / ``from_fn`` constructors; ``fake_job_runner`` wraps one client
in a single-hop ``JobRunner``.
"""

from __future__ import annotations

import io
import signal
import subprocess
import threading
from collections.abc import Callable
from collections.abc import Sequence
from typing import IO

from lesson_builder.clients.llm.base import BaseLlmClient
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.clients.llm.invocation import JobRunner


class FakeHangingTextStream(io.TextIOBase):
    """A text stream that yields ``lines`` and then blocks until closed."""

    def __init__(self, lines: Sequence[str]) -> None:
        self._lines = list(lines)
        self._released = threading.Event()

    def readline(self, size: int | None = -1) -> str:
        del size
        if self._lines:
            return self._lines.pop(0)
        self._released.wait()
        return ""

    def close(self) -> None:
        self._released.set()
        super().close()


class FakeOpencodeProcess:
    """Small `Popen` fake with controllable termination behavior."""

    def __init__(
        self,
        *,
        pid: int,
        stdout: str = "",
        stderr: str = "",
        returncode: int | None = 0,
        exit_on_terminate: bool = True,
        stdout_stream: IO[str] | None = None,
    ) -> None:
        self.pid = pid
        self.stdout = stdout_stream if stdout_stream is not None else io.StringIO(stdout)
        self.stderr = io.StringIO(stderr)
        self.returncode = returncode
        self.exit_on_terminate = exit_on_terminate
        self.signals: list[signal.Signals] = []
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="opencode", timeout=timeout)
        return self.returncode

    def receive_signal(self, sent_signal: signal.Signals) -> None:
        self.signals.append(sent_signal)
        if sent_signal == signal.SIGKILL or self.exit_on_terminate:
            self.returncode = -int(sent_signal)


class FakeCompletedProcess(subprocess.CompletedProcess[str]):
    """Typed completed-process result for OpenCode adapter tests."""

    def __init__(self, *, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        super().__init__(args=["fake-cli"], returncode=returncode, stdout=stdout, stderr=stderr)


# fn(prompt, model) -> response text (or raises to simulate a failing backend).
LlmCallFn = Callable[[str, "str | None"], str]


class FakeLlmClient(BaseLlmClient):
    """Behavior-driven fake LLM client.

    Driven by an ``fn(prompt, model)`` closure so one class covers every case:
    canned, echo, conditional, raise, malformed JSON. Records every call on
    ``calls`` so interaction can be asserted without ``unittest.mock``.
    """

    def __init__(self, fn: LlmCallFn | None = None, *, name: str = "fake") -> None:
        self.name = name
        self._fn: LlmCallFn = fn or (lambda prompt, model: "")
        self.calls: list[tuple[str, str | None]] = []

    def call(
        self,
        prompt: str,
        *,
        model: str | None = None,
        agent: str | None = None,
        variant: str | None = None,
    ) -> LlmResponse:
        self.calls.append((prompt, model))
        return LlmResponse(
            text=self._fn(prompt, model),
            client=self.name,
            model=model or "fake-model",
            agent=agent,
            variant=variant,
        )

    @classmethod
    def responding(cls, response: str, *, name: str = "fake") -> FakeLlmClient:
        """A client that always returns ``response``."""
        return cls(lambda prompt, model: response, name=name)

    @classmethod
    def raising(cls, exc: Exception, *, name: str = "fake") -> FakeLlmClient:
        """A client whose every call raises ``exc`` (e.g. a down/quota backend)."""

        def _raise(prompt: str, model: str | None) -> str:
            raise exc

        return cls(_raise, name=name)

    @classmethod
    def from_fn(cls, fn: LlmCallFn, *, name: str = "fake") -> FakeLlmClient:
        """A client driven by an arbitrary ``fn(prompt, model)`` closure."""
        return cls(fn, name=name)


def fake_job_runner(client: BaseLlmClient) -> JobRunner:
    """A ``JobRunner`` backed by one injected client."""
    return JobRunner(name=client.name, client=client)
