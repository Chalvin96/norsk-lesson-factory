"""Entry points: rich-authoring prompt builders.

Application-owned prompt construction for rich-authoring stages. These builders
render approved plans, immutable prose, and typed policy guidance into model
instructions; provider invocation and response validation stay in workflow and
client layers.
"""

from __future__ import annotations

import json
from typing import Any
from typing import cast

import yaml

from lesson_builder.domain.lesson.models.operations import evidence_route_operations
from lesson_builder.domain.lesson.models.operations import render_evidence_route_guidance
from lesson_builder.domain.lesson.models.operations import render_operation_guidance
from lesson_builder.domain.lesson.models.operations import render_operation_payload_guidance
from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.quality_review import NormalizationPreservationReview
from lesson_builder.domain.lesson.models.rich_authoring import LessonDraftEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizationEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizedPackage
from lesson_builder.domain.lesson.services.quality_review import quality_axes_for_kind
from lesson_builder.formats.yaml import load_unique_yaml

K_RICH_GRAMMAR_DRAFT_GUIDANCE = """
Grammar chapter shape (apply this only when the approved plan kind is grammar):
- Anchor the lesson in one believable named-speaker dialogue or short everyday
  scene. The situation is a reason to use the grammar, not a second vocabulary
  lesson. Let the learner understand the scene before explaining the rule.
- After the anchor, make the learner's purpose and meaning choice explicit in
  plain English. Introduce only the necessary glossary terms, with the
  established Norwegian label on first use (for example, `past tense
  (preteritum)`).
- Teach the first approved grammar decision completely: show aligned Norwegian
  examples with English translations, contrast the relevant forms and meanings,
  state a compact rule, and correct one likely misconception.
- Place checkpoints and practice requests where they help the learner notice,
  retrieve, and use the target. Use as many explanation and practice clusters
  as the chapter needs; do not force a fixed number of sections or insert an
  exercise after every paragraph.
- End with a cumulative retrieval and transfer block in a changed everyday
  situation. Reuse the target forms, but make the learner use them for a new
  purpose rather than repeat the dialogue verbatim.
- Plan as many varied checkpoints as the distinct learner evidence goals
  require. Do not optimize for a fixed count, merge two different practice
  purposes to be concise, or add checkpoints just to make the chapter look busy.
  Mark each checkpoint with exactly one typed directive block:
  `{{checkpoint`
  `handle: stable-handle`
  `objective_ref: approved-objective-id`
  `bloom: level`
  `evidence: One concise sentence of observable learner evidence.`
  `}}`
  Keep each directive on its own lines. Do not repeat its metadata in prose or
  describe an activity or success response. Ordinary checkpoints omit settings
  and context; the final checkpoint includes its changed-situation transfer
  requirement in the evidence sentence. Give each
  checkpoint one primary scored evidence goal. If one sentence illustrates
  several target patterns, say which dimension is scored and which patterns are
  only context; split genuinely different scored goals into separate checkpoints
  rather than relying on mutually exclusive categories.
- Keep the grammar scope bounded by the approved teaching points and
  prerequisites. Do not ask the learner to classify an abstract clause before
  showing what the form helps them say.
""".strip()
K_RICH_GRAMMAR_NORMALIZATION_GUIDANCE = """
Grammar preservation requirements:
- Preserve the chapter's anchor dialogue, meaning-first explanation, aligned
  form/meaning contrasts, glossary introductions, misconception correction,
  every useful checkpoint, and cumulative transfer. Do not compress these into
  a generic summary such as “the conversation contains several kinds of ...”.
- Keep the visible rhythm chapter-like: an orient/context section, one or more
  model sections, contrast/checkpoint sections, and a recap/transfer section.
  Repeat roles whenever the draft needs another explanation; do not force four
  tiny sections. Link model and contrast sections to the approved objective.
- Preserve every standalone `{{exercise: handle}}` marker byte-for-byte at its
  authored position. The route-free checkpoint intent is supplied separately;
  never copy its metadata into learner-facing prose, merge or reorder markers,
  or change an authored field except by adding the evidence route.
- Add compiler metadata, typed audio/example blocks, section IDs, and exercise
  markers mechanically; do not rewrite learner-facing prose for brevity.
- Keep English explanations, instructions, feedback, headings, and
  translations; keep natural Bokmål in dialogue, examples, and answer choices.
  Preserve every Norwegian function word and punctuation mark.
""".strip()


