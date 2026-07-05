"""Entry point: ``author_course_map`` / ``author_concept``.

The writer stage of the curriculum_design loop (Phase 1). Produces
``ConceptRequirements``-valid ``ConceptDraft`` cards (objectives + bloom +
cefr + anchors + notes) from research notes. The agent is DI-injected
(default: the strong registry ``author()`` from ``pipeline.agents``); tests
inject a fake so the suite stays offline. ``prior_signals`` lets the loop feed
the previous round's deterministic signals back into the prompt so the next
author pass revises thin/too-broad/coverage problems rather than re-drafting
blind.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from lesson_builder.pipeline.agents import author
from lesson_builder.pipeline.curriculum_design.models import (
    ConceptDraft,
    CourseMap,
    ResearchNote,
    ReviewSignals,
)


def author_course_map(
    notes: Sequence[ResearchNote],
    *,
    agent: Any | None = None,
    prior_signals: ReviewSignals | None = None,
) -> CourseMap:
    """Author a full ``CourseMap`` from resolved research notes.

    ``agent`` defaults to the strong registry ``author()``; inject a fake for
    tests. ``prior_signals`` (from the previous review round) is folded into the
    prompt so the next pass revises thin/too-broad/coverage problems. The agent
    returns structured JSON validated against ``CourseMap``; every concept is a
    valid ``ConceptRequirements`` card by construction.
    """
    resolved_agent = agent if agent is not None else author()
    prompt = _build_course_map_prompt(notes, prior_signals)
    return resolved_agent.structured(CourseMap).invoke(prompt)


def author_concept(
    note_subset: Sequence[ResearchNote],
    *,
    agent: Any | None = None,
    sequence_index: int = 0,
) -> ConceptDraft:
    """Author one ``ConceptDraft`` from a subset of research notes.

    Used when the loop revises a single concept (e.g. a thin/too-broad hit).
    Returns a valid ``ConceptRequirements`` card plus ordering + provenance.
    """
    resolved_agent = agent if agent is not None else author()
    prompt = _build_concept_prompt(note_subset, sequence_index)
    return resolved_agent.structured(ConceptDraft).invoke(prompt)


def _build_course_map_prompt(
    notes: Sequence[ResearchNote],
    prior_signals: ReviewSignals | None,
) -> str:
    notes_block = _format_notes(notes)
    revision_block = _format_revision_guidance(prior_signals)
    return (
        "You are designing a Norwegian (Bokmål) language curriculum from research notes.\n"
        f"Research notes:\n{notes_block}\n\n"
        f"{revision_block}"
        "Produce a CourseMap: an ordered list of ConceptDrafts.\n"
        "Each ConceptDraft is a valid ConceptRequirements scope card with these fields:\n"
        '- slug: snake_case concept identifier\n'
        '- cefr_level: one of A1, A2, B1, B2, C1, C2\n'
        '- objectives: non-empty list of {id, statement, bloom_targets}; bloom_targets '
        'values must be one of: remember, understand, apply, analyze\n'
        '- required_anchor_forms: list of Norwegian anchor phrases\n'
        '- notes: teaching guidance\n'
        '- sequence_index: 0-based position in the course\n'
        '- source_notes: list of research claims this concept draws on\n\n'
        "Rules:\n"
        "- Cover every research claim across the concept set (no claim left unaddressed).\n"
        "- Each concept needs enough objectives to be teachable (aim for 3+) but not so "
        "many that it becomes unfocused (keep under 5).\n"
        "- Spread concepts across the CEFR range suggested by the notes.\n"
        "- cefr_span: a compact string like 'A1-B1' covering the full range of concepts.\n\n"
        "Respond with ONLY a JSON object matching the CourseMap schema (no markdown fences).\n"
        ' Example: {"concepts": [{"slug": "...", "cefr_level": "A1", "objectives": [...], '
        '"required_anchor_forms": [...], "notes": "...", "sequence_index": 0, '
        '"source_notes": [...]}], "cefr_span": "A1-B1"}'
    )


def _build_concept_prompt(
    note_subset: Sequence[ResearchNote],
    sequence_index: int,
) -> str:
    notes_block = _format_notes(note_subset)
    return (
        "You are authoring one concept for a Norwegian (Bokmål) language curriculum.\n"
        f"Research notes for this concept:\n{notes_block}\n\n"
        f"sequence_index: {sequence_index}\n\n"
        "Produce one ConceptDraft (a ConceptRequirements scope card + ordering + provenance).\n"
        "Fields: slug, cefr_level (A1-C2), objectives (non-empty list of {id, statement, "
        "bloom_targets}), required_anchor_forms, notes, sequence_index, source_notes.\n"
        "bloom_targets values: remember, understand, apply, analyze.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no prose)."
    )


def _format_notes(notes: Sequence[ResearchNote]) -> str:
    if not notes:
        return "(none)"
    lines: list[str] = []
    for i, note in enumerate(notes):
        lines.append(
            f"  {i}. claim: {note.claim}\n"
            f"     quote: {note.quote!r}\n"
            f"     url: {note.url}"
        )
    return "\n".join(lines) + "\n"


def _format_revision_guidance(signals: ReviewSignals | None) -> str:
    if signals is None or signals.is_clean():
        return ""
    parts: list[str] = ["Revision guidance from the previous review round:"]
    if signals.thinness_hits:
        parts.append(
            f"- THIN concepts (too few objectives, add more): {json.dumps(signals.thinness_hits)}"
        )
    if signals.too_broad_hits:
        parts.append(
            f"- TOO BROAD concepts (too many objectives, split or trim): "
            f"{json.dumps(signals.too_broad_hits)}"
        )
    if signals.coverage_gaps:
        parts.append(
            f"- COVERAGE GAPS (cited goals with no concept, add concepts): "
            f"{json.dumps(signals.coverage_gaps)}"
        )
    return "\n".join(parts) + "\n\n"


__all__ = ["author_concept", "author_course_map"]
