"""Entry point: ``run_curriculum_thresholds`` / ``main``.

Live driver for Phase-0 threshold derivation: load all concept-requirements
cards from ``data/concept_requirements/``, derive thinness / too-broad /
coverage thresholds deterministically (NO LLM), and persist them to
``data/calibration/curriculum_thresholds.json``. Promotion to load-bearing
stays explicit (``--promote``).

Mirrors ``calibration.rubric_run``. The pure threshold math lives in
``thresholds``; this module only wires card loading.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lesson_builder.pipeline.concept_requirements import (
    ConceptRequirements,
    load_concept_requirements,
)
from lesson_builder.pipeline.curriculum_design.thresholds import (
    CurriculumThresholds,
    compute_curriculum_thresholds,
    save_curriculum_thresholds,
)

K_REPO_ROOT = Path(__file__).resolve().parents[4]
K_CONCEPT_REQUIREMENTS_RELATIVE_PATH = Path("data") / "concept_requirements"


def _load_cards(repo_root: Path) -> dict[str, ConceptRequirements]:
    cards: dict[str, ConceptRequirements] = {}
    cards_dir = repo_root / K_CONCEPT_REQUIREMENTS_RELATIVE_PATH
    if not cards_dir.exists():
        raise FileNotFoundError(f"no concept-requirements directory at {cards_dir}")
    for path in sorted(cards_dir.glob("*.json")):
        card = load_concept_requirements(path)
        cards[card.slug] = card
    if not cards:
        raise FileNotFoundError(f"no concept-requirements cards found under {cards_dir}")
    return cards


def run_curriculum_thresholds(
    *,
    repo_root: Path = K_REPO_ROOT,
    promote: bool = False,
    out_path: Path | None = None,
) -> CurriculumThresholds:
    """Load all cards, derive thresholds deterministically, persist them."""
    repo_root = Path(repo_root)
    cards = _load_cards(repo_root)
    thresholds = compute_curriculum_thresholds(cards, promote=promote)
    save_curriculum_thresholds(
        thresholds,
        path=out_path or (repo_root / "data" / "calibration" / "curriculum_thresholds.json"),
    )
    return thresholds


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lesson-data calibration-curriculum-thresholds")
    parser.add_argument("--promote", action="store_true", help="mark the thresholds load-bearing")
    parser.add_argument("--repo-root", default=str(K_REPO_ROOT))
    args = parser.parse_args(argv)

    thresholds = run_curriculum_thresholds(
        repo_root=Path(args.repo_root),
        promote=args.promote,
    )
    print(json.dumps(thresholds.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()


__all__ = ["run_curriculum_thresholds", "main"]