def build_rich_draft_prompt(
    plan_text: str,
    *,
    plan_kind: str | None,
    terminology_context: str | None = None,
) -> str:
    """Build the flexible prose-author prompt from an approved plan."""
    prompt = (
        "You are the first author in a Norwegian language-course authoring pipeline. "
        "Write an original mini-chapter in the broad style of a good "
        "clear language textbook. This is a prose draft for another node, so focus "
        "on a complete chapter rather than serialization.\n\n"
        "Start with a believable named-speaker dialogue or short contextual text that "
        "makes the approved learner problem useful. Treat it as a real chapter anchor, "
        "not as a bag of example sentences: let the learner understand what is going "
        "on, pull out useful expressions, and return to the same people or situation "
        "after the explanation. Then teach from that context with a natural English "
        "explanation, several Norwegian examples with accurate English translations, "
        "aligned contrasts, common mistakes, and a gradual set of practice checkpoints. "
        "At each checkpoint, write one typed directive block using exactly this shape:\n"
        "`{{checkpoint`\n"
        "`handle: stable-handle`\n"
        "`objective_ref: approved-objective-id`\n"
        "`bloom: level`\n"
        "`evidence: One concise sentence of observable learner evidence.`\n"
        "`}}`\n"
        "Keep each directive on its own lines. Do not write a prose label such as "
        "`Checkpoint activity:` or repeat the directive metadata outside the block. "
        "The handle is a stable snake_case name, the objective is one approved plan "
        "objective id, and the Bloom level is one of remember, understand, apply, "
        "analyze, and the evidence sentence names observable learner evidence. Do not "
        "describe an activity, setting, context, or success response in an ordinary "
        "checkpoint directive; for the final retrieval directive, include its changed-"
        "situation transfer requirement in the one-sentence evidence field; do not "
        "refer backward to earlier lesson material. Do not write option lists, answer "
        "keys, token orders, or serialized exercise data; another node authors those "
        "details. Build from focused practice of a taught decision toward combining "
        "skills when the approved outcome requires it. Choose practice demands for "
        "the evidence needed, without an operation quota or making every checkpoint "
        "a complete open task. Finish with a short retrieval/review checkpoint in a changed "
        "situation, not a repeat of the opening anchor. The final checkpoint must "
        "require observable use of the approved outcome: for a communicative outcome, "
        "ask for a short new dialogue or exchange; for grammar or phraseology, ask the "
        "learner to produce the target or choose it from meaning in a new context. Do "
        "not make the final checkpoint only a recap, recognition task, definition "
        "match, or request to repeat the anchor. Reuse the same target forms in new "
        "situations so the chapter feels like a lesson rather than a reference-card "
        "definition. Keep the chapter's grammar or communicative "
        "focus bounded by the approved plan; do not invent a second learner outcome. "
        "In ordinary bilingual examples, do not add quotation marks around the Norwegian "
        "or English text unless quotation marks are the lesson target; the `no:` and `en:` "
        "labels already delimit the pair.\n\n"
        "Instructional explanations, exercise instructions, feedback, and headings are "
        "in English. Norwegian target sentences, dialogue turns, and answer choices are "
        "natural Bokmål and should have English translations. Introduce a necessary term "
        "in plain English with its established Norwegian label, for example `past tense "
        "(preteritum)`, then use it consistently. Develop each approved decision "
        "without a length or section-count target. Each section must add a distinction, "
        "worked example, misconception correction, or new practice demand; combine "
        "passages that merely repeat the teaching. Keep retrieval that strengthens "
        "a taught decision, but do not ask for the same complete transfer task again "
        "under a recap heading. Do not bury the learner under metalanguage: introduce only "
        "the terms needed to understand the target choice, and keep the explanation "
        "tied to the dialogue and examples. All text must be original and must not "
        "reproduce wording from any textbook. The chapter prose is the primary "
        "deliverable. Completion criterion: every approved objective is taught with "
        "context, explanation, translated examples, practice evidence, and cumulative retrieval.\n\n"
    )
    if plan_kind == "grammar":
        prompt += K_RICH_GRAMMAR_DRAFT_GUIDANCE + "\n\n"
    if terminology_context:
        prompt += (
            "Selected terminology registry context (use only these established labels; "
            "introduce each needed term in plain English with its Norwegian label):\n" + terminology_context + "\n\n"
        )
    return prompt + ("Approved plan (the only scope authority):\n---BEGIN PLAN---\n" + plan_text + "\n---END PLAN---\n")


