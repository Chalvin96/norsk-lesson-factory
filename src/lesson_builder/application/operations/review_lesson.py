"""Entry points: ``default_judge`` / ``noop_judge`` / ``attempt_review`` — reviewer-backed judge producers.

Each producer builds a prompt over the lesson, asks an injected provider-neutral
reviewer for structured output validating against the judge's existing ``*Review`` schema,
and returns ``model_dump(mode="json")``. On any known LLM failure it returns
``None`` -- the judge is simply absent for that run and the advisory fold is
skipped. Producers NEVER raise into the graph.

All judge output is advisory. Deterministic checks and the human gate own distribution
decisions.

Task C: ``default_judge`` records which surfaces failed and why in an
``_llm_status`` entry so a reviewer outage is surfaced first-class (the graph
stores it in ``LessonQAState.llm_status`` and includes it in the human-gate
payload). ``noop_judge`` produces no status (no failure).
"""

from __future__ import annotations

import json
import os
import statistics
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import lru_cache
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import Field

from lesson_builder.application.operations.load_terminology import load_terminology_registry
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.domain.lesson.models.review_checks import NaturalnessReview
from lesson_builder.domain.lesson.models.review_checks import ObjectiveAlignmentReview
from lesson_builder.domain.lesson.models.review_checks import PedagogyReview
from lesson_builder.domain.lesson.models.reviewer import ReviewerAgent
from lesson_builder.domain.lesson.validation.review_payloads import build_expected_answers
from lesson_builder.domain.lesson.validation.review_payloads import extract_answer_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_attempt_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_open_rubric_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_open_semantic_questions
from lesson_builder.domain.lesson.validation.review_payloads import restore_answer_review_ids
from lesson_builder.domain.lesson.validation.style_anchors import K_STYLE_ANCHORS_REGISTER_POLICY

K_JUDGES_LLM_FAILURES = (BackendDownException, LlmQuotaException, LlmParseException)

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
    "on_concept",
    "complete",
    "bokmal",
    "sequencing",
    "presentable",
    "answerable",
    "depth",
)


def build_answer_prompt(lesson: dict[str, Any]) -> str:
    """Build the keyless learner view used by the blinded answer reviewer.

    Ask a reviewer to solve the full visible exercise without answer keys.

    The expected-answer set excludes open ``speak``/``write`` tasks that this
    deterministic comparison cannot grade.
    """
    expected_ids = set(build_expected_answers(lesson))
    blinded = [question for question in extract_answer_questions(lesson) if question.get("id") in expected_ids]
    return (
        "Solve each closed exercise below from the learner-visible data only. Some exercises "
        "include a `lesson_context` slice containing the referenced dialogue, examples, or "
        "rule. Use that context to interpret the task, but treat it as learner-visible prose, "
        "not as an answer key. The payload is keyless. Return one "
        "{id, status, answer, reason} object per exercise, preserving ids exactly. Set "
        "status=solved when exactly one answer satisfies the visible task; set status=ambiguous when two or "
        "more answers fit the visible prompt/context; set status=unanswerable when the "
        "payload is incomplete or cannot be solved. For ambiguous/unanswerable items, "
        "answer may be null and reason must identify the concrete defect. Use these answer shapes: "
        "choose → option_id; judge → boolean; recall_fill → zero-based option-index list in blank order; "
        "build → ordered token_id list; find_fix → erroneous token_id; match_pairs → "
        "left_id-to-right_id mapping; categorize → item_id-to-bucket_id mapping. If the "
        "visible data is ambiguous or unsolvable, set the explicit status and explain "
        "the concrete defect. Completion criterion: return every exercise id exactly once "
        "with the matching answer shape or an explicit ambiguous/unanswerable status in the answers "
        "array. Apply explicit requested forms and meanings before comparing alternatives; "
        "grammaticality alone does not satisfy a constrained task. Name concrete competing answers "
        "for ambiguity. Alongside the solve, report semantic_issues only for grounded defects, "
        "using one of these categories: factual_premise, competing_valid_answers, "
        "distractor_parallelism, supplied_answer_retrieval, reconstructed_output_completeness, "
        "or rehearsal_vs_production. Check the exercise's factual claims and premises; identify "
        "a second valid answer under the stated context; compare choose options for comparable "
        "scope, specificity, and plausibility; flag retrieval tasks that supply the target form "
        "instead of requiring it; reconstruct keyed-looking sentences from visible spans/tokens "
        "and flag missing subjects, punctuation, or other incomplete output; and distinguish "
        "reading an exact script from independent learner production. Each issue needs a concrete "
        "reason and quoted evidence. These findings are blocking review signals even when the "
        "answer key can be solved.\n\n"
        f"EXERCISES:\n{json.dumps(blinded, ensure_ascii=False)}"
    )


