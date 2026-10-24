"""Entry points: ``prepare_isolated_profile``, ``watch_profile_auth``,
``restore_parent_auth``, and ``stop_auth_sync``, used by
``CodexAppServerClient`` to manage isolated-profile credentials.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO

try:
    import fcntl
except ImportError:  # pragma: no cover - supported POSIX deployments have fcntl
    fcntl = None  # type: ignore[assignment]

from lesson_builder.clients.codex import settings as codex_settings

K_CODEX_AUTH_LOCKS_GUARD = threading.Lock()
K_CODEX_AUTH_LOCKS: dict[str, threading.Lock] = {}
K_CODEX_AUTH_ACTIVE_OWNERS_GUARD = threading.Lock()
K_CODEX_AUTH_ACTIVE_OWNERS: dict[
    str,
    tuple[threading.Event, threading.Thread, Callable[[], None]],
] = {}
K_CODEX_AUTH_LOCAL_PROFILES_GUARD = threading.Lock()
K_CODEX_AUTH_LOCAL_PROFILES: dict[str, set[str]] = {}


@dataclass
class _AuthSnapshot:
    """Complete usable auth file content used as a synchronization baseline."""

    content: bytes
    valid: bool

    @property
    def digest(self) -> str:
        """Return a credential-free digest for shared sync metadata."""
        return hashlib.sha256(self.content).hexdigest()


@dataclass
class CodexAuthSyncState:
    """Record one app-server generation's compare-and-swap auth baseline."""

    last_synced: _AuthSnapshot | None
    generation_id: str
    parent_auth: Path
    profile_dir: Path
    sync_state_path: Path
    parent_changed: bool = False


@dataclass
class _CodexAuthSyncRecord:
    """Keep the persisted auth baseline and the generation that owns writeback."""

    baseline_digest: str | None
    generation_id: str | None
    owner_pid: int | None


def prepare_isolated_profile() -> tuple[Path, CodexAuthSyncState]:
    """Create a private generation profile and recover the persisted auth baseline."""
    parent_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    profile_root = parent_home / codex_settings.K_CODEX_ISOLATED_PROFILE_DIRECTORY
    profile_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(profile_root, 0o700)

    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    sync_state_path = profile_root / codex_settings.K_CODEX_AUTH_SYNC_STATE_FILENAME
    lock_path = parent_home / codex_settings.K_CODEX_AUTH_SYNC_LOCK_FILENAME
    _stop_active_codex_owner(parent_auth)
    with _codex_auth_file_lock(lock_path):
        parent = _read_codex_auth_snapshot(parent_auth)
        profile_dir, state = _create_codex_generation_profile(
            profile_root,
            parent_auth,
            sync_state_path,
            parent,
        )
        _reclaim_codex_generation_profiles(
            profile_root,
            keep_generation_id=state.generation_id,
            parent_present=parent is not None and parent.valid,
        )
    return profile_dir.resolve(), state


def restore_parent_auth(parent_auth: Path, profile_auth: Path, state: CodexAuthSyncState) -> bool:
    """Restore stopped-generation auth and report whether its profile is safe to remove."""
    try:
        with _codex_auth_file_lock(parent_auth.parent / codex_settings.K_CODEX_AUTH_SYNC_LOCK_FILENAME):
            _sync_retired_codex_auth_files(parent_auth, profile_auth, state)
            record = _read_codex_auth_sync_record(state.sync_state_path)
            if record.generation_id == state.generation_id:
                _record_codex_auth_sync(state, state.last_synced, retain_ownership=False)
            parent = _read_codex_auth_snapshot(parent_auth)
            if parent is None:
                profile_auth.unlink(missing_ok=True)
                return True
            if not parent.valid:
                return True
            _copy_codex_auth_atomically(parent_auth, profile_auth)
            return True
    except OSError as exc:
        logging.getLogger(__name__).warning("Could not restore the stopped Codex auth profile: %s", exc)
    return False


