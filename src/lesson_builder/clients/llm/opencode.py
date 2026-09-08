"""Entry point: ``OpencodeClient.call`` runs isolated OpenCode CLI access."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from _thread import LockType
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path
from typing import IO
from typing import cast

try:
    import fcntl
except ImportError:  # pragma: no cover - supported POSIX deployments have fcntl
    fcntl = None  # type: ignore[assignment]

from lesson_builder.clients.llm.base import BaseLlmClient
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.clients.llm.base import is_cli_cancellation_requested
from lesson_builder.clients.llm.base import is_cli_process_cancelled
from lesson_builder.clients.llm.base import register_active_cli_process
from lesson_builder.clients.llm.base import terminate_cli_process
from lesson_builder.clients.llm.base import unregister_active_cli_process
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmCancelledException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.clients.llm.settings import K_OPENCODE_BARE_CLIENT_TIMEOUT
from lesson_builder.clients.llm.settings import K_OPENCODE_DEFAULT_EXECUTION_MODE
from lesson_builder.clients.llm.settings import K_OPENCODE_DEFAULT_LOCK_FILENAME
from lesson_builder.clients.llm.settings import K_OPENCODE_DEFAULT_MODEL
from lesson_builder.clients.llm.settings import K_OPENCODE_EXECUTION_MODE_ENV
from lesson_builder.clients.llm.settings import K_OPENCODE_EXECUTION_MODE_PARALLEL
from lesson_builder.clients.llm.settings import K_OPENCODE_EXECUTION_MODE_SERIAL
from lesson_builder.clients.llm.settings import K_OPENCODE_INLINE_PROMPT_MAX_BYTES
from lesson_builder.clients.llm.settings import K_OPENCODE_OVERRIDE_ENV_ALLOWLIST
from lesson_builder.clients.llm.settings import K_OPENCODE_OVERRIDE_ENV_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_QUOTA_PATTERNS
from lesson_builder.clients.llm.settings import K_OPENCODE_STATE_DIR_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_STDERR_QUOTA_GRACE_SECONDS
from lesson_builder.clients.llm.settings import K_OPENCODE_STREAM_POLL_SECONDS
from lesson_builder.clients.llm.settings import K_OPENCODE_TEMP_ROOT_ENV
from lesson_builder.clients.llm.settings import K_OPENCODE_TERMINATE_GRACE_SECONDS
from lesson_builder.clients.llm.settings import K_OPENCODE_XDG_CACHE_HOME_ENV
from lesson_builder.clients.llm.settings import K_OPENCODE_XDG_DATA_HOME_ENV
from lesson_builder.clients.llm.settings import K_OPENCODE_XDG_STATE_HOME_ENV

K_OPENCODE_STREAM_CHANNEL_COUNT = 2


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
        self.execution_mode = _configured_execution_mode()

    def call(
        self,
        prompt_text: str,
        *,
        model: str | None = None,
        agent: str | None = None,
        variant: str | None = None,
    ) -> LlmResponse:
        chosen_model = model or self.default_model
        # The budget starts here so serial-slot acquisition, environment
        # setup, process startup, and output collection all share one deadline.
        deadline = time.monotonic() + self.timeout
        cwd = str(self.repo_root) if self.repo_root is not None else None
        cmd = [
            "opencode",
            "run",
            "--pure",
            "--format",
            "json",
            "--print-logs",
            "--log-level",
            "ERROR",
        ]
        if agent:
            cmd.extend(["--agent", agent])
        if variant:
            cmd.extend(["--variant", variant])
        cmd.extend(["-m", chosen_model])
        with _opencode_process_environment() as env:
            if len(prompt_text.encode("utf-8")) <= K_OPENCODE_INLINE_PROMPT_MAX_BYTES:
                # "--" ends option parsing so prompt text is always positional
                # data, never interpreted as an OpenCode command-line option.
                cmd.extend(["--", prompt_text])
                _fail_if_deadline_expired(deadline, timeout=self.timeout)
                with _opencode_execution_slot(self.execution_mode, deadline=deadline, timeout=self.timeout):
                    proc = _run_opencode(
                        cmd,
                        prompt_text,
                        timeout=self.timeout,
                        deadline=deadline,
                        env=env,
                        quota_fn=self.matches_quota,
                        cwd=cwd,
                    )
            else:
                with _opencode_temporary_directory(prefix="norsk-opencode-prompt-") as temp_dir:
                    prompt_path = Path(temp_dir) / "prompt.txt"
                    prompt_path.write_text(prompt_text, encoding="utf-8")
                    cmd.extend(
                        [
                            "-f",
                            str(prompt_path),
                            "--",
                            "Execute the attached prompt as the complete task and emit its requested output.",
                        ]
                    )
                    _fail_if_deadline_expired(deadline, timeout=self.timeout)
                    with _opencode_execution_slot(self.execution_mode, deadline=deadline, timeout=self.timeout):
                        proc = _run_opencode(
                            cmd,
                            "",
                            timeout=self.timeout,
                            deadline=deadline,
                            env=env,
                            quota_fn=self.matches_quota,
                            cwd=cwd,
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


K_OPENCODE_PROCESS_LOCKS: dict[str, threading.Lock] = {}
K_OPENCODE_PROCESS_LOCKS_GUARD = threading.Lock()


@contextlib.contextmanager
def _opencode_process_environment() -> Iterator[dict[str, str]]:
    """Yield mandatory child-process XDG paths isolated from shared state."""
    configured_data_home = os.environ.get(K_OPENCODE_XDG_DATA_HOME_ENV, "").strip()
    source_data_home = Path(configured_data_home) if configured_data_home else Path.home() / ".local" / "share"
    source_auth = source_data_home / "opencode" / "auth.json"

    with _opencode_temporary_directory(prefix=K_OPENCODE_STATE_DIR_PREFIX) as temp_dir:
        root = Path(temp_dir)
        data_home = root / "data"
        state_home = root / "state"
        cache_home = root / "cache"
        process_tmp = root / "tmp"
        for directory in (data_home, state_home, cache_home, process_tmp):
            directory.mkdir(mode=0o700)

        if source_auth.is_file():
            target_auth = data_home / "opencode" / "auth.json"
            target_auth.parent.mkdir(mode=0o700)
            shutil.copyfile(source_auth, target_auth)
            target_auth.chmod(0o600)

        child_env = os.environ.copy()
        for override in [key for key in child_env if _is_opencode_override_env(key)]:
            del child_env[override]
        child_env.update(
            {
                K_OPENCODE_XDG_DATA_HOME_ENV: str(data_home),
                K_OPENCODE_XDG_STATE_HOME_ENV: str(state_home),
                K_OPENCODE_XDG_CACHE_HOME_ENV: str(cache_home),
                "TMPDIR": str(process_tmp),
            }
        )
        yield child_env


def _opencode_temporary_directory(*, prefix: str) -> tempfile.TemporaryDirectory[str]:
    """Create disposable adapter state outside a full default temp volume."""
    configured_root = os.environ.get(K_OPENCODE_TEMP_ROOT_ENV, "").strip()
    runtime_root = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    temp_root = configured_root or runtime_root or None
    return tempfile.TemporaryDirectory(prefix=prefix, dir=temp_root)


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
        )
    except FileNotFoundError as exc:
        raise BackendDownException("backend CLI not found: 'opencode'") from exc
    except OSError as exc:
        raise BackendDownException(f"backend CLI could not start: 'opencode': {exc}") from exc

    _register_active_process(process)
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
        )
        if is_cli_process_cancelled(process):
            raise LlmCancelledException("opencode call cancelled")
    except BaseException as exc:
        cancelled = is_cli_process_cancelled(process)
        terminate_cli_process(process)
        if cancelled and not isinstance(exc, LlmCancelledException):
            raise LlmCancelledException("opencode call cancelled") from exc
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
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    ended_streams: set[str] = set()
    stdout_has_text = False
    stderr_quota_message: str | None = None
    stderr_quota_deadline: float | None = None
    while True:
        now = time.monotonic()
        if now >= deadline:
            raise BackendDownException(f"backend timed out after {timeout}s: 'opencode'")
        if (
            stderr_quota_message is not None
            and not stdout_has_text
            and stderr_quota_deadline is not None
            and now >= stderr_quota_deadline
        ):
            raise LlmQuotaException(stderr_quota_message)
        if process.poll() is not None and len(ended_streams) == K_OPENCODE_STREAM_CHANNEL_COUNT and events.empty():
            break
        wait_seconds = min(K_OPENCODE_STREAM_POLL_SECONDS, max(0.0, deadline - now))
        try:
            channel, line = events.get(timeout=wait_seconds)
        except queue.Empty:
            continue
        if line is None:
            ended_streams.add(channel)
            if channel == "stdout" and _is_stderr_quota_due(stderr_quota_message, stdout_has_text=stdout_has_text):
                raise LlmQuotaException(stderr_quota_message)
            continue
        if channel == "stdout":
            stdout_parts.append(line)
            stdout_has_text = _collect_stdout_line(
                line,
                stdout_has_text=stdout_has_text,
                quota_fn=quota_fn,
            )
            continue
        stderr_parts.append(line)
        quota_message = _record_stderr_quota_line(
            line,
            stdout_ended="stdout" in ended_streams,
            stdout_has_text=stdout_has_text,
            quota_fn=quota_fn,
        )
        if quota_message is not None and stderr_quota_message is None:
            stderr_quota_message = quota_message
            stderr_quota_deadline = time.monotonic() + K_OPENCODE_STDERR_QUOTA_GRACE_SECONDS
    return "".join(stdout_parts), "".join(stderr_parts)


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


def _register_active_process(process: subprocess.Popen[str]) -> None:
    register_active_cli_process(process)


def _unregister_active_process(process: subprocess.Popen[str]) -> None:
    unregister_active_cli_process(process)


def _is_opencode_override_env(name: str) -> bool:
    """Return whether one environment variable is an OpenCode policy override.

    ``OPENCODE_``-prefixed variables configure the OpenCode runtime itself
    (config paths, permissions, routing). They are stripped from the child
    environment unless allowlisted as credentials so they cannot silently
    override the validated repository boundary.
    """
    return name.startswith(K_OPENCODE_OVERRIDE_ENV_PREFIX) and name not in K_OPENCODE_OVERRIDE_ENV_ALLOWLIST


def _configured_execution_mode() -> str:
    """Return the validated parallel-by-default OpenCode execution mode."""
    mode = os.environ.get(K_OPENCODE_EXECUTION_MODE_ENV, K_OPENCODE_DEFAULT_EXECUTION_MODE).strip().lower()
    if mode not in (K_OPENCODE_EXECUTION_MODE_PARALLEL, K_OPENCODE_EXECUTION_MODE_SERIAL):
        raise ValueError(
            f"{K_OPENCODE_EXECUTION_MODE_ENV} must be one of "
            f"{K_OPENCODE_EXECUTION_MODE_PARALLEL!r} or {K_OPENCODE_EXECUTION_MODE_SERIAL!r}"
        )
    return mode


def _fail_if_cli_cancellation_requested() -> None:
    """Stop a serial-slot wait when the owning batch has been cancelled."""
    if is_cli_cancellation_requested():
        raise LlmCancelledException("opencode call cancelled")


def _acquire_local_execution_slot(local_lock: LockType, *, deadline: float, timeout: int) -> None:
    """Acquire the in-process serial lease while observing cancellation and deadline."""
    while True:
        _fail_if_cli_cancellation_requested()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise BackendDownException(
                f"backend timed out after {timeout}s waiting for the OpenCode serial execution slot"
            )
        if local_lock.acquire(timeout=min(K_OPENCODE_STREAM_POLL_SECONDS, remaining)):
            return


def _acquire_file_execution_slot(handle: IO[str], *, deadline: float, timeout: int) -> None:
    """Acquire the cross-process serial lease while observing cancellation and deadline."""
    if fcntl is None:
        raise RuntimeError("unavailable")
    while True:
        _fail_if_cli_cancellation_requested()
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError:
            _fail_if_cli_cancellation_requested()
            if time.monotonic() >= deadline:
                raise BackendDownException(
                    f"backend timed out after {timeout}s waiting for the OpenCode serial execution slot"
                ) from None
            time.sleep(min(K_OPENCODE_STREAM_POLL_SECONDS, max(0.0, deadline - time.monotonic())))


@contextlib.contextmanager
def _opencode_execution_slot(mode: str, *, deadline: float, timeout: int) -> Iterator[None]:
    """Yield immediately in parallel mode or use the emergency serial lease.

    Both the in-process lock and the cross-process file lock are acquired
    within the remaining call budget; a serial slot that stays busy fails the
    call at the deadline instead of blocking without bound.
    """
    if mode == K_OPENCODE_EXECUTION_MODE_PARALLEL:
        yield
        return
    if mode != K_OPENCODE_EXECUTION_MODE_SERIAL:
        raise ValueError(f"unsupported OpenCode execution mode: {mode!r}")

    lock_path = str(Path(tempfile.gettempdir()) / K_OPENCODE_DEFAULT_LOCK_FILENAME)
    with K_OPENCODE_PROCESS_LOCKS_GUARD:
        local_lock = K_OPENCODE_PROCESS_LOCKS.setdefault(lock_path, threading.Lock())
    acquired = False
    try:
        _acquire_local_execution_slot(local_lock, deadline=deadline, timeout=timeout)
        acquired = True
        if fcntl is None:
            yield
            return
        lock_file = Path(lock_path)
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with lock_file.open("a+", encoding="utf-8") as handle:
            _acquire_file_execution_slot(handle, deadline=deadline, timeout=timeout)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        if acquired:
            local_lock.release()


def _parse_events(stdout: str, *, quota_fn: Callable[[str], bool]) -> str:
    parts: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        event = _json_object(line)
        if event is None:
            continue
        event_type = event.get("type")
        if event_type == "error":
            message = _error_event_message(event)
            if quota_fn(str(message)):
                raise LlmQuotaException(str(message))
            raise BackendDownException(str(message))
        if event_type == "text":
            part = _object_mapping(event.get("part"))
            text = part.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


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
