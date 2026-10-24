"""Entry point: `isolate_process_home` keeps every test's home and XDG state private."""

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolate_process_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point process home and XDG state at directories owned by this test."""
    for name in tuple(os.environ):
        if name.startswith("OPENCODE_"):
            monkeypatch.delenv(name)
    for name in (
        "HOME",
        "CODEX_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
    ):
        directory = tmp_path / name.lower()
        directory.mkdir()
        monkeypatch.setenv(name, os.fspath(directory))