def build_lesson_review_prompt(
    plan_text: str,
    draft_text: str,
    *,
    plan_kind: str | None,
    terminology_context: str | None = None,
) -> str:
    """Build the tool-free content-review prompt for the exact draft bytes."""
    if plan_kind is None:
        raise ValueError("approved plan has no catalog kind; the lesson review cannot select its rubric")
    expected_axes = quality_axes_for_kind(plan_kind)
    prompt = (
        "You are the independent lesson-content reviewer for a Norwegian lesson "
        "draft. Review the exact draft bytes below before they are normalized into "
        "compiler source. Do not rewrite, extend, or authorize them; judge them. "
        "Score linguistic correctness, natural Bokmål, translation fidelity, "
        "pedagogy, glossary use, CEFR scope, and the plan's family outcome. The "
        "draft is textbook-like prose, so judge teaching quality rather than "
        "serialization: later nodes add compiler structure and exercise payloads.\n\n"
        f"Score every rubric axis 0-2 in this exact order: {list(expected_axes)!r}. "
        "Every score needs draft-grounded rationale. Report one finding per concrete "
        "defect with its exact location, the evidence, and a bounded draft-only "
        "repair instruction. Blocking codes are reserved for incorrect or "
        "unsupported claims, unnatural Bokmål, meaning-changing translations, "
        "accepted forms marked wrong, ambiguous answer keys, target loss, "
        "prerequisite or scope breach, and compiler failure. Use major severity for "
        "a material pedagogy, terminology, or practice gap and minor for copy "
        "polish that does not prevent learning. This is the only defect-discovery "
        "pass before one bounded repair: scan the entire draft line by line and "
        "report every blocking or major defect in the same response. Search for "
        "every occurrence of a claim, including repeated rule prose, tables, "
        "dialogue explanations, checkpoint evidence statements, activity purpose/success text, "
        "transfer tasks, and the retrieval recap. Report independent defects in this pass. "
        "Treat broad plan wording as scope, not proof of a universal rule: when "
        "the draft demonstrates only named constructions, qualify the rule and "
        "limit the claim to those taught patterns rather than inventing exceptions. "
        "Before scoring the prose, build a private scope ledger: map every level-two "
        "section, named dialogue, and typed checkpoint directive to one approved "
        "objective or teaching point from the plan. Report any dedicated explanation "
        "or scored learner action that has no such mapping as a prerequisite_scope_breach "
        "or pedagogy_gap; a brief recognition example may support the target, but a "
        "second outcome must not receive its own rule, practice, or transfer task. "
        "Judge practice checkpoints by their placement, objective, Bloom level, and "
        "one-sentence observable evidence: a typed directive carries intent only, so "
        "do not require activity prose, context narration, or a success-response description, "
        "and flag checkpoint wording that leans on earlier material instead of naming "
        "observable evidence. "
        "For every dialogue, check each adjacent question/answer pair for pragmatic fit "
        "and keep named places, people, and other entities consistent across turns and "
        "translations. If a repair changes a dialogue turn, require the repair to update "
        "the paired turn whenever its meaning or answer fit depends on it. "
        "Completion criterion: score every rubric axis once and report every grounded "
        "blocking or major defect across the complete draft.\n\n"
    )
    if terminology_context:
        prompt += "House terminology rules:\n" + terminology_context + "\n\n"
    return prompt + (
        "Approved plan (the only scope authority):\n---BEGIN PLAN---\n" + plan_text + "\n---END PLAN---\n\n"
        "Exact draft under review:\n---BEGIN DRAFT---\n" + draft_text + "\n---END DRAFT---\n"
    )


