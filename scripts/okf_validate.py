#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml>=6"]
# ///
"""Entry point: ``main``. Validate an OKF v0.1 knowledge bundle locally."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

import yaml

K_RESERVED_NAMES = {"index.md", "log.md"}
K_RECOMMENDED_FIELDS = ("title", "description", "tags", "timestamp")
K_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
K_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*\]\(\s*(?:<([^>]*)>|([^\s)]+))")
# Reference-style uses: `[text][label]` and the implicit `[text][]` (label == text).
K_REF_USE_PATTERN = re.compile(r"(?<!!)\[([^\]]+)\]\[([^\]]*)\]")
# Reference-style definitions: `[label]: target` at the start of a line.
K_REF_DEF_PATTERN = re.compile(r"^\[([^\]]+)\]:\s*(?:<([^>]*)>|(\S+))", re.MULTILINE)


@dataclass
class Report:
    """Validation result for one knowledge bundle."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, path: Path, message: str) -> None:
        self.errors.append(f"{path.as_posix()}: {message}")

    def warning(self, path: Path, message: str) -> None:
        self.warnings.append(f"{path.as_posix()}: {message}")


def validate_bundle(bundle_root: Path, *, check_links: bool) -> Report:
    """Validate frontmatter, reserved files, and optional local link targets."""
    report = Report()
    markdown_files = sorted(bundle_root.rglob("*.md"))
    for path in markdown_files:
        relative_path = path.relative_to(bundle_root)
        if path.name in K_RESERVED_NAMES:
            _validate_reserved(path, relative_path, bundle_root, report)
        else:
            _validate_concept(path, relative_path, report)
    if check_links:
        for path in markdown_files:
            _validate_links(path, bundle_root, report)
    return report


def main() -> int:
    """Run the repository-local OKF validator."""
    parser = argparse.ArgumentParser(description="Validate an OKF v0.1 bundle.")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--strict", action="store_true", help="fail on warnings")
    parser.add_argument("--check-links", action="store_true", help="fail on missing local link targets")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not args.bundle.is_dir():
        print(f"error: {args.bundle} is not a directory", file=sys.stderr)
        return 2
    report = validate_bundle(args.bundle, check_links=args.check_links)
    failed = bool(report.errors) or (args.strict and bool(report.warnings))
    if args.json:
        print(json.dumps({"errors": report.errors, "warnings": report.warnings, "passed": not failed}, indent=2))
    else:
        for message in report.errors:
            print(f"ERROR {message}")
        for message in report.warnings:
            print(f"WARN  {message}")
        print("OKF validation passed" if not failed else "OKF validation failed")
    return 1 if failed else 0


def _frontmatter(path: Path) -> dict[str, object] | None:
    text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        closing_index = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return None
    parsed = yaml.safe_load("\n".join(lines[1:closing_index]))
    return parsed if isinstance(parsed, dict) else None


def _validate_concept(path: Path, relative_path: Path, report: Report) -> None:
    metadata = _frontmatter(path)
    if metadata is None:
        report.error(relative_path, "§9 requires parseable YAML frontmatter")
        return
    concept_type = metadata.get("type")
    if not isinstance(concept_type, str) or not concept_type.strip():
        report.error(relative_path, "§9 requires a non-empty `type` field")
    for field_name in K_RECOMMENDED_FIELDS:
        if field_name not in metadata:
            report.warning(relative_path, f"recommended `{field_name}` field is absent")


def _validate_reserved(path: Path, relative_path: Path, bundle_root: Path, report: Report) -> None:
    metadata = _frontmatter(path)
    if metadata is not None:
        if path.name == "index.md" and path.parent == bundle_root and set(metadata) <= {"okf_version"}:
            return
        report.warning(relative_path, f"reserved `{path.name}` should not have frontmatter")
    if path.name == "log.md":
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## ") and not K_DATE_PATTERN.fullmatch(line[3:].strip()):
                report.warning(relative_path, "log headings should use ISO YYYY-MM-DD dates")


def _validate_links(path: Path, bundle_root: Path, report: Report) -> None:
    text = path.read_text(encoding="utf-8")
    for angle_target, bare_target in K_LINK_PATTERN.findall(text):
        _check_link_target(angle_target or bare_target, path, bundle_root, report)

    # Reference-style links are checked in two halves: the definition's target must
    # exist (same rule as an inline link), and every use must resolve to a definition.
    # Neither half is visible to K_LINK_PATTERN, so without this a `[text][label]`
    # pointing at a deleted file passes the gate in silence.
    definitions = {
        label.lower(): angle_target or bare_target
        for label, angle_target, bare_target in K_REF_DEF_PATTERN.findall(text)
    }
    for target in definitions.values():
        _check_link_target(target, path, bundle_root, report)

    for link_text, label in K_REF_USE_PATTERN.findall(text):
        # `[text][]` is the implicit form: the label is the link text itself.
        resolved_label = (label or link_text).lower()
        if resolved_label not in definitions:
            report.error(
                path.relative_to(bundle_root),
                f"reference-style link has no definition: `[{link_text}][{label}]`",
            )


def _check_link_target(raw_target: str, path: Path, bundle_root: Path, report: Report) -> None:
    target = raw_target.split("#", 1)[0]
    if not target or "://" in target or target.startswith("mailto:"):
        return
    resolved = bundle_root / target.lstrip("/") if target.startswith("/") else path.parent / target
    if not resolved.exists():
        report.error(path.relative_to(bundle_root), f"link target not found: `{target}`")


if __name__ == "__main__":
    raise SystemExit(main())
