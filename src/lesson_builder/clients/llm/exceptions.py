"""Not a check itself — typed LLM exceptions shared by all LLM clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lesson_builder.clients.llm.base import AttemptOutcome


class LlmException(RuntimeError):
    """Base for all LLM-layer failures raised by clients."""

    # Populated by JobRunner after retry exhaustion so callers can inspect the
    # attempt trace. Empty for exceptions raised directly by a client.
    attempts: tuple[AttemptOutcome, ...] = ()
    provider_name: str | None = None


class LlmQuotaException(LlmException):
    """The backend refused the call due to a rate/quota/session limit."""


class LlmParseException(LlmException):
    """The backend returned text that could not be parsed into the requested schema."""


class BackendDownException(LlmException):
    """The backend is transport/auth-unavailable."""


__all__ = [
    "LlmException",
    "LlmQuotaException",
    "LlmParseException",
    "BackendDownException",
]