def watch_profile_auth(
    parent_auth: Path,
    profile_auth: Path,
    stop_event: threading.Event,
    state: CodexAuthSyncState,
    before_parent_auth_adoption: Callable[[], None],
    retire_after_parent_auth_sync: Callable[[], None],
) -> None:
    """Propagate auth content changes across the isolated profile boundary until stopped."""
    owner_key = str(parent_auth.resolve())
    with K_CODEX_AUTH_ACTIVE_OWNERS_GUARD:
        K_CODEX_AUTH_ACTIVE_OWNERS[owner_key] = (
            stop_event,
            threading.current_thread(),
            before_parent_auth_adoption,
        )
    try:
        while not stop_event.wait(codex_settings.K_CODEX_AUTH_SYNC_POLL_SECONDS):
            try:
                with _codex_auth_file_lock(parent_auth.parent / codex_settings.K_CODEX_AUTH_SYNC_LOCK_FILENAME):
                    deleted, parent_auth_changed = _sync_codex_auth_files(
                        parent_auth,
                        profile_auth,
                        state,
                        before_parent_auth_adoption=before_parent_auth_adoption,
                    )
            except OSError as exc:
                logging.getLogger(__name__).warning("Could not sync the isolated Codex auth profile: %s", exc)
                continue
            if parent_auth_changed:
                before_parent_auth_adoption()
                stop_event.set()
                retire_after_parent_auth_sync()
                return
            if deleted:
                stop_event.set()
                retire_after_parent_auth_sync()
                return
        try:
            with _codex_auth_file_lock(parent_auth.parent / codex_settings.K_CODEX_AUTH_SYNC_LOCK_FILENAME):
                _sync_codex_auth_files(parent_auth, profile_auth, state)
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Could not sync the isolated Codex auth profile during shutdown: %s", exc
            )
    finally:
        with K_CODEX_AUTH_ACTIVE_OWNERS_GUARD:
            if K_CODEX_AUTH_ACTIVE_OWNERS.get(owner_key, (None, None, None))[1] is threading.current_thread():
                K_CODEX_AUTH_ACTIVE_OWNERS.pop(owner_key, None)


def stop_auth_sync(stop_event: threading.Event, watcher: threading.Thread | None) -> None:
    """Stop the profile watcher after the child exits and wait for its final sync."""
    stop_event.set()
    if watcher is not None and watcher is not threading.current_thread():
        watcher.join(timeout=codex_settings.K_CODEX_AUTH_SYNC_STOP_TIMEOUT_SECONDS)


def _stop_active_codex_owner(parent_auth: Path) -> None:
    """Stop a locally supervised owner before another generation takes its profile."""
    owner_key = str(parent_auth.resolve())
    with K_CODEX_AUTH_ACTIVE_OWNERS_GUARD:
        active = K_CODEX_AUTH_ACTIVE_OWNERS.pop(owner_key, None)
    if active is not None:
        active[2]()
        stop_auth_sync(active[0], active[1])


def _recover_persisted_codex_refresh(
    parent_auth: Path,
    profile_root: Path,
    sync_state_path: Path,
) -> _AuthSnapshot | None:
    """Recover an owning generation's refresh only while its baseline still matches."""
    parent = _read_codex_auth_snapshot(parent_auth)
    previous = _read_codex_auth_sync_record(sync_state_path)
    if parent is None or not parent.valid or previous.generation_id is None:
        return parent
    if previous.baseline_digest != parent.digest:
        return parent

    previous_auth = profile_root / previous.generation_id / codex_settings.K_CODEX_AUTH_FILENAME
    previous_profile = _read_codex_auth_snapshot(previous_auth)
    if previous_profile is None or not previous_profile.valid or previous_profile.digest == parent.digest:
        return parent

    recovered = _copy_codex_auth_atomically(
        previous_auth,
        parent_auth,
        expected_destination_digest=parent.digest,
    )
    if recovered is not None:
        return recovered
    latest_parent = _read_codex_auth_snapshot(parent_auth)
    return latest_parent if latest_parent is not None and latest_parent.valid else None