def build_pedagogy_prompt(lesson: dict[str, Any]) -> str:
    """Build the pedagogy review prompt for one compiled lesson."""
    return f"{K_JUDGES_PEDAGOGY_PROMPT_PREFIX}\nLESSON:\n{json.dumps(lesson, ensure_ascii=False)}"


def build_objective_alignment_prompt(lesson: dict[str, Any]) -> str:
    """Build the objective-alignment review prompt for one compiled lesson."""
    objectives = lesson.get("objectives", [])
    elements = [e for e in lesson.get("elements", []) if e.get("element_kind") == "exercise"]
    return (
        "Judge whether each exercise actually teaches/tests the objective it claims "
        "(objective_id). This is semantic alignment rather than an id check. Focused subskills can "
        "serve an objective without testing its entire transfer outcome; shared topic vocabulary "
        "alone is insufficient. For each genuine "
        "mismatch emit an issue keyed by objective_id with severity P1..P3, message, evidence "
        "(quote), and fix. Include the exercise id in the message, without adding schema fields. "
        "Return passed, summary, and issues; use empty issues for clean alignment. Completion "
        "criterion: inspect every exercise once; set passed=true "
        "exactly when every exercise serves its objective.\n\n"
        f"OBJECTIVES:\n{json.dumps(objectives, ensure_ascii=False)}\n\n"
        f"EXERCISES:\n{json.dumps(elements, ensure_ascii=False)}"
    )


def build_naturalness_prompt(lesson: dict[str, Any]) -> str:
    """Build the naturalness and terminology review prompt for one compiled lesson."""
    rules = _load_active_rules_table()
    rules_block = f"\n{rules}\n" if rules else ""
    return f"{K_JUDGES_NATURALNESS_PROMPT_PREFIX}{rules_block}\nLESSON:\n{json.dumps(lesson, ensure_ascii=False)}"


def reviewer_sample_count() -> int:
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
        values = [int(r["scores"][axis]) for r in reviews if isinstance(r.get("scores"), dict) and axis in r["scores"]]
        if values:
            merged["scores"][axis] = int(statistics.median_low(values))
    return merged


def run_pedagogy_surface(lesson: dict[str, Any], agent: ReviewerAgent) -> tuple[dict[str, Any] | None, str | None]:
    """Run the pedagogy reviewer N times (N=reviewer_sample_count()) and return
    the per-axis median review. Status is reported only when EVERY sample failed
    (parity with a single _run_surface call at N=1)."""
    reviews: list[dict[str, Any]] = []
    last_status: str | None = None
    for _ in range(reviewer_sample_count()):
        review, status = _run_surface(PedagogyReview, build_pedagogy_prompt, lesson, agent)
        if review is not None:
            reviews.append(review)
        elif status is not None:
            last_status = status
    merged = median_pedagogy_review(reviews)
    return merged, (last_status if merged is None else None)


class AnswerItem(BaseModel):
    id: str
    answer: object
    status: Literal["solved", "ambiguous", "unanswerable"] = "solved"
    reason: str | None = None
    semantic_issues: list[SemanticIssue] = Field(default_factory=list)


class SemanticIssue(BaseModel):
    """One grounded teaching-quality defect found while solving an exercise."""

    category: Literal[
        "factual_premise",
        "competing_valid_answers",
        "distractor_parallelism",
        "supplied_answer_retrieval",
        "reconstructed_output_completeness",
        "rehearsal_vs_production",
    ]
    reason: str
    evidence: str


class AnswerReview(BaseModel):
    """The reviewer's OWN answers to the blinded exercises (not a verdict)."""

    answers: list[AnswerItem]


class AttemptItem(BaseModel):
    """One standalone attempt-surface verdict, with no answer-key field."""

    id: str
    status: Literal["clear", "ambiguous", "unanswerable"]
    reason: str | None = None


