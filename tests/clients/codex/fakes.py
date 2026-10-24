"""Not a check itself — typed fake app-server turns for workbench chat behavior tests."""

from __future__ import annotations

import json
import subprocess
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from queue import Queue
from threading import Event
from threading import Lock

from lesson_builder.clients.codex.chat_settings import CodexChatSettings
from lesson_builder.clients.codex.types import CodexAppServerError
from lesson_builder.clients.codex.types import CodexTurnInterrupted
from lesson_builder.clients.codex.types import CodexTurnResult
from lesson_builder.clients.codex.types import DeltaHandler
from lesson_builder.clients.codex.types import JsonObject
from lesson_builder.clients.codex.types import ThreadHandler
from lesson_builder.clients.codex.types import TurnHandler
from tests.settings import K_TEST_HANG_GUARD_SECONDS


@dataclass
class FakeCodexGate:
    """Pause a scripted turn after its deltas so cancellation races are deterministic."""

    entered: Event
    release: Event

    @classmethod
    def create(cls) -> FakeCodexGate:
        """Create a gate with unset entered and release events."""
        return cls(Event(), Event())


@dataclass(frozen=True)
class FakeCodexTurn:
    """One complete structured response or provider failure for the fake server."""

    text: str | None = None
    error: Exception | None = None
    gate: FakeCodexGate | None = None
    deltas: tuple[str, ...] = ()


@dataclass(frozen=True)
class FakeCodexCall:
    """Captured request data for assertions about current context and thread reuse."""

    thread_id: str | None
    settings: CodexChatSettings
    prompt: str
    output_schema: JsonObject
    server_thread_id: str
    server_turn_id: str


class FakeCodexAppServer:
    """Typed chat client fake that drives thread, turn, delta, interrupt and failure behavior."""

    def __init__(self, *turns: FakeCodexTurn) -> None:
        self._turns = deque(turns)
        self._lock = Lock()
        self._cancelled: set[str] = set()
        self._active_gates: dict[str, FakeCodexGate] = {}
        self._next_turn = 0
        self.calls: list[FakeCodexCall] = []
        self.interrupts: list[tuple[str, str]] = []
        self.closed = False
        self.close_timeout_seconds: float | None = None

    def run_turn(
        self,
        *,
        settings: CodexChatSettings,
        thread_id: str | None,
        prompt: str,
        output_schema: JsonObject,
        on_thread_id: ThreadHandler,
        on_turn_started: TurnHandler,
        on_delta: DeltaHandler,
    ) -> CodexTurnResult:
        """Drive one persisted thread turn and publish deterministic stream chunks."""
        with self._lock:
            if not self._turns:
                raise CodexAppServerError("fake app-server has no scripted turn")
            turn = self._turns.popleft()
            self._next_turn += 1
            index = self._next_turn
        server_thread_id = thread_id or "fake-thread-1"
        server_turn_id = f"fake-turn-{index}"
        if turn.gate is not None:
            with self._lock:
                self._active_gates[server_turn_id] = turn.gate
        on_thread_id(server_thread_id)
        on_turn_started(server_thread_id, server_turn_id)
        self.calls.append(FakeCodexCall(thread_id, settings, prompt, output_schema, server_thread_id, server_turn_id))
        for delta in turn.deltas:
            on_delta(delta)
        if turn.gate is not None:
            turn.gate.entered.set()
            turn.gate.release.wait(K_TEST_HANG_GUARD_SECONDS)
        with self._lock:
            interrupted = server_turn_id in self._cancelled
            self._active_gates.pop(server_turn_id, None)
        if interrupted:
            raise CodexTurnInterrupted("fake turn interrupted")
        if turn.error is not None:
            raise turn.error
        if turn.text is None:
            raise CodexAppServerError("fake turn is missing structured output")
        return CodexTurnResult(server_thread_id, server_turn_id, turn.text)

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        """Interrupt a scripted turn and release any gate waiting on completion."""
        with self._lock:
            self.interrupts.append((thread_id, turn_id))
            self._cancelled.add(turn_id)
            gate = self._active_gates.get(turn_id)
        if gate is not None:
            gate.release.set()

    def close(self, timeout_seconds: float | None = None) -> None:
        """Record that the owning server closed the client."""
        self.closed = True
        self.close_timeout_seconds = timeout_seconds


