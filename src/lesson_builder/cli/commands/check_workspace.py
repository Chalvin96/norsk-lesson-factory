"""Entry point: `add_check_workspace_commands` registers the workspace check.

The command only routes to the read-only workspace aggregate and renders its
report; it owns no validation logic itself. JSON is the default output because
automation agents drive this CLI: exactly one schema-versioned document goes
to stdout, expected invalid outcomes stay silent on stderr, and unexpected
diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lesson_builder.application.operations.check_lesson_data import WorkspaceCheckReport

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root

K_CHECK_EXIT_CODES = {"valid": 0, "invalid": 1, "incomplete": 3}


def add_check_workspace_commands(parent: SubparserRegistrar) -> None:
    """Register the read-only agent-facing workspace check command."""
    parser = parent.add_parser(
        "check",
        help="Check committed content, curriculum plan, and distribution; print an agent-facing report",
    )
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    parser.set_defaults(func=_check_workspace)


def _check_workspace(args: argparse.Namespace) -> int:
    from lesson_builder.application.operations.check_lesson_data import check_workspace

    root = Path(args.workspace_root) if args.workspace_root else get_workspace_root()
    report = check_workspace(root)
    print(_render_json(report) if args.format == "json" else _render_text(report))
    _report_error_diagnostics(report)
    return K_CHECK_EXIT_CODES[report.status]


def _report_error_diagnostics(report: WorkspaceCheckReport) -> None:
    """Send unexpected-failure diagnostics to stderr without touching stdout."""
    if report.status != "incomplete":
        return
    for result in report.checks:
        for issue in result.issues:
            if issue.error_type is not None:
                print(f"lesson-data check: {issue.message}", file=sys.stderr)


def _render_json(report: WorkspaceCheckReport) -> str:
    """Serialize the report as one deterministic JSON document."""
    return json.dumps(asdict(report), ensure_ascii=False, indent=2)


def _render_text(report: WorkspaceCheckReport) -> str:
    """Render the same report for a human reader."""
    counts = report.counts
    lines = [
        f"workspace: {report.workspace_root}",
        f"status: {report.status} "
        f"(passed={counts.passed}, failed={counts.failed}, skipped={counts.skipped}, error={counts.error})",
    ]
    for result in report.checks:
        lines.append("")
        lines.append(f"{result.check_id}: {result.status} - {result.summary}")
        if result.blocked_by:
            lines.append("  blocked by: " + ", ".join(result.blocked_by))
        for issue in result.issues:
            lines.append(f"  issue [{issue.code}] paths: " + (", ".join(issue.paths) or "none"))
            lines.append(f"    {issue.message}")
            if issue.error_type is not None:
                lines.append(f"    error type: {issue.error_type}")
            remedy = issue.remedy
            lines.append(f"    remedy: {remedy.action} - {remedy.description}")
            if remedy.requires_human_decision:
                lines.append("    this remedy requires a human decision")
            if remedy.command is not None:
                lines.append("    command: " + " ".join(remedy.command))
    return "\n".join(lines)


__all__ = ["add_check_workspace_commands"]
