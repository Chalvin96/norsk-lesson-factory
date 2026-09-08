"""Entry point: `add_publication_commands` registers archive publication commands.

This command family packages a complete validated distribution and publishes an
existing archive. Distribution assembly and validation remain in the distribution
operation; external release publication remains here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import get_workspace_root


def add_publication_commands(parent: SubparserRegistrar) -> None:
    """Register distribution packaging and external release publication commands."""
    package = parent.add_parser("package", help="Package one complete validated distribution")
    package.add_argument("--repo-root", default=None)
    package.add_argument("--distribution-root", required=True)
    package.add_argument("--output", required=True)
    package.set_defaults(func=_package_distribution)

    publish = parent.add_parser("publish", help="Publish one existing release archive")
    publish.add_argument("archive")
    publish.add_argument("--distribution-root", required=True)
    publish.add_argument("--s3", action="store_true", help="upload missing audio using .env S3 settings")
    publish.add_argument("--env-file", default=None, help="dotenv file (defaults to <repo-root>/.env)")
    publish.add_argument("--repo-root", default=None)
    publish.add_argument("--github-repository", default=None)
    publish.add_argument("--github-tag", default=None)
    publish.set_defaults(func=_publish_release)


def _package_distribution(args: argparse.Namespace) -> int:
    from lesson_builder.application.operations.package_distribution import package_distribution

    result = package_distribution(
        repo_root=Path(args.repo_root) if args.repo_root else get_workspace_root(),
        distribution_root=Path(args.distribution_root),
        output_path=Path(args.output),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _publish_release(args: argparse.Namespace) -> int:
    from lesson_builder.application.operations.publish_release import publish_release

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    result = publish_release(
        archive_path=Path(args.archive),
        repo_root=repo_root,
        distribution_root=Path(args.distribution_root),
        upload_s3=args.s3,
        env_path=Path(args.env_file) if args.env_file else repo_root / ".env",
        github_repository=args.github_repository,
        github_tag=args.github_tag,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


__all__ = ["add_publication_commands"]
