"""Entry point: `CodexAppServerClient` adapts the Codex stdio app-server for workbench chat."""

from lesson_builder.clients.codex.app_server import CodexAppServerClient
from lesson_builder.clients.codex.chat_settings import CodexChatSettings
from lesson_builder.clients.codex.chat_settings import load_workbench_chat_settings
from lesson_builder.clients.codex.settings import K_CODEX_WORKBENCH_CHAT_EFFORT_OPTIONS
from lesson_builder.clients.codex.settings import K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS
from lesson_builder.clients.codex.types import CodexAppServerError
from lesson_builder.clients.codex.types import CodexChatClient
from lesson_builder.clients.codex.types import CodexTurnInterrupted
from lesson_builder.clients.codex.types import CodexTurnResult

__all__ = [
    # Client and settings
    "CodexAppServerClient",
    "CodexChatSettings",
    "K_CODEX_WORKBENCH_CHAT_EFFORT_OPTIONS",
    "K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS",
    "load_workbench_chat_settings",
    # Results and failures
    "CodexAppServerError",
    "CodexChatClient",
    "CodexTurnInterrupted",
    "CodexTurnResult",
]
