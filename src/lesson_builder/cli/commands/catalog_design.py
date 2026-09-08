"""Entry point: `add_catalog_design_commands` registers the catalog proposal command.

Catalog design discovers, reconciles, and evaluates candidates for one
category while the domain implementation stays in
``lesson_builder.workflow.catalog_design``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root


def add_catalog_design_commands(parent: SubparserRegistrar) -> None:
    """Register the catalog design proposal command."""
    parser = parent.add_parser(
        "catalog",
        help="Design a catalog proposal: discover, reconcile, and evaluate candidates for one category",
    )
    parser.add_argument("category", help="human-governed catalog category to fill")
    parser.add_argument("--guidance", default="", help="optional inclusion/exclusion guidance for this category")
    parser.add_argument(
        "--cefr",
        nargs="+",
        choices=["A1", "A2", "B1", "B2", "C1", "C2"],
        default=[],
        help="optional CEFR metadata tags; tags do not define catalog categories",
    )
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.set_defaults(func=_catalog)


def _catalog(args: argparse.Namespace) -> int:
    from lesson_builder.workflow.catalog_design.runner import run_catalog

    result = run_catalog(
        args.category,
        category_guidance=args.guidance,
        cefr_tags=args.cefr,
        max_iterations=args.max_iterations,
        repo_root=Path(args.repo_root) if args.repo_root else get_workspace_root(),
        run_id=args.run_id,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


__all__ = ["add_catalog_design_commands"]