class FakeCodexAppServerProcess:
    """Speak the app-server JSON-RPC protocol through the public client process boundary."""

    def __init__(
        self,
        *,
        response_handler: Callable[[str, dict[str, object]], dict[str, object] | None] | None = None,
        ignore_terminate: bool = False,
    ) -> None:
        self._output: Queue[str] = Queue()
        self._lock = Lock()
        self.stdout = iter(self._output.get, "")
        self.stdin = _FakeCodexAppServerInput(self)
        self.returncode: int | None = None
        self.turn_start_payloads: list[dict[str, object]] = []
        self.requests: list[dict[str, object]] = []
        self.client_messages: list[dict[str, object]] = []
        self.suppressed_methods: set[str] = set()
        self.response_handler = response_handler
        self.ignore_terminate = ignore_terminate
        self.termination_requested = Event()
        self.killed = Event()
        self.client_message_received = Event()
        self.request_received = Event()
        self._next_thread = 0
        self._next_turn = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.termination_requested.set()
        if not self.ignore_terminate:
            self._finish(-15)

    def kill(self) -> None:
        self.killed.set()
        self._finish(-9)

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="codex app-server", timeout=timeout)
        return self.returncode

    def complete_turn(self, thread_id: str, turn_id: str, text: str) -> None:
        self.send_message(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": thread_id,
                    "turn": {
                        "id": turn_id,
                        "status": "completed",
                        "items": [{"type": "agentMessage", "text": text}],
                    },
                },
            }
        )

    def send_message(self, message: dict[str, object]) -> None:
        self._output.put(json.dumps(message))

    def send_server_request(self, identity: int | str, method: str, params: dict[str, object]) -> None:
        self.send_message({"jsonrpc": "2.0", "id": identity, "method": method, "params": params})

    def finish_stream(self) -> None:
        self._output.put("")

    def _finish(self, returncode: int) -> None:
        with self._lock:
            self.returncode = returncode
        self.finish_stream()

    def exit(self, returncode: int = 1) -> None:
        """End the fake subprocess as if the external server exited unexpectedly."""
        self._finish(returncode)

    def receive_request(self, line: str) -> None:
        request = json.loads(line)
        with self._lock:
            self.requests.append(request)
        self.request_received.set()
        method = request.get("method")
        if not isinstance(method, str):
            with self._lock:
                self.client_messages.append(request)
            self.client_message_received.set()
            return
        params = request.get("params", {})
        params = params if isinstance(params, dict) else {}
        if method == "turn/start":
            with self._lock:
                self.turn_start_payloads.append(params)
        if "id" not in request:
            return
        if method in self.suppressed_methods:
            return
        result = self.response_handler(method, params) if self.response_handler is not None else None
        if result is None:
            result = self._default_response(method)
        self._output.put(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}))

    def _default_response(self, method: str) -> dict[str, object]:
        if method == "config/read":
            return {"config": {"mcp_servers": {}, "apps": {"_default": {"enabled": False}}}, "origins": {}}
        if method == "mcpServerStatus/list":
            return {"data": [], "nextCursor": None}
        if method == "app/installed":
            return {"apps": []}
        if method == "plugin/installed":
            return {"marketplaces": []}
        if method in {"thread/start", "thread/resume"}:
            with self._lock:
                self._next_thread += 1
                thread_id = f"thread-{self._next_thread}"
            return {"thread": {"id": thread_id}}
        if method == "turn/start":
            with self._lock:
                self._next_turn += 1
                turn_id = f"turn-{self._next_turn}"
            return {"turn": {"id": turn_id}}
        return {}


class _FakeCodexAppServerInput:
    """Route app-server stdin writes to the JSON-RPC fake."""

    def __init__(self, process: FakeCodexAppServerProcess) -> None:
        self._process = process

    def write(self, line: str) -> int:
        self._process.receive_request(line)
        return len(line)

    def flush(self) -> None:
        return


__all__ = [
    "FakeCodexAppServer",
    "FakeCodexAppServerProcess",
    "FakeCodexCall",
    "FakeCodexGate",
    "FakeCodexTurn",
]
