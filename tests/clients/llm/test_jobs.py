"""Behavior tests for configuration-selected LLM jobs."""

from __future__ import annotations

from pathlib import Path

import pytest

from lesson_builder.clients.llm.jobs import runner_for_job


@pytest.mark.parametrize("timeout_seconds", [240, 86_400])
def test_runner_for_job_given_temporary_root_config_expect_configured_route_and_override_deadline(
    tmp_path: Path,
    timeout_seconds: int,
) -> None:
    agents_dir = tmp_path / ".opencode" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "custom-author.md").write_text("---\nmode: primary\n---\n", encoding="utf-8")
    (tmp_path / "config.yaml").write_text(
        "llm:\n"
        "  timeout_seconds: 120\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/custom-model\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: custom-author\n",
        encoding="utf-8",
    )

    runner = runner_for_job("author", repo_root=tmp_path, timeout_seconds=timeout_seconds)

    assert runner.client.timeout == timeout_seconds
    assert runner.model == "openai/custom-model"
    assert runner.agent == "custom-author"
    assert runner.variant == "low"
