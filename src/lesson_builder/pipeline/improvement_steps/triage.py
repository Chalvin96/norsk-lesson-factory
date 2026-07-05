"""Entry point: ``triage_request``.

LLM-grounded triage: classify where an improvement/curriculum request belongs by
showing the model the catalog of existing lessons (slug + objective statements)
and asking for a route — patch an existing lesson, split/redirect, or a new
topic. Used by flow-3 (and curriculum drafting) when the target slug is ambiguous
or may be a new topic.

Always human-gated in the graph; this module produces the verdict + evidence the
human gate surfaces. The ``triage_agent`` is injectable so tests run offline.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from lesson_builder.pipeline.agents import author
from lesson_builder.pipeline.improvement_steps.parse_request import ImprovementSpec
from lesson_builder.pipeline.llm.exceptions import (
    BackendDownException,
    LlmParseException,
    LlmQuotaException,
)
from lesson_builder.pipeline.store.index import LessonIndex

TriageVerdict = Literal["exists", "partial", "new"]
ProposedAction = Literal["patch_lesson", "new_topic", "split"]

_LLM_FAILURES = (BackendDownException, LlmQuotaException, LlmParseException)

# Catalog size guard: at ~104 lessons the whole catalog fits one prompt. The cap
# is a backstop so a much larger course degrades gracefully rather than blowing
# the context window (sqlite_vec/embedding retrieval is the documented scale-path).
K_CATALOG_MAX_OBJECTIVES = 4

K_ROUTE_TO_VERDICT: dict[ProposedAction, TriageVerdict] = {
    "patch_lesson": "exists",
    "split": "partial",
    "new_topic": "new",
}


class TriageEvidence(BaseModel):
    slug: str
    unit_id: str
    unit_type: str
    score: float


class TriageResult(BaseModel):
    verdict: TriageVerdict
    evidence: list[TriageEvidence]
    proposed_action: ProposedAction
    rationale: str
    spec: ImprovementSpec


class TriageClassification(BaseModel):
    """The LLM's routing verdict over the lesson catalog."""

    route: ProposedAction
    matched_slug: str | None = None
    rationale: str


def triage_request(
    spec: ImprovementSpec,
    index: LessonIndex,
    *,
    triage_agent: Any | None = None,
) -> TriageResult:
    """Classify where ``spec`` belongs against the catalog of existing lessons.

    Routes: ``patch_lesson`` (fits an existing lesson) / ``split`` (overlaps one
    or more, may need a split/redirect) / ``new_topic`` (no existing lesson fits).
    An empty catalog short-circuits to ``new_topic`` (nothing to match against).
    If the triage agent is unavailable, falls back to ``new_topic`` so the request
    still surfaces for human review rather than raising.
    """
    slugs = index.slugs()
    if not slugs:
        return TriageResult(
            verdict="new",
            evidence=[],
            proposed_action="new_topic",
            rationale="no existing lessons to match against; treating as a new topic",
            spec=spec,
        )

    classification = _classify(triage_agent or author(), spec, _build_catalog(index, slugs))
    if classification is None:
        return TriageResult(
            verdict="new",
            evidence=[],
            proposed_action="new_topic",
            rationale="triage agent unavailable; defaulting to new topic for human review",
            spec=spec,
        )

    matched = classification.matched_slug if classification.matched_slug in slugs else None
    evidence = (
        [TriageEvidence(slug=matched, unit_id="", unit_type="lesson", score=1.0)]
        if matched
        else []
    )
    return TriageResult(
        verdict=K_ROUTE_TO_VERDICT[classification.route],
        evidence=evidence,
        proposed_action=classification.route,
        rationale=classification.rationale,
        spec=spec,
    )


def _classify(
    agent: Any, spec: ImprovementSpec, catalog: str
) -> TriageClassification | None:
    try:
        result = agent.structured(TriageClassification).invoke(_triage_prompt(spec, catalog))
    except _LLM_FAILURES:
        return None
    if isinstance(result, TriageClassification):
        return result
    return TriageClassification.model_validate(result)


def _build_catalog(index: LessonIndex, slugs: list[str]) -> str:
    lines: list[str] = []
    for slug in slugs:
        objectives = [
            unit["text"]
            for unit in index.units_for_slug(slug)
            if unit["unit_type"] == "objective"
        ][:K_CATALOG_MAX_OBJECTIVES]
        summary = "; ".join(objectives) if objectives else "(no objectives indexed)"
        lines.append(f"- {slug}: {summary}")
    return "\n".join(lines)


def _triage_prompt(spec: ImprovementSpec, catalog: str) -> str:
    target = spec.target_slug or "(unspecified)"
    content = spec.target_content or spec.interpretation_summary or spec.operation
    return (
        "You place a lesson-improvement request against an existing Norwegian course.\n"
        "Classify the request into exactly one route:\n"
        "- patch_lesson: the request fits an existing lesson; set matched_slug to it.\n"
        "- split: the request overlaps one or more existing lessons and may need a "
        "split or redirect; set matched_slug to the closest.\n"
        "- new_topic: no existing lesson covers this; matched_slug is null.\n\n"
        f"Request operation: {spec.operation}\n"
        f"Requested target slug: {target}\n"
        f"Requested content: {content}\n\n"
        "Existing lessons (slug: objectives):\n"
        f"{catalog}\n\n"
        "Return the route, matched_slug (or null), and a one-sentence rationale."
    )


__all__ = [
    "ProposedAction",
    "TriageClassification",
    "TriageEvidence",
    "TriageResult",
    "TriageVerdict",
    "triage_request",
]
