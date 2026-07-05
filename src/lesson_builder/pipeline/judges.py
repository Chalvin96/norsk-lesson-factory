"""Reviewer-backed judge producers.

Each producer builds a prompt over the lesson, asks ``reviewer()`` for
structured output validating against the judge's existing ``*Review`` schema,
and returns ``model_dump(mode="json")``. On any known LLM failure it returns
``None`` -- the judge is simply absent for that run and the advisory fold is
skipped. Producers NEVER raise into the graph.

All issue-list output is advisory; the load-bearing LLM layer is the rubric-floor
check over the pedagogy scores (``calibration/rubric_floors.py``), not here.

Task C: ``default_judge`` records which surfaces failed and why in an
``_llm_status`` entry so a reviewer outage is surfaced first-class (the graph
stores it in ``LessonQAState.llm_status`` and includes it in the human-gate
payload). ``noop_judge`` produces no status (no failure).
"""

from __future__ import annotations

import json
import os
import statistics
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from lesson_builder.pipeline.agents import reviewer
from lesson_builder.pipeline.checks.validators.naturalness import NaturalnessReview
from lesson_builder.pipeline.checks.validators.objective_alignment import (
    ObjectiveAlignmentReview,
)
from lesson_builder.pipeline.checks.validators.pedagogy import PedagogyReview
from lesson_builder.pipeline.llm.exceptions import (
    BackendDownException,
    LlmParseException,
    LlmQuotaException,
)
from lesson_builder.pipeline.style_anchors import REGISTER_POLICY

K_STYLE_GUIDE_PATH = Path(__file__).resolve().parents[3] / "docs" / "terminology-style-guide.md"

_LLM_FAILURES = (BackendDownException, LlmQuotaException, LlmParseException)

# Map exception type -> llm_status failure kind (Task C).
K_LLM_STATUS_BACKEND_DOWN = "backend_down"
K_LLM_STATUS_QUOTA = "quota"
K_LLM_STATUS_PARSE = "parse"

# Judge surface names used as llm_status keys.
K_SURFACE_PEDAGOGY = "pedagogy"
K_SURFACE_OBJECTIVE_ALIGNMENT = "objective_alignment"
K_SURFACE_ANSWER = "answer"
K_SURFACE_NATURALNESS = "naturalness"

# Median-of-N reviewer scoring (WI 19). Single-run rubric scores swing +/-3 vs
# floors of 2-4; sampling the reviewer N times and taking the per-axis MEDIAN
# smooths that noise. Default N=1 = a single call = no behavior change. Set the
# env var to an ODD number (3 recommended) to enable smoothing:
#     NORSK_REVIEWER_SAMPLES=3
K_REVIEWER_SAMPLE_ENV = "NORSK_REVIEWER_SAMPLES"
K_REVIEWER_SAMPLE_DEFAULT = 1
K_PEDAGOGY_SCORE_AXES: tuple[str, ...] = (
    "on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth",
)


def _reviewer_sample_count() -> int:
    """Resolve N from the env; default 1, floor 1, non-int -> 1."""
    raw = os.environ.get(K_REVIEWER_SAMPLE_ENV)
    if raw is None:
        return K_REVIEWER_SAMPLE_DEFAULT
    try:
        n = int(raw)
    except ValueError:
        return K_REVIEWER_SAMPLE_DEFAULT
    return n if n >= 1 else K_REVIEWER_SAMPLE_DEFAULT


