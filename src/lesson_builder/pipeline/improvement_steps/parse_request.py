"""Entry point: ``parse_request``.

Parses free-text improvement requests into structured ``ImprovementSpec``
objects. When an LLM agent is available it drives the parsing; otherwise a
deterministic keyword-based parser extracts the operation.

Output:
- ``ImprovementSpec`` with ``operation`` (add_exercises / add_explanation /
  improve), ``target_slug``, ``count``, ``bloom_level``, ``target_content``,
  ``confidence``, ``interpretation_summary``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel

ImprovementOperation = Literal["add_exercises", "add_explanation", "improve"]
ParseConfidence = Literal["high", "low"]

K_KEYWORDS_ADD_EXERCISES = ("add exercise", "add exercises", "more exercise", "harder exercise", "practice")
K_KEYWORDS_ADD_EXPLANATION = ("add explanation", "more explanation", "explain", "add example")
K_KEYWORDS_IMPROVE = ("improve", "fix", "rewrite", "update", "change")


class ImprovementSpec(BaseModel):
    """Structured improvement request parsed from free text or CLI flags."""

    operation: ImprovementOperation
    target_slug: str | None = None
    count: int = 1
    bloom_level: str | None = None
    target_content: str | None = None
    objective_id: str | None = None
    confidence: ParseConfidence = "high"
    interpretation_summary: str = ""


def parse_request(text: str, *, known_slugs: list[str] | None = None) -> ImprovementSpec:
    """Parse free-text improvement request into an ``ImprovementSpec``.

    Deterministic keyword-based parser. When an LLM agent is available it can
    be injected via ``parse_request_with_agent`` for richer interpretation.
    """
    lowered = text.lower().strip()
    known = known_slugs or []

    operation = _detect_operation(lowered)
    target_slug = _detect_slug(lowered, known)
    count = _detect_count(lowered)
    bloom_level = _detect_bloom(lowered)
    target_content = _detect_content(text, known)

    confidence: ParseConfidence = "high" if target_slug else "low"
    summary = _summarize(operation, target_slug, count, bloom_level, target_content)

    return ImprovementSpec(
        operation=operation,
        target_slug=target_slug,
        count=count,
        bloom_level=bloom_level,
        target_content=target_content,
        confidence=confidence,
        interpretation_summary=summary,
    )


def _summarize(
    operation: str,
    target_slug: str | None,
    count: int,
    bloom_level: str | None,
    target_content: str | None,
) -> str:
    """Render the parsed spec as a natural-language request sentence.

    Downstream author prompts (revise_lesson, add_explanation) embed this as the
    human-readable ask, so a real sentence steers the model better than a terse
    ``operation=..., target=...`` machine string.
    """
    where = f"the '{target_slug}' lesson" if target_slug else "the requested lesson"
    about = f" about {target_content}" if target_content else ""
    bloom = f" at the {bloom_level} bloom level" if bloom_level else ""
    if operation == "add_exercises":
        return f"Add {count} exercise(s){about}{bloom} to {where}."
    if operation == "add_explanation":
        return f"Add a clarifying explanation{about} to {where}."
    if operation == "improve":
        focus = f", focusing on {target_content}" if target_content else ""
        return f"Improve {where}{focus}{bloom}."
    return f"Apply '{operation}' to {where}{about}{bloom}."


def parse_request_with_agent(
    text: str,
    agent: Any,
    *,
    known_slugs: list[str] | None = None,
) -> ImprovementSpec:
    """Parse using an LLM agent for richer interpretation.

    Falls back to the deterministic parser if the agent fails.
    """
    try:
        return _agent_parse(text, agent, known_slugs or [])
    except Exception:
        return parse_request(text, known_slugs=known_slugs)


def _agent_parse(text: str, agent: Any, known_slugs: list[str]) -> ImprovementSpec:
    """Use the agent's structured output to parse the request."""
    slug_list = ", ".join(known_slugs[:50]) if known_slugs else "(none)"
    prompt = (
        f"Parse this lesson improvement request into a structured spec.\n\n"
        f"Request: {text}\n\n"
        f"Known lesson slugs: {slug_list}\n\n"
        f"Determine the operation (add_exercises, add_explanation, improve), "
        f"the target lesson slug from the known list (or null if ambiguous), "
        f"how many items to add (default 1), the bloom level (remember, understand, "
        f"apply, analyze, or null), and any target content word.\n"
    )
    result = agent.structured(ImprovementSpec).invoke(prompt)
    if isinstance(result, ImprovementSpec):
        return result
    return ImprovementSpec.model_validate(result)


def _detect_operation(lowered: str) -> ImprovementOperation:
    # Normalize digits between words: "add 2 exercises" -> "add exercises"
    normalized = re.sub(r"\b\d+\s+", "", lowered)
    if any(kw in normalized for kw in K_KEYWORDS_ADD_EXERCISES):
        return "add_exercises"
    if any(kw in normalized for kw in K_KEYWORDS_ADD_EXPLANATION):
        return "add_explanation"
    if any(kw in normalized for kw in K_KEYWORDS_IMPROVE):
        return "improve"
    return "improve"


def _detect_slug(lowered: str, known: list[str]) -> str | None:
    normalized_text = _normalize_phrase(lowered)
    text_tokens = set(normalized_text.split())
    best_slug: str | None = None
    best_overlap = 0
    best_score = 0.0
    for slug in known:
        slug_phrase = _normalize_phrase(slug.replace("_", " "))
        if slug in lowered or slug_phrase in normalized_text:
            return slug
        slug_tokens = slug_phrase.split()
        if not slug_tokens:
            continue
        overlap = len(text_tokens & set(slug_tokens))
        score = overlap / len(slug_tokens)
        minimum_overlap = min(2, len(slug_tokens))
        if overlap >= minimum_overlap and (
            overlap > best_overlap or (overlap == best_overlap and score > best_score and best_slug is None)
        ):
            best_slug = slug
            best_overlap = overlap
            best_score = score
    if best_overlap >= 2 and best_score >= 0.5:
        return best_slug
    return None


def _detect_count(lowered: str) -> int:
    match = re.search(r"(\d+)\s+(?:exercise|exercises|example|examples|item|items)", lowered)
    if match:
        return int(match.group(1))
    if "two" in lowered:
        return 2
    if "three" in lowered:
        return 3
    return 1


def _detect_bloom(lowered: str) -> str | None:
    for level in ("remember", "understand", "apply", "analyze"):
        if level in lowered:
            return level
    return None


def _detect_content(text: str, known: list[str] | None = None) -> str | None:
    """Extract a target content word (e.g. 'fordi', 'word order')."""
    lowered = text.lower()
    # Capture content after "on/about" until "for <slug>" or end of string
    match = re.search(r"(?:on|about)\s+(.+?)(?:\s+for\s+|,|$)", lowered)
    if match:
        return match.group(1).strip()
    return None


def _normalize_phrase(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


__all__ = [
    "ImprovementOperation",
    "ImprovementSpec",
    "ParseConfidence",
    "parse_request",
    "parse_request_with_agent",
]
