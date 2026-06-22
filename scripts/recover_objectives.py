"""Recover corrupted objective statements in data/lessons/ from phase-1 source.

The dist `ExportedLesson` schema has no `objectives` field, so the retired
dist-reconstruction journey dropped objective statement text, leaving placeholders:
``"[imported_unverified] objective statement not recoverable from export"``. The
real statements survive in the phase-1 generated lessons under
``generated/*/lessons/`` (git ref ``b9cbac345^``), keyed by the SAME objective
ids. This restores the statement text by id-match; nothing else is touched.

Read-only on git history; writes only ``data/lessons/<slug>.json`` statement
fields. Run:  python -m scripts.recover_objectives [--apply]
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

K_REPO_ROOT = Path(__file__).resolve().parents[1]
K_SOURCE_REF = "b9cbac345^"  # parent of the phase1 generated-artifact retirement
K_PLACEHOLDER = "not recoverable from export"


def _git_show(ref_path: str) -> str | None:
    proc = subprocess.run(
        ["git", "cat-file", "-p", ref_path], capture_output=True, text=True, cwd=K_REPO_ROOT
    )
    return proc.stdout if proc.returncode == 0 else None


def _build_source_map(ref: str) -> dict[str, dict[str, str]]:
    """slug -> {objective_id: statement} from all generated/*/lessons/ at ``ref``."""
    listing = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref], capture_output=True, text=True, cwd=K_REPO_ROOT
    ).stdout.splitlines()
    gen = [p for p in listing if p.startswith("generated/") and "/lessons/" in p and p.endswith(".json")]
    src: dict[str, dict[str, str]] = {}
    for path in gen:
        slug = path.split("/lessons/")[1][:-5]
        if slug in src:
            continue
        raw = _git_show(f"{ref}:{path}")
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        objs = {
            o["id"]: o["statement"]
            for o in data.get("objectives", [])
            if o.get("statement") and K_PLACEHOLDER not in o.get("statement", "")
        }
        if objs:
            src[slug] = objs
    return src


def recover(*, apply: bool) -> dict[str, Any]:
    src = _build_source_map(K_SOURCE_REF)
    lessons_dir = K_REPO_ROOT / "data" / "lessons"
    fixed: list[str] = []
    unrecoverable: list[str] = []
    clean: list[str] = []

    for path in sorted(lessons_dir.glob("*.json")):
        slug = path.stem
        lesson = json.loads(path.read_text(encoding="utf-8"))
        corrupt_ids = [
            o["id"] for o in lesson.get("objectives", []) if K_PLACEHOLDER in o.get("statement", "")
        ]
        if not corrupt_ids:
            clean.append(slug)
            continue
        source = src.get(slug)
        if not source or not all(cid in source for cid in corrupt_ids):
            unrecoverable.append(slug)
            continue
        for obj in lesson["objectives"]:
            if K_PLACEHOLDER in obj.get("statement", ""):
                obj["statement"] = source[obj["id"]]
        if apply:
            path.write_text(json.dumps(lesson, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        fixed.append(slug)

    return {
        "n_fixed": len(fixed),
        "n_clean": len(clean),
        "n_unrecoverable": len(unrecoverable),
        "unrecoverable": unrecoverable,
        "applied": apply,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="recover_objectives")
    parser.add_argument("--apply", action="store_true", help="write the fixes (default: dry run)")
    args = parser.parse_args(argv)
    result = recover(apply=args.apply)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
