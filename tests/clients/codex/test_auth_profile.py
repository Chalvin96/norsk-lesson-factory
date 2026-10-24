"""Entry point: isolated Codex profile credential recovery behavior tests."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from lesson_builder.clients.codex import auth_profile
from lesson_builder.clients.codex import settings as codex_settings


def test_prepare_isolated_profile_given_parent_auth_deleted_after_restart_expect_orphan_discarded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(json.dumps({"tokens": {"access_token": "synthetic-login"}}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))

    profile_dir, _state = auth_profile.prepare_isolated_profile()
    profile_auth = profile_dir / codex_settings.K_CODEX_AUTH_FILENAME
    assert json.loads(profile_auth.read_text(encoding="utf-8")) == {"tokens": {"access_token": "synthetic-login"}}

    parent_auth.unlink()
    restarted_profile_dir, _restarted_state = auth_profile.prepare_isolated_profile()

    assert restarted_profile_dir != profile_dir
    assert not parent_auth.exists()
    assert not (restarted_profile_dir / codex_settings.K_CODEX_AUTH_FILENAME).exists()


def test_codex_auth_sync_given_unresolved_identity_and_changed_parent_expect_parent_kept(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_auth, profile_auth, state = _prepare_auth_pair(monkeypatch, tmp_path)
    parent_login = _auth_content("parent-login")
    isolated_refresh = _auth_content("isolated-refresh")
    parent_auth.write_text(parent_login, encoding="utf-8")
    profile_auth.write_text(isolated_refresh, encoding="utf-8")
    later_mtime_ns = parent_auth.stat().st_mtime_ns + 2_000_000_000
    os.utime(profile_auth, ns=(later_mtime_ns, later_mtime_ns))

    _sync_auth_files(parent_auth, profile_auth, state)

    assert parent_auth.read_text(encoding="utf-8") == parent_login
    assert profile_auth.read_text(encoding="utf-8") == parent_login


def test_codex_auth_sync_given_same_account_relogin_with_earlier_expiry_expect_parent_kept(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_auth, profile_auth, state = _prepare_auth_pair(monkeypatch, tmp_path)
    parent_login = _auth_content("parent-relogin", account_id="same-account", expires_at=1_800_000_000)
    isolated_refresh = _auth_content("isolated-refresh", account_id="same-account", expires_at=1_900_000_000)
    parent_auth.write_text(parent_login, encoding="utf-8")
    profile_auth.write_text(isolated_refresh, encoding="utf-8")

    _sync_auth_files(parent_auth, profile_auth, state)

    assert parent_auth.read_text(encoding="utf-8") == parent_login
    assert profile_auth.read_text(encoding="utf-8") == parent_login


def test_codex_auth_sync_given_other_watcher_advanced_baseline_expect_parent_kept(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_auth, profile_auth, first_watcher = _prepare_auth_pair(monkeypatch, tmp_path)
    second_profile_dir, second_watcher = auth_profile.prepare_isolated_profile()
    parent_login = _auth_content("parent-relogin", expires_at=1_800_000_000)
    stale_refresh = _auth_content("stale-refresh", expires_at=1_900_000_000)
    parent_auth.write_text(parent_login, encoding="utf-8")

    _sync_auth_files(parent_auth, profile_auth, first_watcher)
    profile_auth.write_text(stale_refresh, encoding="utf-8")
    _sync_auth_files(parent_auth, profile_auth, first_watcher)
    _sync_auth_files(parent_auth, second_profile_dir / codex_settings.K_CODEX_AUTH_FILENAME, second_watcher)

    assert parent_auth.read_text(encoding="utf-8") == parent_login
    assert (second_profile_dir / codex_settings.K_CODEX_AUTH_FILENAME).read_text(encoding="utf-8") == parent_login


def test_codex_auth_sync_given_unchanged_parent_expect_refreshed_auth_written_back(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_auth, profile_auth, state = _prepare_auth_pair(monkeypatch, tmp_path)
    isolated_refresh = _auth_content("isolated-refresh")
    profile_auth.write_text(isolated_refresh, encoding="utf-8")

    _sync_auth_files(parent_auth, profile_auth, state)

    assert parent_auth.read_text(encoding="utf-8") == isolated_refresh
    assert profile_auth.read_text(encoding="utf-8") == isolated_refresh


def test_prepare_isolated_profile_given_parent_wins_and_retired_profile_writes_stale_auth_expect_parent_kept(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_auth, retired_profile_auth, state = _prepare_auth_pair(monkeypatch, tmp_path)
    parent_login = _auth_content("parent-login")
    parent_auth.write_text(parent_login, encoding="utf-8")

    _sync_auth_files(parent_auth, retired_profile_auth, state)
    retired_profile_auth.write_text(_auth_content("stale-retired-refresh"), encoding="utf-8")

    current_profile_dir, _current_state = auth_profile.prepare_isolated_profile()
    current_profile_auth = current_profile_dir / codex_settings.K_CODEX_AUTH_FILENAME

    assert current_profile_dir != retired_profile_auth.parent
    assert parent_auth.read_text(encoding="utf-8") == parent_login
    assert current_profile_auth.read_text(encoding="utf-8") == parent_login


def test_prepare_isolated_profile_given_fresh_process_and_unpersisted_child_refresh_expect_refresh_kept(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_auth, profile_auth, _state = _prepare_auth_pair(monkeypatch, tmp_path)
    refreshed_auth = _auth_content("valid-child-refresh")
    profile_auth.write_text(refreshed_auth, encoding="utf-8")
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(parent_auth.parent)
    environment["NLD_TEST_SOURCE_ROOT"] = str(Path(__file__).resolve().parents[3] / "src")
    source = (
        "import os, sys; "
        "sys.path.insert(0, os.environ['NLD_TEST_SOURCE_ROOT']); "
        "from lesson_builder.clients.codex.auth_profile import prepare_isolated_profile; "
        "prepare_isolated_profile()"
    )

    subprocess.run([sys.executable, "-c", source], check=True, env=environment, capture_output=True, text=True)

    assert parent_auth.read_text(encoding="utf-8") == refreshed_auth


def test_sync_given_parent_change_and_metadata_write_failure_expect_retry_keeps_parent_login(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_auth, profile_auth, state = _prepare_auth_pair(monkeypatch, tmp_path)
    parent_login = _auth_content("new-parent-login")
    parent_auth.write_text(parent_login, encoding="utf-8")
    original_replace = os.replace
    failed = False

    def fail_metadata_once(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        nonlocal failed
        if Path(destination) == state.sync_state_path and not failed:
            failed = True
            raise OSError(errno.ENOSPC, "synthetic metadata failure")
        original_replace(source, destination)

    monkeypatch.setattr(auth_profile.os, "replace", fail_metadata_once)
    with pytest.raises(OSError, match="synthetic metadata failure"):
        _sync_auth_files(parent_auth, profile_auth, state)

    _sync_auth_files(parent_auth, profile_auth, state)
    assert parent_auth.read_text(encoding="utf-8") == parent_login


def test_prepare_given_new_owner_and_late_previous_refresh_expect_refresh_persisted_before_transfer(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_auth, previous_profile_auth, previous_state = _prepare_auth_pair(monkeypatch, tmp_path)
    original_create = auth_profile._create_codex_generation_profile
    late_refresh = _auth_content("valid-late-child-refresh")

    def create_after_previous_refresh(*args, **kwargs):
        previous_profile_auth.write_text(late_refresh, encoding="utf-8")
        return original_create(*args, **kwargs)

    monkeypatch.setattr(auth_profile, "_create_codex_generation_profile", create_after_previous_refresh)
    new_profile_dir, new_state = auth_profile.prepare_isolated_profile()

    auth_profile.restore_parent_auth(parent_auth, previous_profile_auth, previous_state)
    _sync_auth_files(parent_auth, new_profile_dir / codex_settings.K_CODEX_AUTH_FILENAME, new_state)

    assert parent_auth.read_text(encoding="utf-8") == late_refresh


@pytest.mark.parametrize("failure", ["metadata", "seed"])
def test_prepare_given_profile_creation_failure_expect_partial_directory_removed(
    monkeypatch,
    tmp_path: Path,
    failure: str,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_auth_content("baseline"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    profile_root = parent_auth.parent / codex_settings.K_CODEX_ISOLATED_PROFILE_DIRECTORY
    if failure == "metadata":
        original_replace = os.replace

        def fail_metadata(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
            if Path(destination).name == codex_settings.K_CODEX_AUTH_SYNC_STATE_FILENAME:
                raise OSError(errno.ENOSPC, "synthetic metadata failure")
            original_replace(source, destination)

        monkeypatch.setattr(auth_profile.os, "replace", fail_metadata)
    else:
        original_open = Path.open

        def fail_seed(path: Path, *args, **kwargs):
            if path == parent_auth and args and args[0] == "rb":
                raise OSError(errno.EIO, "synthetic seed failure")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fail_seed)

    with pytest.raises(OSError):
        auth_profile.prepare_isolated_profile()

    assert [path for path in profile_root.iterdir() if path.is_dir()] == []


def test_prepare_given_exited_owners_and_deleted_parent_expect_reclaimed_credentials_discarded(
    monkeypatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_auth_content("baseline"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    environment = os.environ.copy()
    environment["NLD_TEST_SOURCE_ROOT"] = str(Path(__file__).resolve().parents[3] / "src")
    source = (
        "import os, sys; "
        "sys.path.insert(0, os.environ['NLD_TEST_SOURCE_ROOT']); "
        "from lesson_builder.clients.codex.auth_profile import prepare_isolated_profile; "
        "prepare_isolated_profile()"
    )
    for _ in range(3):
        subprocess.run([sys.executable, "-c", source], check=True, env=environment, capture_output=True, text=True)

    parent_auth.unlink()

    current_profile_dir, _current_state = auth_profile.prepare_isolated_profile()

    profile_root = parent_auth.parent / codex_settings.K_CODEX_ISOLATED_PROFILE_DIRECTORY
    assert current_profile_dir.exists()
    assert not any(
        candidate != current_profile_dir and (candidate / codex_settings.K_CODEX_AUTH_FILENAME).exists()
        for candidate in profile_root.iterdir()
        if candidate.is_dir()
    )


def _prepare_auth_pair(monkeypatch, tmp_path: Path) -> tuple[Path, Path, auth_profile.CodexAuthSyncState]:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_auth_content("baseline"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    profile_dir, state = auth_profile.prepare_isolated_profile()
    return parent_auth, profile_dir / codex_settings.K_CODEX_AUTH_FILENAME, state


def _sync_auth_files(
    parent_auth: Path,
    profile_auth: Path,
    state: auth_profile.CodexAuthSyncState,
) -> None:
    lock_path = parent_auth.parent / codex_settings.K_CODEX_AUTH_SYNC_LOCK_FILENAME
    with auth_profile._codex_auth_file_lock(lock_path):
        auth_profile._sync_codex_auth_files(parent_auth, profile_auth, state)


def _auth_content(
    access_token: str,
    *,
    account_id: str | None = None,
    expires_at: int | None = None,
) -> str:
    document: dict[str, object] = {"tokens": {"access_token": access_token}}
    if account_id is not None:
        document["account_id"] = account_id
    if expires_at is not None:
        document["expires_at"] = expires_at
    return json.dumps(document)
