"""Entry point: ``check_lesson`` / ``main``.

Gate + dist-verify changed lessons, closing the gap where hand-edits bypass the
QA graph. For each target lesson: (1) run ``gate_lesson_results`` and collect any
``is_blocking`` result; (2) compute the fresh dist export via ``lesson_to_export``
and compare to the on-disk ``dist/lessons/<slug>.json`` (stale or missing dist =
failure). Exits 1 on any blocking gate result or stale dist.

Default targets = lesson files changed vs the merge-base with master
(``git merge-base HEAD master`` then ``git diff --name-only <base> -- data/lessons/``).
Pass explicit lesson paths or ``--all`` to override.

Usage:
    uv run python scripts/check_lessons.py                  # changed vs master
    uv run python scripts/check_lessons.py --all             # every data/lessons/*.json
    uv run python scripts/check_lessons.py data/lessons/x.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.checks.gate_manager import gate_lesson_results
from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.lesson_export import lesson_to_export
from lesson_builder.schema import Lesson

K_REPO_ROOT = Path(__file__).resolve().parents[1]
K_LESSONS_DIR = K_REPO_ROOT / "data" / "lessons"
K_DIST_DIR = K_REPO_ROOT / "dist" / "lessons"


@dataclass(frozen=True)
class LessonCheckResult:
    """Outcome of checking one lesson: its blocking gate findings + dist status.

    ``dist_detail`` is ``None`` when the dist is fresh, ``"missing"`` when the
    dist file does not exist, or ``"stale"`` when it exists but differs from the
    freshly computed export.
    """

    slug: str
    blocking: list[CheckResult] = field(default_factory=list)
    dist_detail: str | None = None

    @property
    def is_clean(self) -> bool:
        return not self.blocking and self.dist_detail is None


def check_lesson(lesson: dict[str, Any], dist_text: str | None) -> LessonCheckResult:
    """Run the gate + compare dist for a single lesson (pure, no filesystem).

    ``dist_text`` is the on-disk dist file content, or ``None`` when the dist
    file is missing. The fresh export is computed via
    ``lesson_to_export(Lesson.model_validate(lesson))`` and compared textually
    (``json.dumps(..., ensure_ascii=False, indent=2) + "\\n"``) so formatting
    drift is caught too.
    """
    slug = str(lesson.get("concept_slug") or lesson.get("key") or "unknown")
    results = gate_lesson_results(lesson, requirements=None)
    blocking = [result for result in results if result.is_blocking]

    fresh_export = lesson_to_export(Lesson.model_validate(lesson))
    fresh_text = json.dumps(fresh_export, ensure_ascii=False, indent=2) + "\n"

    if dist_text is None:
        dist_detail: str | None = "missing"
    elif dist_text != fresh_text:
        dist_detail = "stale"
    else:
        dist_detail = None

    return LessonCheckResult(slug=slug, blocking=blocking, dist_detail=dist_detail)


def check_lesson_at(lesson_path: Path, dist_path: Path) -> LessonCheckResult:
    """File-based wrapper: load lesson JSON + dist text (None if absent), call ``check_lesson``."""
    lesson = json.loads(lesson_path.read_text(encoding="utf-8"))
    dist_text = dist_path.read_text(encoding="utf-8") if dist_path.exists() else None
    return check_lesson(lesson, dist_text)


def _print_result(name: str, result: LessonCheckResult) -> None:
    status = "OK" if result.is_clean else "FAIL"
    print(f"[{status}] {name}")
    for issue in result.blocking:
        print(f"  BLOCKING {issue.check_id} ({issue.unit_id}): {issue.message}")
    if result.dist_detail == "missing":
        print("  STALE DIST: dist file missing")
    elif result.dist_detail == "stale":
        print("  STALE DIST: dist does not match fresh export")


def _changed_lesson_paths() -> list[Path]:
    """Lesson files changed vs the merge-base with master."""
    base = subprocess.run(
        ["git", "merge-base", "HEAD", "master"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(K_REPO_ROOT),
    ).stdout.strip()
    diff_output = subprocess.run(
        ["git", "diff", "--name-only", base, "--", "data/lessons/"],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(K_REPO_ROOT),
    ).stdout.strip()
    return [
        (K_REPO_ROOT / line).resolve()
        for line in diff_output.splitlines()
        if line and (K_REPO_ROOT / line).exists()
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Gate + dist-verify changed lessons (catches hand-edits that bypass the QA graph).",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="Specific lesson JSON files to check (default: changed vs merge-base with master).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Check every data/lessons/*.json regardless of git status.",
    )
    args = parser.parse_args(argv)

    if args.all:
        lesson_paths = sorted(K_LESSONS_DIR.glob("*.json"))
    elif args.paths:
        lesson_paths = [path.resolve() for path in args.paths]
    else:
        lesson_paths = _changed_lesson_paths()

    if not lesson_paths:
        print("no changed lessons")
        return 0

    total = len(lesson_paths)
    total_blocking = 0
    total_stale = 0

    for lesson_path in lesson_paths:
        result = check_lesson_at(lesson_path, K_DIST_DIR / lesson_path.name)
        _print_result(lesson_path.name, result)
        if result.blocking:
            total_blocking += len(result.blocking)
        if result.dist_detail is not None:
            total_stale += 1

    failed = total_blocking + total_stale
    print()
    print(f"SUMMARY: {total} lessons checked, {total_blocking} blocking, {total_stale} stale")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
