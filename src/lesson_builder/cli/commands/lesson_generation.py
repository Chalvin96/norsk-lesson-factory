"""Entry point: `add_lesson_generation_commands` registers lesson lifecycle commands.

Generation, promotion, deterministic replay, and resume share one command
family while their domain implementations stay in
``lesson_builder.workflow.lesson_generation``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root


def add_lesson_generation_commands(parent: SubparserRegistrar) -> None:
    """Register lesson generation, promotion, replay, and resume commands."""
    _add_lesson_generation_parser(parent)
    _add_lesson_promotion_parser(parent)
    _add_lesson_recompile_parser(parent)
    _add_lesson_resume_parser(parent)


def _add_lesson_generation_parser(parent: SubparserRegistrar) -> None:
    p = parent.add_parser(
        "generate-lessons",
        help="Generate one scratch lesson package per approved catalog-plan slot",
    )
    p.add_argument("--plan", required=True, help="approved curriculum plan (normally content/curriculum/plan.yaml)")
    p.add_argument("--batch-id", required=True)
    p.add_argument("--job", default="author", help="configured LLM job used for authoring")
    p.add_argument("--workers", type=int, default=6)
    p.add_argument(
        "--catalog-id",
        action="append",
        default=None,
        help="optionally retry only these catalog IDs; repeat the option for multiple IDs",
    )
    p.add_argument("--repo-root", default=None)
    p.set_defaults(func=_generate_lessons)


def _generate_lessons(args: argparse.Namespace) -> int:
    from lesson_builder.workflow.lesson_generation import generate_lessons

    result = generate_lessons(
        repo_root=Path(args.repo_root) if args.repo_root else get_workspace_root(),
        plan_path=Path(args.plan),
        batch_id=args.batch_id,
        job=args.job,
        max_workers=args.workers,
        catalog_ids=args.catalog_id,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _add_lesson_promotion_parser(parent: SubparserRegistrar) -> None:
    p = parent.add_parser(
        "promote-lessons",
        help="Promote parked lesson packages into tracked Markdown/YAML source and JSON exports",
    )
    p.add_argument("--source-batch", required=False, help="completed batch.yaml checkpoint")
    p.add_argument("--approval", default=None, help="immutable lesson approval to resume promotion")
    p.add_argument(
        "--auto-approve",
        action="store_true",
        help="explicitly approve every selected parked package for canonical promotion",
    )
    p.add_argument(
        "--catalog-id", action="append", default=None, help="promote only this catalog ID; repeat for multiple IDs"
    )
    p.add_argument(
        "--replace",
        action="store_true",
        help="replace changed canonical packages (identical packages are always idempotent)",
    )
    p.add_argument("--repo-root", default=None)
    p.set_defaults(func=_promote_lessons)


def _promote_lessons(args: argparse.Namespace) -> int:
    from lesson_builder.workflow.lesson_generation import promote_lesson_approval
    from lesson_builder.workflow.lesson_generation import promote_lesson_batch

    try:
        root = Path(args.repo_root) if args.repo_root else get_workspace_root()
        if args.approval:
            digest = promote_lesson_approval(repo_root=root, approval_path=Path(args.approval))
            print(
                json.dumps(
                    {"status": "promoted", "source_sha256": digest, "approval_path": args.approval},
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        else:
            if not args.source_batch:
                raise ValueError("promote-lessons requires --source-batch or --approval")
            result = promote_lesson_batch(
                repo_root=root,
                source_batch_path=Path(args.source_batch),
                auto_approve=args.auto_approve,
                catalog_ids=args.catalog_id,
                replace=args.replace,
            )
    except ValueError as exc:
        print(f"error: {exc}")
        partial = getattr(exc, "result", None)
        if partial is not None:
            print(json.dumps(partial.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 1
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _add_lesson_recompile_parser(parent: SubparserRegistrar) -> None:
    p = parent.add_parser(
        "recompile-lessons",
        help="Recompile existing scratch lesson packages without new model calls",
    )
    p.add_argument("--plan", required=True, help="approved curriculum plan (normally content/curriculum/plan.yaml)")
    p.add_argument("--source-batch", required=True, help="prior batch.yaml to replay")
    p.add_argument("--batch-id", required=True, help="new scratch batch identifier")
    p.add_argument(
        "--catalog-id",
        action="append",
        default=None,
        help="optionally replay only these catalog IDs; repeat the option for multiple IDs",
    )
    p.add_argument("--repo-root", default=None)
    p.set_defaults(func=_recompile_lessons)


def _recompile_lessons(args: argparse.Namespace) -> int:
    from lesson_builder.workflow.lesson_generation import recompile_lessons

    result = recompile_lessons(
        repo_root=Path(args.repo_root) if args.repo_root else get_workspace_root(),
        plan_path=Path(args.plan),
        source_batch_path=Path(args.source_batch),
        batch_id=args.batch_id,
        catalog_ids=args.catalog_id,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _add_lesson_resume_parser(parent: SubparserRegistrar) -> None:
    p = parent.add_parser("resume-lessons", help="Resume a scratch lesson batch from its checkpoint")
    p.add_argument("--source-batch", required=True, help="batch.yaml checkpoint to resume")
    p.add_argument("--batch-id", required=True, help="new scratch batch identifier")
    p.add_argument("--plan", default=None, help="optional current plan.yaml override")
    p.add_argument("--job", default=None, help="optional configured LLM job override")
    p.add_argument("--workers", type=int, default=None)
    p.add_argument(
        "--catalog-id",
        action="append",
        default=None,
        help="optionally resume only these catalog IDs; repeat the option",
    )
    p.add_argument("--repo-root", default=None)
    p.set_defaults(func=_resume_lessons)


def _resume_lessons(args: argparse.Namespace) -> int:
    from lesson_builder.workflow.lesson_generation import resume_lessons

    result = resume_lessons(
        repo_root=Path(args.repo_root) if args.repo_root else get_workspace_root(),
        source_batch_path=Path(args.source_batch),
        batch_id=args.batch_id,
        plan_path=Path(args.plan) if args.plan else None,
        job=args.job,
        max_workers=args.workers,
        catalog_ids=args.catalog_id,
    )
    print(json.dumps(result.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


__all__ = ["add_lesson_generation_commands"]