def _sync_retired_codex_auth_files(
    parent_auth: Path,
    profile_auth: Path,
    state: CodexAuthSyncState,
) -> None:
    """Persist a retired child's final refresh while its old parent baseline still matches."""
    record = _read_codex_auth_sync_record(state.sync_state_path)
    if record.generation_id == state.generation_id:
        _sync_codex_auth_files(parent_auth, profile_auth, state)
        return
    parent = _read_codex_auth_snapshot(parent_auth)
    profile = _read_codex_auth_snapshot(profile_auth)
    baseline = state.last_synced
    if (
        state.parent_changed
        or parent is None
        or not parent.valid
        or profile is None
        or not profile.valid
        or baseline is None
    ):
        return
    if parent.digest == baseline.digest and profile.digest != baseline.digest:
        _copy_codex_auth_atomically(profile_auth, parent_auth, expected_destination_digest=baseline.digest)


def _reclaim_codex_generation_profiles(
    profile_root: Path,
    *,
    keep_generation_id: str,
    parent_present: bool,
) -> None:
    """Remove profiles whose owner process has exited, including orphan auth copies."""
    if not profile_root.exists():
        return
    for candidate in profile_root.iterdir():
        if not candidate.is_dir() or candidate.name == keep_generation_id:
            continue
        if parent_present and _is_local_codex_profile(candidate):
            continue
        owner_pid = _read_codex_owner_pid(profile_root, candidate)
        if owner_pid is not None and owner_pid != os.getpid() and _is_process_alive(owner_pid):
            continue
        shutil.rmtree(candidate, ignore_errors=True)
    for owner_path in profile_root.glob(f".*{codex_settings.K_CODEX_AUTH_OWNER_FILENAME}"):
        generation_id = owner_path.name[1 : -len(codex_settings.K_CODEX_AUTH_OWNER_FILENAME)]
        if not (profile_root / generation_id).is_dir():
            owner_path.unlink(missing_ok=True)


def _read_codex_owner_pid(profile_root: Path, profile_dir: Path) -> int | None:
    """Read the process id that created a generation profile."""
    try:
        owner_path = profile_root / f".{profile_dir.name}{codex_settings.K_CODEX_AUTH_OWNER_FILENAME}"
        value = int(owner_path.read_text(encoding="ascii"))
    except (OSError, ValueError):
        return None
    return value if value > 0 else None


def _is_process_alive(process_id: int) -> bool:
    """Return whether a process id still resolves to a running process."""
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_local_codex_profile(profile_dir: Path) -> bool:
    """Return whether this process still owns a generation state for the profile."""
    profile_key = str(profile_dir.resolve())
    with K_CODEX_AUTH_LOCAL_PROFILES_GUARD:
        return any(profile_key in profiles for profiles in K_CODEX_AUTH_LOCAL_PROFILES.values())


def _forget_local_codex_profile(profile_dir: Path) -> None:
    """Stop protecting a generation profile after its owning client has exited."""
    profile_key = str(profile_dir.resolve())
    with K_CODEX_AUTH_LOCAL_PROFILES_GUARD:
        for profiles in K_CODEX_AUTH_LOCAL_PROFILES.values():
            profiles.discard(profile_key)


