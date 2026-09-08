"""Entry point: ``load_llm_job`` reads the root LLM routing configuration.

``config.yaml`` is the single source of truth for this routing layer.
``llm.tiers`` name cheap model/variant pairs by relative cost (cheap through
deep_alt); ``llm.jobs`` bind stable workflow names to exactly one tier plus
one OpenCode agent template. Production nodes select jobs, never raw tiers.
OpenCode is the only supported transport, so no adapter field exists. A
missing tier, job, or ``.opencode/agents/<agent_template>.md`` file fails
here, before any LLM call starts. Retired field names (``adapter``,
``adapter_model``, ``profile``) are unknown fields, not aliases.
``llm.timeout_seconds`` is the configurable default call deadline; it is not
a ceiling, and callers may pass any explicit finite positive override.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.formats.yaml import load_unique_yaml

K_LLM_CONFIG_FILENAME = "config.yaml"
K_LLM_CONFIG_KEY = "llm"
K_LLM_TIMEOUT_KEY = "timeout_seconds"
K_LLM_TIERS_KEY = "tiers"
K_LLM_JOBS_KEY = "jobs"
K_LLM_CONFIG_FIELDS = frozenset({K_LLM_TIMEOUT_KEY, K_LLM_TIERS_KEY, K_LLM_JOBS_KEY})
K_LLM_TIER_REQUIRED_FIELDS = ("model", "variant")
K_LLM_TIER_FIELDS = frozenset(K_LLM_TIER_REQUIRED_FIELDS)
K_LLM_JOB_REQUIRED_FIELDS = ("tier", "agent_template")
K_LLM_JOB_FIELDS = frozenset(K_LLM_JOB_REQUIRED_FIELDS)
K_LLM_AGENT_TEMPLATES_DIR = ".opencode/agents"
K_LLM_AGENT_TEMPLATE_SUFFIX = ".md"


@dataclass(frozen=True)
class LlmTier:
    """One named model/variant pair selected by relative cost."""

    name: str
    model: str
    variant: str


@dataclass(frozen=True)
class LlmJob:
    """One stable workflow name bound to a tier and an OpenCode agent template."""

    name: str
    tier: LlmTier
    agent_template: str


def load_llm_job(job: str, *, repo_root: Path | None = None) -> LlmJob:
    """Load one named job and fail before any LLM invocation if invalid."""
    jobs = load_llm_jobs(repo_root=repo_root)
    try:
        return jobs[job]
    except KeyError as exc:
        raise ValueError(f"config.yaml has no valid LLM job {job!r}") from exc


def load_llm_jobs(*, repo_root: Path | None = None) -> dict[str, LlmJob]:
    """Parse every tier and resolve every configured job, templates included."""
    llm_config = _load_llm_config(repo_root=repo_root)
    root = _config_root(repo_root)
    tiers = _parse_tiers(llm_config.get(K_LLM_TIERS_KEY))
    return _parse_jobs(llm_config.get(K_LLM_JOBS_KEY), tiers, root)


def load_llm_timeout_seconds(*, repo_root: Path | None = None) -> int:
    """Return the configurable default call deadline from ``config.yaml``."""
    llm_config = _load_llm_config(repo_root=repo_root)
    if K_LLM_TIMEOUT_KEY not in llm_config:
        raise ValueError(f"config.yaml llm requires {K_LLM_TIMEOUT_KEY}")
    return _parse_timeout_seconds(llm_config[K_LLM_TIMEOUT_KEY])


def _load_llm_config(*, repo_root: Path | None) -> dict[str, Any]:
    """Load the root YAML mapping before tier, job, or timeout validation."""
    config_path = _config_root(repo_root) / K_LLM_CONFIG_FILENAME
    if not config_path.is_file():
        raise FileNotFoundError(f"LLM config not found at {config_path}")
    try:
        config = load_unique_yaml(config_path.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"config.yaml is not valid YAML: {exc}") from exc
    if not isinstance(config, dict):
        raise TypeError("config.yaml must contain a mapping")
    llm_config = config.get(K_LLM_CONFIG_KEY)
    if not isinstance(llm_config, dict):
        raise TypeError("config.yaml must contain an llm mapping")
    unknown = sorted(set(llm_config) - K_LLM_CONFIG_FIELDS)
    if unknown:
        raise ValueError(f"config.yaml llm has unsupported fields: {', '.join(unknown)}")
    return llm_config


def _config_root(repo_root: Path | None) -> Path:
    """Return the repository root that owns config and agent templates."""
    return Path(repo_root) if repo_root is not None else Path.cwd()


def _parse_timeout_seconds(value: object) -> int:
    """Validate the default deadline: a positive integer with no upper bound."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"config.yaml llm.{K_LLM_TIMEOUT_KEY} must be an integer")
    if value <= 0:
        raise ValueError(f"config.yaml llm.{K_LLM_TIMEOUT_KEY} must be positive")
    return value