def build_normalization_prompt(
    plan_text: str,
    draft_text: str,
    checkpoint_intents_yaml: str,
    *,
    plan_kind: str | None,
) -> str:
    """Build the edit-mode prompt for compiler-valid source conversion."""
    prompt = (
        "You are the source-normalization node in EDIT MODE in a Norwegian lesson "
        "authoring pipeline. Convert the complete prose draft below into learner-facing "
        "lesson Markdown and assign one evidence route to each separately supplied "
        "checkpoint intent. You "
        "are an editor of structure, not a second lesson author. Treat the draft's "
        "learner-facing prose as protected source: preserve its wording, order, dialogue, "
        "translations, examples, explanations, and practice sequence by default. Do not "
        "reorder a local explanation–example sequence: keep each explanation immediately "
        "before or after the exact examples it introduces, because that order carries the "
        "teaching meaning. Do not group examples from separate sequences. Do not "
        "rewrite for style, shorten, summarize, correct terminology, resolve a pedagogical "
        "ambiguity, or invent a better example. If the draft is awkward or ambiguous, "
        "preserve it and let the later review gate report it. The approved plan remains "
        "the scope authority. Never introduce a terminology label that is absent from the "
        "draft just because it appears in the plan or in your own training data; keep the "
        "draft's learner wording. In particular, use the established term `noun phrase` "
        "or a plain description and never introduce a hard-banned terminology label.\n\n"
        "Return one JSON object with exactly two string fields: `lesson_md` and "
        "`exercise_requests_yaml`. The JSON is transport only; the strings are "
        "Markdown/YAML authoring handoffs. Do not include final exercise operations, "
        "answer keys, option lists, token orders, a coverage manifest, transcript, "
        "audio IDs, export JSON, or commentary outside the object.\n\n"
        "Mechanical source contract (the compiler is authoritative):\n"
        "- lesson.md starts with YAML front matter containing type: Lesson, slug, title, "
        "cefr_level, goal, default_lang: nb, grounding_mode: grounded, bloom_targets, "
        "and at least one objective mapping with id, statement, and bloom_targets.\n"
        "- Copy the approved plan's lesson_id exactly into lesson.md slug; do not change "
        "hyphens, underscores, or spelling.\n"
        "- Use as many learner-facing sections as the draft needs. Every `##` heading "
        "must have a unique `{#id role=...}` attribute; roles are orient, model, "
        "contrast, and recap. Include at least one orient, one model, one contrast, "
        "and one recap section, but do not force the prose into four short sections. "
        "Every model or contrast section must link exactly one approved objective "
        "with `objectives=obj-id`; orient and recap may link several objectives.\n"
        "- Audio-bearing Norwegian text must use the compiler's typed blocks: use "
        "`::: examples` with sibling `- no:` and `- en:` list items for bilingual "
        "examples, and use one `::: {.reading translation=... dialogue_id=... "
        "speaker_id=... speaker_name=...}` block per named dialogue turn. Do not "
        "hide all target sentences in bold prose or ordinary paragraphs, because "
        "those forms cannot produce transcript/audio candidates.\n"
        "- Field-type boundary inside typed blocks: `lesson_md` is the only returned "
        "field whose outer value is Markdown source. The values after `- no:` and "
        "`- en:`, the body of a `reading` block, and the `translation`/dialogue "
        "attributes are learner-visible text values, not Markdown wrappers. Keep "
        "these values as plain text: do not put `*`, `**`, backticks, or underscore "
        "emphasis markers around a target sentence or translation. If the draft "
        "uses emphasis to point out a form, keep that emphasis in an ordinary prose "
        "paragraph and copy only the visible plain sentence into the audio-bearing "
        "typed block. Never leave an unmatched Markdown delimiter in a typed value.\n"
        "- Consecutive named-speaker reading turns that belong to one conversation "
        "must share one dialogue_id (for example, `office`); do not assign a unique "
        "numbered dialogue_id to every turn. Start a new dialogue_id only when the "
        "conversation or scene changes.\n"
        "- Every reading block needs a non-empty English `translation` attribute, "
        "including for a short acknowledgement.\n"
        "- Every `::: examples` block must use immediate paired typed lines, one "
        "pair at a time, exactly in this shape: `- no: <Norwegian>` followed by "
        "`- en: <English gloss>`; these are Markdown typed-example lines, so do "
        "not add YAML-style outer quote characters around either value. Any quote "
        "characters inside a value are learner-visible punctuation and belong only "
        "when quotation marks are themselves being taught. The next pair starts "
        "only after that `en:` line. For a negative example use `- no: <bad "
        "Norwegian>` immediately followed by `- en: Incorrect: <English gloss>` "
        "(or `Not:`/`✗`). Never group multiple `no:` items before their `en:` "
        "values, and never move the negative marker into preceding prose.\n"
        "- Every negative or discouraged example must carry its negative status "
        "inside that typed example itself, using an inline compiler-recognized "
        "English marker such as `Incorrect:` or `Not:` at the start of its `en:` "
        "value; keep the Norwegian `no:` bytes unchanged. A preceding heading or "
        "prose label such as `Less useful`, `Vague`, or `Common mistake` does not "
        "protect the example from model audio and is not an encoded status. Do not "
        "mark ordinary positive examples negative.\n"
        "- Keep each `::: examples` block one contiguous list; close it before any "
        "explanation.\n"
        "- In ordinary bilingual examples, preserve the example text without added quote "
        "characters unless quotation marks are themselves being taught. The `no:` and `en:` "
        "labels provide the boundary; JSON escaping must never become a literal backslash "
        "in lesson prose.\n"
        "- Preserve every existing standalone `{{exercise: handle}}` marker byte-for-byte "
        "and in place. Never add a visible checkpoint label, evidence sentence, objective "
        "id, Bloom label, or other request metadata around a marker. Marker position is "
        "the lesson sequence authority, never an exercise dependency. "
        "- `exercise_requests_yaml` must be a top-level YAML list. Each item has only "
        "`handle`, `objective_ref`, `bloom`, `evidence_route`, and `evidence`, in that "
        "order. Copy handle, objective_ref, bloom, and evidence exactly from the supplied "
        "checkpoint intents, preserving list order; add only one `evidence_route` selected "
        "from the registry. This scratch handoff must not contain exercise instructions, "
        "activity prose, backward-looking references, operation payloads, or the retired "
        "verbose fields `evidence_family`, `intent`, `context`, `learner_action`, "
        "`success_criteria`, or `source_section`. Do not put `op`, `options`, `tokens`, "
        "`answer`, `answer_order`, or other operation-specific payloads in this handoff.\n"
        "- Keep all instructional prose and translations in English, with natural Bokmål "
        "targets and answer choices. Keep function words and punctuation in every target "
        "sentence. Do not use a level-one Markdown heading in the body; blockquotes, "
        "bare pipe tables, Markdown fences, multi-paragraph rule blocks, and typed "
        "blocks nested inside list items are rejected by the deterministic source "
        "validators.\n"
        "- Quote every YAML scalar containing a colon, question mark, or apostrophe, "
        "including list values and `key`-like text. The normalizer adds the metadata, "
        "typed blocks, section IDs, and markers required by the contract; it must not "
        "rewrite learner-facing prose for brevity, merge or drop requests, or impose the "
        "old typed brief, fixed move list, coverage response, or a request-count limit. "
        "Do not make exercise decisions in this stage beyond selecting the evidence "
        "route: preserve the supplied intent byte-for-byte, and do not make payload or "
        "answer-key decisions. Completion criterion: every protected teaching unit and "
        "exercise request is preserved in order, lesson_md compiles, and the handoff "
        "contains every required field without operation payloads.\n"
        "Evidence-route registry:\n"
        + render_evidence_route_guidance()
        + "\nWrite only the operation-free handoff fields after selecting the route.\n\n"
    )
    if plan_kind == "grammar":
        prompt += K_RICH_GRAMMAR_NORMALIZATION_GUIDANCE + "\n\n"
    return prompt + (
        "Approved plan:\n---BEGIN PLAN---\n" + plan_text + "\n---END PLAN---\n\n"
        "Learner prose with exercise markers:\n---BEGIN DRAFT---\n" + draft_text + "\n---END DRAFT---\n\n"
        "Frozen checkpoint intents (copy every field except add evidence_route):\n"
        "---BEGIN CHECKPOINT INTENTS---\n" + checkpoint_intents_yaml + "---END CHECKPOINT INTENTS---\n"
    )


