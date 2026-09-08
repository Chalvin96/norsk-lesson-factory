"""Entry point: `check_workspace` reports the committed workspace state for agents.

The module is a read-only application aggregate over the public domain
validators: it runs the authoring character and terminology registries, the
committed curriculum plan, and the committed distribution in one fixed phase order
and converts their outcomes into a schema-versioned, agent-facing report. It
owns no domain rules, reads no scratch or checkpoint state, calls no providers,
and never mutates files. Workflows must not import it; a stale committed
distribution must not block an unrelated generation run.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from lesson_builder.application.operations.load_characters import load_character_registry
from lesson_builder.application.operations.load_terminology import load_terminology_registry
from lesson_builder.application.operations.materialize_curriculum import validate_committed_curriculum_plan
from lesson_builder.application.operations.validate_distribution import validate_committed_distribution
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.settings import K_WORKSPACE_LEGACY_ROOT_DIRS

CheckId = Literal[
    "content_layout",
    "character_registry",
    "terminology_registry",
    "curriculum_plan",
    "distribution",
]
CheckStatus = Literal["passed", "failed", "skipped", "error"]
ReportStatus = Literal["valid", "invalid", "incomplete"]
RemedyAction = Literal[
    "restore_canonical_path",
    "edit_authored_data",
    "run_command",
    "request_human_decision",
    "inspect_environment",
]
IssueCode = Literal[
    "workspace.layout_invalid",
    "authoring.character_registry_invalid",
    "authoring.terminology_registry_invalid",
    "curriculum.plan_invalid",
    "distribution.artifacts_invalid",
    "workspace.check_error",
]


@dataclass(frozen=True)
class WorkspaceRemedy:
    """One safe next action an agent may take for an issue."""

    action: RemedyAction
    description: str
    command: tuple[str, ...] | None
    requires_human_decision: bool


@dataclass(frozen=True)
class WorkspaceIssue:
    """One committed-state problem with the paths it involves."""

    code: IssueCode
    message: str
    paths: tuple[str, ...]
    error_type: str | None
    remedy: WorkspaceRemedy


@dataclass(frozen=True)
class WorkspaceCheckResult:
    """The outcome of one fixed workspace check phase."""

    check_id: CheckId
    status: CheckStatus
    summary: str
    issues: tuple[WorkspaceIssue, ...]
    blocked_by: tuple[CheckId, ...]


@dataclass(frozen=True)
class WorkspaceCheckCounts:
    """Per-status check totals for one report."""

    passed: int
    failed: int
    skipped: int
    error: int


@dataclass(frozen=True)
class WorkspaceCheckReport:
    """The complete read-only workspace verdict for an automation agent."""

    schema_version: Literal[1]
    workspace_root: str
    status: ReportStatus
    read_only: Literal[True]
    checks: tuple[WorkspaceCheckResult, ...]
    counts: WorkspaceCheckCounts


def check_workspace(root: Path) -> WorkspaceCheckReport:
    """Compose every committed-state check into one agent-facing report."""
    resolved = Path(root).resolve()
    paths = WorkspacePaths(resolved)
    content_usable = paths.content_root.is_dir()
    lessons_usable = paths.lessons_root.is_dir()
    content_block: tuple[CheckId, ...] = () if content_usable else ("content_layout",)
    layout = _check_content_layout(paths)
    character = _check_character_registry(resolved, paths, blocked_by=content_block)
    terminology = _check_terminology_registry(resolved, paths, blocked_by=content_block)
    curriculum = _check_curriculum_plan(resolved, paths, blocked_by=content_block)
    distribution = _check_distribution(
        resolved,
        paths,
        blocked_by=_distribution_blockers(
            content_usable=content_usable,
            lessons_usable=lessons_usable,
            character=character,
            curriculum=curriculum,
        ),
    )
    checks = (layout, character, terminology, curriculum, distribution)
    return WorkspaceCheckReport(
        schema_version=1,
        workspace_root=str(resolved),
        status=_report_status(checks),
        read_only=True,
        checks=checks,
        counts=WorkspaceCheckCounts(
            passed=sum(result.status == "passed" for result in checks),
            failed=sum(result.status == "failed" for result in checks),
            skipped=sum(result.status == "skipped" for result in checks),
            error=sum(result.status == "error" for result in checks),
        ),
    )


def _check_content_layout(paths: WorkspacePaths) -> WorkspaceCheckResult:
    """Require canonical committed inputs and reject legacy root paths."""
    required_files = (
        paths.character_registry,
        paths.catalog_file,
        paths.catalog_registry,
        paths.curriculum_plan,
    )
    missing: list[str] = [_workspace_relative(paths, path) for path in required_files if not path.is_file()]
    if not paths.lessons_root.is_dir():
        missing.append(_workspace_relative(paths, paths.lessons_root))
    legacy = tuple(name for name in K_WORKSPACE_LEGACY_ROOT_DIRS if (paths.root / name).exists())
    issues: list[WorkspaceIssue] = []
    if missing:
        issues.append(
            WorkspaceIssue(
                code="workspace.layout_invalid",
                message="canonical workspace paths are missing: " + ", ".join(missing),
                paths=tuple(missing),
                error_type=None,
                remedy=_restore_canonical_path_remedy(
                    "restore each missing canonical path from version control or regenerate it"
                ),
            )
        )
    if legacy:
        issues.append(
            WorkspaceIssue(
                code="workspace.layout_invalid",
                message="legacy root paths remain outside content/: " + ", ".join(legacy),
                paths=legacy,
                error_type=None,
                remedy=_restore_canonical_path_remedy(
                    "move each legacy root under content/ or remove it once it holds no untracked authoring data",
                    requires_human_decision=True,
                ),
            )
        )
    if issues:
        return WorkspaceCheckResult(
            check_id="content_layout",
            status="failed",
            summary="canonical content layout is invalid",
            issues=tuple(issues),
            blocked_by=(),
        )
    return WorkspaceCheckResult(
        check_id="content_layout",
        status="passed",
        summary="canonical content layout present with no legacy root paths",
        issues=(),
        blocked_by=(),
    )


def _check_character_registry(
    root: Path,
    paths: WorkspacePaths,
    *,
    blocked_by: tuple[CheckId, ...],
) -> WorkspaceCheckResult:
    """Validate the committed recurring-character registry."""
    if blocked_by:
        return _skipped_result("character_registry", blocked_by)
    registry_paths = (_workspace_relative(paths, paths.character_registry),)
    try:
        registry = load_character_registry(root)
    except (FileNotFoundError, TypeError, ValueError, yaml.YAMLError) as exc:
        return _failed_result(
            "character_registry",
            summary="character registry is invalid",
            code="authoring.character_registry_invalid",
            message=str(exc),
            paths=registry_paths,
            remedy=_edit_authored_data_remedy(
                "fix content/authoring/characters.yaml until the character registry validator passes"
            ),
        )
    except Exception as exc:
        return _error_result("character_registry", paths=registry_paths, exc=exc)
    return WorkspaceCheckResult(
        check_id="character_registry",
        status="passed",
        summary=f"character registry valid ({len(registry.characters)} characters)",
        issues=(),
        blocked_by=(),
    )


def _check_terminology_registry(
    root: Path,
    paths: WorkspacePaths,
    *,
    blocked_by: tuple[CheckId, ...],
) -> WorkspaceCheckResult:
    """Validate the committed terminology glossary registry."""
    if blocked_by:
        return _skipped_result("terminology_registry", blocked_by)
    registry_paths = (_workspace_relative(paths, paths.terminology_registry),)
    try:
        registry = load_terminology_registry(paths.terminology_registry, use_cache=False)
    except (FileNotFoundError, TypeError, ValueError, yaml.YAMLError) as exc:
        return _failed_result(
            "terminology_registry",
            summary="terminology registry is invalid",
            code="authoring.terminology_registry_invalid",
            message=str(exc),
            paths=registry_paths,
            remedy=_edit_authored_data_remedy(
                "fix content/terminology/glossary.yaml until the terminology registry validator passes"
            ),
        )
    except Exception as exc:
        return _error_result("terminology_registry", paths=registry_paths, exc=exc)
    return WorkspaceCheckResult(
        check_id="terminology_registry",
        status="passed",
        summary=f"terminology registry valid ({len(registry.concepts)} concepts)",
        issues=(),
        blocked_by=(),
    )


def _check_curriculum_plan(
    root: Path,
    paths: WorkspacePaths,
    *,
    blocked_by: tuple[CheckId, ...],
) -> WorkspaceCheckResult:
    """Validate the committed plan against the approved catalog."""
    if blocked_by:
        return _skipped_result("curriculum_plan", blocked_by)
    plan_paths = (
        _workspace_relative(paths, paths.curriculum_plan),
        _workspace_relative(paths, paths.catalog_file),
    )
    try:
        plan = validate_committed_curriculum_plan(repo_root=root)
    except (FileNotFoundError, TypeError, ValueError, yaml.YAMLError) as exc:
        return _failed_result(
            "curriculum_plan",
            summary="committed curriculum plan is invalid or stale",
            code="curriculum.plan_invalid",
            message=str(exc),
            paths=plan_paths,
            remedy=_request_human_decision_remedy(
                "ask a human to review the approved catalog change; after approval, run "
                "lesson-data curriculum catalog --repo-root <workspace_root> --auto-approve --commit"
            ),
        )
    except Exception as exc:
        return _error_result("curriculum_plan", paths=plan_paths, exc=exc)
    return WorkspaceCheckResult(
        check_id="curriculum_plan",
        status="passed",
        summary=f"committed curriculum plan matches the approved catalog ({len(plan.slots)} slots)",
        issues=(),
        blocked_by=(),
    )


def _check_distribution(
    root: Path,
    paths: WorkspacePaths,
    *,
    blocked_by: tuple[CheckId, ...],
) -> WorkspaceCheckResult:
    """Validate the committed distribution against canonical lesson source."""
    if blocked_by:
        return _skipped_result("distribution", blocked_by)
    distribution_paths = (_workspace_relative(paths, paths.dist_root),)
    try:
        summary = validate_committed_distribution(root)
    except (FileNotFoundError, TypeError, ValueError, yaml.YAMLError) as exc:
        return _failed_result(
            "distribution",
            summary="committed distribution is invalid or stale",
            code="distribution.artifacts_invalid",
            message=str(exc),
            paths=distribution_paths,
            remedy=_run_command_remedy(
                "regenerate the committed distribution from the canonical lesson source",
                ("lesson-data", "regenerate-dist", "--repo-root", str(root)),
            ),
        )
    except Exception as exc:
        return _error_result("distribution", paths=distribution_paths, exc=exc)
    return WorkspaceCheckResult(
        check_id="distribution",
        status="passed",
        summary=f"committed distribution complete and current ({summary['lesson_count']} lessons)",
        issues=(),
        blocked_by=(),
    )


def _distribution_blockers(
    *,
    content_usable: bool,
    lessons_usable: bool,
    character: WorkspaceCheckResult,
    curriculum: WorkspaceCheckResult,
) -> tuple[CheckId, ...]:
    """List the checks that genuinely block the distribution phase."""
    blockers: list[CheckId] = []
    if not content_usable or not lessons_usable:
        blockers.append("content_layout")
    if character.status != "passed":
        blockers.append("character_registry")
    if curriculum.status != "passed":
        blockers.append("curriculum_plan")
    return tuple(blockers)


def _report_status(checks: tuple[WorkspaceCheckResult, ...]) -> ReportStatus:
    """Reduce check statuses to the overall report verdict."""
    if any(result.status == "error" for result in checks):
        return "incomplete"
    if any(result.status == "failed" for result in checks):
        return "invalid"
    return "valid"


def _skipped_result(check_id: CheckId, blocked_by: tuple[CheckId, ...]) -> WorkspaceCheckResult:
    """Build the non-verdict result for one genuinely blocked phase."""
    return WorkspaceCheckResult(
        check_id=check_id,
        status="skipped",
        summary="skipped; resolve blocked checks first: " + ", ".join(blocked_by),
        issues=(),
        blocked_by=blocked_by,
    )


def _failed_result(
    check_id: CheckId,
    *,
    summary: str,
    code: IssueCode,
    message: str,
    paths: tuple[str, ...],
    remedy: WorkspaceRemedy,
) -> WorkspaceCheckResult:
    """Build the actionable failure result for one phase."""
    return WorkspaceCheckResult(
        check_id=check_id,
        status="failed",
        summary=summary,
        issues=(
            WorkspaceIssue(
                code=code,
                message=message,
                paths=paths,
                error_type=None,
                remedy=remedy,
            ),
        ),
        blocked_by=(),
    )


def _error_result(
    check_id: CheckId,
    *,
    paths: tuple[str, ...],
    exc: Exception,
) -> WorkspaceCheckResult:
    """Build the inability-to-judge result for one unexpected failure."""
    error_type = type(exc).__name__
    return WorkspaceCheckResult(
        check_id=check_id,
        status="error",
        summary=f"could not judge: unexpected {error_type}",
        issues=(
            WorkspaceIssue(
                code="workspace.check_error",
                message=f"unexpected {error_type}: {exc}",
                paths=paths,
                error_type=error_type,
                remedy=WorkspaceRemedy(
                    action="inspect_environment",
                    description="diagnose the environment or code failure before editing any lesson content",
                    command=None,
                    requires_human_decision=False,
                ),
            ),
        ),
        blocked_by=(),
    )


def _restore_canonical_path_remedy(description: str, *, requires_human_decision: bool = True) -> WorkspaceRemedy:
    """Build the remedy for canonical layout problems."""
    return WorkspaceRemedy(
        action="restore_canonical_path",
        description=description,
        command=None,
        requires_human_decision=requires_human_decision,
    )


def _edit_authored_data_remedy(description: str) -> WorkspaceRemedy:
    """Build the remedy for authored-data failures."""
    return WorkspaceRemedy(
        action="edit_authored_data",
        description=description,
        command=None,
        requires_human_decision=True,
    )


def _request_human_decision_remedy(description: str) -> WorkspaceRemedy:
    """Build the remedy for changes that require explicit authoring approval."""
    return WorkspaceRemedy(
        action="request_human_decision",
        description=description,
        command=None,
        requires_human_decision=True,
    )


def _run_command_remedy(description: str, command: tuple[str, ...]) -> WorkspaceRemedy:
    """Build the guided-command remedy for derived-artifact staleness."""
    return WorkspaceRemedy(
        action="run_command",
        description=description,
        command=command,
        requires_human_decision=False,
    )


def _workspace_relative(paths: WorkspacePaths, path: Path) -> str:
    """Return one workspace-relative POSIX path for report issues."""
    return path.relative_to(paths.root).as_posix()


__all__ = [
    # Report contract.
    "CheckId",
    "CheckStatus",
    "IssueCode",
    "RemedyAction",
    "ReportStatus",
    "WorkspaceCheckCounts",
    "WorkspaceCheckReport",
    "WorkspaceCheckResult",
    "WorkspaceIssue",
    "WorkspaceRemedy",
    # Application entry point.
    "check_workspace",
]
