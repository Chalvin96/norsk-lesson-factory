"""Not a check itself — shared LLM client contracts and the mechanics the OpenCode client leans on.

Anything used by the client, or by ``invocation.py``, lives here instead of
getting its own file. See ``README.md`` in this package for the boundary rules.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

from json_repair import loads as repair_json_loads

from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.llm.settings import K_LLM_CLI_TERMINATE_GRACE_SECONDS

K_LLM_JSON_FENCE_MIN_LINES = 2


@dataclass(frozen=True)
class LlmResponse:
    """Envelope returned by every LLM call: the text plus provenance/usage.

    ``latency_ms`` and ``attempts`` are filled in by ``JobRunner`` (the call
    wall time and retry attempt trace); individual clients leave them at their
    defaults.
    """

    text: str
    client: str
    model: str
    agent: str | None = None
    variant: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    attempts: tuple[AttemptOutcome, ...] = ()


@dataclass(frozen=True)
class AttemptOutcome:
    """One retry attempt: which client/model was used, and what happened."""

    client: str
    model: str
    outcome: str  # "ok" | "quota" | "down"
    error: str | None = None
    agent: str | None = None
    variant: str | None = None


class BaseLlmClient(ABC):
    """Common call contract for every LLM adapter."""

    name: str
    quota_patterns: tuple[str, ...] = ()

    def matches_quota(self, text: str) -> bool:
        lowered = (text or "").lower()
        return any(pattern in lowered for pattern in self.quota_patterns)

    @abstractmethod
    def call(
        self,
        prompt: str,
        *,
        model: str | None = None,
        agent: str | None = None,
        variant: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> LlmResponse: ...


K_ACTIVE_CLI_PROCESSES: dict[int, subprocess.Popen[str]] = {}
K_ACTIVE_CLI_CANCELLATIONS: dict[int, CancellationToken] = {}
K_LLM_CLI_CANCELLATION_TOKEN = CancellationToken()
K_ACTIVE_CLI_PROCESSES_GUARD = threading.Lock()


def cancel_active_cli_calls() -> None:
    """Terminate and reap every active CLI process owned by this process."""
    with K_ACTIVE_CLI_PROCESSES_GUARD:
        cancellations = (K_LLM_CLI_CANCELLATION_TOKEN, *K_ACTIVE_CLI_CANCELLATIONS.values())
        active = tuple(K_ACTIVE_CLI_PROCESSES.values())
    for cancellation in cancellations:
        cancellation.cancel("author", "LLM call cancelled.")
    for process in active:
        terminate_cli_process(process)


def reset_cli_cancellation() -> None:
    """Clear the batch cancellation latch after all worker calls have stopped."""
    global K_LLM_CLI_CANCELLATION_TOKEN
    with K_ACTIVE_CLI_PROCESSES_GUARD:
        if K_ACTIVE_CLI_PROCESSES:
            raise RuntimeError("cannot reset CLI cancellation while processes remain active")
        K_LLM_CLI_CANCELLATION_TOKEN = CancellationToken()


def register_active_cli_process(
    process: subprocess.Popen[str], cancellation: CancellationToken | None = None
) -> CancellationToken:
    """Register one child process for cancellation by the batch interrupt path."""
    with K_ACTIVE_CLI_PROCESSES_GUARD:
        batch_cancellation = K_LLM_CLI_CANCELLATION_TOKEN
        active_cancellation = cancellation or batch_cancellation
        K_ACTIVE_CLI_PROCESSES[process.pid] = process
        K_ACTIVE_CLI_CANCELLATIONS[process.pid] = active_cancellation
        batch_cancelled = batch_cancellation.is_cancelled
    if batch_cancelled:
        active_cancellation.cancel("author", "LLM call cancelled.")
    if active_cancellation.is_cancelled:
        terminate_cli_process(process)
    return active_cancellation


def unregister_active_cli_process(process: subprocess.Popen[str]) -> None:
    """Remove one child process after its output has been reaped."""
    with K_ACTIVE_CLI_PROCESSES_GUARD:
        K_ACTIVE_CLI_PROCESSES.pop(process.pid, None)
        K_ACTIVE_CLI_CANCELLATIONS.pop(process.pid, None)


def cli_cancellation_token() -> CancellationToken:
    """Return the current process-wide token used by uncoupled CLI calls."""
    with K_ACTIVE_CLI_PROCESSES_GUARD:
        return K_LLM_CLI_CANCELLATION_TOKEN


def raise_if_cli_call_cancelled(cancellation: CancellationToken | None, *, cause: Exception | None = None) -> None:
    """Raise the operation's reason, falling back to the process-wide batch token."""
    active = cancellation or cli_cancellation_token()
    try:
        active.raise_if_cancelled()
    except Cancelled as exc:
        if cause is not None:
            raise exc from cause
        raise


def terminate_cli_process(process: subprocess.Popen[str]) -> None:
    """Stop one owned CLI process group, escalating after the grace period."""
    if process.poll() is not None:
        return
    _signal_cli_process(process, signal.SIGTERM)
    try:
        process.wait(timeout=K_LLM_CLI_TERMINATE_GRACE_SECONDS)
        return
    except subprocess.TimeoutExpired:
        _signal_cli_process(process, signal.SIGKILL)
        process.wait()


def extract_json_object(text: str) -> dict[str, Any]:
    """Return one JSON object, repairing common LLM drift such as prose or unquoted keys."""
    candidate = _unwrap_json_fence(text.strip())
    try:
        return _loads_single_json_object(candidate)
    except ValueError:
        repaired = repair_json_loads(candidate)
        if not isinstance(repaired, dict):
            raise TypeError("Top-level JSON value must be an object") from None
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
        raise TypeError("Top-level JSON value must be an object")
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
    if len(lines) < K_LLM_JSON_FENCE_MIN_LINES:
        return text
    last = lines[-1].strip()
    if last != "```":
        return text
    return "\n".join(lines[1:-1]).strip()


def _signal_cli_process(process: subprocess.Popen[str], sent_signal: signal.Signals) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, sent_signal)
        elif sent_signal == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()
    except ProcessLookupError:
        return