def build_preservation_review_prompt(
    *,
    plan_text: str,
    draft_text: str,
    lesson_md: str,
    exercise_requests_yaml: str,
) -> str:
    """Build the content-preservation review of one normalization."""
    return (
        "You are the normalization-preservation reviewer for a Norwegian lesson "
        "conversion. The reviewed draft below is protected source. The normalized "
        "source must preserve every important reviewed teaching unit: rules, "
        "examples, aligned contrasts, dialogue turns, glossary introductions, and "
        "every practice checkpoint in the same order with the same handle, "
        "objective, Bloom level, and observable evidence goal. Checkpoint markers "
        "must stay at their taught position, because marker position is the "
        "lesson's sequence authority. The typed directive carries intent only; do "
        "not demand activity prose, context narration, or success-response wording "
        "in it. Mechanical compiler syntax, "
        "typed-block validity, and marker/request identity are checked separately "
        "by deterministic validators. For every negative or discouraged example, "
        "verify that the normalized typed item still carries an inline compiler-"
        "recognized negative marker (`Incorrect:`, `Not:`, or `✗`) in its own "
        "English value. A surrounding heading or explanatory sentence is not "
        "equivalent; losing or failing to encode that status is a semantic "
        "preservation defect requiring `needs_repair`. Display labels may be "
        "learner-readable and "
        "hyphenated; only exercise markers and request handles are identities. "
        "Report only lost or semantically changed content and restore the protected "
        "meaning with bounded findings.\n\n"
        "`pass` requires every important reviewed teaching unit to survive with its "
        "meaning intact. Any dropped or semantically changed rule, example, "
        "contrast, dialogue turn, glossary introduction, or practice checkpoint "
        "requires `needs_repair` with one finding per defect. Each finding's repair "
        "instruction must restore the missing content. Completion criterion: compare "
        "every protected teaching unit and exercise intent once; pass exactly when all "
        "survive with meaning and order intact.\n\n"
        "Approved plan (scope authority):\n---BEGIN PLAN---\n" + plan_text + "\n---END PLAN---\n\n"
        "Reviewed draft (protected source):\n---BEGIN DRAFT---\n" + draft_text + "\n---END DRAFT---\n\n"
        "Normalized lesson Markdown:\n---BEGIN LESSON---\n" + lesson_md + "\n---END LESSON---\n\n"
        "Normalized exercise requests:\n---BEGIN REQUESTS---\n" + exercise_requests_yaml + "\n---END REQUESTS---\n"
    )


