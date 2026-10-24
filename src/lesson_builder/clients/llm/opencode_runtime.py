"""Entry point: ``opencode_runtime_environment`` owns OpenCode runtime paths and cache leases."""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import stat
import tempfile
import threading
import time
from _thread import LockType
from collections.abc import Callable
from collections.abc import Iterator
from contextvars import ContextVar
from pathlib import Path
from typing import IO

try:
    import fcntl
except ImportError:  # pragma: no cover - supported POSIX deployments have fcntl
    fcntl = None  # type: ignore[assignment]

from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.cancellation import active_cancellation_token
from lesson_builder.clients.llm.base import cli_cancellation_token
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.settings import K_OPENCODE_AUTH_LOCK_POLL_SECONDS
from lesson_builder.clients.llm.settings import K_OPENCODE_CACHE_INSTALL_LOCKS_DIR
from lesson_builder.clients.llm.settings import K_OPENCODE_CACHE_INTENT_LOCK_FILENAME
from lesson_builder.clients.llm.settings import K_OPENCODE_CACHE_MARKER_LOCK_FILENAME
from lesson_builder.clients.llm.settings import K_OPENCODE_CACHE_PENDING_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_CACHE_POPULATION_LOCK_FILENAME
from lesson_builder.clients.llm.settings import K_OPENCODE_CACHE_RECORD_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_CACHE_WARM_MARKER_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_CONFIG_CONTENT
from lesson_builder.clients.llm.settings import K_OPENCODE_CONFIG_CONTENT_ENV
from lesson_builder.clients.llm.settings import K_OPENCODE_OVERRIDE_ENV_ALLOWLIST
from lesson_builder.clients.llm.settings import K_OPENCODE_OVERRIDE_ENV_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_PROMPT_DIR_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_SHARED_CACHE_RELATIVE_PATH
from lesson_builder.clients.llm.settings import K_OPENCODE_STATE_DIR_PREFIX
from lesson_builder.clients.llm.settings import K_OPENCODE_TEMP_LIVE_LOCK_FILENAME
from lesson_builder.clients.llm.settings import K_OPENCODE_TEMP_ROOT_ENV
from lesson_builder.clients.llm.settings import K_OPENCODE_TEMP_ROOT_LOCK_FILENAME
from lesson_builder.clients.llm.settings import K_OPENCODE_XDG_CACHE_HOME_ENV
from lesson_builder.clients.llm.settings import K_OPENCODE_XDG_DATA_HOME_ENV
from lesson_builder.clients.llm.settings import K_OPENCODE_XDG_STATE_HOME_ENV

K_OPENCODE_FILE_PROCESS_LOCKS: dict[str, tuple[LockType, int]] = {}
K_OPENCODE_FILE_PROCESS_LOCKS_GUARD = threading.Lock()
K_OPENCODE_SWEEP_PROCESS_LOCK = threading.Lock()
K_OPENCODE_SWEPT_TEMP_ROOTS: set[str] = set()
K_OPENCODE_INHERITED_LOCK_FDS: ContextVar[tuple[int, ...]] = ContextVar("opencode_lock_fds", default=())


