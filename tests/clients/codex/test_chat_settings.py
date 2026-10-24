"""Entry point: Codex chat configuration validation behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from lesson_builder.clients.codex.chat_settings import load_workbench_chat_settings
from lesson_builder.clients.codex.settings import K_CODEX_APP_SERVER_VERSION


def test_codex_settings_given_repository_config_expect_pinned_chat_model_and_effort() -> None:
    settings = load_workbench_chat_settings(repo_root=Path(__file__).parents[3])

    assert K_CODEX_APP_SERVER_VERSION == "0.162.1"
    assert settings.model == "gpt-6.1-sol"
    assert settings.effort == "medium"
    assert settings.timeout_seconds == 1800


def test_codex_settings_given_unsupported_effort_expect_validation_error(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("codex:\n  workbench_chat:\n    model: gpt-6.1-sol\n    effort: unknown\n")

    with pytest.raises(ValueError):
        load_workbench_chat_settings(repo_root=tmp_path)
