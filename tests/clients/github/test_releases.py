"""Entry point: `upload_release_asset` behavior at the ``gh`` command boundary.

Offline: the subprocess invocation is faked. These tests own the provider
contract: the exact ``gh release upload`` argument shape and the configured
timeout bound.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from lesson_builder.clients.github.releases import reconcile_release_asset
from lesson_builder.clients.github.releases import upload_release_asset
from lesson_builder.clients.github.settings import K_GITHUB_RELEASE_UPLOAD_TIMEOUT_SECONDS


def test_upload_release_asset_given_archive_expect_immutable_gh_upload_with_timeout(monkeypatch, tmp_path: Path):
    archive = tmp_path / "lessons.tar.gz"
    archive.write_bytes(b"archive")
    calls: list[dict[str, object]] = []

    def fake_run(command: list[str], *, check: bool, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append({"command": command, "check": check, "timeout": timeout})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("lesson_builder.clients.github.releases.subprocess.run", fake_run)

    upload_release_asset(archive_path=archive, repository="owner/repo", tag="lessons-2026.08.23")

    assert calls == [
        {
            "command": [
                "gh",
                "release",
                "upload",
                "lessons-2026.08.23",
                str(archive),
                "--repo",
                "owner/repo",
            ],
            "check": True,
            "timeout": K_GITHUB_RELEASE_UPLOAD_TIMEOUT_SECONDS,
        }
    ]


def test_upload_release_asset_given_gh_failure_expect_check_true_propagation(monkeypatch, tmp_path: Path):
    def failing_run(command: list[str], *, check: bool, timeout: float) -> subprocess.CompletedProcess[str]:
        assert check is True
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr("lesson_builder.clients.github.releases.subprocess.run", failing_run)

    with pytest.raises(subprocess.CalledProcessError):
        upload_release_asset(
            archive_path=tmp_path / "lessons.tar.gz",
            repository="owner/repo",
            tag="lessons-2026.08.23",
        )


def test_reconcile_release_asset_given_missing_release_expect_create_then_upload(monkeypatch, tmp_path: Path):
    archive = tmp_path / "lessons.tar.gz"
    archive.write_bytes(b"archive")
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ["release", "view"]:
            raise subprocess.CalledProcessError(1, command, stderr="release not found")
        return subprocess.CompletedProcess(command, 0, stdout="")

    monkeypatch.setattr("lesson_builder.clients.github.releases.subprocess.run", fake_run)
    assert reconcile_release_asset(archive_path=archive, repository="owner/repo", tag="v1") == "uploaded"
    assert calls[1][1:3] == ["release", "create"]
    assert calls[2][1:3] == ["release", "upload"]


def test_reconcile_release_asset_given_matching_asset_expect_skip_upload(monkeypatch, tmp_path: Path):
    archive = tmp_path / "lessons.tar.gz"
    archive.write_bytes(b"archive")
    digest = hashlib.sha256(b"archive").hexdigest()
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0, stdout=f'{{"assets":[{{"name":"{archive.name}","size":7,"digest":"sha256:{digest}"}}]}}'
        )

    monkeypatch.setattr("lesson_builder.clients.github.releases.subprocess.run", fake_run)
    assert reconcile_release_asset(archive_path=archive, repository="owner/repo", tag="v1") == "matched"
    assert len(calls) == 1


def test_reconcile_release_asset_given_conflicting_asset_expect_fail_closed(monkeypatch, tmp_path: Path):
    archive = tmp_path / "lessons.tar.gz"
    archive.write_bytes(b"archive")

    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout=f'{{"assets":[{{"name":"{archive.name}","size":99,"digest":"sha256:other"}}]}}'
        )

    monkeypatch.setattr("lesson_builder.clients.github.releases.subprocess.run", fake_run)
    with pytest.raises(ValueError, match="different bytes"):
        reconcile_release_asset(archive_path=archive, repository="owner/repo", tag="v1")


def test_reconcile_release_asset_given_first_timeout_then_matching_expect_success(monkeypatch, tmp_path: Path):
    archive = tmp_path / "lessons.tar.gz"
    archive.write_bytes(b"archive")
    digest = hashlib.sha256(b"archive").hexdigest()
    views = 0

    def fake_run(command, **kwargs):
        nonlocal views
        if command[1:3] == ["release", "view"]:
            views += 1
            assets = [] if views == 1 else [{"name": archive.name, "size": 7, "digest": f"sha256:{digest}"}]
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"assets": assets}))
        raise subprocess.TimeoutExpired(command, 300)

    monkeypatch.setattr("lesson_builder.clients.github.releases.subprocess.run", fake_run)
    assert reconcile_release_asset(archive_path=archive, repository="owner/repo", tag="v1") == "matched"


def test_reconcile_release_asset_given_repeated_timeout_expect_bounded_unknown_error(monkeypatch, tmp_path: Path):
    archive = tmp_path / "lessons.tar.gz"
    archive.write_bytes(b"archive")

    def fake_run(command, **kwargs):
        if command[1:3] == ["release", "view"]:
            return subprocess.CompletedProcess(command, 0, stdout='{"assets":[]}')
        raise subprocess.TimeoutExpired(command, 300)

    monkeypatch.setattr("lesson_builder.clients.github.releases.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="remains unknown"):
        reconcile_release_asset(archive_path=archive, repository="owner/repo", tag="v1")


def test_reconcile_release_asset_given_non_not_found_view_failure_expect_propagation(monkeypatch, tmp_path: Path):
    archive = tmp_path / "lessons.tar.gz"
    archive.write_bytes(b"archive")

    def fake_run(command, **kwargs):
        raise subprocess.CalledProcessError(2, command, stderr="permission denied")

    monkeypatch.setattr("lesson_builder.clients.github.releases.subprocess.run", fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        reconcile_release_asset(archive_path=archive, repository="owner/repo", tag="v1")