class AttemptReview(BaseModel):
    """Review of whether each exercise is understandable without hidden context."""

    checks: list[AttemptItem]


class OpenRubricReview(BaseModel):
    """Review of open-write prompt and hidden rubric alignment."""

    checks: list[AttemptItem]


class SemanticReviewItem(BaseModel):
    """Semantic findings for one open task and its authored evidence claim."""

    id: str
    semantic_issues: list[SemanticIssue] = Field(default_factory=list)


class OpenSemanticReview(BaseModel):
    """Review of open-task evidence claims outside deterministic answer comparison."""

    checks: list[SemanticReviewItem]


def pedagogy_review(lesson: dict[str, Any], *, agent: ReviewerAgent) -> dict[str, Any] | None:
    return _structured(agent, PedagogyReview, build_pedagogy_prompt(lesson))


def objective_alignment_review(*, lesson: dict[str, Any], agent: ReviewerAgent) -> dict[str, Any] | None:
    return _structured(agent, ObjectiveAlignmentReview, build_objective_alignment_prompt(lesson))


def answer_review(*, lesson: dict[str, Any], agent: ReviewerAgent) -> dict[str, Any] | None:
    result = _structured(agent, AnswerReview, build_answer_prompt(lesson))
    if result is None:
        return None
    result = restore_answer_review_ids(result, lesson)
    # Keep the historical payload compact for callers that only need solved
    # answers; non-default status/reason fields remain explicit for the strict
    # exercise verifier.
    for item in result.get("answers", []):
        if isinstance(item, dict) and item.get("status") == "solved":
            item.pop("status", None)
            item.pop("reason", None)
        if isinstance(item, dict) and not item.get("semantic_issues"):
            item.pop("semantic_issues", None)
    return result


def attempt_review(*, lesson: dict[str, Any], agent: ReviewerAgent) -> dict[str, Any] | None:
    """Review the true attempt-time surface without exposing hidden targets or criteria."""
    return _structured(agent, AttemptReview, build_attempt_prompt(lesson))


def build_attempt_prompt(lesson: dict[str, Any]) -> str:
    """Build the standalone reviewer prompt from attempt-time fields only."""
    attempt_questions = extract_attempt_questions(lesson)
    return (
        "Review the standalone attempt-time surface of every exercise below. You see only "
        "what the learner can see and submit: never infer or request lesson sections, "
        "derived_from references, answer assignments, feedback, rationales, hidden speak "
        "targets, or write criteria/judge prompts. Mark `clear` only when the visible cue "
        "makes the requested learner action understandable and solvable. For every "
        "closed task, verify that at least one visible option or answer can satisfy the "
        "whole stated situation and requested meaning, not merely the grammatical shape; "
        "a grammatical option about the wrong object, person, event, or context does not "
        "make the task answerable. Mark `unanswerable` when no visible answer fits the "
        "complete situation. Mark `ambiguous` when a closed task permits multiple answers "
        "after its explicit form and meaning constraints are applied. Name concrete "
        "competing answers. Open write "
        "and speak tasks may allow multiple responses without ambiguity. A speak cue "
        "must specify what to say or accomplish, but do not invent exact-match grading "
        "or infer a hidden target from this keyless view. Repeating an absent utterance "
        "is missing context. For write, check "
        "only visible instructions and bounds; a hidden criterion cannot repair missing "
        "attempt context. A write task with response_language `no` must not require the "
        "learner to produce English. Mark `unanswerable` when the operation asks the "
        "learner to write, reorder, explain, or submit something its attempt surface does "
        "not collect. Return a checks array with exactly one {id, status, reason} per item, "
        "preserving ids and naming concrete defects in failure reasons.\n\n"
        f"EXERCISES:\n{json.dumps(attempt_questions, ensure_ascii=False)}"
    )


def open_rubric_review(*, lesson: dict[str, Any], agent: ReviewerAgent) -> dict[str, Any] | None:
    """Review write-task obligations against hidden criteria and judge metadata."""
    if not extract_open_rubric_questions(lesson):
        return {"checks": []}
    return _structured(agent, OpenRubricReview, build_open_rubric_prompt(lesson))


