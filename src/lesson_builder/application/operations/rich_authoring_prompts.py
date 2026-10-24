"""Entry points: rich-authoring prompt builders.

Application-owned prompt construction for rich-authoring stages. These builders
render approved plans, immutable prose, and typed policy guidance into model
instructions; provider invocation and response validation stay in workflow and
client layers.
"""

from __future__ import annotations

import yaml

from lesson_builder.domain.lesson.models.operations import evidence_route_operations
from lesson_builder.domain.lesson.models.operations import render_evidence_route_guidance
from lesson_builder.domain.lesson.models.operations import render_operation_guidance
from lesson_builder.domain.lesson.models.operations import render_operation_payload_guidance
from lesson_builder.domain.lesson.services.quality_review import quality_axes_for_kind

K_RICH_GRAMMAR_DRAFT_GUIDANCE = """
Grammar chapter shape (apply this only when the approved plan kind is grammar):
- Teach each approved grammar decision completely: show aligned Norwegian
  examples with English translations, contrast the relevant forms and meanings,
  state a compact rule, and correct one likely misconception.
- Keep the grammar scope bounded by the approved teaching points and
  prerequisites. Do not ask the learner to classify an abstract clause before
  showing what the form helps them say.
""".strip()
K_RICH_GRAMMAR_NORMALIZATION_GUIDANCE = """
Grammar preservation requirements:
- Preserve the meaning-first explanation, aligned form/meaning contrasts,
  compact rules, and misconception corrections; do not replace them with a
  generic grammar summary.
""".strip()


