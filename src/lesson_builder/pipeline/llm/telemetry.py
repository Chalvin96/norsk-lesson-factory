"""Entry point: ``log_llm_call`` (success) and ``log_llm_failure`` (total failover
failure). Append-only JSONL telemetry for LLM calls.

One record per Agent call. Default target ``store/llm_calls.jsonl`` (gitignored);
override with ``NORSK_LLM_LOG_PATH``; set it to ``off`` to disable. A telemetry
IO failure must never break a pipeline run: it logs a warning and returns.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.llm.base import AttemptOutcome, LlmResponse
from lesson_builder.settings import K_LLM_LOG_DEFAULT_PATH, K_LLM_LOG_PATH_ENV

logger = logging.getLogger(__name__)


def _target(path: Path | None) -> Path | None:
    """Resolve the sink path, or None when telemetry is disabled (``off``)."""
    env_value = os.environ.get(K_LLM_LOG_PATH_ENV, "")
    if env_value.lower() == "off":
        return None
    return Path(env_value) if env_value else (path or Path(K_LLM_LOG_DEFAULT_PATH))


def _append(target: Path, record: dict[str, Any]) -> None:
    """Append one JSON line; a telemetry IO failure warns and returns, never raises."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("llm telemetry write failed (%s): %s", target, exc)


def log_llm_call(agent_name: str, response: LlmResponse, *, path: Path | None = None) -> None:
    target = _target(path)
    if target is None:
        return
    _append(
        target,
        {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "agent": agent_name,
            "client": response.client,
            "model": response.model,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "latency_ms": response.latency_ms,
            "attempts": [asdict(a) for a in response.attempts],
        },
    )


def log_llm_failure(
    agent_name: str, attempts: Sequence[AttemptOutcome], *, path: Path | None = None
) -> None:
    """Record a call where every backend in the chain failed (no ``LlmResponse``).

    Same sink/precedence rules as ``log_llm_call``; writes a partial record marked
    ``failed`` carrying the accumulated per-step attempt trace and no tokens/latency,
    so failover-storm patterns during an outage stay visible in the log.
    """
    target = _target(path)
    if target is None:
        return
    _append(
        target,
        {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "agent": agent_name,
            "client": None,
            "model": None,
            "input_tokens": None,
            "output_tokens": None,
            "latency_ms": None,
            "attempts": [asdict(a) for a in attempts],
            "failed": True,
        },
    )