def median_pedagogy_review(reviews: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Merge N validated pedagogy-review dicts into one with per-axis MEDIAN
    scores; summary/passes/issues are taken from the first sample. A single
    review is returned unchanged (N=1 no-op); an empty list returns None.

    Uses statistics.median_low so the result is always an actual sampled integer
    score (valid for the 0-5 axis constraint) with no rounding -- for the intended
    odd N this equals the true median."""
    if not reviews:
        return None
    if len(reviews) == 1:
        return reviews[0]
    merged = deepcopy(reviews[0])
    for axis in K_PEDAGOGY_SCORE_AXES:
        values = [
            int(r["scores"][axis])
            for r in reviews
            if isinstance(r.get("scores"), dict) and axis in r["scores"]
        ]
        if values:
            merged["scores"][axis] = int(statistics.median_low(values))
    return merged


def _run_pedagogy_surface(
    lesson: dict[str, Any], agent: Any | None
) -> tuple[dict[str, Any] | None, str | None]:
    """Run the pedagogy reviewer N times (N=_reviewer_sample_count()) and return
    the per-axis median review. Status is reported only when EVERY sample failed
    (parity with a single _run_surface call at N=1)."""
    reviews: list[dict[str, Any]] = []
    last_status: str | None = None
    for _ in range(_reviewer_sample_count()):
        review, status = _run_surface(
            K_SURFACE_PEDAGOGY, PedagogyReview, _pedagogy_prompt, lesson, agent
        )
        if review is not None:
            reviews.append(review)
        elif status is not None:
            last_status = status
    merged = median_pedagogy_review(reviews)
    return merged, (last_status if merged is None else None)


class AnswerItem(BaseModel):
    id: str
    answer: Any


class AnswerReview(BaseModel):
    """The reviewer's OWN answers to the blinded exercises (not a verdict)."""

    answers: list[AnswerItem]


def _structured(agent: Any, schema: type[BaseModel], prompt: str) -> dict[str, Any] | None:
    try:
        result = agent.structured(schema).invoke(prompt)
    except _LLM_FAILURES:
        return None
    return cast("dict[str, Any]", result.model_dump(mode="json"))


def pedagogy_review(lesson: dict[str, Any], *, agent: Any | None = None) -> dict[str, Any] | None:
    return _structured(agent or reviewer(), PedagogyReview, _pedagogy_prompt(lesson))


def objective_alignment_review(
    *, lesson: dict[str, Any], agent: Any | None = None
) -> dict[str, Any] | None:
    return _structured(agent or reviewer(), ObjectiveAlignmentReview, _alignment_prompt(lesson))


def answer_review(*, lesson: dict[str, Any], agent: Any | None = None) -> dict[str, Any] | None:
    return _structured(agent or reviewer(), AnswerReview, _answer_prompt(lesson))


def naturalness_review(lesson: dict[str, Any], *, agent: Any | None = None) -> dict[str, Any] | None:
    return _structured(agent or reviewer(), NaturalnessReview, _naturalness_prompt(lesson))


def default_judge(lesson: dict[str, Any], *, agent: Any | None = None) -> dict[str, dict[str, Any] | None]:
    """Run all four producers concurrently; return the four state keys.

    Task D: pedagogy/objective_alignment/answer/naturalness run in a
    ``ThreadPoolExecutor`` (one thread per producer) so the panel latency is the
    max surface latency, not the sum. Each surface preserves the existing
    ``None``-on-failure behavior.

    Task C: surfaces a companion ``_llm_status`` mapping recording which surfaces
    failed and why (backend_down / quota / parse). The graph stores this in
    ``LessonQAState.llm_status`` and surfaces it at the human gate.
    """
    surfaces: dict[str, dict[str, Any] | None] = {}
    statuses: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(_run_pedagogy_surface, lesson, agent): "pedagogy_review",
            pool.submit(
                _run_surface,
                K_SURFACE_OBJECTIVE_ALIGNMENT,
                ObjectiveAlignmentReview,
                _alignment_prompt,
                lesson,
                agent,
            ): "objective_alignment_review",
            pool.submit(
                _run_surface,
                K_SURFACE_ANSWER,
                AnswerReview,
                _answer_prompt,
                lesson,
                agent,
            ): "answer_review",
            pool.submit(
                _run_surface,
                K_SURFACE_NATURALNESS,
                NaturalnessReview,
                _naturalness_prompt,
                lesson,
                agent,
            ): "naturalness_review",
        }
        for future, state_key in futures.items():
            review, status = future.result()
            surfaces[state_key] = review
            if status is not None:
                statuses[K_SURFACE_BY_STATE_KEY[state_key]] = status
    if statuses:
        surfaces["_llm_status"] = statuses
    return surfaces


# Reverse lookup: state key -> surface name (for llm_status keys).
K_SURFACE_BY_STATE_KEY: dict[str, str] = {
    "pedagogy_review": K_SURFACE_PEDAGOGY,
    "objective_alignment_review": K_SURFACE_OBJECTIVE_ALIGNMENT,
    "answer_review": K_SURFACE_ANSWER,
    "naturalness_review": K_SURFACE_NATURALNESS,
}


