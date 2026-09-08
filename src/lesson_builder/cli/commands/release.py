"""Entry point: `add_release_commands` registers distribution commands.

Distribution commands assemble and validate the derived public projection. Archive
packaging and external publication are registered by the publication command
module; neither owns lesson authoring or catalog decisions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root


def add_release_commands(parent: SubparserRegistrar) -> None:
    """Register distribution export and validation commands."""
    _add_regenerate_dist_parser(parent)
    _add_export_distribution_parser(parent)
    _add_validate_distribution_parser(parent)


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


def _validate_distribution(args: argparse.Namespace) -> int:
    from lesson_builder.application.operations.validate_distribution import validate_committed_distribution

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    distribution_root = Path(args.distribution_root) if args.distribution_root else repo_root
    result = validate_committed_distribution(repo_root, distribution_root=distribution_root)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


__all__ = ["add_release_commands"]
