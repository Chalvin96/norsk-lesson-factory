"""Typed LLM exceptions shared by all LLM clients."""

from __future__ import annotations


class LlmException(RuntimeError):
    """Base for all LLM-layer failures raised by clients."""


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
