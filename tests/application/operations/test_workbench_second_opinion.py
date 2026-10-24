"""Entry points: enabled second-opinion review results and advisory tags are covered."""

import json
import shutil
from _thread import RLock
from pathlib import Path

import pytest

from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_review import review_workbench_lesson
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettings
from lesson_builder.application.operations.workbench_settings_operations import default_workbench_settings
from lesson_builder.application.operations.workbench_settings_operations import write_workbench_settings
from tests.application.fakes import FakeLlmClient
from tests.application.fakes import workbench_review_reply


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    repo = Path(__file__).parents[3]
    shutil.copy(repo / "config.yaml", tmp_path / "config.yaml")
    shutil.copytree(repo / ".opencode" / "agents", tmp_path / ".opencode" / "agents")
    shutil.copytree(
        repo / "content" / "lessons" / "greet_and_introduce_yourself",
        tmp_path / "content" / "lessons" / "greet_and_introduce_yourself",
    )
    return tmp_path


def test_workbench_review_given_second_opinion_enabled_expect_advisory_findings_from_selected_model(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    block = next(item for item in read_workbench_source(workspace, lesson_id).blocks if item.kind == "reading")
    defaults = default_workbench_settings(workspace)
    changed = WorkbenchSettings.model_validate(
        {
            **defaults.model_dump(mode="json"),
            "second_opinion": {"enabled": True, "model": "anthropic/claude-sonnet-5-5"},
        }
    )
    write_workbench_settings(workspace, changed)

    def respond(prompt: str, model: str | None) -> str:
        if '"NaturalnessReview"' in prompt:
            return json.dumps(
                {
                    "scores": {
                        "idiomatic_phrasing": 4,
                        "register_appropriateness": 4,
                        "terminology_consistency": 4,
                    },
                    "issues": [
                        {
                            "unit_id": block.key.partition("#")[0],
                            "severity": "P2",
                            "message": f"Finding from {model}.",
                        }
                    ],
                }
            )
        return workbench_review_reply(prompt, model)

    client = FakeLlmClient.from_fn(respond)
    monkeypatch.setattr("lesson_builder.clients.llm.role_jobs.client_for_job", lambda *args, **kwargs: client)

    result = review_workbench_lesson(workspace, lesson_id, "item", block.key, RLock())

    assert result["second_opinion_model"] == "anthropic/claude-sonnet-5-5"
    assert [finding["reviewer"] for finding in result["findings"]] == ["primary", "second_opinion"]
    assert [finding["explanation"] for finding in result["findings"]] == [
        "Finding from openai/gpt-6.1-sol.",
        "Finding from anthropic/claude-sonnet-5-5.",
    ]
    assert sorted(model for _, model in client.calls) == sorted(
        [
            "openai/gpt-6.1-sol",
            "openai/gpt-6.1-sol",
            "anthropic/claude-sonnet-5-5",
            "anthropic/claude-sonnet-5-5",
        ]
    )