def open_semantic_review(*, lesson: dict[str, Any], agent: ReviewerAgent) -> dict[str, Any] | None:
    """Review open write/speak evidence claims without producing answer keys."""
    if not extract_open_semantic_questions(lesson):
        return {"checks": []}
    return _structured(agent, OpenSemanticReview, build_open_semantic_prompt(lesson))


def build_open_semantic_prompt(lesson: dict[str, Any]) -> str:
    """Build the semantic review prompt for open-task evidence claims."""
    questions = extract_open_semantic_questions(lesson)
    return (
        "Review every open write and speak task below as authored evidence, separately from any "
        "deterministic answer comparison. Inspect the visible prompt together with the authored "
        "objective, Bloom level, response constraints, hidden write criteria/judge prompt, and "
        "speak target. Report only grounded defects. Check whether the operation actually elicits "
        "the claimed evidence: flag supplied-answer retrieval, an exact script presented as "
        "independent production, missing or contradictory obligations, reconstructed output that "
        "would be incomplete, factual premises, or competing valid answers. A supplied target is "
        "valid rehearsal only when the task claims rehearsal; do not penalize a clearly labeled "
        "model or repeat exercise. Return exactly one {id, semantic_issues} object per task. Every "
        "issue must include a concrete reason and quoted evidence from the supplied task. Return "
        "an empty semantic_issues list when the evidence claim is sound. Do not solve the task or "
        "compare it with an answer key.\n\n"
        f"OPEN TASKS AND EVIDENCE CLAIMS:\n{json.dumps(questions, ensure_ascii=False)}"
    )


def build_open_rubric_prompt(lesson: dict[str, Any]) -> str:
    """Build the reviewer prompt that may inspect hidden write-task criteria."""
    questions = extract_open_rubric_questions(lesson)
    return (
        "Review each open Norwegian write task for bidirectional prompt/rubric coverage. "
        "This review may inspect the hidden criteria and judge_prompt, but must not treat "
        "them as learner-visible context: separately verify that the visible prompt is "
        "complete on its own. Identify every atomic learner obligation requested by the "
        "prompt and require an observable criterion for each. Also require every criterion "
        "to follow from the visible prompt and response constraints; hidden, unrequested obligations "
        "are defects. Criteria can jointly cover obligations without one-to-one wording. Check "
        "judge_prompt too: it must neither waive requested forms nor add requirements. Bounds and "
        "response_language are visible constraints. "
        "When response_language is `no`, reject any criterion or learner instruction that "
        "requires English output (English instructions are allowed). Accept appropriate "
        "standard Bokmål variants, including common-gender/feminine `en` forms, rather "
        "than making criteria reject a valid variant. Enforce explicitly requested tense, "
        "construction, and gender forms; a grammatical response that omits a requested form need not "
        "pass. Examples are illustrative unless the visible task makes them exhaustive. Mark `clear` "
        "only when both directions "
        "are covered, `ambiguous` for a rubric that wrongly rejects a valid variant, and "
        "`unanswerable` for missing or contradictory coverage. Return exactly one check per "
        "write task, preserving ids, with a concise evidence-based reason for failures.\n\n"
        f"WRITE TASKS WITH RUBRICS:\n{json.dumps(questions, ensure_ascii=False)}"
    )


def naturalness_review(lesson: dict[str, Any], *, agent: ReviewerAgent) -> dict[str, Any] | None:
    return _structured(agent, NaturalnessReview, build_naturalness_prompt(lesson))