@contextlib.contextmanager
def opencode_runtime_environment(
    *,
    deadline: float,
    timeout: int,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[dict[str, str]]:
    """Yield private runtime paths with only the provider installation cache shared."""
    _ensure_opencode_temp_swept_once(deadline=deadline, timeout=timeout, is_cancelled=is_cancelled)
    with create_opencode_temporary_directory(
        prefix=K_OPENCODE_STATE_DIR_PREFIX, deadline=deadline, timeout=timeout, is_cancelled=is_cancelled
    ) as temp_dir:
        root = Path(temp_dir)
        data_home, state_home, process_tmp = (root / name for name in ("data", "state", "tmp"))
        for directory in (data_home, state_home, process_tmp):
            directory.mkdir(mode=0o700)
        cache_home = _opencode_shared_cache_home()
        install_locks = cache_home / K_OPENCODE_CACHE_INSTALL_LOCKS_DIR
        install_locks.mkdir(mode=0o700, parents=True, exist_ok=True)
        (state_home / "opencode").mkdir(mode=0o700)
        (state_home / "opencode" / "locks").symlink_to(install_locks, target_is_directory=True)
        child_env = {key: value for key, value in os.environ.items() if not _is_opencode_override_env(key)}
        child_env.update(
            {
                K_OPENCODE_XDG_DATA_HOME_ENV: str(data_home),
                K_OPENCODE_XDG_STATE_HOME_ENV: str(state_home),
                K_OPENCODE_XDG_CACHE_HOME_ENV: str(cache_home),
                K_OPENCODE_CONFIG_CONTENT_ENV: K_OPENCODE_CONFIG_CONTENT,
                "TMPDIR": str(process_tmp),
            }
        )
        yield child_env


def sweep_stale_opencode_directories() -> int:
    """Remove OpenCode state and prompt directories without a live lock owner."""
    temp_root = _opencode_temp_root()
    removed = _sweep_stale_opencode_directories(temp_root)
    with K_OPENCODE_SWEEP_PROCESS_LOCK:
        K_OPENCODE_SWEPT_TEMP_ROOTS.add(str(temp_root.resolve()))
    return removed


def collect_opencode_lock_fds() -> tuple[int, ...]:
    """Descriptors the OpenCode child must retain if its Python owner dies."""
    return K_OPENCODE_INHERITED_LOCK_FDS.get()


@contextlib.contextmanager
def create_opencode_temporary_directory(
    *,
    prefix: str,
    deadline: float | None = None,
    timeout: int = 0,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[str]:
    """Keep a temporary directory live until its Python and subprocess owners exit."""
    temp_root = _opencode_temp_root()
    with contextlib.ExitStack() as lifetime:
        with hold_opencode_file_lock(
            temp_root / K_OPENCODE_TEMP_ROOT_LOCK_FILENAME.format(uid=os.getuid()),
            deadline=deadline,
            timeout=timeout,
            is_cancelled=is_cancelled,
            purpose="temporary directory creation",
        ):
            temporary = tempfile.TemporaryDirectory(prefix=prefix, dir=temp_root)
            lifetime.callback(temporary.cleanup)
            lifetime.enter_context(
                hold_opencode_file_lock(
                    Path(temporary.name) / K_OPENCODE_TEMP_LIVE_LOCK_FILENAME,
                    deadline=deadline,
                    timeout=timeout,
                    is_cancelled=is_cancelled,
                    purpose="temporary directory liveness",
                    inherit_to_child=True,
                )
            )
        yield temporary.name


def fail_if_opencode_call_cancelled(is_cancelled: Callable[[], bool] | None) -> None:
    """Raise cancellation from either the owning operation or this call."""
    cancellation = active_cancellation_token() or cli_cancellation_token()
    if cancellation.is_cancelled:
        cancellation.raise_if_cancelled()
    if is_cancelled is not None and is_cancelled():
        raise Cancelled("author", "LLM call cancelled.")


@contextlib.contextmanager
def hold_opencode_cache(
    cache_home: Path,
    model: str,
    *,
    deadline: float,
    timeout: int,
    is_cancelled: Callable[[], bool] | None,
) -> Iterator[Callable[[], None]]:
    """Serialize cold calls and recovery without letting new readers starve a writer."""
    cache_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    marker_key = hashlib.sha256(model.encode("utf-8")).hexdigest()
    warm_marker = cache_home / f"{K_OPENCODE_CACHE_WARM_MARKER_PREFIX}{marker_key}"
    lock_path = cache_home / K_OPENCODE_CACHE_POPULATION_LOCK_FILENAME
    with contextlib.ExitStack() as population:
        with hold_opencode_file_lock(
            cache_home / K_OPENCODE_CACHE_INTENT_LOCK_FILENAME,
            deadline=deadline,
            timeout=timeout,
            is_cancelled=is_cancelled,
            purpose="cache population intent",
        ):
            population.enter_context(
                hold_opencode_file_lock(
                    lock_path,
                    deadline=deadline,
                    timeout=timeout,
                    is_cancelled=is_cancelled,
                    purpose="cache population",
                    shared=True,
                    inherit_to_child=True,
                )
            )
            if not warm_marker.is_file() or _has_abandoned_opencode_cache_call(cache_home):
                population.close()
                population.enter_context(
                    hold_opencode_file_lock(
                        lock_path,
                        deadline=deadline,
                        timeout=timeout,
                        is_cancelled=is_cancelled,
                        purpose="cache population",
                        inherit_to_child=True,
                    )
                )
                if _has_abandoned_opencode_cache_call(cache_home):
                    _recover_abandoned_opencode_cache_calls(cache_home)
                if warm_marker.is_file():
                    population.close()
                    population.enter_context(
                        hold_opencode_file_lock(
                            lock_path,
                            deadline=deadline,
                            timeout=timeout,
                            is_cancelled=is_cancelled,
                            purpose="cache population",
                            shared=True,
                            inherit_to_child=True,
                        )
                    )
        with _record_opencode_cache_call(
            cache_home, warm_marker, deadline=deadline, timeout=timeout, is_cancelled=is_cancelled
        ) as mark_warm:
            yield mark_warm


@contextlib.contextmanager
def hold_opencode_file_lock(
    lock_path: Path,
    *,
    deadline: float | None,
    timeout: int,
    is_cancelled: Callable[[], bool] | None = None,
    purpose: str,
    inherit_to_child: bool = False,
    shared: bool = False,
) -> Iterator[None]:
    """Hold a named file lock across processes and threads with bounded waiting."""
    lock_key = str(lock_path.resolve())
    with K_OPENCODE_FILE_PROCESS_LOCKS_GUARD:
        lock_record = K_OPENCODE_FILE_PROCESS_LOCKS.get(lock_key)
        local_lock = lock_record[0] if lock_record is not None else threading.Lock()
        next_count = 1 if lock_record is None else lock_record[1] + 1
        K_OPENCODE_FILE_PROCESS_LOCKS[lock_key] = (local_lock, next_count)
    local_locked = False
    handle: IO[str] | None = None
    file_locked = False
    inherited_token = None
    try:
        if not shared or fcntl is None:
            _acquire_opencode_local_file_lock(
                local_lock,
                deadline=deadline,
                timeout=timeout,
                is_cancelled=is_cancelled,
                purpose=purpose,
            )
            local_locked = True
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        os.chmod(lock_path, 0o600)
        if fcntl is not None:
            _acquire_opencode_file_flock(
                handle,
                deadline=deadline,
                timeout=timeout,
                is_cancelled=is_cancelled,
                purpose=purpose,
                shared=shared,
            )
            file_locked = True
        if inherit_to_child and handle is not None:
            inherited_token = K_OPENCODE_INHERITED_LOCK_FDS.set((*K_OPENCODE_INHERITED_LOCK_FDS.get(), handle.fileno()))
        yield
    finally:
        if inherited_token is not None:
            K_OPENCODE_INHERITED_LOCK_FDS.reset(inherited_token)
        if file_locked and fcntl is not None and handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        if handle is not None:
            handle.close()
        if local_locked:
            local_lock.release()
        _release_opencode_lock_record(lock_key, local_lock)


def _release_opencode_lock_record(lock_key: str, local_lock: LockType) -> None:
    """Drop one local lock user without retaining a record after its last owner."""
    with K_OPENCODE_FILE_PROCESS_LOCKS_GUARD:
        current = K_OPENCODE_FILE_PROCESS_LOCKS.get(lock_key)
        if current is None or current[0] is not local_lock:
            return
        if current[1] == 1:
            del K_OPENCODE_FILE_PROCESS_LOCKS[lock_key]
        else:
            K_OPENCODE_FILE_PROCESS_LOCKS[lock_key] = (local_lock, current[1] - 1)


def _acquire_opencode_local_file_lock(
    local_lock: LockType,
    *,
    deadline: float | None,
    timeout: int,
    is_cancelled: Callable[[], bool] | None = None,
    purpose: str,
) -> None:
    """Acquire the local half of a file lock while honoring deadline and cancellation."""
    if _can_wait_on_auth_lock_without_polling(deadline, is_cancelled):
        local_lock.acquire()
        return
    while True:
        fail_if_opencode_call_cancelled(is_cancelled)
        if local_lock.acquire(blocking=False):
            return
        if local_lock.acquire(timeout=_opencode_file_lock_wait_seconds(deadline, timeout, purpose)):
            return


def _acquire_opencode_file_flock(
    handle: IO[str],
    *,
    deadline: float | None,
    timeout: int,
    is_cancelled: Callable[[], bool] | None = None,
    purpose: str,
    shared: bool = False,
) -> None:
    """Acquire the cross-process file lock, honoring deadline and cancellation."""
    if fcntl is None:
        return
    lock_kind = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    if _can_wait_on_auth_lock_without_polling(deadline, is_cancelled):
        fcntl.flock(handle.fileno(), lock_kind)
        return
    while True:
        fail_if_opencode_call_cancelled(is_cancelled)
        try:
            fcntl.flock(handle.fileno(), lock_kind | fcntl.LOCK_NB)
            return
        except OSError:
            fail_if_opencode_call_cancelled(is_cancelled)
            time.sleep(_opencode_file_lock_wait_seconds(deadline, timeout, purpose))


def _can_wait_on_auth_lock_without_polling(deadline: float | None, is_cancelled: Callable[[], bool] | None) -> bool:
    """Return whether an auth lock can block without deadline or cancellation polling."""
    return deadline is None and is_cancelled is None


def _opencode_file_lock_wait_seconds(deadline: float | None, timeout: int, purpose: str) -> float:
    """Return one bounded auth-lock wait or raise when its deadline has expired."""
    if deadline is None:
        return K_OPENCODE_AUTH_LOCK_POLL_SECONDS
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BackendDownException(f"backend timed out after {timeout}s waiting for an OpenCode {purpose} lock")
    return min(K_OPENCODE_AUTH_LOCK_POLL_SECONDS, remaining)


def _opencode_temp_root() -> Path:
    """Resolve the runtime directory used for per-call OpenCode state."""
    configured_root = os.environ.get(K_OPENCODE_TEMP_ROOT_ENV, "").strip()
    runtime_root = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    return Path(configured_root or runtime_root or tempfile.gettempdir())


def _opencode_shared_cache_home() -> Path:
    """Resolve the persistent cache root from this process's parent XDG environment."""
    configured_cache_home = os.environ.get(K_OPENCODE_XDG_CACHE_HOME_ENV, "").strip()
    parent = Path(configured_cache_home) if configured_cache_home else Path.home() / ".cache"
    return parent / K_OPENCODE_SHARED_CACHE_RELATIVE_PATH


@contextlib.contextmanager
def _record_opencode_cache_call(
    cache_home: Path,
    warm_marker: Path,
    *,
    deadline: float,
    timeout: int,
    is_cancelled: Callable[[], bool] | None,
) -> Iterator[Callable[[], None]]:
    descriptor, filename = tempfile.mkstemp(prefix=K_OPENCODE_CACHE_RECORD_PREFIX, dir=cache_home)
    temporary = Path(filename)
    pending = temporary.with_name(
        K_OPENCODE_CACHE_PENDING_PREFIX + temporary.name.removeprefix(K_OPENCODE_CACHE_RECORD_PREFIX)
    )
    try:
        with os.fdopen(descriptor, "w") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            json.dump(sorted(_collect_opencode_package_directories(cache_home)), handle)
            handle.flush()
            os.replace(temporary, pending)
            token = K_OPENCODE_INHERITED_LOCK_FDS.set((*K_OPENCODE_INHERITED_LOCK_FDS.get(), handle.fileno()))

            def mark_warm() -> None:
                try:
                    _write_opencode_warm_marker(
                        cache_home, warm_marker, deadline=deadline, timeout=timeout, is_cancelled=is_cancelled
                    )
                    pending.unlink(missing_ok=True)
                except OSError as exc:
                    logging.getLogger(__name__).warning("Could not mark OpenCode cache warm: %s", exc)

            try:
                yield mark_warm
            finally:
                K_OPENCODE_INHERITED_LOCK_FDS.reset(token)
                pending.unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)


def _write_opencode_warm_marker(
    cache_home: Path,
    warm_marker: Path,
    *,
    deadline: float,
    timeout: int,
    is_cancelled: Callable[[], bool] | None,
) -> None:
    with hold_opencode_file_lock(
        cache_home / K_OPENCODE_CACHE_MARKER_LOCK_FILENAME,
        deadline=deadline,
        timeout=timeout,
        is_cancelled=is_cancelled,
        purpose="cache warm marker",
    ):
        descriptor, filename = tempfile.mkstemp(prefix=K_OPENCODE_CACHE_RECORD_PREFIX, dir=cache_home)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w") as handle:
                json.dump(sorted(_collect_opencode_package_directories(cache_home)), handle)
            os.replace(temporary, warm_marker)
        finally:
            temporary.unlink(missing_ok=True)


def _has_abandoned_opencode_cache_call(cache_home: Path) -> bool:
    if fcntl is None:
        return any(cache_home.glob(f"{K_OPENCODE_CACHE_PENDING_PREFIX}*"))
    for pending in cache_home.glob(f"{K_OPENCODE_CACHE_PENDING_PREFIX}*"):
        try:
            with pending.open("r") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                return True
        except FileNotFoundError:
            continue
    return False


def _collect_opencode_package_directories(cache_home: Path) -> set[str]:
    packages = cache_home / "opencode" / "packages"
    directories: set[str] = set()
    for root, names, _ in os.walk(packages):
        for name in names:
            directories.add(str((Path(root) / name).relative_to(packages)))
        if "node_modules" in names:
            names.remove("node_modules")
    return directories


def _recover_abandoned_opencode_cache_calls(cache_home: Path) -> None:
    packages = cache_home / "opencode" / "packages"
    committed: set[str] = set()
    for marker in cache_home.glob(f"{K_OPENCODE_CACHE_WARM_MARKER_PREFIX}*"):
        inventory = _find_opencode_package_inventory(marker)
        if inventory is not None:
            committed.update(inventory)
    for pending in cache_home.glob(f"{K_OPENCODE_CACHE_PENDING_PREFIX}*"):
        baseline = _find_opencode_package_inventory(pending)
        if baseline is None:
            _invalidate_opencode_packages_and_warm_markers(cache_home)
        else:
            additions = _collect_opencode_package_directories(cache_home) - baseline - committed
            for relative in sorted(additions, key=lambda name: len(Path(name).parts)):
                directory = packages / relative
                if directory.is_symlink():
                    directory.unlink(missing_ok=True)
                elif directory.exists():
                    shutil.rmtree(directory)
        pending.unlink(missing_ok=True)


def _find_opencode_package_inventory(path: Path) -> set[str] | None:
    try:
        inventory = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(inventory, list) or not all(isinstance(entry, str) for entry in inventory):
        return None
    return set(inventory)


def _invalidate_opencode_packages_and_warm_markers(cache_home: Path) -> None:
    packages = cache_home / "opencode" / "packages"
    if packages.exists():
        shutil.rmtree(packages)
    for marker in cache_home.glob(f"{K_OPENCODE_CACHE_WARM_MARKER_PREFIX}*"):
        marker.unlink(missing_ok=True)


def _ensure_opencode_temp_swept_once(
    *,
    deadline: float,
    timeout: int,
    is_cancelled: Callable[[], bool] | None,
) -> None:
    """Sweep the selected temp root once per process before its first OpenCode call."""
    temp_root = _opencode_temp_root()
    root_key = str(temp_root.resolve())
    _acquire_opencode_local_file_lock(
        K_OPENCODE_SWEEP_PROCESS_LOCK,
        deadline=deadline,
        timeout=timeout,
        is_cancelled=is_cancelled,
        purpose="temporary directory sweep",
    )
    try:
        if root_key in K_OPENCODE_SWEPT_TEMP_ROOTS:
            return
        _sweep_stale_opencode_directories(
            temp_root,
            deadline=deadline,
            timeout=timeout,
            is_cancelled=is_cancelled,
        )
        K_OPENCODE_SWEPT_TEMP_ROOTS.add(root_key)
    finally:
        K_OPENCODE_SWEEP_PROCESS_LOCK.release()


def _sweep_stale_opencode_directories(
    temp_root: Path,
    *,
    deadline: float | None = None,
    timeout: int = 0,
    is_cancelled: Callable[[], bool] | None = None,
) -> int:
    """Remove lockless matches under the root lock; creation holds it until a live lock exists."""
    if fcntl is None or not temp_root.is_dir():
        return 0
    removed = 0
    _fail_if_opencode_sweep_expired(deadline=deadline, timeout=timeout, is_cancelled=is_cancelled)
    with hold_opencode_file_lock(
        temp_root / K_OPENCODE_TEMP_ROOT_LOCK_FILENAME.format(uid=os.getuid()),
        deadline=deadline,
        timeout=timeout,
        is_cancelled=is_cancelled,
        purpose="temporary directory sweep",
    ):
        for candidate in temp_root.iterdir():
            if not (
                candidate.name.startswith(K_OPENCODE_STATE_DIR_PREFIX)
                or candidate.name.startswith(K_OPENCODE_PROMPT_DIR_PREFIX)
            ):
                continue
            _fail_if_opencode_sweep_expired(deadline=deadline, timeout=timeout, is_cancelled=is_cancelled)
            try:
                candidate_stat = candidate.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(candidate_stat.st_mode) or candidate_stat.st_uid != os.getuid():
                continue
            if _try_remove_unlocked_opencode_directory(candidate):
                removed += 1
    return removed


def _fail_if_opencode_sweep_expired(
    *,
    deadline: float | None,
    timeout: int,
    is_cancelled: Callable[[], bool] | None,
) -> None:
    """Keep an opportunistic sweep inside its owning OpenCode call budget."""
    fail_if_opencode_call_cancelled(is_cancelled)
    if deadline is not None and time.monotonic() >= deadline:
        raise BackendDownException(
            f"backend timed out after {timeout}s waiting for an OpenCode temporary directory sweep"
        )


def _try_remove_unlocked_opencode_directory(candidate: Path) -> bool:
    """Remove a lockless directory or one whose live lock is no longer held."""
    lock_path = candidate / K_OPENCODE_TEMP_LIVE_LOCK_FILENAME
    try:
        lock_path.lstat()
    except FileNotFoundError:
        try:
            shutil.rmtree(candidate)
        except FileNotFoundError:
            return False
        except OSError:
            return False
        return True
    except OSError:
        return False

    return _try_remove_unlocked_live_directory(candidate, lock_path)


def _try_remove_unlocked_live_directory(candidate: Path, lock_path: Path) -> bool:
    """Remove a locked-format directory when its owner no longer holds the flock."""
    if fcntl is None:
        return False
    lock_key = str(lock_path.resolve())
    local_lock: LockType | None = None
    with K_OPENCODE_FILE_PROCESS_LOCKS_GUARD:
        lock_record = K_OPENCODE_FILE_PROCESS_LOCKS.get(lock_key)
        if lock_record is not None:
            local_lock = lock_record[0]
            if not local_lock.acquire(blocking=False):
                return False

    try:
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError:
        if local_lock is not None:
            local_lock.release()
        return False
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        try:
            shutil.rmtree(candidate)
        except FileNotFoundError:
            return False
        return True
    except OSError:
        return False
    finally:
        handle.close()
        if local_lock is not None:
            local_lock.release()


def _is_opencode_override_env(name: str) -> bool:
    """Return whether one environment variable is an OpenCode policy override.

    ``OPENCODE_``-prefixed variables configure the OpenCode runtime itself
    (config paths, permissions, routing). They are stripped from the child
    environment unless allowlisted as credentials so they cannot silently
    override the validated repository boundary.
    """
    return name.startswith(K_OPENCODE_OVERRIDE_ENV_PREFIX) and name not in K_OPENCODE_OVERRIDE_ENV_ALLOWLIST


__all__ = [
    "opencode_runtime_environment",
    "sweep_stale_opencode_directories",
    "create_opencode_temporary_directory",
    "collect_opencode_lock_fds",
    "hold_opencode_cache",
    "hold_opencode_file_lock",
    "fail_if_opencode_call_cancelled",
]
