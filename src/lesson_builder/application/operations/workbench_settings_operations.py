"""Entry points: `read_workbench_settings` and `workbench_settings_response` serve Settings; `read_workbench_codex_settings` resolves each chat turn; `write_workbench_settings` and `restore_default_workbench_settings` save or reset values; `workbench_runner_for_role` configures OpenCode jobs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal
from typing import TypedDict
from typing import cast

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_DEFAULT_SECOND_OPINION_MODEL
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_JOB_BY_ROLE
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_OPENCODE_EFFORT_OPTIONS
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_SETTINGS_EFFORT_OPTIONS
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_SETTINGS_MODEL_OPTIONS
from lesson_builder.clients.codex.chat_settings import CodexChatSettings
from lesson_builder.clients.codex.chat_settings import load_workbench_chat_settings
from lesson_builder.clients.codex.settings import K_CODEX_WORKBENCH_CHAT_EFFORT_OPTIONS
from lesson_builder.clients.codex.settings import K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS
from lesson_builder.clients.llm import role_jobs as llm_jobs
from lesson_builder.clients.llm.config import LlmJob
from lesson_builder.clients.llm.config import load_llm_job
from lesson_builder.clients.llm.config import load_llm_jobs
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.formats.workbench_settings_format import parse_workbench_settings
from lesson_builder.formats.workbench_settings_format import render_workbench_settings
from lesson_builder.workspace.workbench_settings_store import read_workbench_settings_text
from lesson_builder.workspace.workbench_settings_store import reset_workbench_settings
from lesson_builder.workspace.workbench_settings_store import write_workbench_settings_text

WorkbenchRole = Literal["chat", "generation", "lesson_review", "exercise_review"]
OpenCodeWorkbenchRole = Literal["generation", "lesson_review", "exercise_review"]
WorkbenchEffort = Literal["minimal", "low", "medium", "high", "xhigh"]
ReviewScope = Literal["lesson", "section", "item"]


class WorkbenchRoleSetting(BaseModel):
    """Model and reasoning effort selected for one workbench role."""

    model_config = ConfigDict(extra="forbid")

    model: str
    effort: WorkbenchEffort


class WorkbenchRoles(BaseModel):
    """Role-specific choices for workbench jobs."""

    model_config = ConfigDict(extra="forbid")

    chat: WorkbenchRoleSetting
    generation: WorkbenchRoleSetting
    lesson_review: WorkbenchRoleSetting
    exercise_review: WorkbenchRoleSetting

    @model_validator(mode="after")
    def validate_role_options(self) -> WorkbenchRoles:
        if self.chat.model not in K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS:
            raise ValueError("Chat model is not an available Codex workbench model")
        if self.chat.effort not in K_CODEX_WORKBENCH_CHAT_EFFORT_OPTIONS:
            raise ValueError("Chat effort is not available for Codex workbench chat")
        for role in (self.generation, self.lesson_review, self.exercise_review):
            if role.model not in K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS:
                raise ValueError("OpenCode workbench role model is not available")
            if role.effort not in K_APPLICATION_WORKBENCH_OPENCODE_EFFORT_OPTIONS:
                raise ValueError("OpenCode workbench role effort is not available")
        return self


class SecondOpinionSetting(BaseModel):
    """Optional reviewer choice; any resulting findings remain advisory."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    model: str

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        if value not in K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS:
            raise ValueError("second-opinion model is not an available workbench model")
        return value


class WorkbenchDefaults(BaseModel):
    """Saved defaults used when opening a review action."""

    model_config = ConfigDict(extra="forbid")

    review_scope: ReviewScope


class WorkbenchAudioSettings(BaseModel):
    """Provider spend limit applied to workbench audio synthesis."""

    model_config = ConfigDict(extra="forbid")

    monthly_cap_usd: float = Field(default=5.0, ge=0, allow_inf_nan=False)


class WorkbenchSettings(BaseModel):
    """Validated, ignored per-workspace Settings page values."""

    model_config = ConfigDict(extra="forbid")

    roles: WorkbenchRoles
    second_opinion: SecondOpinionSetting
    defaults: WorkbenchDefaults
    audio: WorkbenchAudioSettings = Field(default_factory=WorkbenchAudioSettings)

    @model_validator(mode="after")
    def validate_second_opinion_family(self) -> WorkbenchSettings:
        if self.second_opinion.enabled:
            second_family = self.second_opinion.model.split("/", maxsplit=1)[0]
            primary_families = {
                self.roles.lesson_review.model.split("/", maxsplit=1)[0],
                self.roles.exercise_review.model.split("/", maxsplit=1)[0],
            }
            if second_family in primary_families:
                raise ValueError("second-opinion reviewer must use another model family")
        return self


class WorkbenchSettingsResponse(TypedDict):
    """Settings page projection returned by the workbench settings operation."""

    settings: WorkbenchSettings
    modelOptions: dict[WorkbenchRole | Literal["second_opinion"], list[str]]
    effortOptions: dict[WorkbenchRole, list[WorkbenchEffort]]
    reviewScopeOptions: list[ReviewScope]


def read_workbench_settings(root: Path) -> WorkbenchSettings:
    """Return validated workspace overrides or defaults from config.yaml."""
    source = read_workbench_settings_text(root)
    if source is None:
        return default_workbench_settings(root)
    return WorkbenchSettings.model_validate(parse_workbench_settings(source))


