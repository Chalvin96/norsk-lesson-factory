"""Entry point: `add_terminology_commands` registers terminology tools.

These deterministic maintenance commands operate on the authored glossary and
derived lesson exports; they do not perform model calls.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lesson_builder.cli.commands.registration import SubparserRegistrar
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.paths import get_workspace_root


def add_terminology_commands(parent: SubparserRegistrar) -> None:
    term_parser = parent.add_parser("terminology", help="Terminology registry audit and maintenance")
    term_sub = term_parser.add_subparsers(dest="terminology_command", required=True)

    audit_parser = term_sub.add_parser("audit", help="Scan lesson JSON files for banned phrases and prose tells")
    audit_parser.add_argument(
        "paths", nargs="*", help="lesson JSON files to scan (defaults to all dist/lessons/*.json)"
    )
    audit_parser.add_argument("--repo-root", default=None)
    audit_parser.add_argument(
        "--style-guide",
        default=None,
        help="path to glossary.yaml or terminology-style-guide.md (defaults to content/terminology/glossary.yaml)",
    )
    audit_parser.add_argument(
        "--summary", action="store_true", help="summarize counts by phrase instead of emitting per-finding rows"
    )
    audit_parser.set_defaults(func=_audit)

    list_parser = term_sub.add_parser("list", help="List concepts, hard bans, and prose tells from the YAML registry")
    list_parser.add_argument("--repo-root", default=None)
    list_parser.add_argument("--style-guide", default=None)
    list_parser.set_defaults(func=_list)

    ban_parser = term_sub.add_parser("ban", help="Add a deterministic hard-ban phrase (writes the YAML registry)")
    ban_parser.add_argument("phrase")
    ban_parser.add_argument("--repo-root", default=None)
    ban_parser.add_argument("--style-guide", default=None)
    ban_parser.set_defaults(func=_ban)

    unban_parser = term_sub.add_parser(
        "unban", help="Remove a deterministic hard-ban phrase (writes the YAML registry)"
    )
    unban_parser.add_argument("phrase")
    unban_parser.add_argument("--repo-root", default=None)
    unban_parser.add_argument("--style-guide", default=None)
    unban_parser.set_defaults(func=_unban)


def _audit(args: argparse.Namespace) -> int:
    from lesson_builder.application.operations.load_terminology import load_terminology_bans
    from lesson_builder.domain.lesson.validation.checks.validators.terminology import terminology_audit

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    registry_path = Path(args.style_guide) if args.style_guide else WorkspacePaths(repo_root).terminology_registry
    bans = load_terminology_bans(registry_path)

    paths: list[Path]
    if args.paths:
        paths = [Path(path) for path in args.paths]
    else:
        dist_root = WorkspacePaths(repo_root).dist_root / "lessons"
        paths = sorted(dist_root.glob("*.json"))

    all_rows: list[dict[str, Any]] = []
    for path in paths:
        lesson = json.loads(path.read_text(encoding="utf-8"))
        rows = terminology_audit(lesson, bans)
        for row in rows:
            row["slug"] = lesson.get("concept_slug") or lesson.get("key") or path.stem
        all_rows.extend(rows)

    if args.summary:
        summary: dict[tuple[str, str], int] = {}
        for row in all_rows:
            key = (row.get("check_id", "?"), row.get("phrase", "?"))
            summary[key] = summary.get(key, 0) + 1
        print(
            json.dumps(
                [
                    {"check_id": check_id, "phrase": phrase, "count": count}
                    for (check_id, phrase), count in sorted(summary.items())
                ],
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps(all_rows, indent=2, ensure_ascii=False))
    return 0


def _list(args: argparse.Namespace) -> int:
    from lesson_builder.application.operations.load_terminology import load_terminology_registry
    from lesson_builder.domain.lesson.validation.terminology_registry import build_registry_version

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    registry_path = Path(args.style_guide) if args.style_guide else WorkspacePaths(repo_root).terminology_registry
    registry = load_terminology_registry(registry_path)
    print(
        json.dumps(
            {
                "schema_version": registry.schema_version,
                "registry_version": build_registry_version(registry),
                "active_concepts": [concept.model_dump(mode="json") for concept in registry.collect_active_concepts()],
                "reference_concepts": [
                    concept.model_dump(mode="json") for concept in registry.collect_reference_concepts()
                ],
                "banned_phrases": registry.banned_phrases,
                "prose_tells": registry.prose_tell_phrases,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _ban(args: argparse.Namespace) -> int:
    import yaml

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    yaml_path = Path(args.style_guide) if args.style_guide else WorkspacePaths(repo_root).terminology_registry
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("concepts"), list):
        raise SystemExit(f"invalid terminology registry: {yaml_path}")
    phrase = args.phrase.strip()
    if not phrase:
        raise SystemExit("phrase is required")
    existing = set()
    for concept in raw["concepts"]:
        for forbidden in concept.get("forbidden_phrases") or []:
            existing_phrase = forbidden.get("phrase") if isinstance(forbidden, dict) else str(forbidden)
            if isinstance(existing_phrase, str):
                existing.add(existing_phrase.lower())
    if phrase.lower() not in existing:
        first_active = next(
            (concept for concept in raw["concepts"] if isinstance(concept, dict) and concept.get("scope") == "active"),
            raw["concepts"][0],
        )
        first_active.setdefault("forbidden_phrases", []).append(
            {"phrase": phrase, "rationale": "Added via CLI ban command."}
        )
        yaml_path.write_text(
            yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False), encoding="utf-8"
        )
    print(json.dumps({"banned_phrases": sorted(existing | {phrase.lower()})}, indent=2, ensure_ascii=False))
    return 0


def _unban(args: argparse.Namespace) -> int:
    import yaml

    repo_root = Path(args.repo_root) if args.repo_root else get_workspace_root()
    yaml_path = Path(args.style_guide) if args.style_guide else WorkspacePaths(repo_root).terminology_registry
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("concepts"), list):
        raise SystemExit(f"invalid terminology registry: {yaml_path}")
    phrase = args.phrase.strip().lower()
    remaining: list[str] = []
    for concept in raw["concepts"]:
        forbidden_list = concept.get("forbidden_phrases") or []
        kept: list[dict[str, str]] = []
        for forbidden in forbidden_list:
            existing_phrase = forbidden.get("phrase") if isinstance(forbidden, dict) else str(forbidden)
            if isinstance(existing_phrase, str) and existing_phrase.lower() == phrase:
                continue
            kept.append(forbidden)
        concept["forbidden_phrases"] = kept
        for forbidden in kept:
            existing_phrase = forbidden.get("phrase") if isinstance(forbidden, dict) else str(forbidden)
            if isinstance(existing_phrase, str):
                remaining.append(existing_phrase)
    yaml_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=False), encoding="utf-8"
    )
    print(json.dumps({"banned_phrases": remaining}, indent=2, ensure_ascii=False))
    return 0


__all__ = ["add_terminology_commands"]
