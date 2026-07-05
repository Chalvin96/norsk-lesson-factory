"""Eval harness: deterministic generator-quality defect metrics over data/lessons.

Read-only over ``data/lessons/*.json``. Computes five defect metrics, prints a
summary table, and writes a machine-readable JSON summary. The detection core
(constants, span walkers, predicates, normalization) is imported from
``lesson_builder.pipeline.checks.defect_rules`` — the same module the
deterministic validators use — so the harness cannot drift from the gate.

Usage:
    python scripts/eval_lesson_defects.py [--paths a.json b.json] [--out report.json]

Metrics:
    canned_opener_ratio      — fraction of exercise explanations whose first text
                               span starts with a canned opener.
    render_bug_blocker       — English function words mis-tagged lang="no" (blocker).
    render_bug_advisory      — foreign_term spans with lang != "no" (advisory;
                               legitimate for inline English contrast terms).
    answer_leak_count        — exercise stems with an English meta-gloss that
                               telegraphs the answer.
    findfix_integrity_violations — find_fix exercises with a no-op or mis-pointed
                               error token.
    section_coverage_gaps    — objectives not covered by any section's objective_ids.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.checks.defect_rules import (
    check_error_token_alignment,
    classify_foreign_term,
    collapse_ws,
    extract_corrected_sentence,
    extract_stem_texts,
    find_foreign_term_spans,
    grounding_gap_count,
    has_answer_leak,
    is_canned_opener,
    normalize,
    section_coverage_gaps,
)

K_REPO_ROOT = Path(__file__).resolve().parents[1]
K_LESSONS_DIR = K_REPO_ROOT / "data" / "lessons"


# ---------------------------------------------------------------------------
# Metric: canned_opener_ratio
# ---------------------------------------------------------------------------


def _canned_opener_stats(lesson: dict[str, Any]) -> tuple[int, int]:
    total = 0
    canned = 0
    for el in lesson.get("elements", []):
        if not isinstance(el, dict) or el.get("element_kind") != "exercise":
            continue
        explanation = el.get("explanation")
        if not isinstance(explanation, list) or not explanation:
            continue
        first = explanation[0]
        if isinstance(first, dict) and isinstance(first.get("value"), str) and first["value"]:
            total += 1
            if is_canned_opener(first["value"]):
                canned += 1
    return canned, total


# ---------------------------------------------------------------------------
# Metric: render_bug_count
# ---------------------------------------------------------------------------


def _render_bug_counts(lesson: dict[str, Any]) -> tuple[int, int]:
    """Return (advisory, blocker): advisory = lang!="no" spans (legit English
    contrast terms, flagged for review); blocker = English function words
    mis-tagged lang="no"."""
    advisory = 0
    blocker = 0
    for el in lesson.get("elements", []):
        if not isinstance(el, dict):
            continue
        for span in find_foreign_term_spans(el):
            defect = classify_foreign_term(span)
            if defect == "advisory":
                advisory += 1
            elif defect == "blocker":
                blocker += 1
    return advisory, blocker


# ---------------------------------------------------------------------------
# Metric: answer_leak_count
# ---------------------------------------------------------------------------


def _answer_leak_count(lesson: dict[str, Any]) -> int:
    count = 0
    for el in lesson.get("elements", []):
        if not isinstance(el, dict) or el.get("element_kind") != "exercise":
            continue
        for text in extract_stem_texts(el):
            if has_answer_leak(text):
                count += 1
                break
    return count


# ---------------------------------------------------------------------------
# Metric: findfix_integrity_violations
# ---------------------------------------------------------------------------


def _findfix_integrity_violations(lesson: dict[str, Any]) -> int:
    count = 0
    for el in lesson.get("elements", []):
        if not isinstance(el, dict):
            continue
        if el.get("element_kind") != "exercise" or el.get("operation") != "find_fix":
            continue
        payload = el.get("payload", {})
        tokens = payload.get("tokens", []) or []
        error_token_id = payload.get("error_token_id")
        feedback = str(payload.get("feedback", "") or "")

        presented = " ".join(str(t.get("text", "")) for t in tokens if isinstance(t, dict))
        corrected = extract_corrected_sentence(feedback)
        if corrected is None:
            continue

        presented_norm = normalize(presented)
        corrected_norm = normalize(corrected)

        # no-change gate is case/punctuation SENSITIVE (parity with the validator)
        if collapse_ws(presented) == collapse_ws(corrected):
            count += 1
            continue

        if check_error_token_alignment(tokens, error_token_id, presented_norm, corrected_norm):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Aggregation + reporting
# ---------------------------------------------------------------------------


def _evaluate_lesson(lesson: dict[str, Any]) -> dict[str, Any]:
    canned, total = _canned_opener_stats(lesson)
    render_advisory, render_blocker = _render_bug_counts(lesson)
    return {
        "key": lesson.get("key", ""),
        "canned_openers": canned,
        "canned_opener_total": total,
        "render_bug_advisory": render_advisory,
        "render_bug_blocker": render_blocker,
        "answer_leak_count": _answer_leak_count(lesson),
        "findfix_integrity_violations": _findfix_integrity_violations(lesson),
        "section_coverage_gaps": section_coverage_gaps(lesson),
        "grounding_gaps": grounding_gap_count(lesson),
    }


def _build_summary(per_lesson: list[dict[str, Any]]) -> dict[str, Any]:
    total_explanations = sum(r["canned_opener_total"] for r in per_lesson)
    total_canned = sum(r["canned_openers"] for r in per_lesson)
    return {
        "lessons_evaluated": len(per_lesson),
        "canned_opener_ratio": round(total_canned / total_explanations, 4) if total_explanations else 0.0,
        "canned_opener_count": total_canned,
        "canned_opener_total": total_explanations,
        "render_bug_advisory": sum(r["render_bug_advisory"] for r in per_lesson),
        "render_bug_blocker": sum(r["render_bug_blocker"] for r in per_lesson),
        "answer_leak_count": sum(r["answer_leak_count"] for r in per_lesson),
        "findfix_integrity_violations": sum(r["findfix_integrity_violations"] for r in per_lesson),
        "section_coverage_gaps": sum(len(r["section_coverage_gaps"]) for r in per_lesson),
        "grounding_gaps": sum(r["grounding_gaps"] for r in per_lesson),
    }


def _print_table(per_lesson: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    print("=" * 72)
    print("LESSON DEFECT EVALUATION — SUMMARY")
    print("=" * 72)
    print(f"Lessons evaluated:      {summary['lessons_evaluated']}")
    print(f"Canned opener ratio:    {summary['canned_opener_ratio']:.4f} "
          f"({summary['canned_opener_count']}/{summary['canned_opener_total']})")
    print(f"Render bug (blocker):   {summary['render_bug_blocker']}")
    print(f"Render bug (advisory):  {summary['render_bug_advisory']}")
    print(f"Answer leak count:      {summary['answer_leak_count']}")
    print(f"FindFix integrity viol: {summary['findfix_integrity_violations']}")
    print(f"Section coverage gaps:  {summary['section_coverage_gaps']}")
    print(f"Grounding gaps (diag):  {summary['grounding_gaps']}")
    print()

    flagged = [
        r for r in per_lesson
        if r["render_bug_advisory"]
        or r["render_bug_blocker"]
        or r["answer_leak_count"]
        or r["findfix_integrity_violations"]
        or r["section_coverage_gaps"]
    ]
    if flagged:
        print("-" * 72)
        print("FLAGGED LESSONS")
        print("-" * 72)
        header = f"{'Lesson':<40} {'RndBlk':>6} {'RndAdv':>6} {'Leak':>4} {'FixFF':>5} {'CovGap':>6}"
        print(header)
        for r in sorted(flagged, key=lambda x: x["key"]):
            print(
                f"{r['key']:<40} {r['render_bug_blocker']:>6} {r['render_bug_advisory']:>6} "
                f"{r['answer_leak_count']:>4} {r['findfix_integrity_violations']:>5} "
                f"{len(r['section_coverage_gaps']):>6}"
            )
        print()

    gap_lessons = [r for r in per_lesson if r["section_coverage_gaps"]]
    if gap_lessons:
        print("-" * 72)
        print("SECTION COVERAGE GAP DETAILS")
        print("-" * 72)
        for r in gap_lessons:
            print(f"  {r['key']}: {r['section_coverage_gaps']}")
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate deterministic generator-quality defect metrics.")
    parser.add_argument(
        "--paths",
        nargs="*",
        type=Path,
        default=None,
        help="Specific lesson JSON files to evaluate (default: all data/lessons/*.json).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write machine-readable JSON summary to this path (default: stdout).",
    )
    args = parser.parse_args(argv)

    lesson_paths = sorted(args.paths) if args.paths else sorted(K_LESSONS_DIR.glob("*.json"))
    if not lesson_paths:
        print("No lesson files found.", file=sys.stderr)
        return 1

    per_lesson: list[dict[str, Any]] = []
    for path in lesson_paths:
        lesson = json.loads(path.read_text(encoding="utf-8"))
        per_lesson.append(_evaluate_lesson(lesson))

    summary = _build_summary(per_lesson)
    _print_table(per_lesson, summary)

    output = {"summary": summary, "per_lesson": per_lesson}
    payload = json.dumps(output, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(payload + "\n", encoding="utf-8")
        print(f"JSON summary written to {args.out}")
    else:
        print("--- JSON ---")
        print(payload)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
