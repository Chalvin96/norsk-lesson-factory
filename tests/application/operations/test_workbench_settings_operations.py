"""Entry points: workbench role defaults, overrides, and role-runner behavior tests."""

import shutil
from pathlib import Path

import pytest

from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettings
from lesson_builder.application.operations.workbench_settings_operations import default_workbench_settings
from lesson_builder.application.operations.workbench_settings_operations import read_workbench_role_setting
from lesson_builder.application.operations.workbench_settings_operations import workbench_runner_for_role
from lesson_builder.application.operations.workbench_settings_operations import workbench_settings_response
from lesson_builder.application.operations.workbench_settings_operations import write_workbench_settings
from lesson_builder.clients.llm.invocation import JobRunner
from tests.application.fakes import FakeLlmClient


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    repo = Path(__file__).parents[3]
    shutil.copy(repo / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(repo / ".opencode" / "agents", tmp_path / ".opencode" / "agents")
    return tmp_path


def test_workbench_role_setting_given_no_override_expect_config_tier_values(workspace: Path) -> None:
    settings = default_workbench_settings(workspace)

    assert settings.roles.chat.model == "gpt-6.1-sol"
    assert settings.roles.chat.effort == "medium"
    assert settings.roles.generation.model == "openai/gpt-6.1-sol"
    assert settings.roles.generation.effort == "medium"
    assert read_workbench_role_setting(workspace, "lesson_review") == settings.roles.lesson_review
    assert not (workspace / ".workbench" / "settings.yaml").exists()


def test_workbench_settings_response_given_role_options_expect_codex_and_opencode_models(
    workspace: Path,
) -> None:
    response = workbench_settings_response(workspace)

    assert "gpt-6-luna" in response["modelOptions"]["chat"]
    assert "openai/gpt-6-luna" not in response["modelOptions"]["chat"]
    assert "openai/gpt-6-luna" in response["modelOptions"]["generation"]
    assert "anthropic/claude-sonnet-5-5" in response["modelOptions"]["second_opinion"]
    assert "minimal" in response["effortOptions"]["chat"]
    assert "minimal" not in response["effortOptions"]["generation"]


def test_workbench_role_setting_given_saved_override_expect_role_runner_uses_model_and_effort(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = default_workbench_settings(workspace)
    changed = WorkbenchSettings.model_validate(
        {
            **settings.model_dump(mode="json"),
            "roles": {
                **settings.roles.model_dump(mode="json"),
                "generation": {"model": "zai-coding-plan/glm-5.3", "effort": "high"},
            },
        }
    )
    write_workbench_settings(workspace, changed)
    fake = FakeLlmClient.responding("ok")
    monkeypatch.setattr(
        "lesson_builder.clients.llm.role_jobs.client_for_job",
        lambda *args, **kwargs: fake,
    )

    runner = workbench_runner_for_role(workspace, "generation")
    assert isinstance(runner, JobRunner)
    assert runner.invoke("prompt") == "ok"
    assert runner.model == "zai-coding-plan/glm-5.3"
    assert runner.variant == "high"
    assert fake.calls[0][1] == "zai-coding-plan/glm-5.3"