def build_rich_draft_prompt(
    plan_text: str,
    *,
    plan_kind: str | None,
    terminology_context: str | None = None,
    size_guidance: str | None = None,
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
        "`handle: stable_handle`\n"
        "`objective_ref: approved-objective-id`\n"
        "`bloom: level`\n"
        "`evidence: One concise sentence of observable learner evidence.`\n"
        "`}}`\n"
        "Keep each directive on its own lines. Do not write a prose label such as "
        "`Checkpoint activity:` or repeat the directive metadata outside the block. "
        "The handle is a stable snake_case name, the objective is one approved plan "
        "objective id, and the Bloom level is one of remember, understand, apply, "
        "analyze, and the evidence sentence names observable learner evidence. Give "
        "each checkpoint one primary scored evidence goal and one cohesive learner "
        "submission. If one sentence illustrates several target patterns, say which "
        "dimension is scored and which patterns are only context; split genuinely "
        "different scored goals into separate checkpoints rather than relying on "
        "mutually exclusive categories. Split only when the requested outputs can be "
        "attempted, omitted, judged, and remediated independently: repeated "
        "homogeneous items and one cohesive multi-part product stay as one "
        "submission, and word, item, or criterion counts are not the rule. Do not "
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
        "natural Bokmål. Translate teaching examples, but never supply an exercise target "
        "answer through a translation, gloss, or copied form. Introduce a necessary term "
        "in plain English with its established Norwegian label, for example `past tense "
        "(preteritum)`, then use it consistently. "
        + (size_guidance or "Develop each approved decision without a length or section-count target.")
        + " Each section must add a distinction, "
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
        f"Score every rubric axis 0-2: {list(expected_axes)!r}. "
        "Every score needs draft-grounded rationale. Report each possible concern "
        "with its location, an exact source excerpt as evidence, and rationale. Every finding is "
        "an unverified AI suggestion. Findings never authorize edits, require the "
        "author to apply them, or block approval. Use major severity for material "
        "concerns and minor for copy polish. Scan the entire draft line by line and "
        "report possible concerns in the same response. Search for "
        "every occurrence of a claim, including repeated rule prose, tables, "
        "dialogue explanations, checkpoint evidence statements, activity purpose/success text, "
        "transfer tasks, and the retrieval recap. Report independent defects in this pass. "
        "Do not present a linguistic judgment as verified fact; lack of an example "
        "in this lesson is not evidence of a contradiction. Treat plan wording "
        "as a scope check: flag a genuinely over-broad rule only when evidence shows "
        "that its stated scope is wrong, not merely because this lesson teaches fewer "
        "cases. "
        "Report any dedicated explanation or scored learner action outside the approved "
        "objectives as a prerequisite_scope_breach or pedagogy_gap. A brief recognition "
        "example may support the target, but a second outcome must not receive its own "
        "rule, practice, or transfer task. "
        "Judge practice checkpoints by their placement, objective, Bloom level, and "
        "one-sentence observable evidence: a typed directive carries intent only, so "
        "do not require activity prose, context narration, or a success-response description, "
        "and flag checkpoint wording that leans on earlier material instead of naming "
        "observable evidence. Report a checkpoint evidence statement that bundles "
        "independently attemptable, omittable, judgeable, and remediable outputs as "
        "a `practice_gap` finding with major severity and rationale. "
        "A cohesive dialogue, "
        "report, or operation-native homogeneous collection is one submission, not "
        "a compound deliverable, so do not report it as a gap merely for containing "
        "several turns, items, or criteria. "
        "For every dialogue, check each adjacent question/answer pair for pragmatic fit "
        "and keep named places, people, and other entities consistent across turns and "
        "translations. Completion criterion: score every rubric axis once and report "
        "possible material concerns across the complete draft as unverified suggestions.\n\n"
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
        "preserve it and let the later advisory review report it. The approved plan remains "
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
        "when quotation marks are themselves being taught. Never group multiple "
        "`no:` items before their `en:` values.\n"
        "- Examples default to `teaching_role=model`. Put wrong forms in their own "
        "`::: {.examples teaching_role=incorrect}` block and discouraged but valid "
        "forms in `::: {.examples teaching_role=caution}`. Keep one role per block; "
        "adjacent blocks still render as one ordered group. Do not add `✗`, "
        "`Incorrect:`, or other status text to the learner sentence.\n"
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
        "`handle`, `objective_ref`, `bloom`, `evidence_route`, and `evidence`. Copy "
        "handle, objective_ref, bloom, and evidence exactly from the supplied "
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
        "- The normalizer adds the metadata, typed blocks, section IDs, and markers "
        "required by the contract; it must not "
        "rewrite learner-facing prose for brevity, merge or drop requests, or impose the "
        "old typed brief, fixed move list, coverage response, or a request-count limit. "
        "Do not make exercise decisions in this stage beyond selecting the evidence "
        "route: preserve the supplied intent byte-for-byte, and do not make payload or "
        "answer-key decisions. Completion criterion: every protected teaching unit and "
        "exercise request is preserved in order, lesson_md compiles, and the handoff "
        "contains every required field without operation payloads.\n"
        "Evidence-route registry:\n"
        + render_evidence_route_guidance()
        + "\nRoute-selection policy: `meaning_selection` names one primary "
        "selection, so it fits a checkpoint whose observable evidence is one "
        "chosen meaning or interpretation. A cohesive multi-expression "
        "correspondence, where several independently represented forms each "
        "connect to a meaning or communicative function, is `pair_matching`; "
        "do not collapse such a set into one selection route. Write only the "
        "operation-free handoff fields after selecting the route.\n\n"
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
        "equivalent; losing or failing to encode that status is a possible "
        "preservation loss. Display labels may be "
        "learner-readable and "
        "hyphenated; only exercise markers and request handles are identities. "
        "Report only lost or semantically changed content. This review is advisory: "
        "it does not authorize edits or block the normalized package. Describe the "
        "possible loss for author inspection instead of directing an automatic repair.\n\n"
        "Report each possible loss for author inspection. Do not propose or apply edits. "
        "Compare every protected teaching unit and exercise intent once, then report "
        "any possible loss as an advisory "
        "suggestion.\n\n"
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
        "distractors, feedback, and open-response criteria. If the mapped operation "
        "cannot collect the requested evidence, fail the stage rather than weaken "
        "the evidence goal.\n\n"
        "Put every fact and source text needed to attempt the task in its "
        "learner-visible payload. Lesson prose is teaching context, not attempt "
        "context; do not refer the learner to earlier material.\n\n"
        "Choose the operation required by each request's `evidence_route`; do not "
        "change the route or substitute a merely Bloom-compatible operation. The "
        "route-to-operation registry is the evidence contract; preserve the "
        "request's Bloom level as a separate constraint. Do not emit "
        "`derived_from` or any other internal provenance field; the public payload "
        "is self-contained.\n\n"
        "Base authored answer examples and distractors on the taught target forms. "
        "Use the approved plan's CEFR level for instruction language: A1 and A2 task "
        "directions in prompt_md, stem_md, and per-item cues use concise English; B1 and "
        "higher task directions use natural Bokmål. Preserve Norwegian target text, "
        "dialogue, example utterances, and answer choices when the exercise needs them. "
        "Apply the review criterion supplied_answer_retrieval to prompts, stimuli, "
        "stems, options, and segments: temporarily ignore the Norwegian text. If "
        "English labels, prefatory explanations, or parenthetical glosses already "
        "determine the intended bucket, flag supplied-answer retrieval. Ordinary "
        "context and vocabulary translations are useful support when they do not "
        "state the very distinction being tested. Never supply a target form in a "
        "worked sentence beside a recall gap requiring that form. Put answer-revealing "
        "translations and forms in post-answer explanation_md or feedback instead. Hidden "
        "judge instructions and criteria may use English. Apply answer-policy precedence in this order: first "
        "enforce every explicit requirement in the request and visible prompt, including "
        "the requested meaning, tense, construction, frame, and gender/form; a response "
        "missing one of those features is not an equivalent answer. Then accept natural "
        "variants only within those explicit constraints. Examples and sample answers "
        "are illustrative unless the visible task explicitly makes them exhaustive, so "
        "do not infer a restriction from an example alone. For a single-answer operation "
        "such as categorize or choose, score the request's primary evidence dimension; "
        "do not force a sentence that illustrates multiple patterns into mutually "
        "exclusive categories without saying which dimension is being scored.\n\n"
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
        "`write`, align `prompt_md`, structured word bounds, `criteria`, and `judge_prompt`: "
        "every assessed language or content requirement must be visible, and every requested "
        "language or content requirement must be assessed. Do not add restrictions such as no "
        "extra details or items unless the request requires them and the visible prompt states "
        "them. Add word bounds only when the evidence goal needs them, using `min_words` and "
        "`max_words`; do not repeat a numeric word budget in `prompt_md` or make a model judge "
        "count words. "
        "Check that a valid alternative within "
        "the explicit constraints would pass and that a response missing a requested "
        "feature would fail. Keep incidental vocabulary "
        "and response length appropriate to the learner level and evidence goal.\n\n"
        "Write learner-visible directions like concise textbook tasks: begin with a "
        "short imperative in the level's instruction language, and use at most two "
        "sentences. State context once in the stem or stimulus. Put dialogue in "
        "`stimulus` as `kind: dialogue` with ordered `turns` containing `speaker` "
        "and `text_md`. Show binding requested forms compactly. Keep assessor "
        "language, scoring rules, and rubric reasoning out of prompt_md and stem_md.\n\n"
        "For closed choices, use plausible target-related confusions with comparable "
        "specificity; avoid giving away the key through length or irrelevant detail. "
        "Use supported feedback fields or optional `explanation_md` for a concise "
        "Bokmål explanation of the target decision or a likely error. Keep feedback "
        "consistent with the keyed answer and accepted variants. Omit optional feedback "
        "that only restates the answer; do not duplicate a rationale across fields. "
        "Keep answers and feedback out of the learner prompt.\n\n"
        "Operation/Bloom/payload policy (copy the request Bloom; use its mapped "
        "operation):\n"
        + render_operation_guidance(operation_scope)
        + "\n\nConcrete operation payload shapes (copy these source fields literally; "
        "the names in parentheses are documentation, not YAML keys):\n"
        + render_operation_payload_guidance(operation_scope)
        + "\n\nEvidence-route registry (the request route is authoritative):\n"
        + render_evidence_route_guidance(route_scope)
        + "\n\n"
        "Each `choose` option must represent one scored decision; do not bundle "
        "independently judgeable mappings or propositions into one option.\n\n"
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
    "build_lesson_review_prompt",
    "build_normalization_prompt",
    "build_preservation_review_prompt",
    "build_rich_draft_prompt",
]