def _create_codex_generation_profile(
    profile_root: Path,
    parent_auth: Path,
    sync_state_path: Path,
    parent: _AuthSnapshot | None,
) -> tuple[Path, CodexAuthSyncState]:
    """Create a fresh generation directory and persist it as the writable owner."""
    generation_id = uuid.uuid4().hex
    profile_dir = profile_root / generation_id
    owner_path = profile_root / f".{generation_id}{codex_settings.K_CODEX_AUTH_OWNER_FILENAME}"
    try:
        profile_dir.mkdir(mode=0o700)
        os.chmod(profile_dir, 0o700)
        owner_path.write_text(str(os.getpid()), encoding="ascii")
        os.chmod(owner_path, 0o600)
        recovered_parent = _recover_persisted_codex_refresh(parent_auth, profile_root, sync_state_path)
        current_parent = recovered_parent if recovered_parent is not None and recovered_parent.valid else parent
        current_parent = current_parent if current_parent is not None and current_parent.valid else None
        if current_parent is not None:
            seeded = _copy_codex_auth_atomically(parent_auth, profile_dir / codex_settings.K_CODEX_AUTH_FILENAME)
            if seeded is not None:
                current_parent = seeded
        state = CodexAuthSyncState(
            last_synced=current_parent,
            generation_id=generation_id,
            parent_auth=parent_auth,
            profile_dir=profile_dir,
            sync_state_path=sync_state_path,
        )
        _record_codex_auth_sync(state, current_parent)
        with K_CODEX_AUTH_LOCAL_PROFILES_GUARD:
            K_CODEX_AUTH_LOCAL_PROFILES.setdefault(str(parent_auth.resolve()), set()).add(str(profile_dir.resolve()))
        return profile_dir, state
    except BaseException:
        shutil.rmtree(profile_dir, ignore_errors=True)
        owner_path.unlink(missing_ok=True)
        raise


def _sync_codex_auth_files(
    parent_auth: Path,
    profile_auth: Path,
    state: CodexAuthSyncState,
    *,
    before_parent_auth_adoption: Callable[[], None] | None = None,
) -> tuple[bool, bool]:
    """Reconcile only the persisted owner's profile through content compare-and-swap."""
    record = _read_codex_auth_sync_record(state.sync_state_path)
    if record.generation_id != state.generation_id:
        return False, True
    parent = _read_codex_auth_snapshot(parent_auth)
    baseline = state.last_synced
    if parent is None:
        return _discard_profile_without_parent(profile_auth, state, before_parent_auth_adoption)
    profile = _read_codex_auth_snapshot(profile_auth)
    if _has_invalid_codex_auth_snapshot(parent, profile):
        return False, False
    if baseline is None:
        _establish_codex_auth_baseline(parent_auth, profile_auth, parent, profile, state)
        return False, False
    if parent.digest != baseline.digest:
        _adopt_changed_codex_parent(
            parent_auth,
            profile_auth,
            parent,
            state,
            before_parent_auth_adoption,
        )
        return False, True
    if profile is None:
        return _discard_parent_after_profile_logout(parent_auth, state, before_parent_auth_adoption)
    if profile.digest != baseline.digest:
        return _write_refreshed_codex_auth(parent_auth, profile_auth, baseline, state, before_parent_auth_adoption)

    _record_codex_auth_sync(state, parent)
    return False, False


def _discard_profile_without_parent(
    profile_auth: Path,
    state: CodexAuthSyncState,
    before_parent_auth_adoption: Callable[[], None] | None,
) -> tuple[bool, bool]:
    """Discard a generation profile when parent auth is absent or was logged out."""
    if state.last_synced is not None:
        if before_parent_auth_adoption is not None:
            before_parent_auth_adoption()
        profile_auth.unlink(missing_ok=True)
        _clear_codex_auth_sync(state)
        return True, True
    profile_auth.unlink(missing_ok=True)
    return False, False


def _discard_parent_after_profile_logout(
    parent_auth: Path,
    state: CodexAuthSyncState,
    before_parent_auth_adoption: Callable[[], None] | None,
) -> tuple[bool, bool]:
    """Make a child logout authoritative while the parent still matches baseline."""
    if before_parent_auth_adoption is not None:
        before_parent_auth_adoption()
    parent_auth.unlink(missing_ok=True)
    _clear_codex_auth_sync(state)
    return True, False


