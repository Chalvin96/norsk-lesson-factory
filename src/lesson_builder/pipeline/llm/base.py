"""Shared LLM client contracts and the mechanics every client leans on.

Anything used by more than one client, or by ``invocation.py``, lives here
instead of getting its own file. See ``README.md`` in this package for the
boundary rules.
"""

from __future__ import annotations

import json
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, cast

import httpx
from json_repair import loads as repair_json_loads

from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException
from lesson_builder.settings import K_LLM_TRANSIENT_BACKOFF_SECONDS, K_LLM_TRANSIENT_RETRIES


@dataclass(frozen=True)
class LlmResponse:
    """Envelope returned by every LLM call: the text plus provenance/usage.

    ``latency_ms`` and ``attempts`` are filled in by ``call_llm`` (the per-step
    wall time and the full failover attempt trace); individual clients leave
    them at their defaults.
    """

    text: str
    client: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    attempts: tuple[AttemptOutcome, ...] = ()


@dataclass(frozen=True)
class AttemptOutcome:
    """One tried hop in a failover chain: which client/model, and what happened."""

    client: str
    model: str
    outcome: str  # "ok" | "quota" | "down"
    error: str | None = None


class BaseLlmClient(ABC):
    """Common call contract for every LLM adapter."""

    name: str
    quota_patterns: tuple[str, ...] = ()

    def matches_quota(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(pattern in lowered for pattern in self.quota_patterns)

    @abstractmethod
    def call(self, prompt: str, *, model: str | None = None) -> LlmResponse: ...


class BaseLlmCli(BaseLlmClient):
    """Base for adapters expected to invoke a local CLI."""


class BaseLlmApi(BaseLlmClient):
    """Base API adapter for OpenAI-compatible chat-completions providers."""

    base_url: str
    default_model: str
    timeout: float
    quota_status_codes: tuple[HTTPStatus, ...] = (HTTPStatus.TOO_MANY_REQUESTS,)

    @abstractmethod
    def api_key(self) -> str: ...

    def transport_label(self) -> str:
        return self.name

    def call(self, prompt_text: str, *, model: str | None = None) -> LlmResponse:
        chosen_model = model or self.default_model
        label = self.transport_label()
        response = post_with_transient_retry(
            lambda: httpx.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key()}"},
                json={"model": chosen_model, "messages": [{"role": "user", "content": prompt_text}]},
                timeout=self.timeout,
            ),
            label=label,
            should_retry=lambda r: not self.matches_quota(r.text),
        )
        if response.status_code < HTTPStatus.BAD_REQUEST:
            text, usage = self._content(response, label)
            return LlmResponse(
                text=text,
                client=self.name,
                model=chosen_model,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
            )
        if response.status_code in self.quota_status_codes or self.matches_quota(response.text):
            raise LlmQuotaException(f"{label} quota: {response.status_code}")
        if response.status_code in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            raise BackendDownException(f"{label} auth error: {response.status_code}")
        if response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            raise BackendDownException(f"{label} server error: {response.status_code}")
        raise BackendDownException(f"{label} client error: {response.status_code}: {response.text[:200]}")

    @staticmethod
    def _content(response: httpx.Response, label: str) -> tuple[str, dict[str, Any]]:
        try:
            response_payload = response.json()
        except ValueError as exc:
            raise BackendDownException(f"{label} returned invalid JSON") from exc
        choices = response_payload.get("choices") or []
        if not choices:
            raise BackendDownException(f"{label} returned no choices")
        content = choices[0].get("message", {}).get("content")
        if not content:
            raise BackendDownException(f"{label} returned empty content")
        usage = response_payload.get("usage") or {}
        return cast("str", content), usage


def post_with_transient_retry(
    post_fn: Callable[[], httpx.Response],
    *,
    label: str,
    should_retry: Callable[[httpx.Response], bool] | None = None,
) -> httpx.Response:
    """Run post_fn; on transport error or 5xx, retry once after a short pause.

    Anything else (2xx-4xx) returns immediately for the caller to classify. A 5xx
    is retried only if ``should_retry`` is None or returns True for it — callers
    pass a predicate that excludes quota/auth signals so a 5xx whose body is a
    quota message is not retried (quota failures must never be retried).
    """
    last_exc: httpx.HTTPError | None = None
    for attempt in range(K_LLM_TRANSIENT_RETRIES + 1):
        try:
            response = post_fn()
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < K_LLM_TRANSIENT_RETRIES:
                time.sleep(K_LLM_TRANSIENT_BACKOFF_SECONDS)
                continue
            raise BackendDownException(f"{label} transport error: {exc}") from exc
        retryable = should_retry is None or should_retry(response)
        if (
            response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
            and retryable
            and attempt < K_LLM_TRANSIENT_RETRIES
        ):
            time.sleep(K_LLM_TRANSIENT_BACKOFF_SECONDS)
            continue
        return response
    raise BackendDownException(f"{label} transport error: {last_exc}")


def exec_subprocess(
    cmd: list[str], prompt_text: str, *, timeout: int, stdin: bool = True
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            input=prompt_text if stdin else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise BackendDownException(f"backend CLI not found: {cmd[0]!r}") from e
    except subprocess.TimeoutExpired as e:
        raise BackendDownException(f"backend timed out after {timeout}s: {cmd[0]!r}") from e


def extract_json_object(text: str) -> dict[str, Any]:
    """Return one JSON object, repairing common LLM drift such as prose or unquoted keys."""
    candidate = _unwrap_json_fence(text.strip())
    try:
        return _loads_single_json_object(candidate)
    except ValueError:
        repaired = repair_json_loads(candidate)
        if not isinstance(repaired, dict):
            raise ValueError("Top-level JSON value must be an object") from None
        return repaired


def _loads_single_json_object(candidate: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    try:
        obj, end = decoder.raw_decode(candidate)
    except json.JSONDecodeError as exc:
        raise ValueError("No valid JSON object found in text") from exc
    trailing = candidate[end:].strip()
    if trailing:
        raise ValueError("Trailing content after JSON object")
    if not isinstance(obj, dict):
        raise ValueError("Top-level JSON value must be an object")
    return obj


def _unwrap_json_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if not lines:
        return text
    first = lines[0].strip()
    if not first.startswith("```"):
        return text
    if len(lines) < 2:
        return text
    last = lines[-1].strip()
    if last != "```":
        return text
    return "\n".join(lines[1:-1]).strip()