def build_exercise_author_prompt(
    *,
    plan_text: str,
    lesson_md: str,
    exercise_requests_yaml: str,
    terminology_context: str | None = None,
) -> str:
    """Build the downstream prompt that fills exercise details only."""
    lesson_context, context_mode = _exercise_author_lesson_context(lesson_md)
    operation_scope, route_scope = _exercise_prompt_policy_scope(exercise_requests_yaml)
    prompt = (
        "You are the exercise-author node in a Norwegian lesson pipeline. The lesson "
        "Markdown below is immutable source prose authored by another node. Generate "
        "only the final exercises YAML; never rewrite, summarize, or return lesson "
        "Markdown. Each compact checkpoint request must become exactly one exercise. "
        "Preserve the request order, stable handle, objective reference, and Bloom "
        "level; do not merge, skip, duplicate, retag, or replace a request with a "
        "different evidence goal.\n\n"
        "Treat each request's `evidence_route` and `evidence` statement as the "
        "complete evidence contract. The route names the operation that can collect "
        "that evidence; the statement names the observable learner evidence. You own "
        "everything else: the complete standalone prompt, answer material, "
        "distractors, feedback, and open-response criteria. If a request asks the "
        "learner to explain, classify, or distinguish several error types, choose an "
        "operation that can collect that evidence or fail the stage; a single token "
        "tap cannot stand in for an explanation or a relational word-order swap.\n\n"
        "Every exercise must be understandable and performable from its own "
        "learner-visible payload. The immutable lesson is grounding for what has "
        "been taught; it is not runtime context. Restate in the exercise payload "
        "every fact, item, phrase, or situation the task needs: the words being "
        "manipulated, the dialogue lines being answered, the sentence being judged, "
        "the frames being practiced. Never refer the learner to a dialogue turn, a "
        'checkpoint, another section, an earlier exercise, or anything "above"; a '
        "task that only works after re-reading the lesson is defective. Document "
        "position may inform what the learner has already been taught, never what "
        "the payload may omit.\n\n"
        "Choose the operation required by each request's `evidence_route`; do not "
        "change the route or substitute a merely Bloom-compatible operation. The "
        "route-to-operation registry is the evidence contract, while Bloom remains "
        "a separate cognitive-level constraint. Do not default to "
        "choose: use recognition/meaning selection for understanding, recall_fill or "
        "match_pairs for bounded retrieval, build/find_fix/judge for controlled use or "
        "repair, speak for a spoken response when the lesson calls for it, and write "
        "for genuinely open Norwegian production. Vary operations when the requests "
        "call for different evidence. Do not invent an operation. Do not emit "
        "`derived_from` or any other internal provenance field; the public payload "
        "is self-contained.\n\n"
        "Use the repository's existing operation schema: top-level YAML list items have "
        "`handle`, `op`, `objective`, `bloom`, `prompt_md`, and optional "
        "`explanation_md`; operation-specific "
        "payloads must use the supported fields. A choose item has option mappings "
        "with `id`, `text`, `correct`, and optional `why`. A build item has `tokens` "
        "with `token_id` and `text`, plus `answer_order`. A find_fix item has `tokens`, "
        "`error_token_id`, and `feedback`. A recall_fill item has an explicit Norwegian "
        "`audio_target` plus `segments` alternating "
        "plain `{text_md: ...}` spans and `{blank_id, options: [strings], answer_index}` "
        "blanks using those fields directly. A write item has `response_language`, optional "
        "`min_words`/`max_words` bounds, `judge_prompt`, and non-empty criteria. Preserve exact Norwegian "
        "function words and punctuation from the lesson.\n\n"
        "Base authored answer examples and distractors on the taught target forms. "
        "Keep prompts, explanations, feedback, and judge instructions in English; "
        "keep Norwegian target content and answer choices in natural Bokmål with the "
        "lesson's translations. Apply answer-policy precedence in this order: first "
        "enforce every explicit requirement in the request and visible prompt, including "
        "the requested meaning, tense, construction, frame, and gender/form; a response "
        "missing one of those features is not an equivalent answer. Then accept natural "
        "variants only within those explicit constraints. Examples and sample answers "
        "are illustrative unless the visible task explicitly makes them exhaustive, so "
        "do not infer a restriction from an example alone. For a single-answer operation "
        "such as categorize or choose, score the request's primary evidence dimension; "
        "do not force a sentence that illustrates multiple patterns into mutually "
        "exclusive categories without saying which dimension is being scored. Do not add "
        "a second lesson objective. If "
        "a request cannot be expressed by a legal supported operation, preserve the "
        "request and fail the stage rather than silently changing its evidence goal.\n\n"
        "Payload quality rules: use disjoint items for `categorize`; never place an item "
        "in two conceptual buckets and hope the learner guesses a priority. For "
        "`build`, include exactly the tokens that belong in the answer; this schema has "
        "no distractor-token field. Keep distractors in `choose` instead of adding them "
        "to a build token list. For "
        "`find_fix`, target one unambiguous local token error; use `build`, `choose`, or "
        "another supported operation when the learner must reorder elements or classify "
        "an error type. For `recall_fill`, each blank should test one coherent taught unit "
        "with answer options of the same grammatical shape; do not bundle partial and "
        "complete phrases as competing answers unless the distinction is the target. For "
        "requests that require changed-situation transfer, change the communicative "
        "situation while keeping the taught language usable; do not copy a complete "
        "target sentence verbatim from the lesson. Bounded retrieval of an explicitly "
        "requested taught phrase may reuse that phrase; do not change its evidence "
        "goal to force novelty. For "
        "`write`, align `prompt_md`, any word bounds, `criteria`, and `judge_prompt`: "
        "every assessed requirement must be visible, and every requested requirement "
        "must be assessed. Do not add restrictions such as no extra details or items "
        "unless the request requires them and the visible prompt states them. "
        "Add word bounds only when the evidence goal needs them, "
        "and state them in the visible prompt. Check that a valid alternative within "
        "the explicit constraints would pass and that a response missing a requested "
        "feature would fail. Keep incidental vocabulary "
        "and response length appropriate to the learner level and evidence goal.\n\n"
        "For closed choices, use plausible target-related confusions with comparable "
        "specificity; avoid giving away the key through length or irrelevant detail. "
        "Use supported feedback fields or optional `explanation_md` for a concise "
        "English explanation of the target decision or a likely error. Keep feedback "
        "consistent with the keyed answer and accepted variants. Omit optional feedback "
        "that only restates the answer; do not duplicate a rationale across fields. "
        "Keep answers and feedback out of the learner prompt.\n\n"
        "Build invariant: every `token_id` in one build's `tokens` list must be unique, "
        "and `answer_order` must contain each of those IDs exactly once. If the same "
        "word appears twice in the target, give its two token mappings different IDs; "
        "never repeat an ID in `answer_order`, omit an ID, or invent a second occurrence "
        "without a corresponding token mapping.\n\n"
        "For `write`, `criteria` must be a YAML list of mappings, each with a stable "
        "`id` and an `instruction` string; do not emit bare criterion strings.\n\n"
        "Operation/Bloom/payload policy (copy the request Bloom; choose only "
        "a compatible operation):\n"
        "The internal build-stage labels used by factory diagnostics are not source "
        "fields. Never emit `stage` or `build_stage` in an exercise mapping.\n"
        + render_operation_guidance(operation_scope)
        + "\n\nConcrete operation payload shapes (copy these source fields literally; "
        "the names in parentheses are documentation, not YAML keys):\n"
        + render_operation_payload_guidance(operation_scope)
        + "\n\nEvidence-route registry (the request route is authoritative):\n"
        + render_evidence_route_guidance(route_scope)
        + "\n\n"
        "Never use `write` for a request whose Bloom is `understand`; the public `write` "
        "operation is reserved for open Norwegian production and is not a general text "
        "answer box. For an understanding request that asks for labels, meanings, or "
        "multiple interpretation parts, encode the complete interpretations as options "
        "in one `choose` item, or use `recall_fill`/`judge` when their payload "
        "fits. If no supported operation can preserve the evidence goal, fail rather "
        "than retag the request.\n\n"
        "For inline Markdown blanks, use the literal `[BLANK]` marker; never use `__` "
        "or a run of underscores.\n\n"
        "Completion criterion: every request becomes exactly one schema-valid exercise "
        "in order, preserving identity, Bloom, and evidence goal with a defensible answer.\n\n"
        "Return one JSON object with exactly one string field, `exercises_yaml`. "
        "Do not include `lesson_md`, exercise requests, coverage, transcript, or prose.\n\n"
        "Approved plan:\n---BEGIN PLAN---\n"
        + plan_text
        + "\n---END PLAN---\n\n"
        + f"Immutable lesson context ({context_mode}):\n---BEGIN LESSON CONTEXT---\n"
        + lesson_context
        + "\n---END LESSON CONTEXT---\n\nExercise requests:\n---BEGIN REQUESTS---\n"
        + exercise_requests_yaml
        + "\n---END REQUESTS---\n"
    )
    if terminology_context:
        prompt += (
            "\nSelected terminology registry context (use only labels actually taught by "
            "the immutable lesson):\n" + terminology_context + "\n"
        )
    return prompt


