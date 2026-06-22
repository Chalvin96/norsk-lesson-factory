"""Entry point: ``run_curriculum_loop``.

The curriculum_design loop core (Phase 1). Each round: research -> resolve +
drop bad citations -> write/revise map -> review. The loop applies the
deterministic review signals to drive the next round and converges on a
STABLE PASS (signals empty, or no signal fires twice) within ``max_rounds``.

A non-converging loop (signals persist at ``max_rounds``) MUST return
``status="needs_human"`` with the unresolved signals — it NEVER silently
truncates as if done. This mirrors the lesson convergence guard: a stuck loop
escalates to a human instead of burning rounds on no-progress revisions.

All collaborators (research backend, writer/reviewer agents) are DI-injected;
tests inject fakes so the suite stays fully offline. Live web is exercised
only at runtime.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.pipeline.curriculum_design.author import author_course_map
from lesson_builder.pipeline.curriculum_design.models import (
    CourseMap,
    CurriculumLoopResult,
    ReviewSignals,
)
from lesson_builder.pipeline.curriculum_design.research import (
    ResearchBackend,
    researcher,
    resolve_citations,
)
from lesson_builder.pipeline.curriculum_design.review import review_map
from lesson_builder.pipeline.curriculum_design.thresholds import (
    CurriculumThresholds,
    load_curriculum_thresholds,
)

K_CURRICULUM_LOOP_MAX_ROUNDS = 3


def run_curriculum_loop(
    seed: str,
    *,
    research_backend: ResearchBackend | None = None,
    writer_agent: Any | None = None,
    reviewer_agent: Any | None = None,
    thresholds: CurriculumThresholds | None = None,
    max_rounds: int = K_CURRICULUM_LOOP_MAX_ROUNDS,
) -> CurriculumLoopResult:
    """Run the research -> write -> review -> converge loop for ``seed``.

    Each round:
    1. Research: ``researcher(seed, backend=research_backend)`` (hard-fails if
       no backend is wired).
    2. Resolve + drop: ``resolve_citations`` then drop notes whose citation did
       not corroborate (blocks promotion).
    3. Write/revise: ``author_course_map`` with ``prior_signals`` from the
       previous round so the next pass revises deterministic hits.
    4. Review: ``review_map`` against ``thresholds`` and the cited goals
       (derived from resolved note claims).

    Convergence (``status="converged"`` requires CLEAN signals — nothing else):
    - ``status="converged"`` ONLY when ``signals.is_clean()`` (no deterministic hit).
    - ``status="needs_human"`` when deterministic signals persist — either the same
      signal fingerprint repeats across rounds (no progress → escalate early rather
      than burning the budget) or signals are still present at ``max_rounds``. The
      loop NEVER silently truncates a still-dirty map as if done.

    The cited goals passed to review are the claims from the resolved notes
    (research-derived), so coverage_gaps catches any claim no concept addresses.
    """
    if max_rounds < 1:
        raise ValueError(f"max_rounds must be >= 1; got {max_rounds}")
    resolved_thresholds = thresholds if thresholds is not None else load_curriculum_thresholds()

    course_map: CourseMap | None = None
    signals: ReviewSignals | None = None
    seen_fingerprints: set[tuple[str, ...]] = set()

    for round_num in range(1, max_rounds + 1):
        notes = researcher(seed, backend=research_backend)
        resolved_notes = resolve_citations(notes)
        cited_goals = [note.claim for note in resolved_notes if note.resolved]
        corroborated = [note for note in resolved_notes if note.resolved]

        course_map = author_course_map(
            corroborated,
            agent=writer_agent,
            prior_signals=signals,
        )
        signals = review_map(
            course_map,
            resolved_thresholds,
            cited_goals,
            agent=reviewer_agent,
        )

        if signals.is_clean():
            return CurriculumLoopResult(
                status="converged",
                course_map=course_map,
                signals=signals,
                rounds=round_num,
            )

        fingerprint = _fingerprint(signals)
        if fingerprint in seen_fingerprints:
            # No-progress: the same deterministic signal set repeated. Further
            # rounds will not help; escalate now rather than burning the budget.
            assert course_map is not None and signals is not None
            return CurriculumLoopResult(
                status="needs_human",
                course_map=course_map,
                signals=signals,
                rounds=round_num,
            )
        seen_fingerprints.add(fingerprint)

    assert course_map is not None and signals is not None
    return CurriculumLoopResult(
        status="needs_human",
        course_map=course_map,
        signals=signals,
        rounds=max_rounds,
    )


def _fingerprint(signals: ReviewSignals) -> tuple[str, ...]:
    """A stable, order-independent fingerprint of the deterministic signal set."""
    return tuple(sorted(signals.deterministic_hits()))


__all__ = ["K_CURRICULUM_LOOP_MAX_ROUNDS", "run_curriculum_loop"]