def default_workbench_settings(root: Path) -> WorkbenchSettings:
    """Project Codex and OpenCode defaults into the Settings page's role schema."""
    if (root / "config.yaml").is_file():
        jobs = load_llm_jobs(repo_root=root)
        chat = load_workbench_chat_settings(repo_root=root)
        roles = {"chat": WorkbenchRoleSetting(model=chat.model, effort=chat.effort)}
        roles.update(
            {
                role: WorkbenchRoleSetting(
                    model=jobs[job_name].tier.model,
                    effort=_configured_effort(jobs[job_name].tier.variant),
                )
                for role, job_name in K_APPLICATION_WORKBENCH_JOB_BY_ROLE.items()
            }
        )
    else:
        roles = {"chat": WorkbenchRoleSetting(model=K_CODEX_WORKBENCH_CHAT_MODEL_OPTIONS[0], effort="medium")}
        roles.update(
            {
                role: WorkbenchRoleSetting(model=K_APPLICATION_WORKBENCH_OPENCODE_MODEL_OPTIONS[0], effort="medium")
                for role in K_APPLICATION_WORKBENCH_JOB_BY_ROLE
            }
        )
    return WorkbenchSettings(
        roles=WorkbenchRoles.model_validate(roles),
        second_opinion=SecondOpinionSetting(enabled=False, model=K_APPLICATION_WORKBENCH_DEFAULT_SECOND_OPINION_MODEL),
        defaults=WorkbenchDefaults(review_scope="lesson"),
    )


def read_workbench_role_setting(root: Path, role: WorkbenchRole) -> WorkbenchRoleSetting:
    """Return the configured model and effort for a single workbench role."""
    settings = read_workbench_settings(root).roles
    return {
        "chat": settings.chat,
        "generation": settings.generation,
        "lesson_review": settings.lesson_review,
        "exercise_review": settings.exercise_review,
    }[role]


def read_workbench_codex_settings(root: Path) -> CodexChatSettings:
    """Resolve the active Chat override onto Codex's workspace config for one turn."""
    configured = load_workbench_chat_settings(repo_root=root)
    source = read_workbench_settings_text(root)
    if source is None:
        return configured
    override = WorkbenchSettings.model_validate(parse_workbench_settings(source)).roles.chat
    return configured.model_copy(update={"model": override.model, "effort": override.effort})


def write_workbench_settings(root: Path, settings: WorkbenchSettings) -> WorkbenchSettings:
    """Validate and atomically persist workspace-local Settings page values."""
    validated = WorkbenchSettings.model_validate(settings)
    write_workbench_settings_text(root, render_workbench_settings(validated.model_dump(mode="json")))
    return validated


def restore_default_workbench_settings(root: Path) -> WorkbenchSettings:
    """Remove overrides and return the active config.yaml defaults."""
    reset_workbench_settings(root)
    return default_workbench_settings(root)


def workbench_settings_response(root: Path) -> WorkbenchSettingsResponse:
    """Return settings and the fixed model/effort choices needed to render the page."""
    model_options: dict[WorkbenchRole | Literal["second_opinion"], list[str]] = {
        cast(WorkbenchRole | Literal["second_opinion"], role): list(options)
        for role, options in K_APPLICATION_WORKBENCH_SETTINGS_MODEL_OPTIONS.items()
    }
    effort_options: dict[WorkbenchRole, list[WorkbenchEffort]] = {
        cast(WorkbenchRole, role): cast(list[WorkbenchEffort], list(options))
        for role, options in K_APPLICATION_WORKBENCH_SETTINGS_EFFORT_OPTIONS.items()
    }
    return {
        "settings": cast(WorkbenchSettings, read_workbench_settings(root).model_dump(mode="json")),
        "modelOptions": model_options,
        "effortOptions": effort_options,
        "reviewScopeOptions": ["lesson", "section", "item"],
    }


def workbench_runner_for_role(root: Path, role: OpenCodeWorkbenchRole) -> JobRunner:
    """Build the configured workbench job with the selected role's model and effort."""
    return _build_workbench_runner(root, role, read_workbench_role_setting(root, role))


def workbench_runner_for_review_model(
    root: Path, role: Literal["lesson_review", "exercise_review"], model: str
) -> JobRunner:
    """Build a review job with an alternate model and that review role's effort."""
    setting = read_workbench_role_setting(root, role).model_copy(update={"model": model})
    return _build_workbench_runner(root, role, WorkbenchRoleSetting.model_validate(setting))


def _build_workbench_runner(root: Path, role: OpenCodeWorkbenchRole, setting: WorkbenchRoleSetting) -> JobRunner:
    job_name = K_APPLICATION_WORKBENCH_JOB_BY_ROLE[role]
    configured = load_llm_job(job_name, repo_root=root)
    overridden = _job_with_setting(configured, setting)
    return JobRunner(
        name=overridden.name,
        client=llm_jobs.client_for_job(overridden, repo_root=root),
        model=overridden.tier.model,
        agent=overridden.agent_template,
        variant=overridden.tier.variant,
    )


def _job_with_setting(job: LlmJob, setting: WorkbenchRoleSetting) -> LlmJob:
    return replace(job, tier=replace(job.tier, model=setting.model, variant=setting.effort))


def _configured_effort(value: str) -> WorkbenchEffort:
    if value == "minimal":
        return "minimal"
    if value == "low":
        return "low"
    if value == "high":
        return "high"
    if value == "xhigh":
        return "xhigh"
    return "medium"


__all__ = [
    "SecondOpinionSetting",
    "WorkbenchDefaults",
    "WorkbenchEffort",
    "WorkbenchRole",
    "WorkbenchRoleSetting",
    "WorkbenchRoles",
    "WorkbenchSettings",
    "default_workbench_settings",
    "read_workbench_codex_settings",
    "read_workbench_role_setting",
    "read_workbench_settings",
    "restore_default_workbench_settings",
    "workbench_runner_for_role",
    "workbench_runner_for_review_model",
    "workbench_settings_response",
    "write_workbench_settings",
]
