"""Entry point: add_doctor_commands registers capability diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

if TYPE_CHECKING:
    from lesson_builder.application.operations.check_environment import EnvironmentReport

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root

K_DOCTOR_EXIT_CODES = {"ready": 0, "blocked": 1, "incomplete": 3}


def add_doctor_commands(parent: SubparserRegistrar) -> None:
    """Register the read-only environment doctor."""
    parser = parent.add_parser("doctor", help="Diagnose local capability prerequisites without provider calls")
    parser.add_argument("--capability", choices=("offline", "generation", "audio", "eval", "all"), default="offline")
    parser.add_argument("--workspace-root", default=None)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.set_defaults(func=_doctor)


def _doctor(args: argparse.Namespace) -> int:
    """Run and render one doctor report."""
    root = Path(args.workspace_root) if args.workspace_root else get_workspace_root()
    selected = ("offline", "generation", "audio", "eval") if args.capability == "all" else (args.capability,)
    from lesson_builder.application.operations.check_environment import check_environment

    report = check_environment(root, capabilities=cast(Any, selected))
    print(
        json.dumps(asdict(report), ensure_ascii=False, indent=2)
        if args.format == "json"
        else _render_text_report(report)
    )
    if report.status == "incomplete":
        for finding in report.findings:
            if finding.status == "incomplete":
                print(f"lesson-data doctor: {finding.summary}", file=sys.stderr)
    return K_DOCTOR_EXIT_CODES[report.status]


def _render_text_report(report: EnvironmentReport) -> str:
    """Render one concise human-readable report."""
    lines = [
        f"workspace: {report.workspace_root}",
        f"status: {report.status}",
        "capabilities: " + ", ".join(report.capabilities),
    ]
    for finding in report.findings:
        lines.append(f"- {finding.check_id}: {finding.status} - {finding.summary}")
        if finding.next_action:
            lines.append(f"  next: {finding.next_action}")
    return "\n".join(lines)


__all__ = ["add_doctor_commands"]
