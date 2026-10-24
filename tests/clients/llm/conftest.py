"""Entry point: `opencode_auth_data_home` provides test-only provider credentials."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def opencode_auth_data_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Provide disposable provider credentials for tests that exercise a spawned-call fake."""
    data_home = tmp_path / "opencode-data"
    auth_path = data_home / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True)
    auth_path.write_text(
        json.dumps(
            {
                "openai": {"type": "api", "key": "test-openai-key"},
                "opencode-go": {"type": "api", "key": "test-opencode-key"},
                "zai-coding-plan": {"type": "api", "key": "test-zai-key"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
