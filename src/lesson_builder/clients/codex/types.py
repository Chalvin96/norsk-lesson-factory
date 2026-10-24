"""Not a check itself — typed transport values for the Codex app-server client."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import Protocol

from lesson_builder.clients.codex.chat_settings import CodexChatSettings

JsonObject = dict[str, Any]
DeltaHandler = Callable[[str], None]
ThreadHandler = Callable[[str], None]
TurnHandler = Callable[[str, str], None]


class CodexChatClient(Protocol):
    """The app-server operations used by workbench chat and its job controller."""

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
    ) -> CodexTurnResult: ...

    def interrupt(self, thread_id: str, turn_id: str) -> None: ...

    def close(self, timeout_seconds: float | None = None) -> None: ...


@dataclass(frozen=True)
class CodexTurnResult:
    """Completed structured output and Codex identifiers for one server turn."""

    thread_id: str
    turn_id: str
    text: str


class CodexAppServerError(RuntimeError):
    """The app-server process or JSON-RPC exchange failed."""


class CodexTurnInterrupted(CodexAppServerError):
    """Codex reported that the current turn was interrupted."""


__all__ = [
    "CodexAppServerError",
    "CodexChatClient",
    "CodexTurnInterrupted",
    "CodexTurnResult",
    "DeltaHandler",
    "JsonObject",
    "ThreadHandler",
    "TurnHandler",
]
