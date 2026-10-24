"""Entry point: ``load_workbench_chat_settings`` reads the Codex chat role from config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator

from lesson_builder.clients.codex.settings import K_CODEX_CHAT_KEY
from lesson_builder.clients.codex.settings import K_CODEX_CONFIG_FILENAME
from lesson_builder.clients.codex.settings import K_CODEX_CONFIG_KEY
from lesson_builder.clients.codex.settings import K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS
from lesson_builder.formats.yaml import load_unique_yaml


def load_workbench_chat_settings(*, repo_root: Path | None = None) -> CodexChatSettings:
    """Read and validate Codex model and effort settings from the workspace config."""
    root = Path(repo_root) if repo_root is not None else Path.cwd()
    path = root / K_CODEX_CONFIG_FILENAME
    if not path.is_file():
        raise FileNotFoundError(f"Codex config not found at {path}")
    try:
        config = load_unique_yaml(path.read_text(encoding="utf-8"))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"config.yaml is not valid YAML: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get(K_CODEX_CONFIG_KEY), dict):
        raise TypeError("config.yaml must contain a codex mapping")
    raw = config[K_CODEX_CONFIG_KEY].get(K_CODEX_CHAT_KEY)
    if not isinstance(raw, dict):
        raise TypeError(f"config.yaml codex.{K_CODEX_CHAT_KEY} must contain a mapping")
    return CodexChatSettings.model_validate(raw)


class CodexChatSettings(BaseModel):
    """Model routing for the Codex-backed workbench chat role."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    effort: Literal["minimal", "low", "medium", "high", "xhigh"]
    timeout_seconds: int = Field(default=1800, gt=0)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if value not in K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS:
            raise ValueError("model is not an available Codex workbench model")
        return value


__all__ = ["CodexChatSettings", "load_workbench_chat_settings"]