def _run_surface(
    surface: str,
    schema: type[BaseModel],
    prompt_builder: Any,
    lesson: dict[str, Any],
    agent: Any | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run one judge surface, capturing the failure kind (Task C).

    Calls the agent's structured-output path directly (rather than going through
    the public ``pedagogy_review``/``objective_alignment_review``/``answer_review``
    producers, which swallow ``_LLM_FAILURES`` and return ``None``) so the failure
    kind is visible to the judge panel. Returns ``(review_or_None,
    failure_kind_or_None)``.

    The public producers keep their historical ``None``-on-failure contract for
    external callers (e.g. the rejudge seam); the panel goes through here so it
    can surface outages first-class instead of silently reading as "clean."
    """
    effective_agent = agent or reviewer()
    prompt = prompt_builder(lesson)
    try:
        result = effective_agent.structured(schema).invoke(prompt)
    except _LLM_FAILURES as exc:
        return None, _llm_status_kind(exc)
    return result.model_dump(mode="json"), None


def _llm_status_kind(exc: Exception) -> str:
    if isinstance(exc, BackendDownException):
        return K_LLM_STATUS_BACKEND_DOWN
    if isinstance(exc, LlmQuotaException):
        return K_LLM_STATUS_QUOTA
    if isinstance(exc, LlmParseException):
        return K_LLM_STATUS_PARSE
    return "unknown"


def noop_judge(lesson: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    """Offline/test default: no reviews produced (advisory fold skipped)."""
    del lesson
    return {
        "pedagogy_review": None,
        "objective_alignment_review": None,
        "answer_review": None,
        "naturalness_review": None,
    }


# Ported verbatim from the phase-1 reviewer (scripts/phase1_pedagogy_review.py) — phase 1
# is the quality standard; the LangGraph rewrite must be at-or-above it. The CALIBRATION
# block + per-axis guidance are load-bearing: without them the reviewer turns harsh and
# over-flags `complete`/`presentable` (a corpus audit showed 80/104 false-blocked).
_PEDAGOGY_PROMPT_PREFIX = """\
You are a fair, calibrated reviewer of Norwegian (Bokmal) grammar lessons for adult English
speakers. SCORE one generated lesson against the rubric and list its REAL defects, each with
evidence you can quote from the lesson. Do not invent problems; missing a real defect and inventing
a fake one are equally bad.

CALIBRATION (important): the hand-authored golden lessons are the top of the scale. A careful,
professionally written lesson scores 5 on most axes and 4 on the rest. Reserve a score of 0-2 for a
genuine, demonstrable failure on that axis — not a minor imperfection or a stylistic preference. If
your scores would rate a competent human-written lesson as mediocre, you are being too harsh;
recalibrate upward. Judge the lesson on its own concept; do not reward table count or block volume.

This is NOT a schema review and NOT an answer-key review; those already happened. If a lesson brief
or derived scope card is provided, treat its deferred topics as out of scope: never lower the
"complete" score for omitting a deferred topic.

Rubric — score each axis 0-5 (5 = golden-quality, 4 = good, 3 = acceptable, 0-2 = a real failure):
- on_concept: teaches what the title/goal promises; no scope bleed into a neighbor lesson.
- complete: teaches, not just lists. When the lesson names endings/classes/rules, it should also say
  which cases they apply to (a brief explanation + an example is enough). Only score low if a rule is
  a bare list with no explanation at all (e.g. "past tense ends in -et, -te, or -de" and nothing more).
- bokmal: Bokmal, no Nynorsk. Only lower this if you can QUOTE the specific non-Bokmal form AND give
  its correct Bokmal form. If every form is valid standard Bokmal, score 5. Never score bokmal below
  4 without citing a concrete wrong form — do not guess. Watch for fabricated inflections that look
  plausible but are not real Bokmal (e.g. 'godere' for 'bedre', 'storere' for 'storre', 'setts' for 'sett').
- sequencing: the prose teaches the pattern (with a why and an example), not only the exercises.
  Gloss metalinguistic terms on first use. Minor terseness is a 4, not a 2.
