"""Opencode CLI client (``opencode run --pure --format json``)."""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from lesson_builder.pipeline.llm.base import BaseLlmCli, LlmResponse, exec_subprocess
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException
from lesson_builder.settings import (
    K_OPENCODE_DEFAULT_MODEL,
    K_OPENCODE_DEFAULT_TIMEOUT,
    K_OPENCODE_QUOTA_PATTERNS,
    K_OPENCODE_TIMEOUT_ENV,
)


class OpencodeClient(BaseLlmCli):
    name = "opencode"

    def __init__(
        self,
        *,
        default_model: str = K_OPENCODE_DEFAULT_MODEL,
        timeout: int | None = None,
        quota_patterns: tuple[str, ...] = K_OPENCODE_QUOTA_PATTERNS,
    ) -> None:
        self.default_model = default_model
        self.timeout = (
            timeout
            if timeout is not None
            else int(os.environ.get(K_OPENCODE_TIMEOUT_ENV, str(K_OPENCODE_DEFAULT_TIMEOUT)))
        )
        self.quota_patterns = quota_patterns

    def call(self, prompt_text: str, *, model: str | None = None) -> LlmResponse:
        chosen_model = model or self.default_model
        cmd = [
            "opencode", "run", "--pure", "--format", "json",
            "-m", chosen_model, prompt_text,
        ]
        proc = exec_subprocess(cmd, prompt_text, timeout=self.timeout, stdin=False)
        joined = f"{proc.stderr or ''}\n{proc.stdout or ''}"
        if self.matches_quota(joined):
            raise LlmQuotaException(proc.stderr or proc.stdout or "opencode quota reached")
        text = _parse_events(proc.stdout or "", quota_fn=self.matches_quota)
        if not text and proc.returncode != 0:
            raise BackendDownException(f"opencode run failed: {proc.stderr}")
        if not text:
            raise BackendDownException("opencode produced no text output")
        return LlmResponse(text=text, client="opencode", model=chosen_model)


def _parse_events(stdout: str, *, quota_fn: Callable[[str], bool]) -> str:
    parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")
        if event_type == "error":
            message = (event.get("error") or {}).get("data", {}).get("message") or "opencode error"
            if quota_fn(str(message)):
                raise LlmQuotaException(str(message))
            raise BackendDownException(str(message))
        if event_type == "text":
            part = event.get("part") or {}
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "".join(parts)