def build_lesson_repair_prompt(
    draft_prompt: str,
    draft_text: str,
    review: LessonQualityReview,
) -> str:
    """Build one bounded exact-edit prompt from the exact findings."""
    del draft_prompt
    findings_yaml = cast(
        str,
        yaml.safe_dump(
            _lesson_finding_payloads(review),
            allow_unicode=True,
            sort_keys=False,
        ),
    )
    return (
        "Bounded draft repair instructions:\n"
        "You are an exact-edit lesson editor. The independent "
        "reviewer rejected the draft below with the exact findings that follow. Do "
        "not regenerate, rewrite, summarize, or return a replacement chapter. "
        "Return only JSON matching the schema below with literal old_text/new_text "
        "replacements. Keep every correct dialogue turn, example, translation, "
        "explanation, and practice request. Apply every material finding as a set: "
        "search the complete chapter for all repetitions or semantically equivalent "
        "statements of each diagnosed claim, including activity success meanings, "
        "transfer bullets, tables, and recap text, and provide one or more exact "
        "edits per finding reference. Every old_text must be a unique byte anchor "
        "that occurs exactly once in the supplied draft; use a longer local anchor "
        "when a phrase repeats. Anchors must not overlap, and no edit may depend on "
        "text produced by another edit: all replacements apply to the original draft. "
        "Do not use a "
        "whole-draft old_text. Do not introduce a broader rule while repairing a "
        "narrow one. Whenever an edit adds or increases a learner action, it must "
        "also add or clarify the required language or skill prerequisite before "
        "that task. Before returning, privately check the complete edited draft "
        "so no contradictory sentence remains and every repaired task is teachable. "
        "Completion criterion: every material finding reference has at least one exact "
        "edit and every repeated instance of its diagnosed claim is resolved.\n\n"
        "---BEGIN DRAFT UNDER REPAIR---\n" + draft_text + "\n---END DRAFT UNDER REPAIR---\n\n"
        "---BEGIN REVIEW FINDINGS---\n" + findings_yaml + "---END REVIEW FINDINGS---\n"
        "\n---BEGIN RESPONSE SCHEMA---\n"
        + json.dumps(LessonDraftEditResponse.model_json_schema(), ensure_ascii=False)
        + "\n---END RESPONSE SCHEMA---\n"
    )


def build_preservation_repair_prompt(
    normalization_prompt: str,
    package: NormalizedPackage,
    review: NormalizationPreservationReview,
) -> str:
    """Build one representation-only exact-edit normalization prompt."""
    del normalization_prompt
    findings_yaml = cast(
        str,
        yaml.safe_dump(
            _normalization_finding_payloads(review),
            allow_unicode=True,
            sort_keys=False,
        ),
    )
    return (
        "Representation repair instructions:\n"
        "You are an exact source-structure editor. The "
        "normalization-preservation reviewer found the exact defects below. Return "
        "only JSON matching the response schema with literal old_text/new_text "
        "replacements against the previous fields. Do not regenerate or return a "
        "complete lesson or request YAML. The original prose draft is protected: "
        "restore only the dropped or semantically changed units and fix only the "
        "reported structure defects. Do not rewrite learner-facing prose, merge or "
        "drop exercise requests, or invent new content. Address each finding "
        "reference with one or more edits as needed. Every old_text must be a "
        "unique byte anchor occurring exactly once in its named previous artifact. "
        "Anchors within an artifact must not overlap or depend on another edit: all "
        "replacements apply to the supplied previous fields. Completion criterion: every finding "
        "reference is addressed and protected prose outside those repairs is unchanged.\n\n"
        "---BEGIN PREVIOUS LESSON---\n" + package.lesson_md + "\n---END PREVIOUS LESSON---\n\n"
        "---BEGIN PREVIOUS REQUESTS---\n" + package.exercise_requests_yaml + "\n---END PREVIOUS REQUESTS---\n\n"
        "---BEGIN PRESERVATION FINDINGS---\n" + findings_yaml + "---END PRESERVATION FINDINGS---\n"
        "\n---BEGIN RESPONSE SCHEMA---\n"
        + json.dumps(NormalizationEditResponse.model_json_schema(), ensure_ascii=False)
        + "\n---END RESPONSE SCHEMA---\n"
    )