def default_judge(lesson: dict[str, Any], *, agent: ReviewerAgent) -> dict[str, dict[str, Any] | None]:
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
            pool.submit(run_pedagogy_surface, lesson, agent): "pedagogy_review",
            pool.submit(
                _run_surface,
                ObjectiveAlignmentReview,
                build_objective_alignment_prompt,
                lesson,
                agent,
            ): "objective_alignment_review",
            pool.submit(
                _run_surface,
                AnswerReview,
                build_answer_prompt,
                lesson,
                agent,
            ): "answer_review",
            pool.submit(
                _run_surface,
                NaturalnessReview,
                build_naturalness_prompt,
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


def noop_judge(lesson: dict[str, Any]) -> dict[str, dict[str, Any] | None]:
    """Offline/test default: no reviews produced (advisory fold skipped)."""
    del lesson
    return {
        "pedagogy_review": None,
        "objective_alignment_review": None,
        "answer_review": None,
        "naturalness_review": None,
    }


def _structured(agent: ReviewerAgent, schema: type[BaseModel], prompt: str) -> dict[str, Any] | None:
    try:
        result = agent.structured(schema).invoke(prompt)
    except K_JUDGES_LLM_FAILURES:
        return None
    return result.model_dump(mode="json")


# Reverse lookup: state key -> surface name (for llm_status keys).
K_SURFACE_BY_STATE_KEY: dict[str, str] = {
    "pedagogy_review": K_SURFACE_PEDAGOGY,
    "objective_alignment_review": K_SURFACE_OBJECTIVE_ALIGNMENT,
    "answer_review": K_SURFACE_ANSWER,
    "naturalness_review": K_SURFACE_NATURALNESS,
}


def _run_surface(
    schema: type[BaseModel],
    prompt_builder: Callable[[dict[str, Any]], str],
    lesson: dict[str, Any],
    agent: ReviewerAgent,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run one judge surface, capturing the failure kind (Task C).

    Calls the agent's structured-output path directly (rather than going through
    the public ``pedagogy_review``/``objective_alignment_review``/``answer_review``
    producers, which swallow ``K_JUDGES_LLM_FAILURES`` and return ``None``) so the failure
    kind is visible to the judge panel. Returns ``(review_or_None,
    failure_kind_or_None)``.

    The public producers keep their historical ``None``-on-failure contract for
    external callers (e.g. the rejudge seam); the panel goes through here so it
    can surface outages first-class instead of silently reading as "clean."
    """
    prompt = prompt_builder(lesson)
    try:
        result = agent.structured(schema).invoke(prompt)
    except K_JUDGES_LLM_FAILURES as exc:
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


K_JUDGES_PEDAGOGY_PROMPT_PREFIX = """\
You are a fair reviewer of Norwegian (Bokmal) grammar lessons for adult English
speakers. SCORE one generated lesson against the rubric and list its REAL defects, each with
evidence you can quote from the lesson. Report grounded defects with exact lesson evidence. Do not mistake deliberately incorrect distractors or clearly labeled negative examples for endorsed teaching.

Use the full rubric consistently. Reserve a score of 0-2 for a genuine, demonstrable failure on
that axis, not a minor imperfection or a stylistic preference. Judge the lesson on its own concept;
do not reward table count or block volume.

Schema and answer-key review happen on separate surfaces. If a lesson brief or
derived scope card is provided, treat its deferred topics as out of scope.

Rubric — score each axis 0-5 (5 = excellent, 4 = good, 3 = acceptable, 0-2 = a real failure):
- on_concept: teaches what the title/goal promises; no scope bleed into a neighbor lesson.
- complete: teaches, not just lists. When the lesson names endings/classes/rules, it should also say
  which cases they apply to (a brief explanation + an example is enough). Only score low if a rule is
  a bare list with no explanation at all (e.g. "past tense ends in -et, -te, or -de" and nothing more).
- bokmal: Bokmal, no Nynorsk. Only lower this if you can QUOTE the specific non-Bokmal form AND give
  its correct Bokmal form. If every form is valid standard Bokmal, score 5. Never score bokmal below
  4 without citing a concrete wrong form — do not guess. Watch for fabricated inflections that look
  plausible but are not real Bokmal (e.g. 'godere' for 'bedre', 'storere' for 'større', 'setts' for 'sett').
- sequencing: the prose teaches the pattern (with a why and an example), not only the exercises.
  Gloss metalinguistic terms on first use. Minor terseness is a 4, not a 2.
- presentable: learner-facing prose written TO the learner about the language, not ABOUT the course.
  Flag CEFR-level talk in learner text (A1/A2/B1/B2, "at this level", "a real A2 challenge") and
  internal/meta strings.
- answerable: every choose/recall_fill has a carrier stem, exactly one correct answer, plausible
  learner-error distractors, and explanatory feedback when it helps teach the target.
  Missing optional feedback for simple recall is not a defect when it would only restate the key.
  A distractor that is valid under the stated
  prompt/context is a serious answerability defect, even if the lesson prefers another form. A
  "real explanation" clarifies the target choice or likely error when useful. Concise
  feedback can explain the contrast without separately rejecting every distractor or
  duplicating option feedback. Apply explicitly requested forms before accepting variants.
- depth: practice develops the target decision toward transfer. Judge what the learner
  retrieves, produces, or diagnoses, not operation names or counts. A meaning-based choice
  in a changed situation can demonstrate transfer; a token build is not automatically deep.


Defects to check for when present, with quoted evidence:
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
Completion criterion: score every rubric axis once and report every grounded defect in the full lesson.
"""


@lru_cache(maxsize=1)
def _load_active_rules_table() -> str:
    """Render the active registry concepts as reviewer-only Markdown context."""
    try:
        registry = load_terminology_registry()
    except (FileNotFoundError, ValueError):
        return ""
    lines = [
        "## Active rules",
        "",
        "| Concept | Pinned term | Also OK | Guidance / hard bans | Level note |",
        "|---|---|---|---|---|",
    ]
    for concept in registry.collect_active_concepts():
        alternatives = "; ".join(concept.alternative_labels) or "—"
        if concept.norwegian_label:
            alternatives = f"{alternatives}; Norwegian: {concept.norwegian_label}"
        guidance = concept.guidance or "—"
        bans = ", ".join(forbidden.phrase for forbidden in concept.forbidden_phrases) or "—"
        lines.append(
            f"| {concept.id} | **{concept.preferred_label}** | {alternatives} | "
            f"{guidance}; bans: {bans} | {concept.cefr_note or 'all levels'} |"
        )
    return "\n".join(lines)


K_JUDGES_NATURALNESS_PROMPT_PREFIX = f"""\
You are a NATIVE Norwegian (Bokmal) speaker AND a CEFR-trained language teacher reviewing a \
Norwegian lesson for adult English speakers. Your lens is NATURALNESS and REGISTER: does the \
Norwegian read like something a native speaker would actually say/write, and is the language \
and terminology appropriate for the lesson's CEFR level?

Score three axes 1-5 (5 = consistently natural and level-appropriate; 1 = frequently stilted or \
register-inappropriate):
- idiomatic_phrasing: natural word order, adverb/particle placement, collocation, and message \
register/punctuation. Flag stilted or unnatural phrasing. Consider context before preferring a neutral default: e.g. \
"heller ikke" is common, but do not reject contextual alternatives solely for differing word order; watch for awkward collocations \
and stilted message/e-mail register (greeting, line-break, sign-off, punctuation).
- register_appropriateness: is the grammar terminology and description register appropriate for \
the lesson's cefr_level? Apply this standard, flagging deviations from it: {K_STYLE_ANCHORS_REGISTER_POLICY}
- terminology_consistency: does the lesson use the pinned house-style terms consistently and \
level-appropriately vs the Active rules table below? Score 5 when every concept uses the pinned \
term (or an "Also OK" variant at the right level); score lower when a concept uses a non-pinned \
label, drifts between two labels for one concept, or uses a term above/below its level note. \
This axis is advisory (issues stay advisory; the score is a review signal).

For each real defect, emit an issue with: unit_id (the element/exercise id, or "" for whole-lesson \
prose), severity (P1 serious / P2 real but minor / P3 polish), and a message naming the specific \
phrase and the natural/level-appropriate alternative. Ground every issue in exact lesson text.

Preserve valid Bokmål variants and distinguish real usage problems from optional rewrites. Do not flag deliberately incorrect distractors or labeled negative examples as endorsed prose. Grammar correctness and answer keys are reviewed on separate surfaces. Judge naturalness, register \
appropriateness, and terminology consistency for the stated cefr_level. Completion criterion: score \
all three axes and report every grounded defect in those axes.
"""


__all__ = [
    "AnswerReview",
    "AnswerItem",
    "SemanticIssue",
    "AttemptReview",
    "AttemptItem",
    "pedagogy_review",
    "objective_alignment_review",
    "answer_review",
    "attempt_review",
    "naturalness_review",
    "default_judge",
    "noop_judge",
    "median_pedagogy_review",
    "reviewer_sample_count",
    "run_pedagogy_surface",
    "build_answer_prompt",
    "build_naturalness_prompt",
    "build_objective_alignment_prompt",
    "build_pedagogy_prompt",
    "build_attempt_prompt",
    "OpenRubricReview",
    "open_rubric_review",
    "build_open_rubric_prompt",
    "OpenSemanticReview",
    "SemanticReviewItem",
    "open_semantic_review",
    "build_open_semantic_prompt",
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