def _write_refreshed_codex_auth(
    parent_auth: Path,
    profile_auth: Path,
    baseline: _AuthSnapshot,
    state: CodexAuthSyncState,
    before_parent_auth_adoption: Callable[[], None] | None,
) -> tuple[bool, bool]:
    """Write a child refresh only while parent still matches the observed baseline."""
    copied = _copy_codex_auth_atomically(
        profile_auth,
        parent_auth,
        expected_destination_digest=baseline.digest,
    )
    if copied is not None:
        _record_codex_auth_sync(state, copied)
        return False, False
    latest_parent = _read_codex_auth_snapshot(parent_auth)
    if latest_parent is not None and latest_parent.valid and latest_parent.digest != baseline.digest:
        _adopt_changed_codex_parent(
            parent_auth,
            profile_auth,
            latest_parent,
            state,
            before_parent_auth_adoption,
        )
        return False, True
    return False, False


def _establish_codex_auth_baseline(
    parent_auth: Path,
    profile_auth: Path,
    parent: _AuthSnapshot,
    profile: _AuthSnapshot | None,
    state: CodexAuthSyncState,
) -> None:
    """Seed a profile from a parent when no trusted child baseline exists."""
    if profile is None or parent.content != profile.content:
        copied = _copy_codex_auth_atomically(parent_auth, profile_auth)
        if copied is not None:
            _record_codex_auth_sync(state, copied)
        return
    _record_codex_auth_sync(state, parent)


def _adopt_changed_codex_parent(
    parent_auth: Path,
    profile_auth: Path,
    parent: _AuthSnapshot,
    state: CodexAuthSyncState,
    before_parent_auth_adoption: Callable[[], None] | None,
) -> None:
    """Revoke this generation's writeback before adopting changed parent auth."""
    state.parent_changed = True
    if before_parent_auth_adoption is not None:
        before_parent_auth_adoption()
    _record_codex_auth_sync(state, parent, retain_ownership=False)
    copied = _copy_codex_auth_atomically(parent_auth, profile_auth)
    if copied is not None:
        _record_codex_auth_sync(state, copied, retain_ownership=False)


def _record_codex_auth_sync(
    state: CodexAuthSyncState,
    snapshot: _AuthSnapshot | None,
    *,
    owner_generation_id: str | None = None,
    retain_ownership: bool = True,
) -> None:
    """Persist a digest and its owning generation under the caller's auth lock."""
    owner_id = state.generation_id if retain_ownership else owner_generation_id
    owner_pid = os.getpid() if retain_ownership else None
    _write_codex_auth_sync_record(
        state.sync_state_path,
        _CodexAuthSyncRecord(
            baseline_digest=snapshot.digest if snapshot is not None else None,
            generation_id=owner_id,
            owner_pid=owner_pid,
        ),
    )
    state.last_synced = snapshot


def _clear_codex_auth_sync(state: CodexAuthSyncState) -> None:
    """Clear the baseline and revoke generation ownership after logout."""
    _record_codex_auth_sync(state, None, retain_ownership=False)