def build_exercise_repair_prompt(
    base_prompt: str | None = None,
    *,
    plan_text: str | None = None,
    lesson_md: str | None = None,
    exercise_requests_yaml: str | None = None,
    failed_handles: list[str],
    verification: dict[str, Any],
    learner_visible_payload: list[dict[str, Any]],
) -> str:
    """Ask the exercise author to replace failed handles only."""
    if plan_text is not None and lesson_md is not None and exercise_requests_yaml is not None:
        scoped_requests = _filter_exercise_requests_yaml(exercise_requests_yaml, failed_handles)
        retry_base = build_exercise_author_prompt(
            plan_text=plan_text,
            lesson_md=lesson_md,
            exercise_requests_yaml=scoped_requests,
        )
        retry_context = scoped_requests
    elif base_prompt is not None:
        retry_base = base_prompt
        retry_context = exercise_requests_yaml or "(request handoff omitted by legacy caller)"
    else:
        raise ValueError("exercise repair prompt requires a base prompt or scoped lesson inputs")
    return (
        f"{retry_base}\n\n"
        "This is a bounded exercise-only repair. The lesson Markdown is immutable. "
        f"Replace ONLY these failed handles: {failed_handles!r}. Keep every other "
        "exercise byte-for-byte equivalent after deterministic normalization. Do not "
        "change any request handle, objective, Bloom level, or evidence goal. Return "
        "one item for each failed handle; omit unaffected handles. Completion criterion: "
        "return every failed handle exactly once with its identity and evidence goal preserved.\n\n"
        "Verifier diagnostics (treat these as concrete defects, not a reason to rewrite "
        "the lesson):\n"
        f"{json.dumps(verification, ensure_ascii=False, indent=2)}\n\n"
        "The following is the learner-visible payload for the failed handles. It is deliberately "
        "keyless: do not ask for or infer authored answer assignments from it. Use the request "
        "and diagnostics to produce a newly solvable replacement.\n"
        "---BEGIN FAILED LEARNER PAYLOAD---\n"
        f"{json.dumps(learner_visible_payload, ensure_ascii=False, indent=2)}\n"
        "---END FAILED LEARNER PAYLOAD---\n"
        "The scoped request handoff for these handles is:\n"
        "---BEGIN FAILED REQUESTS---\n"
        f"{retry_context}\n"
        "---END FAILED REQUESTS---\n"
    )


def _lesson_finding_payloads(review: LessonQualityReview) -> list[dict[str, Any]]:
    """Add stable per-occurrence references to lesson findings for an editor."""
    code_counts: dict[str, int] = {}
    payloads: list[dict[str, Any]] = []
    for finding in review.findings:
        code_counts[finding.code] = code_counts.get(finding.code, 0) + 1
        payload = finding.model_dump(mode="json")
        payload["finding_ref"] = f"{finding.code}:{code_counts[finding.code]}"
        payloads.append(payload)
    return payloads


def _normalization_finding_payloads(
    review: NormalizationPreservationReview,
) -> list[dict[str, Any]]:
    """Add stable per-occurrence references to preservation findings."""
    category_counts: dict[str, int] = {}
    payloads: list[dict[str, Any]] = []
    for finding in review.findings:
        category_counts[finding.category] = category_counts.get(finding.category, 0) + 1
        payload = finding.model_dump(mode="json")
        payload["finding_ref"] = f"{finding.category}:{category_counts[finding.category]}"
        payloads.append(payload)
    return payloads


def _filter_exercise_requests_yaml(requests_yaml: str, handles: list[str]) -> str:
    """Serialize only failed exercise intents for a bounded repair prompt."""
    raw = load_unique_yaml(requests_yaml)
    if not isinstance(raw, list):
        raise TypeError("exercise request handoff must be a list for repair scoping")
    wanted = set(handles)
    scoped = [item for item in raw if isinstance(item, dict) and item.get("handle") in wanted]
    if {item.get("handle") for item in scoped} != wanted:
        raise ValueError("exercise repair request scope does not match failed handles")
    return cast(str, yaml.safe_dump(scoped, allow_unicode=True, sort_keys=False))


def _exercise_prompt_policy_scope(
    exercise_requests_yaml: str,
) -> tuple[tuple[str, ...] | None, tuple[str, ...] | None]:
    """Select only route-relevant operation schemas for one exercise prompt."""
    try:
        raw = yaml.safe_load(exercise_requests_yaml)
    except yaml.YAMLError:
        return None, None
    if not isinstance(raw, list):
        return None, None
    routes: list[str] = []
    operations: list[str] = []
    for request in raw:
        if not isinstance(request, dict):
            continue
        route = request.get("evidence_route")
        if not isinstance(route, str) or route in routes:
            continue
        routes.append(route)
        for operation in evidence_route_operations(route):
            if operation not in operations:
                operations.append(operation)
    if not operations or not routes:
        return None, None
    return tuple(operations), tuple(routes)


def _exercise_author_lesson_context(lesson_md: str) -> tuple[str, str]:
    """Return the full immutable lesson for grounding the exercise author."""
    return lesson_md, "full immutable lesson"


__all__ = [
    "build_exercise_author_prompt",
    "build_exercise_repair_prompt",
    "build_lesson_repair_prompt",
    "build_lesson_review_prompt",
    "build_normalization_prompt",
    "build_preservation_repair_prompt",
    "build_preservation_review_prompt",
    "build_rich_draft_prompt",
]
