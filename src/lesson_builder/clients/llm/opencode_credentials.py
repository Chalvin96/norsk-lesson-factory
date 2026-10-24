"""Entry point: ``opencode_process_environment`` scopes credentials for one OpenCode process."""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from collections.abc import Callable
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.opencode_runtime import fail_if_opencode_call_cancelled
from lesson_builder.clients.llm.opencode_runtime import hold_opencode_file_lock
from lesson_builder.clients.llm.opencode_runtime import opencode_runtime_environment
from lesson_builder.clients.llm.settings import K_OPENCODE_AUTH_LOCK_POLL_SECONDS
from lesson_builder.clients.llm.settings import K_OPENCODE_OAUTH_EXPIRY_MILLISECONDS_THRESHOLD
from lesson_builder.clients.llm.settings import K_OPENCODE_OAUTH_REFRESH_WINDOW_SECONDS
from lesson_builder.clients.llm.settings import K_OPENCODE_XDG_DATA_HOME_ENV


@dataclass
class _OpenCodeCredentialSyncState:
    """Track one process's provider-entry compare-and-swap baseline."""

    baseline_entry: object
    parent_won: bool = False


@contextlib.contextmanager
def opencode_process_environment(
    model: str,
    *,
    deadline: float,
    timeout: int,
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[dict[str, str]]:
    """Yield isolated XDG paths and persist a changed selected-provider credential."""
    configured_data_home = os.environ.get(K_OPENCODE_XDG_DATA_HOME_ENV, "").strip()
    source_data_home = Path(configured_data_home) if configured_data_home else Path.home() / ".local" / "share"
    source_auth = source_data_home / "opencode" / "auth.json"
    provider = _opencode_model_provider(model)
    fail_if_opencode_call_cancelled(is_cancelled)
    credentials = _read_opencode_auth(source_auth, provider)
    provider_credential = _require_opencode_credential(credentials, provider)
    refresh_lock_stack = contextlib.ExitStack()
    refresh_lock_release_guard = threading.Lock()
    refresh_lock_released = False

    def release_refresh_lock() -> None:
        nonlocal refresh_lock_released
        with refresh_lock_release_guard:
            if not refresh_lock_released:
                refresh_lock_stack.close()
                refresh_lock_released = True

    try:
        hold_refresh_lock = False
        if _is_opencode_oauth_refresh_due(provider_credential):
            refresh_lock_path = source_auth.with_name(f".{source_auth.name}.{provider}.refresh.lock")
            refresh_lock_stack.enter_context(
                hold_opencode_file_lock(
                    refresh_lock_path,
                    deadline=deadline,
                    timeout=timeout,
                    is_cancelled=is_cancelled,
                    purpose="credential",
                )
            )
            # A waiting caller must copy the source that the preceding refresh
            # wrote, rather than reuse its pre-lock token snapshot.
            credentials = _read_opencode_auth(source_auth, provider)
            provider_credential = _require_opencode_credential(credentials, provider)
            hold_refresh_lock = _is_opencode_oauth_refresh_due(provider_credential)
            if not hold_refresh_lock:
                release_refresh_lock()

        fail_if_opencode_call_cancelled(is_cancelled)
        sync_state = _OpenCodeCredentialSyncState(dict(provider_credential))

        with opencode_runtime_environment(deadline=deadline, timeout=timeout, is_cancelled=is_cancelled) as child_env:
            data_home = Path(child_env[K_OPENCODE_XDG_DATA_HOME_ENV])
            target_auth = data_home / "opencode" / "auth.json"
            target_auth.parent.mkdir(mode=0o700)
            target_auth.write_text(json.dumps({provider: provider_credential}), encoding="utf-8")
            target_auth.chmod(0o600)

            refresh_watch_stop = threading.Event()
            refresh_watcher: threading.Thread | None = None
            if provider_credential.get("type") == "oauth":
                refresh_watcher = threading.Thread(
                    target=_watch_opencode_oauth_refresh,
                    args=(
                        source_auth,
                        target_auth,
                        provider,
                        sync_state,
                        refresh_watch_stop,
                        release_refresh_lock,
                    ),
                    name=f"opencode-auth-refresh-{provider}",
                    daemon=True,
                )
                refresh_watcher.start()
            try:
                yield child_env
            finally:
                refresh_watch_stop.set()
                if refresh_watcher is not None:
                    refresh_watcher.join()
                _write_refreshed_opencode_credential(
                    source_auth,
                    target_auth,
                    provider=provider,
                    sync_state=sync_state,
                )
    finally:
        release_refresh_lock()


def _read_opencode_auth(source_auth: Path, provider: str) -> dict[str, object]:
    """Read OpenCode's auth object or return an empty one when it is absent."""
    if not source_auth.is_file():
        return {}
    try:
        credentials = json.loads(source_auth.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendDownException(
            f"OpenCode auth.json could not be read for provider '{provider}'. "
            "Run 'opencode auth login' and select that provider."
        ) from exc
    if not isinstance(credentials, dict):
        raise BackendDownException(
            f"OpenCode auth.json could not be read for provider '{provider}'. "
            "Run 'opencode auth login' and select that provider."
        )
    return credentials


def _require_opencode_credential(credentials: dict[str, object], provider: str) -> dict[str, object]:
    """Return a configured provider entry or raise the established login error."""
    provider_credential = credentials.get(provider)
    if not isinstance(provider_credential, dict) or not provider_credential:
        raise BackendDownException(
            f"No OpenCode credential found for provider '{provider}'. "
            "Run 'opencode auth login' and select that provider."
        )
    return provider_credential


def _is_opencode_oauth_refresh_due(credential: dict[str, object]) -> bool:
    """Return whether an OAuth credential needs serialized startup refresh."""
    if credential.get("type") != "oauth":
        return False
    expires = _opencode_credential_expiry(credential)
    return expires is not None and expires <= time.time() + K_OPENCODE_OAUTH_REFRESH_WINDOW_SECONDS


def _opencode_credential_expiry(credential: dict[str, object]) -> float | None:
    """Return an OAuth expiry as Unix seconds, accepting OpenCode's seconds or milliseconds."""
    expires = credential.get("expires")
    if isinstance(expires, bool) or not isinstance(expires, (int, float, str)):
        return None
    try:
        timestamp = float(expires)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(timestamp):
        return None
    if timestamp > K_OPENCODE_OAUTH_EXPIRY_MILLISECONDS_THRESHOLD:
        timestamp /= 1000
    return timestamp


def _write_refreshed_opencode_credential(
    source_auth: Path,
    target_auth: Path,
    *,
    provider: str,
    sync_state: _OpenCodeCredentialSyncState,
) -> None:
    """Write one provider refresh only while its source entry still matches the baseline."""
    if sync_state.parent_won:
        return
    refreshed_entry = _read_refreshed_opencode_entry(target_auth, provider)
    if refreshed_entry is None:
        return
    try:
        result = _persist_refreshed_opencode_entry(source_auth, provider, refreshed_entry, sync_state)
    except OSError as exc:
        logging.getLogger(__name__).exception(
            "Could not persist refreshed OpenCode credential for provider %r: %s",
            provider,
            exc,
        )
        return
    if result is None:
        return
    written, latest_entry = result
    if not written:
        _adopt_opencode_parent_entry(target_auth, provider, latest_entry)
        sync_state.baseline_entry = latest_entry
        sync_state.parent_won = True
        return
    sync_state.baseline_entry = refreshed_entry


def _read_refreshed_opencode_entry(target_auth: Path, provider: str) -> dict[str, object] | None:
    """Read the isolated provider refresh, logging unusable child state."""
    try:
        target_credentials = json.loads(target_auth.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logging.getLogger(__name__).warning(
            "Skipping OpenCode credential writeback for provider %r: isolated auth.json is unavailable or invalid: %s",
            provider,
            exc,
        )
        return None
    if isinstance(target_credentials, dict) and isinstance(target_credentials.get(provider), dict):
        return cast(dict[str, object], target_credentials[provider])
    logging.getLogger(__name__).warning(
        "Skipping OpenCode credential writeback for provider %r: isolated auth.json has no object entry",
        provider,
    )
    return None


def _persist_refreshed_opencode_entry(
    source_auth: Path,
    provider: str,
    refreshed_entry: dict[str, object],
    sync_state: _OpenCodeCredentialSyncState,
) -> tuple[bool, object] | None:
    """Compare under the cooperating-writer lock and atomically merge a child refresh."""
    auth_lock_path = source_auth.with_name(f".{source_auth.name}.writeback.lock")
    with hold_opencode_file_lock(auth_lock_path, deadline=None, timeout=0, purpose="credential"):
        try:
            source_credentials = json.loads(source_auth.read_text(encoding="utf-8"))
        except FileNotFoundError:
            source_credentials = {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logging.getLogger(__name__).warning(
                "Skipping OpenCode credential writeback for provider %r: source auth.json is unavailable or invalid: %s",
                provider,
                exc,
            )
            return None
        if not isinstance(source_credentials, dict):
            raise OSError("source auth.json is not an object")

        current_entry = source_credentials.get(provider)
        if current_entry != sync_state.baseline_entry:
            return False, current_entry
        if refreshed_entry == current_entry:
            return None
        return _merge_opencode_auth_entry_atomically(
            source_auth,
            provider,
            expected_entry=sync_state.baseline_entry,
            refreshed_entry=refreshed_entry,
        )


def _adopt_opencode_parent_entry(target_auth: Path, provider: str, current_entry: object) -> None:
    """Replace the isolated provider entry with the parent's current entry or discard it."""
    try:
        if isinstance(current_entry, dict):
            _write_opencode_auth_atomically(target_auth, {provider: current_entry})
        else:
            target_auth.unlink(missing_ok=True)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Could not adopt the current OpenCode credential for provider %r into isolated auth.json: %s",
            provider,
            exc,
        )


def _watch_opencode_oauth_refresh(
    source_auth: Path,
    target_auth: Path,
    provider: str,
    sync_state: _OpenCodeCredentialSyncState,
    stop_event: threading.Event,
    release_refresh_lock: Callable[[], None],
) -> None:
    """Persist each changed child OAuth entry while releasing any refresh lease it advances."""
    last_entry = sync_state.baseline_entry
    while not stop_event.wait(K_OPENCODE_AUTH_LOCK_POLL_SECONDS):
        target_entry = _find_opencode_auth_entry(target_auth, provider)
        if target_entry is None or target_entry.get("type") != "oauth" or target_entry == last_entry:
            continue
        try:
            _write_refreshed_opencode_credential(
                source_auth,
                target_auth,
                provider=provider,
                sync_state=sync_state,
            )
        except OSError as exc:
            logging.getLogger(__name__).warning(
                "Could not persist refreshed OpenCode credential for provider %r while its process was running: %s",
                provider,
                exc,
            )
            continue
        if sync_state.parent_won:
            release_refresh_lock()
            return
        source_entry = _find_opencode_auth_entry(source_auth, provider)
        if source_entry == target_entry:
            release_refresh_lock()
        last_entry = target_entry


def _find_opencode_auth_entry(auth_path: Path, provider: str) -> dict[str, object] | None:
    """Return one object-shaped provider entry without logging transient partial files."""
    try:
        credentials = json.loads(auth_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(credentials, dict):
        return None
    entry = credentials.get(provider)
    return entry if isinstance(entry, dict) else None


def _write_opencode_auth_atomically(source_auth: Path, credentials: dict[str, object]) -> None:
    """Replace auth.json using a private same-directory temporary file and fsync."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{source_auth.name}.", dir=source_auth.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(credentials, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, source_auth)
        directory_descriptor = os.open(source_auth.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _merge_opencode_auth_entry_atomically(
    source_auth: Path,
    provider: str,
    *,
    expected_entry: object,
    refreshed_entry: dict[str, object],
) -> tuple[bool, object]:
    """Merge one provider into the freshest parent document before atomic replacement."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{source_auth.name}.", dir=source_auth.parent)
    temporary = Path(temporary_name)
    descriptor_open = True
    try:
        try:
            source_credentials = json.loads(source_auth.read_text(encoding="utf-8"))
        except FileNotFoundError:
            source_credentials = {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OSError("source auth.json is unavailable or invalid") from exc
        if not isinstance(source_credentials, dict):
            raise OSError("source auth.json is not an object")
        current_entry = source_credentials.get(provider)
        if current_entry != expected_entry:
            os.close(descriptor)
            descriptor_open = False
            temporary.unlink(missing_ok=True)
            return False, current_entry

        source_credentials[provider] = refreshed_entry
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor_open = False
        with handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(source_credentials, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, source_auth)
        directory_descriptor = os.open(source_auth.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        if descriptor_open:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return True, refreshed_entry


def _opencode_model_provider(model: str) -> str:
    """Return the provider segment from OpenCode's provider/model identifier."""
    provider, separator, _ = model.partition("/")
    if not separator or not provider:
        raise BackendDownException(f"OpenCode model '{model}' does not name a provider.")
    return provider


__all__ = ["opencode_process_environment"]
