"""Entry point: ``CourseMap`` / ``ConceptDraft`` / ``ResearchNote`` / ``ReviewSignals``.

Not checks themselves — the shared Pydantic models the curriculum_design loop
(Phase 1+) passes between its research, author, review, and converge stages.
``ConceptDraft`` extends the Phase-A ``ConceptRequirements`` scope card with
ordering (``sequence_index``) and provenance (``source_notes``) so a draft
remains a valid scope card while carrying loop-internal metadata.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lesson_builder.pipeline.concept_requirements import ConceptRequirements


class ResearchNote(BaseModel):
    """One research claim with its citation: a URL plus the quoted source text.

    ``resolved`` starts ``False`` and is flipped to ``True`` only by
    ``resolve_citations`` once the URL fetches 200 AND the ``quote`` appears in
    the page text. An unresolved note is dropped by the loop (blocks promotion)
    so a resolvable-but-wrong URL can never pass.
    """

    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    url: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    resolved: bool = False


class ConceptDraft(ConceptRequirements):
    """A ``ConceptRequirements`` scope card plus ordering + provenance.

    The loop authors one ``ConceptDraft`` per concept. It IS-A
    ``ConceptRequirements`` (valid card, consumed by cold-author with no shape
    adapter) and ADDS ``sequence_index`` (position in the course) and
    ``source_notes`` (the research claims that motivated it).
    """

    sequence_index: int = Field(ge=0)
    source_notes: list[str] = Field(default_factory=list)


class CourseMap(BaseModel):
    """An ordered set of concept drafts spanning a CEFR range."""

    model_config = ConfigDict(extra="forbid")

    concepts: list[ConceptDraft] = Field(min_length=1)
    cefr_span: str = Field(min_length=1)


class ReviewSignals(BaseModel):
    """Deterministic + advisory signals from reviewing a ``CourseMap``.

    The three deterministic lists are LOAD-BEARING: they drive the loop's
    convergence (mirrors the lesson three-tier trust — deterministic signals
    route, LLM advisory only informs). ``is_clean`` is True when no
    deterministic signal fires; the loop converges on a clean pass.
    """

    model_config = ConfigDict(extra="forbid")

    thinness_hits: list[str] = Field(default_factory=list)
    too_broad_hits: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    advisory: list[str] = Field(default_factory=list)

    def is_clean(self) -> bool:
        """True when no deterministic signal fires (advisory is ignored)."""
        return not (self.thinness_hits or self.too_broad_hits or self.coverage_gaps)

    def deterministic_hits(self) -> list[str]:
        """All deterministic hits flattened (for fingerprinting / escalation)."""
        return [*self.thinness_hits, *self.too_broad_hits, *self.coverage_gaps]


LoopStatus = Literal["converged", "needs_human"]


class CurriculumLoopResult(BaseModel):
    """Outcome of one ``run_curriculum_loop`` invocation.

    ``status="converged"`` means a clean (or stable) pass was reached within
    ``max_rounds``. ``status="needs_human"`` means deterministic signals
    persisted to ``max_rounds`` — the loop NEVER silently truncates as if done;
    the unresolved signals are surfaced for human review.
    """

    model_config = ConfigDict(extra="forbid")

    status: LoopStatus
    course_map: CourseMap
    signals: ReviewSignals
    rounds: int = Field(ge=1)


__all__ = [
    "ConceptDraft",
    "CourseMap",
    "CurriculumLoopResult",
    "LoopStatus",
    "ResearchNote",
    "ReviewSignals",
]
