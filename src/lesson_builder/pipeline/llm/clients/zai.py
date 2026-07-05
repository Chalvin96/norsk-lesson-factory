"""HTTP client for Z.AI GLM via its Anthropic-compatible messages API.

Talks to Z.AI directly at ``$GLM_BASE_URL/v1/messages`` with ``$GLM_API_KEY``.
It uses the Anthropic message format (not OpenAI chat-completions), hence a
custom ``call`` rather than ``BaseLlmApi``.
"""

from __future__ import annotations

import os
from http import HTTPStatus
from typing import Any

import httpx

from lesson_builder.pipeline.llm.base import BaseLlmClient, LlmResponse, post_with_transient_retry
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException
from lesson_builder.settings import (
    K_ZAI_API_KEY_ENV,
    K_ZAI_BASE_URL_ENV,
    K_ZAI_DEFAULT_BASE_URL,
    K_ZAI_DEFAULT_MODEL,
    K_ZAI_DEFAULT_TIMEOUT,
    K_ZAI_MAX_TOKENS,
    K_ZAI_QUOTA_PATTERNS,
    K_ZAI_TIMEOUT_ENV,
)

_QUOTA_STATUS = (HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.PAYMENT_REQUIRED)
_AUTH_STATUS = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)


class ZaiClient(BaseLlmClient):
    name = "zai"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float | None = None,
        max_tokens: int = K_ZAI_MAX_TOKENS,
        quota_patterns: tuple[str, ...] = K_ZAI_QUOTA_PATTERNS,
    ) -> None:
        self.base_url = base_url or os.environ.get(K_ZAI_BASE_URL_ENV, K_ZAI_DEFAULT_BASE_URL)
        self.default_model = default_model or K_ZAI_DEFAULT_MODEL
        self.timeout = (
            timeout
            if timeout is not None
            else float(os.environ.get(K_ZAI_TIMEOUT_ENV, str(K_ZAI_DEFAULT_TIMEOUT)))
        )
        self.max_tokens = max_tokens
        self.quota_patterns = quota_patterns

    def api_key(self) -> str:
        key = os.environ.get(K_ZAI_API_KEY_ENV, "")
        if not key:
            raise BackendDownException(f"{K_ZAI_API_KEY_ENV} is not set")
        return key

    def call(self, prompt_text: str, *, model: str | None = None) -> LlmResponse:
        chosen_model = model or self.default_model
        response = post_with_transient_retry(
            lambda: httpx.post(
                f"{self.base_url.rstrip('/')}/v1/messages",
                headers={
                    "x-api-key": self.api_key(),
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": chosen_model,
                    "max_tokens": self.max_tokens,
                    "messages": [{"role": "user", "content": prompt_text}],
                },
                timeout=self.timeout,
            ),
            label="zai",
            should_retry=lambda r: not self.matches_quota(r.text),
        )

        if response.status_code < HTTPStatus.BAD_REQUEST:
            text, usage = self._content(response)
            return LlmResponse(
                text=text,
                client="zai",
                model=chosen_model,
                input_tokens=usage.get("input_tokens"),
                output_tokens=usage.get("output_tokens"),
            )
        if response.status_code in _QUOTA_STATUS or self.matches_quota(response.text):
            raise LlmQuotaException(f"zai quota: {response.status_code}")
        if response.status_code in _AUTH_STATUS:
            raise BackendDownException(f"zai auth error: {response.status_code}")
        raise BackendDownException(
            f"zai error {response.status_code}: {response.text[:200]}"
        )

    @staticmethod
    def _content(response: httpx.Response) -> tuple[str, dict[str, Any]]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BackendDownException("zai returned invalid JSON") from exc
        blocks = payload.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text:
            raise BackendDownException("zai returned empty content")
        usage = payload.get("usage") or {}
        return text, usage
