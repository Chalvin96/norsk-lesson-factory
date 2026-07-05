"""Entry point: ``run_rubric_calibration`` / ``main``.

Live driver for the rubric-floor calibration (#2): score the golden lessons with
the real pedagogy reviewer, derive per-axis floors, and persist them to
``data/calibration/rubric_floors.json``. Promotion to load-bearing stays explicit
(``--promote``).

The pure floor math lives in ``rubric_floors``; this module only wires the live
reviewer + golden loading. ``scorer`` is injectable so tests stay offline.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.calibration.rubric_floors import (
    K_DEFAULT_EXCLUDE_BELOW,
    K_DEFAULT_FLOOR_TOLERANCE,
    RubricFloors,
    RubricScorer,
    compute_rubric_floors,
    save_rubric_floors,
)
from lesson_builder.pipeline.checks.gate_manager import gate_lesson_results
from lesson_builder.pipeline.checks.validators.pedagogy import PedagogyReview

K_REPO_ROOT = Path(__file__).resolve().parents[4]

# The validated golden lessons used as the quality benchmark (mirrors the orphaned
# rubric_calibration.overflagging.json benchmark set).
K_RUBRIC_GOLDEN_SLUGS = (
    "collocations",
    "formal_vs_informal_register",
    "past_tense",
    "preterite_vs_present_perfect",
    "word_order_main_clauses",
)

K_LIVE_MODEL = "reviewer-chain"


def _load_goldens(repo_root: Path, slugs: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    goldens: dict[str, dict[str, Any]] = {}
    for slug in slugs:
        path = repo_root / "data" / "lessons" / f"{slug}.json"
        if path.exists():
            goldens[slug] = json.loads(path.read_text(encoding="utf-8"))
    if not goldens:
        raise FileNotFoundError(
            f"no golden lessons found under {repo_root / 'data' / 'lessons'} for {slugs}"
        )
    return goldens


def _assert_goldens_pass_gate(goldens: dict[str, dict[str, Any]]) -> None:
    """Golden-hygiene precondition: every golden must pass the deterministic gate
    (no blocking findings) BEFORE its scores set the rubric floors.

    A golden that trips a blocker — e.g. a placeholder ``"Learn <slug>"``
    objective (``objective_placeholder``) — would bake that defect into the
    quality benchmark and license the same defect in every calibrated lesson.
    Raises ``ValueError`` naming the offending goldens and their blockers.
    """
    offenders: dict[str, list[str]] = {}
    for slug, lesson in goldens.items():
        blocking = [
            result
            for result in gate_lesson_results(lesson, requirements=None)
            if result.is_blocking
        ]
        if blocking:
            offenders[slug] = [f"{r.check_id}:{r.unit_id}" for r in blocking]
    if offenders:
        raise ValueError(
            "golden lessons must pass the deterministic gate before floor "
            f"calibration (golden-hygiene precondition); blocking findings: {offenders}"
        )


def _live_scorer() -> RubricScorer:
    """A pedagogy scorer backed by the live reviewer chain."""
    from lesson_builder.pipeline.judges import pedagogy_review

    def scorer(lesson: dict[str, Any]) -> PedagogyReview | None:
        payload = pedagogy_review(lesson=lesson)
        if payload is None:
            return None
        return PedagogyReview.model_validate(payload)

    return scorer


def run_rubric_calibration(
    *,
    repo_root: Path = K_REPO_ROOT,
    slugs: tuple[str, ...] = K_RUBRIC_GOLDEN_SLUGS,
    scorer: RubricScorer | None = None,
    model: str = K_LIVE_MODEL,
    exclude_below: float = K_DEFAULT_EXCLUDE_BELOW,
    floor_tolerance: int = K_DEFAULT_FLOOR_TOLERANCE,
    promote: bool = False,
    out_path: Path | None = None,
) -> RubricFloors:
    """Score goldens, derive floors, persist them. ``scorer`` injectable for tests."""
    repo_root = Path(repo_root)
    goldens = _load_goldens(repo_root, slugs)
    _assert_goldens_pass_gate(goldens)
    floors = compute_rubric_floors(
        goldens,
        scorer or _live_scorer(),
        model=model,
        exclude_below=exclude_below,
        floor_tolerance=floor_tolerance,
        promote=promote,
    )
    save_rubric_floors(
        floors,
        path=out_path or (repo_root / "data" / "calibration" / "rubric_floors.json"),
    )
    return floors


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lesson-data calibration-rubric")
    parser.add_argument("--promote", action="store_true", help="mark the floors load-bearing")
    parser.add_argument("--exclude-below", type=float, default=K_DEFAULT_EXCLUDE_BELOW)
    parser.add_argument(
        "--floor-tolerance",
        type=int,
        default=K_DEFAULT_FLOOR_TOLERANCE,
        help="points subtracted from each axis's golden minimum (gate collapse, not a dip)",
    )
    parser.add_argument("--repo-root", default=str(K_REPO_ROOT))
    args = parser.parse_args(argv)

    floors = run_rubric_calibration(
        repo_root=Path(args.repo_root),
        exclude_below=args.exclude_below,
        floor_tolerance=args.floor_tolerance,
        promote=args.promote,
    )
    print(json.dumps(floors.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()


__all__ = ["run_rubric_calibration", "K_RUBRIC_GOLDEN_SLUGS", "main"]
