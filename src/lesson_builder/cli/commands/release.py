"""Entry point: `add_release_commands` registers distribution commands.

Distribution commands assemble and validate the derived public projection. Archive
packaging and external publication are registered by the publication command
module; neither owns lesson authoring or catalog decisions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import cast

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root


def add_release_commands(parent: SubparserRegistrar) -> None:
    """Register the release flow and distribution export/validation commands."""
    _add_release_parser(parent)
    _add_release_verify_parser(parent)
    _add_regenerate_dist_parser(parent)
    _add_export_distribution_parser(parent)
    _add_validate_distribution_parser(parent)


def _add_release_parser(parent: SubparserRegistrar) -> None:
    """Register the simple plan-then-approve release flow."""
    parser = parent.add_parser(
        "release",
        help="Plan an audio-complete release; pass --approve to synthesize and publish it",
    )
    parser.add_argument("--approve", action="store_true", help="resume the plan and perform paid/external actions")
    parser.add_argument("--tag", default=None, help="release tag (defaults to today's UTC vYYYY.MM.DD tag)")
    parser.add_argument("--github-repository", default=None)
    parser.add_argument("--service-account", default=None, help="Google service-account JSON path")
    parser.add_argument(
        "--max-cost", type=float, default=None, help="fail approval when estimated TTS cost exceeds this"
    )
    parser.add_argument("--format", choices=("text", "json"), default="text", help="bounded text or full JSON output")
    parser.add_argument("--repo-root", default=None)
    parser.set_defaults(func=_run_release)


def _add_release_verify_parser(parent: SubparserRegistrar) -> None:
    """Register the provider-free published-release verification for tag CI."""
    parser = parent.add_parser(
        "release-verify",
        help="Verify a published GitHub release archive against canonical source without synthesizing",
    )
    parser.add_argument("--tag", required=True, help="release tag whose published archive is verified")
    parser.add_argument("--github-repository", default=None)
    parser.add_argument("--format", choices=("text", "json"), default="text", help="bounded text or full JSON output")
    parser.add_argument("--repo-root", default=None)
    parser.set_defaults(func=_run_release_verify)


def _add_regenerate_dist_parser(parent: SubparserRegistrar) -> None:
    """Register the provider-free source-to-export projection."""
    parser = parent.add_parser(
        "regenerate-dist",
        help="Build the provider-free JSON projection from content/lessons/* Markdown/YAML",
    )
    parser.add_argument("--repo-root", default=None)
    parser.set_defaults(func=_regenerate_dist)


def _add_export_distribution_parser(parent: SubparserRegistrar) -> None:
    parser = parent.add_parser("export", help="Export the complete approved curriculum and synthesize lesson audio")
    parser.add_argument(
        "--output-root", default=None, help="distribution output root (defaults to store/scratch/audio-distribution)"
    )
    parser.add_argument("--service-account", default=None, help="Google service-account JSON path")
    parser.add_argument(
        "--audio-public-base-url",
        default=None,
        help="override LESSON_AUDIO_PUBLIC_BASE_URL for this export",
    )
    parser.add_argument(
        "--workers", type=int, default=1, help="bounded number of lessons synthesized concurrently (default: 1)"
    )
    parser.add_argument("--repo-root", default=None)
    parser.set_defaults(func=_export_distribution)


def _add_validate_distribution_parser(parent: SubparserRegistrar) -> None:
    parser = parent.add_parser(
        "validate-distribution", help="Validate the complete distribution against canonical lesson source"
    )
    parser.add_argument("--repo-root", default=None)
    parser.add_argument(
        "--distribution-root", default=None, help="distribution root containing dist/ (defaults to repo root)"
    )
    parser.set_defaults(func=_validate_distribution)


def _export_distribution(args: argparse.Namespace) -> int:
    from lesson_builder.application.operations.export_distribution import export_distribution
    from lesson_builder.application.operations.synthesize_audio import load_audio_settings

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    output_root = Path(args.output_root) if args.output_root else repo_root / "store" / "scratch" / "audio-distribution"
    settings = load_audio_settings(
        repo_root=repo_root,
        service_account_path=Path(args.service_account) if args.service_account else None,
    )
    result = export_distribution(
        repo_root,
        output_root=output_root,
        audio_settings=settings,
        audio_workers=args.workers,
        audio_public_base_url=args.audio_public_base_url,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _regenerate_dist(args: argparse.Namespace) -> int:
    """Compile canonical Markdown/YAML source into the derived JSON distribution."""
    from lesson_builder.application.operations.export_distribution import export_distribution

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    result = export_distribution(repo_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _run_release(args: argparse.Namespace) -> int:
    """Plan or execute one release without interactive prompts."""
    from lesson_builder.application.operations.release import run_release

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    result = run_release(
        repo_root=repo_root,
        approve=args.approve,
        tag=args.tag,
        github_repository=args.github_repository,
        service_account_path=Path(args.service_account) if args.service_account else None,
        max_cost=args.max_cost,
    )
    print(_render_release_result(result, machine=args.format == "json"))
    return 0


def _run_release_verify(args: argparse.Namespace) -> int:
    """Verify one published release against canonical source."""
    from lesson_builder.application.operations.release import verify_release

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    result = verify_release(
        repo_root=repo_root,
        tag=args.tag,
        github_repository=args.github_repository,
    )
    print(_render_verify_result(result, machine=args.format == "json"))
    return 0


def _render_release_result(result: dict[str, object], *, machine: bool) -> str:
    """Render one release result as full JSON or a bounded human summary."""
    if machine:
        return json.dumps(result, indent=2, ensure_ascii=False, default=str)
    if result.get("approved") is True:
        plan = cast(dict[str, object], result.get("plan", {}))
        return "\n".join(
            (
                f"approved {result.get('tag')} for {result.get('repository')}",
                _render_plan_counts(plan) + f" · cost: ${_format_money(plan.get('estimated_cost_usd'))}",
                "published: " + _render_published_destinations(result),
                f"plan: {plan.get('plan_path')}",
            )
        )
    return "\n".join(
        (
            _render_plan_counts(result),
            f"reused: {result.get('reused_count', 0)} · missing: {result.get('missing_count', 0)} · "
            f"estimated synthesis: ${_format_money(result.get('estimated_cost_usd'))}",
            f"plan: {result.get('plan_path')}",
            f"next: {result.get('next_command')}",
        )
    )


def _render_verify_result(result: dict[str, object], *, machine: bool) -> str:
    """Render one verification result as full JSON or a bounded human summary."""
    if machine:
        return json.dumps(result, indent=2, ensure_ascii=False, default=str)
    return "\n".join(
        (
            f"verified {result.get('tag')} in {result.get('repository')}",
            f"{result.get('lesson_count', 0)} lessons · {result.get('audio_reference_count', 0)} audio references",
            f"archive: {result.get('asset_name')} (sha256 {result.get('archive_sha256')})",
        )
    )


def _render_plan_counts(plan: dict[str, object]) -> str:
    """Render the bounded lesson and recording counts for one plan summary."""
    return f"{plan.get('lesson_count', 0)} lessons · {plan.get('unique_count', 0)} recordings"


def _render_published_destinations(result: dict[str, object]) -> str:
    """Render the publication destinations recorded in one approval result."""
    publication = result.get("publication")
    published = publication.get("published") if isinstance(publication, dict) else None
    if not isinstance(published, list):
        return ""
    return ", ".join(item for item in published if isinstance(item, str))


def _format_money(value: object) -> str:
    """Render one cost value with two decimals."""
    return f"{float(value if isinstance(value, (int, float)) else 0):.2f}"


def _validate_distribution(args: argparse.Namespace) -> int:
    from lesson_builder.application.operations.validate_distribution import validate_committed_distribution

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    distribution_root = Path(args.distribution_root) if args.distribution_root else repo_root
    result = validate_committed_distribution(repo_root, distribution_root=distribution_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


__all__ = ["add_release_commands"]