- presentable: learner-facing prose written TO the learner about the language, not ABOUT the course.
  Flag CEFR-level talk in learner text (A1/A2/B1/B2, "at this level", "a real A2 challenge") and
  internal/meta strings.
- answerable: every choose/recall_fill has a carrier stem, exactly one correct answer, plausible
  learner-error distractors, and a real explanation. A distractor that is valid under the stated
  prompt/context is a serious answerability defect, even if the lesson prefers another form. A
  "real explanation" must teach the contrast, not just bless the answer: for an exercise with
  competing options it should say why the key distractor(s) are wrong, not only why the correct
  option is right. An explanation that justifies only the correct answer and ignores the
  distractors is a P2 answerability weakness (unless each option already carries its own rationale).
- depth: includes meaningful production or diagnosis (build/find_fix/judge), not recognition-only.

Defects to check for (flag ONLY if actually present, with quoted evidence):
- Enumeration with NO explanation at all: a pattern/ending/class named and never explained.
- Rushed teaching: a rule stated with no example and no "why".
- CEFR-level leakage in learner-facing text.
- A non-Bokmal or wrong Norwegian form you can name, with its correct form.
- A valid alternate Bokmal form, word order, spelling/pronunciation variant, or translation used as
  a wrong choose/recall_fill option (e.g. feminine/common doera/doeren, object han/ham, synthetic vs
  mer/mest comparison, det er vs det finnes, skal vs kommer til aa, neutral vs contrastive word order).
- A find_fix item that targets a sentence which is grammatical but less preferred, more formal,
  contrastive, stylistically awkward, or only wrong for an unstated meaning. find_fix must diagnose
  an objectively wrong replaceable token; do not use it for optional style/register improvements.
- A judge item that marks a valid fragment/variant/contrastive order simply false when the real
  issue is "not a complete main clause", "not the requested meaning", or "not the pattern taught here".
- A match_pairs/categorize item whose buckets overlap under learner-level reasoning.
- A malformed completion stem where the learner cannot see the insertion point (e.g. a missing blank).
- Ungrammatical Norwegian in a normal teaching example, table answer, match item, or correct option.

Severity: P0 teaches a wrong rule or misleads; P1 a serious gap likely to confuse; P2 a real but
minor weakness; P3 polish. Be conservative with P0/P1 — reserve them for issues that genuinely hurt
a learner. Give an issue for each real defect with quoted evidence and a specific, applicable fix.
"""


def _pedagogy_prompt(lesson: dict[str, Any]) -> str:
    return f"{_PEDAGOGY_PROMPT_PREFIX}\nLESSON:\n{json.dumps(lesson, ensure_ascii=False)}"


def _alignment_prompt(lesson: dict[str, Any]) -> str:
    objectives = lesson.get("objectives", [])
    elements = [e for e in lesson.get("elements", []) if e.get("element_kind") == "exercise"]
    return (
        "Judge whether each exercise actually teaches/tests the objective it claims "
        "(objective_id). This is SEMANTIC alignment, not an id check. For each genuine "
        "mismatch emit an issue keyed by objective_id with severity P1..P3, message, evidence "
        "(quote), and fix. passed=true only if every exercise serves its objective.\n\n"
        f"OBJECTIVES:\n{json.dumps(objectives, ensure_ascii=False)}\n\n"
        f"EXERCISES:\n{json.dumps(elements, ensure_ascii=False)}"
    )


def _answer_prompt(lesson: dict[str, Any]) -> str:
    """Blinded view: prompts only, NO answer keys, so the reviewer answers independently."""
    blinded = []
    for e in lesson.get("elements", []):
        if e.get("element_kind") != "exercise":
            continue
        blinded.append({"id": e.get("id"), "operation": e.get("operation"), "prompt": e.get("prompt")})
    return (
        "For each exercise below, give the single answer a competent learner would give, "
        "based ONLY on the visible prompt. Do not guess wildly; if a prompt is ambiguous or "
        "unanswerable, answer with your best single interpretation. Return one {id, answer} "
        "per exercise.\n\n"
        f"EXERCISES:\n{json.dumps(blinded, ensure_ascii=False)}"
    )


@lru_cache(maxsize=1)
def _load_active_rules_table() -> str:
    """Extract the 'Active rules' section from docs/terminology-style-guide.md.

    Inject only the active pipe table. The fenced ``bans`` block is a
    deterministic-linter surface, not LLM reviewer context. Returns an empty
    string when the file is unavailable so the reviewer still runs without the
    table.
    """
    if not K_STYLE_GUIDE_PATH.exists():
        return ""
    text = K_STYLE_GUIDE_PATH.read_text(encoding="utf-8")
    start_marker = "## Active rules"
    start = text.find(start_marker)
    if start < 0:
        return ""
    table_start = text.find("| Concept |", start)
    if table_start < 0:
        return ""
    end = table_start
    table_lines: list[str] = []
    while end < len(text):
        next_end = text.find("\n", end)
        if next_end < 0:
            next_end = len(text)
            line_end = next_end
        else:
            line_end = next_end + 1
        line = text[end:next_end]
        if not line.startswith("|"):
            break
        table_lines.append(text[end:line_end])
        end = line_end
    return f"{start_marker}\n\n{''.join(table_lines).strip()}".strip()


_NATURALNESS_PROMPT_PREFIX = f"""\
You are a NATIVE Norwegian (Bokmal) speaker AND a CEFR-trained language teacher reviewing a \
Norwegian lesson for adult English speakers. Your lens is NATURALNESS and REGISTER: does the \
Norwegian read like something a native speaker would actually say/write, and is the language \
and terminology appropriate for the lesson's CEFR level?