def _read_codex_auth_sync_record(path: Path) -> _CodexAuthSyncRecord:
    """Read credential-free baseline ownership metadata, ignoring incomplete records."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return _CodexAuthSyncRecord(baseline_digest=None, generation_id=None, owner_pid=None)
    if not isinstance(value, dict):
        return _CodexAuthSyncRecord(baseline_digest=None, generation_id=None, owner_pid=None)
    baseline_digest = value.get("baseline_sha256")
    generation_id = value.get("generation_id")
    owner_pid = value.get("owner_pid")
    if baseline_digest is not None and not _is_sha256_digest(baseline_digest):
        return _CodexAuthSyncRecord(baseline_digest=None, generation_id=None, owner_pid=None)
    if generation_id is not None and not _is_generation_id(generation_id):
        return _CodexAuthSyncRecord(baseline_digest=None, generation_id=None, owner_pid=None)
    if owner_pid is not None and (not isinstance(owner_pid, int) or isinstance(owner_pid, bool) or owner_pid <= 0):
        return _CodexAuthSyncRecord(baseline_digest=None, generation_id=None, owner_pid=None)
    return _CodexAuthSyncRecord(
        baseline_digest=baseline_digest,
        generation_id=generation_id,
        owner_pid=owner_pid,
    )


def _write_codex_auth_sync_record(path: Path, record: _CodexAuthSyncRecord) -> None:
    """Atomically write credential-free generation ownership metadata."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        payload = {
            "baseline_sha256": record.baseline_digest,
            "generation_id": record.generation_id,
            "owner_pid": record.owner_pid,
        }
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_parent_directory(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _is_sha256_digest(value: object) -> bool:
    """Return whether a value is a lowercase SHA-256 digest."""
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _is_generation_id(value: object) -> bool:
    """Return whether a persisted profile owner id has the generated UUID-hex shape."""
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def _has_invalid_codex_auth_snapshot(parent: _AuthSnapshot | None, profile: _AuthSnapshot | None) -> bool:
    """Return whether either side of the sync contains unusable auth bytes."""
    return (parent is not None and not parent.valid) or (profile is not None and not profile.valid)


def _read_codex_auth_snapshot(path: Path) -> _AuthSnapshot | None:
    """Read auth bytes and mark malformed or non-regular files as unusable snapshots."""
    try:
        file_stat = path.stat()
    except FileNotFoundError:
        return None
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    valid = stat.S_ISREG(file_stat.st_mode) and _has_valid_codex_auth_content(content)
    return _AuthSnapshot(content, valid)


def _has_valid_codex_auth_content(content: bytes) -> bool:
    """Return whether auth bytes parse to an object containing a tokens object."""
    try:
        document = json.loads(content)
    except ValueError:
        return False
    return isinstance(document, dict) and isinstance(document.get("tokens"), dict)


def _copy_codex_auth_atomically(
    source: Path,
    destination: Path,
    *,
    expected_destination_digest: str | None = None,
) -> _AuthSnapshot | None:
    """Copy auth privately and optionally compare the destination just before replacement."""
    with source.open("rb") as source_handle:
        source_stat = os.fstat(source_handle.fileno())
        contents = source_handle.read()
        final_source_stat = os.fstat(source_handle.fileno())
    if (source_stat.st_mtime_ns, source_stat.st_size) != (final_source_stat.st_mtime_ns, final_source_stat.st_size):
        return None
    if not _has_valid_codex_auth_content(contents):
        return None

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination_handle:
            os.fchmod(destination_handle.fileno(), 0o600)
            destination_handle.write(contents)
            destination_handle.flush()
            os.utime(
                destination_handle.fileno(),
                ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns),
            )
            os.fsync(destination_handle.fileno())
        if expected_destination_digest is not None:
            latest_destination = _read_codex_auth_snapshot(destination)
            if (
                latest_destination is None
                or not latest_destination.valid
                or latest_destination.digest != expected_destination_digest
            ):
                temporary.unlink(missing_ok=True)
                return None
        os.replace(temporary, destination)
        _sync_parent_directory(destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return _AuthSnapshot(contents, True)


def _sync_parent_directory(path: Path) -> None:
    """Persist a same-directory atomic replacement."""
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


@contextmanager
def _codex_auth_file_lock(lock_path: Path) -> Iterator[None]:
    """Serialize auth synchronization across threads and processes."""
    lock_key = str(lock_path.resolve())
    with K_CODEX_AUTH_LOCKS_GUARD:
        local_lock = K_CODEX_AUTH_LOCKS.setdefault(lock_key, threading.Lock())
    local_lock.acquire()
    handle: IO[str] | None = None
    file_locked = False
    try:
        lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = lock_path.open("a+", encoding="utf-8")
        os.chmod(lock_path, 0o600)
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            file_locked = True
        yield
    finally:
        if file_locked and fcntl is not None and handle is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        if handle is not None:
            handle.close()
        local_lock.release()