def _parse_tiers(raw_tiers: object) -> dict[str, LlmTier]:
    """Validate every configured model tier."""
    if not isinstance(raw_tiers, dict) or not raw_tiers:
        raise ValueError(f"config.yaml llm.{K_LLM_TIERS_KEY} must contain a non-empty mapping")
    return {name: _parse_tier(name, value) for name, value in raw_tiers.items()}


def _parse_tier(name: object, value: object) -> LlmTier:
    """Validate one raw model-tier mapping."""
    if not isinstance(name, str) or not name:
        raise ValueError("config.yaml tier names must be non-empty strings")
    if not isinstance(value, dict):
        raise TypeError(f"config.yaml tier {name!r} must be a mapping")
    unknown = sorted(set(value) - K_LLM_TIER_FIELDS)
    if unknown:
        raise ValueError(f"config.yaml tier {name!r} has unsupported fields: {', '.join(unknown)}")
    missing = [field for field in K_LLM_TIER_REQUIRED_FIELDS if not _non_empty_string(value.get(field))]
    if missing:
        raise ValueError(f"config.yaml tier {name!r} requires {', '.join(missing)}")
    return LlmTier(name=name, model=value["model"], variant=value["variant"])


def _parse_jobs(raw_jobs: object, tiers: dict[str, LlmTier], root: Path) -> dict[str, LlmJob]:
    """Resolve every stable job binding against a configured tier and template."""
    if not isinstance(raw_jobs, dict) or not raw_jobs:
        raise ValueError(f"config.yaml llm.{K_LLM_JOBS_KEY} must contain a non-empty mapping")
    jobs: dict[str, LlmJob] = {}
    for name, value in raw_jobs.items():
        job = _parse_job(name, value, tiers, root)
        if job.name in tiers:
            raise ValueError(f"config.yaml job {job.name!r} duplicates a model tier name")
        jobs[job.name] = job
    return jobs


def _parse_job(name: object, value: object, tiers: dict[str, LlmTier], root: Path) -> LlmJob:
    """Validate one job mapping and its agent-template file."""
    if not isinstance(name, str) or not name:
        raise ValueError("config.yaml job names must be non-empty strings")
    if not isinstance(value, dict):
        raise TypeError(f"config.yaml job {name!r} must be a mapping")
    unknown = sorted(set(value) - K_LLM_JOB_FIELDS)
    if unknown:
        raise ValueError(f"config.yaml job {name!r} has unsupported fields: {', '.join(unknown)}")
    missing = [field for field in K_LLM_JOB_REQUIRED_FIELDS if not _non_empty_string(value.get(field))]
    if missing:
        raise ValueError(f"config.yaml job {name!r} requires {', '.join(missing)}")
    tier_name = value["tier"]
    tier = tiers.get(tier_name)
    if tier is None:
        raise ValueError(f"config.yaml job {name!r} references unknown tier {tier_name!r}")
    agent_template = value["agent_template"]
    template_path = _agent_template_path(root, agent_template)
    if not template_path.is_file():
        raise ValueError(
            f"config.yaml job {name!r} references agent template {agent_template!r} with no file at {template_path}"
        )
    return LlmJob(name=name, tier=tier, agent_template=agent_template)


def _agent_template_path(root: Path, agent_template: str) -> Path:
    """Return the agent-template path confined to the repository's agents directory.

    Fails closed: the template must be a bare file name (never absolute, never
    a path with separators or ``..`` components), the resolved agents directory
    must stay inside the resolved repository root, and the resolved template
    must stay inside that resolved agents directory, so no symlink can serve
    repository content from outside the agents directory.
    """
    if Path(agent_template).name != agent_template or agent_template in (".", ".."):
        raise ValueError(
            f"config.yaml agent_template {agent_template!r} must be a bare file name inside {K_LLM_AGENT_TEMPLATES_DIR}"
        )
    agents_dir = root / K_LLM_AGENT_TEMPLATES_DIR
    candidate = agents_dir / f"{agent_template}{K_LLM_AGENT_TEMPLATE_SUFFIX}"
    if not agents_dir.resolve().is_relative_to(root.resolve()):
        raise ValueError(
            f"config.yaml agent_template {agent_template!r} resolves outside the repository: "
            f"{K_LLM_AGENT_TEMPLATES_DIR} must stay inside the repository"
        )
    if not candidate.resolve().is_relative_to(agents_dir.resolve()):
        raise ValueError(
            f"config.yaml agent_template {agent_template!r} resolves outside the repository's "
            f"{K_LLM_AGENT_TEMPLATES_DIR} directory"
        )
    return candidate


def _non_empty_string(value: object) -> bool:
    """Return whether a value is a non-empty string."""
    return isinstance(value, str) and bool(value.strip())


__all__ = ["LlmJob", "LlmTier", "load_llm_job", "load_llm_jobs", "load_llm_timeout_seconds"]
