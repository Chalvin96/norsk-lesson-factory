"""Entry point: ``OpencodeClient.call`` runs isolated OpenCode CLI access."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import IO
from typing import cast

from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.cancellation import active_cancellation_token
from lesson_builder.clients.cancellation import provider_call_cancellation
from lesson_builder.clients.llm.base import BaseLlmClient
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.clients.llm.base import register_active_cli_process
from lesson_builder.clients.llm.base import terminate_cli_process
from lesson_builder.clients.llm.base import unregister_active_cli_process
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.clients.llm.opencode_credentials import opencode_process_environment
from lesson_builder.clients.llm.opencode_runtime import collect_opencode_lock_fds
from lesson_builder.clients.llm.opencode_runtime import create_opencode_temporary_directory
from lesson_builder.clients.llm.opencode_runtime import fail_if_opencode_call_cancelled
from lesson_builder.clients.llm.opencode_runtime import hold_opencode_cache
from lesson_builder.clients.llm.settings import K_OPENCODE_BARE_CLIENT_TIMEOUT
from lesson_builder.clients.llm.settings import K_OPENCODE_DEFAULT_MODEL
from lesson_builder.clients.llm.settings import K_OPENCODE_INLINE_PROMPT_MAX_BYTES
from lesson_builder.clients.llm.settings import K_OPENCODE_PROMPT_DIR_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_QUOTA_PATTERNS
from lesson_builder.clients.llm.settings import K_OPENCODE_STDERR_QUOTA_GRACE_SECONDS
from lesson_builder.clients.llm.settings import K_OPENCODE_STREAM_CHANNEL_COUNT
from lesson_builder.clients.llm.settings import K_OPENCODE_STREAM_POLL_SECONDS
from lesson_builder.clients.llm.settings import K_OPENCODE_TERMINATE_GRACE_SECONDS


class OpencodeClient(BaseLlmClient):
    name = "opencode"

    def __init__(
        self,
        *,
        default_model: str = K_OPENCODE_DEFAULT_MODEL,
        timeout: int = K_OPENCODE_BARE_CLIENT_TIMEOUT,
        quota_patterns: tuple[str, ...] = K_OPENCODE_QUOTA_PATTERNS,
        repo_root: Path | None = None,
    ) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            raise TypeError("OpencodeClient timeout must be a positive integer")
        if timeout <= 0:
            raise ValueError("OpencodeClient timeout must be positive")
        self.default_model = default_model
        self.timeout = timeout
        self.quota_patterns = quota_patterns
        self.repo_root = Path(repo_root) if repo_root is not None else None

    def call(
        self,
        prompt_text: str,
        *,
        model: str | None = None,
        agent: str | None = None,
        variant: str | None = None,
        cancellation: CancellationToken | None = None,
    ) -> LlmResponse:
        active_cancellation = active_cancellation_token()
        if cancellation is not None and active_cancellation is not cancellation:
            with provider_call_cancellation(cancellation):
                return self.call(
                    prompt_text,
                    model=model,
                    agent=agent,
                    variant=variant,
                    cancellation=cancellation,
                )
        cancellation = cancellation or active_cancellation
        is_cancelled = (lambda: cancellation.is_cancelled) if cancellation is not None else None
        fail_if_opencode_call_cancelled(is_cancelled)
        chosen_model = model or self.default_model
        deadline = time.monotonic() + self.timeout
        cwd = str(self.repo_root) if self.repo_root is not None else None
        cmd = _build_opencode_command(chosen_model, agent, variant)
        proc = _run_opencode_prompt(
            self,
            prompt_text,
            cmd,
            chosen_model,
            deadline=deadline,
            cwd=cwd,
            is_cancelled=is_cancelled,
        )
        text = _parse_events(proc.stdout or "", quota_fn=self.matches_quota)
        if not text and self.matches_quota(proc.stderr or ""):
            raise LlmQuotaException(proc.stderr or "opencode quota reached")
        if not text and proc.returncode != 0:
            if self.matches_quota(proc.stdout or ""):
                raise LlmQuotaException(proc.stdout or "opencode quota reached")
            raise BackendDownException(f"opencode run failed: {proc.stderr}")
        if not text:
            raise BackendDownException("opencode produced no text output")
        return LlmResponse(
            text=text,
            client="opencode",
            model=chosen_model,
            agent=agent,
            variant=variant,
        )


def _build_opencode_command(model: str, agent: str | None, variant: str | None) -> list[str]:
    command = ["opencode", "run", "--pure", "--format", "json", "--print-logs", "--log-level", "ERROR"]
    if agent:
        command.extend(["--agent", agent])
    if variant:
        command.extend(["--variant", variant])
    command.extend(["-m", model])
    return command


def _run_opencode_prompt(
    client: OpencodeClient,
    prompt: str,
    command: list[str],
    model: str,
    *,
    deadline: float,
    cwd: str | None,
    is_cancelled: Callable[[], bool] | None,
) -> subprocess.CompletedProcess[str]:
    """Run inline or file-backed prompt with one shared environment deadline."""
    with (
        opencode_process_environment(
            model, deadline=deadline, timeout=client.timeout, is_cancelled=is_cancelled
        ) as env,
        hold_opencode_cache(
            Path(env["XDG_CACHE_HOME"]), model, deadline=deadline, timeout=client.timeout, is_cancelled=is_cancelled
        ) as mark_cache_warm,
    ):
        if len(prompt.encode("utf-8")) <= K_OPENCODE_INLINE_PROMPT_MAX_BYTES:
            result = _run_inline_opencode_prompt(
                client, prompt, command, env, deadline=deadline, cwd=cwd, is_cancelled=is_cancelled
            )
        else:
            result = _run_file_opencode_prompt(
                client, prompt, command, env, deadline=deadline, cwd=cwd, is_cancelled=is_cancelled
            )
        if result.returncode == 0 and _parse_events(result.stdout or "", quota_fn=client.matches_quota):
            mark_cache_warm()
        return result


def _run_inline_opencode_prompt(
    client: OpencodeClient,
    prompt: str,
    command: list[str],
    env: dict[str, str],
    *,
    deadline: float,
    cwd: str | None,
    is_cancelled: Callable[[], bool] | None,
) -> subprocess.CompletedProcess[str]:
    """Send a short prompt as positional data after the option terminator."""
    command.extend(["--", prompt])
    _fail_if_deadline_expired(deadline, timeout=client.timeout)
    return _run_opencode(
        command,
        prompt,
        timeout=client.timeout,
        deadline=deadline,
        env=env,
        quota_fn=client.matches_quota,
        cwd=cwd,
        is_cancelled=is_cancelled,
    )


def _run_file_opencode_prompt(
    client: OpencodeClient,
    prompt: str,
    command: list[str],
    env: dict[str, str],
    *,
    deadline: float,
    cwd: str | None,
    is_cancelled: Callable[[], bool] | None,
) -> subprocess.CompletedProcess[str]:
    """Send a large prompt through a temporary file."""
    with create_opencode_temporary_directory(
        prefix=K_OPENCODE_PROMPT_DIR_PREFIX,
        deadline=deadline,
        timeout=client.timeout,
        is_cancelled=is_cancelled,
    ) as temp_dir:
        prompt_path = Path(temp_dir) / "prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        command.extend(
            [
                "-f",
                str(prompt_path),
                "--",
                "Execute the attached prompt as the complete task and emit its requested output.",
            ]
        )
        _fail_if_deadline_expired(deadline, timeout=client.timeout)
        return _run_opencode(
            command,
            "",
            timeout=client.timeout,
            deadline=deadline,
            env=env,
            quota_fn=client.matches_quota,
            cwd=cwd,
            is_cancelled=is_cancelled,
        )


@dataclass
class _CollectedOpenCodeOutput:
    """State collected from the paired OpenCode output streams."""

    stdout_parts: list[str] = field(default_factory=list)
    stderr_parts: list[str] = field(default_factory=list)
    ended_streams: set[str] = field(default_factory=set)
    stdout_has_text: bool = False
    stderr_quota_message: str | None = None
    stderr_quota_deadline: float | None = None


def _fail_if_deadline_expired(deadline: float, *, timeout: int) -> None:
    """Fail a call whose environment or prompt setup consumed its whole budget."""
    if time.monotonic() >= deadline:
        raise BackendDownException(f"backend timed out after {timeout}s before the process started: 'opencode'")


def _run_opencode(
    cmd: list[str],
    prompt_text: str,
    *,
    timeout: int,
    deadline: float,
    env: dict[str, str],
    quota_fn: Callable[[str], bool],
    cwd: str | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run OpenCode while surfacing quota diagnostics before its retry loop stalls."""
    del prompt_text  # OpenCode receives the prompt through argv or an attached file.
    _fail_if_deadline_expired(deadline, timeout=timeout)
    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            cwd=cwd,
            start_new_session=os.name == "posix",
            pass_fds=collect_opencode_lock_fds(),
        )
    except FileNotFoundError as exc:
        raise BackendDownException("backend CLI not found: 'opencode'") from exc
    except OSError as exc:
        raise BackendDownException(f"backend CLI could not start: 'opencode': {exc}") from exc

    process_cancellation = _register_active_process(process)
    events: queue.Queue[tuple[str, str | None]] = queue.Queue()
    stdout_stream = cast(IO[str], process.stdout)
    stderr_stream = cast(IO[str], process.stderr)
    readers = (
        threading.Thread(
            target=_read_process_stream,
            args=("stdout", stdout_stream, events),
            name=f"opencode-stdout-{process.pid}",
            daemon=True,
        ),
        threading.Thread(
            target=_read_process_stream,
            args=("stderr", stderr_stream, events),
            name=f"opencode-stderr-{process.pid}",
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()

    try:
        stdout_text, stderr_text = _collect_opencode_output(
            process,
            events,
            timeout=timeout,
            deadline=deadline,
            quota_fn=quota_fn,
            is_cancelled=is_cancelled,
        )
        process_cancellation.raise_if_cancelled()
    except BaseException as exc:
        terminate_cli_process(process)
        if process_cancellation.is_cancelled:
            try:
                process_cancellation.raise_if_cancelled()
            except Cancelled as cancelled:
                raise cancelled from exc
        raise
    finally:
        for reader in readers:
            reader.join(timeout=K_OPENCODE_TERMINATE_GRACE_SECONDS)
        _unregister_active_process(process)

    return subprocess.CompletedProcess(
        args=cmd,
        returncode=process.returncode or 0,
        stdout=stdout_text,
        stderr=stderr_text,
    )


def _collect_opencode_output(
    process: subprocess.Popen[str],
    events: queue.Queue[tuple[str, str | None]],
    *,
    timeout: int,
    deadline: float,
    quota_fn: Callable[[str], bool],
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[str, str]:
    """Drain both OpenCode streams until completion or a typed failure.

    Structured quota/error events on stdout are authoritative while
    collecting. A quota-shaped stderr log is only a fallback signal: once
    stdout has produced model text, it is ignored so a completed, valid
    stdout response is never rejected by stderr wording. Without model text
    it fails the call at stdout end, or after a bounded grace period when
    stdout stays open, which keeps a stalling OpenCode retry loop from
    waiting for the process timeout.
    """
    collected = _CollectedOpenCodeOutput()
    while True:
        fail_if_opencode_call_cancelled(is_cancelled)
        now = time.monotonic()
        _require_output_before_deadline(collected, now, timeout=timeout, deadline=deadline)
        if _opencode_output_complete(process, events, collected):
            break
        wait_seconds = min(K_OPENCODE_STREAM_POLL_SECONDS, max(0.0, deadline - now))
        try:
            channel, line = events.get(timeout=wait_seconds)
        except queue.Empty:
            continue
        _collect_opencode_event(collected, channel, line, quota_fn)
    return "".join(collected.stdout_parts), "".join(collected.stderr_parts)


def _require_output_before_deadline(
    collected: _CollectedOpenCodeOutput, now: float, *, timeout: int, deadline: float
) -> None:
    if now >= deadline:
        raise BackendDownException(f"backend timed out after {timeout}s: 'opencode'")
    if (
        collected.stderr_quota_message is not None
        and not collected.stdout_has_text
        and collected.stderr_quota_deadline is not None
        and now >= collected.stderr_quota_deadline
    ):
        raise LlmQuotaException(collected.stderr_quota_message)


def _opencode_output_complete(
    process: subprocess.Popen[str],
    events: queue.Queue[tuple[str, str | None]],
    collected: _CollectedOpenCodeOutput,
) -> bool:
    return (
        process.poll() is not None
        and len(collected.ended_streams) == K_OPENCODE_STREAM_CHANNEL_COUNT
        and events.empty()
    )


def _collect_opencode_event(
    collected: _CollectedOpenCodeOutput,
    channel: str,
    line: str | None,
    quota_fn: Callable[[str], bool],
) -> None:
    if line is None:
        collected.ended_streams.add(channel)
        if channel == "stdout" and _is_stderr_quota_due(
            collected.stderr_quota_message, stdout_has_text=collected.stdout_has_text
        ):
            raise LlmQuotaException(collected.stderr_quota_message or "opencode quota reached")
        return
    if channel == "stdout":
        collected.stdout_parts.append(line)
        collected.stdout_has_text = _collect_stdout_line(
            line, stdout_has_text=collected.stdout_has_text, quota_fn=quota_fn
        )
        return
    collected.stderr_parts.append(line)
    quota_message = _record_stderr_quota_line(
        line,
        stdout_ended="stdout" in collected.ended_streams,
        stdout_has_text=collected.stdout_has_text,
        quota_fn=quota_fn,
    )
    if quota_message is not None and collected.stderr_quota_message is None:
        collected.stderr_quota_message = quota_message
        collected.stderr_quota_deadline = time.monotonic() + K_OPENCODE_STDERR_QUOTA_GRACE_SECONDS


def _is_stderr_quota_due(stderr_quota_message: str | None, *, stdout_has_text: bool) -> bool:
    """Return whether a deferred stderr quota now fails a text-less call."""
    return stderr_quota_message is not None and not stdout_has_text


def _collect_stdout_line(line: str, *, stdout_has_text: bool, quota_fn: Callable[[str], bool]) -> bool:
    """Raise for a structured quota event and return the updated text-seen state."""
    quota_message = _structured_quota_message(line, quota_fn=quota_fn)
    if quota_message is not None:
        raise LlmQuotaException(quota_message)
    return stdout_has_text or _has_stdout_line_text(line)


def _record_stderr_quota_line(
    line: str,
    *,
    stdout_ended: bool,
    stdout_has_text: bool,
    quota_fn: Callable[[str], bool],
) -> str | None:
    """Classify one stderr log line against completed model text.

    Raises ``LlmQuotaException`` when stdout already ended without any model
    text, defers the message while stdout may still produce text (the caller
    enforces a bounded grace period), and ignores quota wording entirely once
    model text exists.
    """
    if stdout_has_text:
        return None
    if not _is_primary_quota_log(line, quota_fn=quota_fn):
        return None
    if stdout_ended:
        raise LlmQuotaException(line.strip())
    return line.strip()


def _has_stdout_line_text(line: str) -> bool:
    """Return whether one structured stdout line carries model text output."""
    event = _json_object(line)
    if event is None:
        return False
    if event.get("type") != "text":
        return False
    part = _object_mapping(event.get("part"))
    return isinstance(part.get("text"), str) and bool(part["text"])


def _read_process_stream(
    channel: str,
    stream: IO[str],
    events: queue.Queue[tuple[str, str | None]],
) -> None:
    """Drain one process stream without allowing the other pipe to block."""
    try:
        for line in stream:
            events.put((channel, line))
    finally:
        events.put((channel, None))


def _structured_quota_message(
    line: str,
    *,
    quota_fn: Callable[[str], bool],
) -> str | None:
    """Return quota text only from a structured OpenCode error event."""
    event = _json_object(line)
    if event is None:
        return None
    if event.get("type") != "error":
        return None
    message = _error_event_message(event)
    return str(message) if quota_fn(str(message)) else None


def _is_primary_quota_log(line: str, *, quota_fn: Callable[[str], bool]) -> bool:
    """Ignore title-agent failures while stopping on the primary call's quota."""
    if "small=true" in line and "agent=title" in line:
        return False
    return quota_fn(line)


def _register_active_process(process: subprocess.Popen[str]) -> CancellationToken:
    return register_active_cli_process(process, active_cancellation_token())


def _unregister_active_process(process: subprocess.Popen[str]) -> None:
    unregister_active_cli_process(process)


def _parse_events(stdout: str, *, quota_fn: Callable[[str], bool]) -> str:
    parts: list[str] = []
    for line in stdout.splitlines():
        text = _parse_event_line(line, quota_fn=quota_fn)
        if text is not None:
            parts.append(text)
    return "".join(parts)


def _parse_event_line(line: str, *, quota_fn: Callable[[str], bool]) -> str | None:
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    event = _json_object(line)
    if event is None:
        return None
    if event.get("type") == "error":
        message = str(_error_event_message(event))
        if quota_fn(message):
            raise LlmQuotaException(message)
        raise BackendDownException(message)
    if event.get("type") != "text":
        return None
    text = _object_mapping(event.get("part")).get("text")
    return text if isinstance(text, str) else None


def _json_object(line: str) -> dict[str, object] | None:
    """Decode one JSON line only when it contains an object-shaped event."""
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _object_mapping(value: object) -> dict[str, object]:
    """Return an object-shaped JSON value or an empty mapping."""
    return value if isinstance(value, dict) else {}


def _error_event_message(event: dict[str, object]) -> object:
    """Return a structured error message with a stable backend-error fallback."""
    error = _object_mapping(event.get("error"))
    data = _object_mapping(error.get("data"))
    return data.get("message") or "opencode error"
