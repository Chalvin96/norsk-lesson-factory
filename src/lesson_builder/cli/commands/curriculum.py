"""Entry point: `add_curriculum_commands` registers curriculum workflows.

Curriculum commands draft incremental changes, catalog-derived plans, and
dependency reviews without coupling them to lesson generation or distribution code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root


def add_curriculum_commands(parent: SubparserRegistrar) -> None:
    """Register curriculum planning and dependency workflows."""
    from lesson_builder.domain.catalog.settings import K_CATALOG_PLANNER_KIND_ORDER

    parser = parent.add_parser("curriculum", help="Draft or commit curriculum artifacts")
    parser.add_argument(
        "text",
        nargs="?",
        help=(
            "'catalog' for a plan from approved YAML, 'dependencies' for a dependency "
            "review, or 'promote-dependencies' for an explicitly approved graph"
        ),
    )
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--from-review", default=None, help="Dependency review input for retry or promotion")
    parser.add_argument("--approval", default=None, help="Human approval ledger for dependency promotion")
    parser.add_argument(
        "--catalog-kind",
        dest="catalog_kinds",
        action="append",
        choices=K_CATALOG_PLANNER_KIND_ORDER,
        default=None,
        help=("include this catalog kind in a `curriculum catalog` plan; repeat for multiple kinds"),
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="mark a `curriculum catalog` plan ready for generation without a second curriculum review; lesson artifact gates remain pending",
    )
    parser.add_argument(
        "--commit", action="store_true", help="write an approved derived plan to committed content/curriculum/plan.yaml"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check whether committed content/curriculum/plan.yaml matches the approved catalog",
    )
    parser.set_defaults(func=_curriculum)


def _curriculum(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()

    if args.text == "catalog":
        from lesson_builder.application.operations.materialize_curriculum import materialize_curriculum_plan
        from lesson_builder.application.operations.materialize_curriculum import validate_committed_curriculum_plan

        if args.check:
            if args.commit or args.auto_approve:
                raise SystemExit("curriculum catalog --check cannot be combined with --commit or --auto-approve")
            plan = validate_committed_curriculum_plan(repo_root=repo_root)
            print(
                json.dumps(
                    {
                        "status": "current",
                        "plan_path": "content/curriculum/plan.yaml",
                        "source_catalog": plan.source_catalog,
                        "source_catalog_hash": plan.source_catalog_hash,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0

        catalog_result = materialize_curriculum_plan(
            repo_root=repo_root,
            run_id=args.run_id,
            included_kinds=args.catalog_kinds,
            auto_approve=args.auto_approve,
            commit=args.commit,
        )
        print(json.dumps(catalog_result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0

    if args.text == "dependencies":
        from lesson_builder.application.operations.design_dependencies import rerun_dependency_second_opinion
        from lesson_builder.application.operations.design_dependencies import run_dependency_design

        if args.from_review:
            dependency_result = rerun_dependency_second_opinion(
                repo_root=repo_root,
                source_review_path=Path(args.from_review),
                run_id=args.run_id or "dependency-second-opinion-retry",
            )
        else:
            dependency_result = run_dependency_design(repo_root=repo_root, run_id=args.run_id or "dependency-review")
        print(json.dumps(dependency_result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0

    if args.text == "promote-dependencies":
        from lesson_builder.application.operations.apply_dependency_review import apply_dependency_review

        if not args.from_review or not args.approval:
            raise SystemExit("promote-dependencies requires --from-review and --approval")
        promotion_result = apply_dependency_review(
            repo_root=repo_root,
            review_path=Path(args.from_review),
            approval_path=Path(args.approval),
        )
        print(json.dumps(promotion_result.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0

    raise SystemExit("curriculum requires one of: catalog, dependencies, promote-dependencies")


__all__ = ["add_curriculum_commands"]
