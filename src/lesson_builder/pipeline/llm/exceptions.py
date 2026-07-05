"""Typed LLM exceptions shared by all LLM clients."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lesson_builder.pipeline.llm.base import AttemptOutcome


class LlmException(RuntimeError):
    """Base for all LLM-layer failures raised by clients."""

    # Populated by call_llm on failover exhaustion: the per-step attempt trace,
    # so telemetry can record a failed call. Empty for exceptions raised outside
    # a failover chain.
    attempts: tuple[AttemptOutcome, ...] = ()


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
