"""Entry point: ``review_map``.

The reviewer stage of the curriculum_design loop (Phase 1). Mirrors the
lesson three-tier trust: DETERMINISTIC signals are load-bearing (they drive
the loop's convergence), the LLM advisory layer only informs. Loads
``CurriculumThresholds`` (Phase 0) for thinness/too-broad floors.

Deterministic checks:
- thinness: a concept with fewer objectives than ``thinness_floor`` is too thin.
- too_broad: a concept with more objectives than ``too_broad_ceiling`` is too broad.
- coverage_gaps: each cited competence goal must map to >=1 concept; an
  uncovered goal is a gap (a concept "covers" a goal when the goal's text
  appears in the concept's searchable fields).

The LLM advisory is optional (``agent=None`` skips it entirely).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from lesson_builder.pipeline.curriculum_design.models import (
    CourseMap,
    ReviewSignals,
)
from lesson_builder.pipeline.curriculum_design.thresholds import CurriculumThresholds

# Tokens shorter than this are ignored when matching a goal to a concept so
# stopwords like "a", "the", "to" cannot satisfy coverage on their own.
K_COVERAGE_MIN_TOKEN_LENGTH = 3


def review_map(
    course_map: CourseMap,
    thresholds: CurriculumThresholds,
    cited_goals: Sequence[str],
    *,
    agent: Any | None = None,
) -> ReviewSignals:
    """Review a ``CourseMap`` against deterministic thresholds + optional LLM advisory.

    The deterministic signals (thinness, too_broad, coverage_gaps) are
    load-bearing — they drive the loop's convergence. The LLM advisory
    (pedagogy, sequencing, paradigm warnings) is only collected when an
    ``agent`` is supplied and NEVER blocks convergence.
    """
    thinness_hits = _thinness_hits(course_map, thresholds)
    too_broad_hits = _too_broad_hits(course_map, thresholds)
    coverage_gaps = _coverage_gaps(course_map, cited_goals)
    advisory = _llm_advisory(course_map, agent) if agent is not None else []

    return ReviewSignals(
        thinness_hits=thinness_hits,
        too_broad_hits=too_broad_hits,
        coverage_gaps=coverage_gaps,
        advisory=advisory,
    )


def _thinness_hits(course_map: CourseMap, thresholds: CurriculumThresholds) -> list[str]:
    if thresholds.thinness_floor <= 0:
        return []
    hits: list[str] = []
    for concept in course_map.concepts:
        if len(concept.objectives) < thresholds.thinness_floor:
            hits.append(concept.slug)
    return hits


def _too_broad_hits(course_map: CourseMap, thresholds: CurriculumThresholds) -> list[str]:
    if thresholds.too_broad_ceiling <= 0:
        return []
    hits: list[str] = []
    for concept in course_map.concepts:
        if len(concept.objectives) > thresholds.too_broad_ceiling:
            hits.append(concept.slug)
    return hits


def _coverage_gaps(course_map: CourseMap, cited_goals: Sequence[str]) -> list[str]:
    gaps: list[str] = []
    for goal in cited_goals:
        tokens = _significant_tokens(goal)
        if not tokens:
            # A goal with no significant tokens: require exact substring match.
            if not any(_concept_contains_exact(course_map, goal)):
                gaps.append(goal)
            continue
        if not any(_concept_covers_goal(concept, tokens) for concept in course_map.concepts):
            gaps.append(goal)
    return gaps


def _concept_covers_goal(concept: Any, tokens: set[str]) -> bool:
    """A concept covers a goal when ALL significant goal tokens appear in its text."""
    searchable = _concept_searchable_text(concept)
    return all(token in searchable for token in tokens)


def _concept_contains_exact(course_map: CourseMap, goal: str) -> list[bool]:
    return [
        goal.lower() in _concept_searchable_text(concept)
        for concept in course_map.concepts
    ]


def _concept_searchable_text(concept: Any) -> str:
    objectives_text = " ".join(
        stmt for obj in concept.objectives for stmt in (obj.statement, *obj.bloom_targets)
    )
    anchors_text = " ".join(concept.required_anchor_forms)
    return " ".join([
        concept.slug,
        getattr(concept, "notes", ""),
        objectives_text,
        anchors_text,
    ]).lower()


def _significant_tokens(goal: str) -> set[str]:
    return {
        token
        for token in goal.lower().split()
        if len(token.strip()) >= K_COVERAGE_MIN_TOKEN_LENGTH
    }


def _llm_advisory(course_map: CourseMap, agent: Any) -> list[str]:
    """Collect optional LLM advisory findings (never load-bearing)."""
    prompt = _build_advisory_prompt(course_map)
    try:
        raw = agent.invoke(prompt)
    except Exception as exc:  # noqa: BLE001 — advisory only; never break the loop
        return [f"advisory agent unavailable: {type(exc).__name__}"]
    return _parse_advisory(raw)


def _build_advisory_prompt(course_map: CourseMap) -> str:
    concepts_block = json.dumps(
        [
            {
                "slug": c.slug,
                "cefr_level": c.cefr_level,
                "n_objectives": len(c.objectives),
                "notes": c.notes,
            }
            for c in course_map.concepts
        ],
        ensure_ascii=False,
    )
    return (
        "You are reviewing a Norwegian (Bokmål) curriculum map for advisory pedagogy feedback.\n"
        f"Concepts: {concepts_block}\n\n"
        "Return a JSON list of advisory findings (strings). Each finding should be a "
        "single sentence about sequencing, paradigm fit, or pedagogy. Return [] if the "
        "map looks sound. This is advisory only — it does NOT block.\n\n"
        "Respond with ONLY a JSON list (no markdown fences): "
        '["finding 1", "finding 2"]'
    )


def _parse_advisory(raw: str) -> list[str]:
    import json as _json

    try:
        parsed = _json.loads(raw.strip())
    except (ValueError, TypeError):
        return [f"advisory agent returned unparseable output: {raw[:120]!r}"]
    if not isinstance(parsed, list):
        return [f"advisory agent returned non-list: {raw[:120]!r}"]
    return [str(item) for item in parsed]


__all__ = ["review_map"]
