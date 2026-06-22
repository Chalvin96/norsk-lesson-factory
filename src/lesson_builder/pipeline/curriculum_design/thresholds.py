"""Entry point: ``compute_curriculum_thresholds`` / ``CurriculumThresholds``.

Deterministic, corpus-free calibration for curriculum design. Derives
objective-count and coverage thresholds from the 104 GLM-authored
concept-requirements cards so the curriculum_design loop (Phase 1+) can flag
concepts that are too thin (too few objectives), too broad (too many
objectives), or incomplete (too few objectives or anchors).

Mirrors ``calibration.rubric_floors``: pure threshold math here, live card
loading in ``thresholds_run``. ``promote=True`` marks the thresholds
load-bearing (advisory otherwise).

exclude-low guard (like ``rubric_floors.exclude_below``): the 104 cards are
GLM-authored, not an oracle. A degenerate concept (0 objectives or 0 anchors)
would drag every floor down, so degenerate concepts are EXCLUDED from the
distribution and listed in ``excluded``. Phase 0 deliberately ignores
anchor-overlap as a merge signal: anchors are inflected surface forms with ~0
overlap by construction, and matching them loosely is noise. Only
thinness / too-broad / coverage thresholds are derived here. See
``plans/2026-06-21-curriculum-design-build.md`` Phase 0.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from pydantic import BaseModel, Field

from lesson_builder.pipeline.concept_requirements import ConceptRequirements

K_CURRICULUM_THRESHOLDS_RELATIVE_PATH = Path("data") / "calibration" / "curriculum_thresholds.json"
K_CURRICULUM_THRESHOLDS_PATH = (
    Path(__file__).resolve().parents[4] / K_CURRICULUM_THRESHOLDS_RELATIVE_PATH
)


class DistributionStats(BaseModel):
    """Min / median / max for a count distribution across kept concepts."""

    minimum: int
    median: float
    maximum: int


class CoverageFloor(BaseModel):
    """Minimum objectives + anchors for a concept to count as 'complete'."""

    min_objectives: int
    min_anchors: int


class CurriculumThresholds(BaseModel):
    """Derived thinness / too-broad / coverage thresholds over the concept corpus.

    ``thinness_floor`` is the minimum objective count across kept concepts; a new
    concept with fewer objectives is suspiciously thin. ``too_broad_ceiling`` is
    the maximum; above it a concept is trying to cover too much.
    ``coverage_floor`` carries the per-axis minima (objectives + anchors) a
    complete concept must meet.
    """

    n_concepts: int = 0
    n_in_benchmark: int = 0
    excluded: list[str] = Field(default_factory=list)
    thinness_floor: int = 0
    too_broad_ceiling: int = 0
    coverage_floor: CoverageFloor = Field(
        default_factory=lambda: CoverageFloor(min_objectives=0, min_anchors=0)
    )
    objective_count_distribution: DistributionStats = Field(
        default_factory=lambda: DistributionStats(minimum=0, median=0.0, maximum=0)
    )
    anchor_count_distribution: DistributionStats = Field(
        default_factory=lambda: DistributionStats(minimum=0, median=0.0, maximum=0)
    )
    per_concept: dict[str, dict[str, int]] = Field(default_factory=dict)
    load_bearing: bool = False


def compute_curriculum_thresholds(
    cards: dict[str, ConceptRequirements],
    *,
    promote: bool = False,
) -> CurriculumThresholds:
    """Derive thinness / too-broad / coverage thresholds from concept cards.

    Degenerate concepts (0 objectives or 0 anchors) are excluded from the
    distribution so a thin outlier cannot drag a floor down; they remain listed
    in ``excluded`` and recorded in ``per_concept``. Raises ``ValueError`` if no
    concept survives exclusion (no basis for a floor).
    """
    per_concept: dict[str, dict[str, int]] = {}
    excluded: list[str] = []
    objective_counts: list[int] = []
    anchor_counts: list[int] = []

    for slug in sorted(cards):
        card = cards[slug]
        n_obj = len(card.objectives)
        n_anchors = len(card.required_anchor_forms)
        per_concept[slug] = {"objectives": n_obj, "anchors": n_anchors}
        if n_obj == 0 or n_anchors == 0:
            excluded.append(slug)
            continue
        objective_counts.append(n_obj)
        anchor_counts.append(n_anchors)

    if not objective_counts:
        raise ValueError(
            f"no concept cleared the exclude-low guard; cannot derive thresholds "
            f"(loaded {len(cards)}, all excluded: {excluded})"
        )

    obj_dist = _distribution(objective_counts)
    anchor_dist = _distribution(anchor_counts)

    return CurriculumThresholds(
        n_concepts=len(cards),
        n_in_benchmark=len(objective_counts),
        excluded=sorted(excluded),
        thinness_floor=obj_dist.minimum,
        too_broad_ceiling=obj_dist.maximum,
        coverage_floor=CoverageFloor(
            min_objectives=obj_dist.minimum,
            min_anchors=anchor_dist.minimum,
        ),
        objective_count_distribution=obj_dist,
        anchor_count_distribution=anchor_dist,
        per_concept=per_concept,
        load_bearing=promote,
    )


def _distribution(counts: list[int]) -> DistributionStats:
    return DistributionStats(
        minimum=min(counts),
        median=round(statistics.median(counts), 2),
        maximum=max(counts),
    )


def save_curriculum_thresholds(
    thresholds: CurriculumThresholds,
    *,
    path: Path | None = None,
) -> Path:
    path = Path(path) if path else K_CURRICULUM_THRESHOLDS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(thresholds.model_dump(), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def load_curriculum_thresholds(path: Path | None = None) -> CurriculumThresholds:
    path = Path(path) if path else K_CURRICULUM_THRESHOLDS_PATH
    if not path.exists():
        return CurriculumThresholds()
    return CurriculumThresholds.model_validate(json.loads(path.read_text(encoding="utf-8")))


def curriculum_thresholds_path_for_repo(repo_root: Path) -> Path:
    """Resolve the curriculum-thresholds file for a specific repo root."""
    return Path(repo_root) / K_CURRICULUM_THRESHOLDS_RELATIVE_PATH


__all__ = [
    "CoverageFloor",
    "CurriculumThresholds",
    "DistributionStats",
    "K_CURRICULUM_THRESHOLDS_PATH",
    "compute_curriculum_thresholds",
    "curriculum_thresholds_path_for_repo",
    "load_curriculum_thresholds",
    "save_curriculum_thresholds",
]
