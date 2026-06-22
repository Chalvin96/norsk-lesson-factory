"""Codex CLI client (``codex exec`` with gpt-5.x models)."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from lesson_builder.pipeline.llm.base import BaseLlmCli, exec_subprocess
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException
from lesson_builder.settings import (
    K_CODEX_DEFAULT_MODEL,
    K_CODEX_DEFAULT_TIMEOUT,
    K_CODEX_QUOTA_PATTERNS,
    K_CODEX_TIMEOUT_ENV,
)


class CodexClient(BaseLlmCli):
    name = "codex"

    def __init__(
        self,
        *,
        default_model: str = K_CODEX_DEFAULT_MODEL,
        timeout: int | None = None,
        quota_patterns: tuple[str, ...] = K_CODEX_QUOTA_PATTERNS,
    ) -> None:
        self.default_model = default_model
        self.timeout = (
            timeout if timeout is not None else int(os.environ.get(K_CODEX_TIMEOUT_ENV, str(K_CODEX_DEFAULT_TIMEOUT)))
        )
        self.quota_patterns = quota_patterns

    def call(self, prompt_text: str, *, model: str | None = None) -> str:
        chosen_model = model or self.default_model
        cmd = ["codex", "exec", "--ephemeral", "-m", chosen_model]
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.txt"
            cmd.extend(["-o", str(out), "-"])
            proc = exec_subprocess(cmd, prompt_text, timeout=self.timeout, stdin=True)
            joined = f"{proc.stderr or ''}\n{proc.stdout or ''}"
            if self.matches_quota(joined):
                raise LlmQuotaException(proc.stderr or proc.stdout or "Codex quota reached")
            if proc.returncode != 0 and not out.exists():
                raise BackendDownException(f"codex exec failed: {proc.stderr}")
            if not out.exists():
                raise BackendDownException("codex produced no output file")
            text = out.read_text().strip()
            if not text:
                raise BackendDownException("codex output empty")
            return text
