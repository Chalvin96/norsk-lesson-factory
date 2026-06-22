"""HTTP client for OpenRouter's OpenAI-compatible chat-completions API."""

from __future__ import annotations

import os
from http import HTTPStatus

from lesson_builder.pipeline.llm.base import BaseLlmApi
from lesson_builder.pipeline.llm.exceptions import BackendDownException
from lesson_builder.settings import (
    K_OPENROUTER_DEFAULT_BASE_URL,
    K_OPENROUTER_DEFAULT_MODEL,
    K_OPENROUTER_DEFAULT_TIMEOUT,
    K_OPENROUTER_QUOTA_PATTERNS,
)


class OpenrouterClient(BaseLlmApi):
    name = "openrouter"
    quota_status_codes = (HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.PAYMENT_REQUIRED)

    def __init__(
        self,
        *,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float | None = None,
        quota_patterns: tuple[str, ...] = K_OPENROUTER_QUOTA_PATTERNS,
    ) -> None:
        self.base_url = (
            base_url if base_url is not None else os.environ.get("OPENROUTER_BASE_URL", K_OPENROUTER_DEFAULT_BASE_URL)
        )
        self.default_model = (
            default_model
            if default_model is not None
            else os.environ.get("OPENROUTER_MODEL", K_OPENROUTER_DEFAULT_MODEL)
        )
        self.timeout = (
            timeout
            if timeout is not None
            else float(os.environ.get("OPENROUTER_TIMEOUT_SECONDS", str(K_OPENROUTER_DEFAULT_TIMEOUT)))
        )
        self.quota_patterns = quota_patterns

    def api_key(self) -> str:
        key_env = "OPENROUTER_API_KEY"
        key = os.environ.get(key_env, "")
        if not key:
            raise BackendDownException(f"{key_env} is not set")
        return key
