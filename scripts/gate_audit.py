"""Audit the load-bearing gate across the real lesson corpus.

For each lesson in ``data/lessons/`` (optionally limited), run the deterministic
gate + the live pedagogy reviewer + the rubric-floor check, and report which
lessons WOULD be blocked and on which axes. This validates that the now
load-bearing rubric floors are calibrated — accepted lessons should mostly pass;
heavy blocking means the floors are too tight (over-flagging).

Read-only: never writes lessons. Run:
    python -m scripts.gate_audit [--limit N] [--out report.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

K_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_requirements(repo_root: Path, slug: str) -> dict[str, Any] | None:
    path = repo_root / "data" / "concept_requirements" / f"{slug}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def audit_lesson(repo_root: Path, slug: str) -> dict[str, Any]:
    from lesson_builder.pipeline.checks.gate_manager import (
        gate_advisory_results,
        gate_lesson_results,
    )
    from lesson_builder.pipeline.judges import pedagogy_review

    lesson = json.loads((repo_root / "data" / "lessons" / f"{slug}.json").read_text(encoding="utf-8"))
    requirements = _load_requirements(repo_root, slug)

    det = gate_lesson_results(lesson, requirements=requirements, baseline_export=None,
                              recorded_requirements_hash=None)
    det_blockers = [r.message for r in det if r.is_blocking]

    review = pedagogy_review(lesson=lesson)
    advisory = gate_advisory_results(lesson, pedagogy_review=review, repo_root=repo_root)
    rubric = [r for r in advisory if r.check_id == "rubric_floor"]
    rubric_blockers = sorted(r.unit_id for r in rubric if r.is_blocking)
    rubric_revision = sorted(r.unit_id for r in rubric if r.revision_target)
    scores = (review or {}).get("scores") if isinstance(review, dict) else None

    return {
        "slug": slug,
        "reviewed": review is not None,
        "deterministic_blockers": det_blockers,
        "rubric_blocked_axes": rubric_blockers,  # hard blocks (should be none — floors are revision targets)
        "rubric_revision_axes": rubric_revision,  # below-floor -> routes to revise loop
        "scores": scores,
        "blocked": bool(det_blockers or rubric_blockers),  # hard-block only
        "needs_revision": bool(rubric_revision),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="gate_audit")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--repo-root", default=str(K_REPO_ROOT))
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root)
    slugs = sorted(p.stem for p in (repo_root / "data" / "lessons").glob("*.json"))
    if args.limit:
        slugs = slugs[: args.limit]

    rows: list[dict[str, Any]] = []
    for i, slug in enumerate(slugs, 1):
        row = audit_lesson(repo_root, slug)
        rows.append(row)
        mark = "BLOCK" if row["blocked"] else ("revise" if row["needs_revision"] else "ok")
        print(f"[{i}/{len(slugs)}] {slug}: {mark} "
              f"det={len(row['deterministic_blockers'])} revise={row['rubric_revision_axes']}",
              flush=True)

    blocked = [r for r in rows if r["blocked"]]
    needs_rev = [r for r in rows if r["needs_revision"]]
    unreviewed = [r for r in rows if not r["reviewed"]]
    rev_axis_counts: dict[str, int] = {}
    for r in rows:
        for axis in r["rubric_revision_axes"]:
            rev_axis_counts[axis] = rev_axis_counts.get(axis, 0) + 1

    import statistics
    axes = ("on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth")
    scored = [r for r in rows if r.get("scores")]
    score_medians = {
        a: statistics.median(r["scores"][a] for r in scored) for a in axes
    } if scored else {}

    summary = {
        "n_lessons": len(rows),
        "n_hard_blocked": len(blocked),
        "n_needs_revision": len(needs_rev),
        "n_clean": len(rows) - len(blocked) - len(needs_rev),
        "n_unreviewed": len(unreviewed),
        "score_medians": score_medians,
        "rubric_axis_revision_counts": dict(sorted(rev_axis_counts.items(), key=lambda kv: -kv[1])),
        "hard_blocked_slugs": [r["slug"] for r in blocked],
    }
    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(
            json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
