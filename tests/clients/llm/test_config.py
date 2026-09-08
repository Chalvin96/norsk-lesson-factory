"""Behavior tests for strict root LLM tier/job routing configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from lesson_builder.clients.llm.config import load_llm_job
from lesson_builder.clients.llm.config import load_llm_jobs
from lesson_builder.clients.llm.config import load_llm_timeout_seconds

K_VALID_CONFIG = """\
llm:
  timeout_seconds: 1800
  tiers:
    cheap:
      model: openai/gpt-5.6-luna
      variant: xhigh
    standard:
      model: openai/gpt-5.6-sol
      variant: low
  jobs:
    author:
      tier: standard
      agent_template: lesson-author
    normalizer:
      tier: cheap
      agent_template: lesson-author
"""


def test_load_llm_job_given_valid_root_config_expect_tier_and_agent_template(tmp_path: Path):
    _write_config(tmp_path, K_VALID_CONFIG)

    job = load_llm_job("author", repo_root=tmp_path)

    assert job.name == "author"
    assert job.tier.name == "standard"
    assert job.tier.model == "openai/gpt-5.6-sol"
    assert job.tier.variant == "low"
    assert job.agent_template == "lesson-author"


def test_load_llm_job_given_unknown_job_name_expect_configuration_error(tmp_path: Path):
    _write_config(tmp_path, K_VALID_CONFIG)

    with pytest.raises(ValueError, match="no valid LLM job 'catalog_router'"):
        load_llm_job("catalog_router", repo_root=tmp_path)


def test_load_llm_timeout_seconds_given_valid_config_expect_positive_default(tmp_path: Path):
    _write_config(tmp_path, K_VALID_CONFIG)

    assert load_llm_timeout_seconds(repo_root=tmp_path) == 1800


def test_load_llm_tier_given_missing_variant_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="tier 'standard' requires variant"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_job_given_missing_agent_template_field_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n",
    )

    with pytest.raises(ValueError, match="job 'author' requires agent_template"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_config_given_retired_adapter_fields_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      adapter: opencode\n"
        "      adapter_model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="tier 'standard' has unsupported fields: adapter, adapter_model"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_config_given_retired_profiles_section_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  profiles:\n"
        "    normal:\n"
        "      adapter: opencode\n"
        "      adapter_model: openai/gpt-5.6-sol\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="llm has unsupported fields: profiles"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_config_given_job_field_alias_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      profile: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="job 'author' has unsupported fields: profile"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_job_given_unknown_tier_reference_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: strong\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="job 'author' references unknown tier 'strong'"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_job_given_missing_agent_template_file_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: missing-author\n",
    )

    with pytest.raises(ValueError, match="agent template 'missing-author' with no file"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_config_given_job_name_collides_with_tier_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    standard:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="job 'standard' duplicates a model tier name"):
        load_llm_jobs(repo_root=tmp_path)


@pytest.mark.parametrize("timeout_yaml", ["0", "-5"])
def test_load_llm_timeout_seconds_given_non_positive_integer_expect_value_error(tmp_path: Path, timeout_yaml: str):
    _write_config(
        tmp_path,
        "llm:\n"
        f"  timeout_seconds: {timeout_yaml}\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="timeout_seconds must be"):
        load_llm_timeout_seconds(repo_root=tmp_path)


@pytest.mark.parametrize("timeout_yaml", ["true", "1.5", '"90"', "ninety"])
def test_load_llm_timeout_seconds_given_non_integer_expect_type_error(tmp_path: Path, timeout_yaml: str):
    _write_config(
        tmp_path,
        "llm:\n"
        f"  timeout_seconds: {timeout_yaml}\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(TypeError, match="timeout_seconds must be"):
        load_llm_timeout_seconds(repo_root=tmp_path)


def test_load_llm_timeout_seconds_given_missing_key_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="llm requires timeout_seconds"):
        load_llm_timeout_seconds(repo_root=tmp_path)


def test_load_llm_config_given_duplicate_yaml_key_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      model: attacker/model\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="duplicate YAML key"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_config_given_non_string_model_scalar_expect_configuration_error(tmp_path: Path):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: 42\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        "      agent_template: lesson-author\n",
    )

    with pytest.raises(ValueError, match="tier 'standard' requires model"):
        load_llm_job("author", repo_root=tmp_path)


@pytest.mark.parametrize(
    "agent_template",
    ["/etc/opencode/agents/evil", "../outside-agent", "nested/../outside-agent", "..", "."],
)
def test_load_llm_job_given_unconfined_agent_template_expect_configuration_error(tmp_path: Path, agent_template: str):
    _write_config(
        tmp_path,
        "llm:\n"
        "  timeout_seconds: 90\n"
        "  tiers:\n"
        "    standard:\n"
        "      model: openai/gpt-5.6-sol\n"
        "      variant: low\n"
        "  jobs:\n"
        "    author:\n"
        "      tier: standard\n"
        f"      agent_template: {agent_template}\n",
    )

    with pytest.raises(ValueError, match="agent_template"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_job_given_agent_template_symlink_escape_expect_configuration_error(tmp_path: Path):
    _write_config(tmp_path, K_VALID_CONFIG)
    template = tmp_path / ".opencode" / "agents" / "lesson-author.md"
    template.unlink()
    template.symlink_to("/etc/hosts")

    with pytest.raises(ValueError, match="resolves outside"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_job_given_agent_template_symlink_inside_repo_outside_agents_expect_configuration_error(
    tmp_path: Path,
):
    _write_config(tmp_path, K_VALID_CONFIG)
    repository_readme = tmp_path / "README.md"
    repository_readme.write_text("# repository readme\n", encoding="utf-8")
    template = tmp_path / ".opencode" / "agents" / "lesson-author.md"
    template.unlink()
    template.symlink_to(repository_readme)

    with pytest.raises(ValueError, match="resolves outside"):
        load_llm_job("author", repo_root=tmp_path)


def test_load_llm_job_given_agents_directory_symlink_outside_repository_expect_configuration_error(tmp_path: Path):
    _write_config(tmp_path, K_VALID_CONFIG)
    agents_dir = tmp_path / ".opencode" / "agents"
    for child in agents_dir.iterdir():
        child.unlink()
    agents_dir.rmdir()
    agents_dir.symlink_to("/tmp")

    with pytest.raises(ValueError, match="resolves outside the repository"):
        load_llm_job("author", repo_root=tmp_path)


def _write_config(tmp_path: Path, llm_yaml: str) -> Path:
    """Install one root config plus the agent template it references."""
    (tmp_path / "config.yaml").write_text(llm_yaml, encoding="utf-8")
    agents_dir = tmp_path / ".opencode" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "lesson-author.md").write_text("---\nmode: primary\n---\n", encoding="utf-8")
    (agents_dir / "other.md").write_text("---\nmode: primary\n---\n", encoding="utf-8")
    return tmp_path