Score three axes 1-5 (5 = consistently natural and level-appropriate; 1 = frequently stilted or \
register-inappropriate):
- idiomatic_phrasing: natural word order, adverb/particle placement, collocation, and message \
register/punctuation. Flag stilted or unnatural phrasing. Prefer the neutral default: e.g. \
"heller ikke" (not "ikke ... heller") as the standard word order; watch for awkward collocations \
and stilted message/e-mail register (greeting, line-break, sign-off, punctuation).
- register_appropriateness: is the grammar terminology and description register appropriate for \
the lesson's cefr_level? Apply this standard, flagging deviations from it: {REGISTER_POLICY}
- terminology_consistency: does the lesson use the pinned house-style terms consistently and \
level-appropriately vs the Active rules table below? Score 5 when every concept uses the pinned \
term (or an "Also OK" variant at the right level); score lower when a concept uses a non-pinned \
label, drifts between two labels for one concept, or uses a term above/below its level note. \
This axis is advisory (issues stay advisory; the score is the calibratable signal).

For each real defect, emit an issue with: unit_id (the element/exercise id, or "" for whole-lesson \
prose), severity (P1 serious / P2 real but minor / P3 polish), and a message naming the specific \
phrase and the natural/level-appropriate alternative. Do not invent problems; missing a real defect \
and inventing a fake one are equally bad.

This is NOT a grammar-correctness review (Bokmal forms are checked elsewhere) and NOT an \
answer-key review. Judge only naturalness, register appropriateness, and terminology consistency \
for the stated cefr_level.
"""


def _naturalness_prompt(lesson: dict[str, Any]) -> str:
    rules = _load_active_rules_table()
    rules_block = f"\n{rules}\n" if rules else ""
    return f"{_NATURALNESS_PROMPT_PREFIX}{rules_block}\nLESSON:\n{json.dumps(lesson, ensure_ascii=False)}"


__all__ = [
    "AnswerReview",
    "AnswerItem",
    "pedagogy_review",
    "objective_alignment_review",
    "answer_review",
    "naturalness_review",
    "default_judge",
    "noop_judge",
    "median_pedagogy_review",
    "K_SURFACE_PEDAGOGY",
    "K_SURFACE_OBJECTIVE_ALIGNMENT",
    "K_SURFACE_ANSWER",
    "K_SURFACE_NATURALNESS",
    "K_SURFACE_BY_STATE_KEY",
    "K_PEDAGOGY_SCORE_AXES",
    "K_REVIEWER_SAMPLE_ENV",
    "K_REVIEWER_SAMPLE_DEFAULT",
    "K_LLM_STATUS_BACKEND_DOWN",
    "K_LLM_STATUS_QUOTA",
    "K_LLM_STATUS_PARSE",
]
