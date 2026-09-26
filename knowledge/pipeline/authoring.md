---
type: Domain Decision
title: Authoring and Evaluation Decisions
description: Durable curriculum, exercise, and review choices that code alone cannot explain.
tags: [authoring, curriculum, evaluation]
timestamp: 2026-09-19
---

## Curriculum and practice

One catalog owner is one independently teachable learner decision. The catalog owns
identity, outcomes, scope, exclusions, and prerequisites; the curriculum plan owns
order. Levels describe learner targets, while topics and examples are inputs rather
than catalog units. Exact sequence and rationale belong in
`content/curriculum/sequence.yaml`.

Core modal verbs and conversational particles remain separate owners. Modal verbs
frame the subject's relationship to an action or event; particles such as *vel*,
*jo*, *nok*, and conversational *da* frame the speaker's relationship to a claim
and listener. Within B2 pragmatics, hedging extends familiar particles with open
possibility, indirect source, opinion, and approximation. Advanced pragmatics owns
repair of shared understanding, where the outcome is an accurate shared plan rather
than use of a marker inventory.

Approved curriculum follows one authoring path through independent review and human
acceptance. Automation assists that path but does not create a competing authority.
Generated output is review evidence. See
[source and publication boundaries](../project/boundaries.md).

Exercise redesign is drafted, not authored in place. Local draft exercises and
old-to-new evidence mappings may be kept under ignored `plans/` while they are
reviewed. A draft must pass the deterministic source audit before promotion.
The exact-speech cue check recognizes both English instructions and Norwegian
imperatives such as *si*, *les*, or *gjenta* with *akkurat* or *nøyaktig*; the
target text must still be visible in the learner prompt.
Promotion stays the human path above: review the draft and its mapping, apply it
to source, inspect the preview, and regenerate distribution with stable exercise
ids. Local draft and mapping files are review aids, not published content.

Exercises must stand alone: every fact or situation needed to answer belongs in the
learner-visible payload, not hidden lesson context. Spoken and written modes may be
used wherever they serve the objective. Interaction, presentation, and learner
scoring remain application responsibilities.

Practice moves from focused decisions toward the approved transfer outcome. The
final retrieval checkpoint uses the same target in a changed situation rather than
repeating the opening anchor. Pronunciation and spaced-review tracks stay outside
the default curriculum until their outcomes can be represented and assessed
honestly; text exercises cannot establish pronunciation competence or schedule
learner review.

An exercise normally has one primary scored evidence goal and one cohesive learner
submission. Split a request only when its outputs can be attempted, omitted, judged,
and remediated independently. Repeated homogeneous items and one cohesive multi-turn
or multi-constraint product remain one exercise; word, item, and criterion counts
are not the rule. A closed choice therefore represents one decision among competing
answers, while a genuine correspondence set may use `match_pairs`.

Visible instructions, structured bounds, answers, criteria, and judge instructions
must agree. Word-length limits belong in `min_words` and `max_words`, which the
consumer can display and enforce separately; learner prompts state the language
task without repeating a numeric word budget. Semantic criteria assess the
response's meaning and form rather than asking a model to recount words.
Explicitly requested forms are binding, but examples are not exhaustive answer lists
and valid alternatives within the stated constraints must pass. Feedback should
explain a useful contrast or likely error instead of repeating the key.

Learner-visible prompt and stem wording uses concise textbook language: a short
imperative naming the task, one or two sentences, context given once, and no
restated rule tables, scoring weights, or assessor logic. Rubric reasoning lives in
criteria, judge prompts, and feedback instead.

For A1 and A2 lessons, learner-facing task directions in prompts, stems, and
per-item cues are in concise English. B1 and higher lessons use natural Bokmål
directions. This rule concerns the instruction language: Norwegian target text,
dialogue, example utterances, and answer options stay in the language needed for
the exercise. Keep helpful English glosses and translations. Hidden criteria and
judge instructions may use English. Semantic exercise review flags material
instruction-language breaches for the lesson level or prose addressed to an
author, assessor, or AI. It quotes the evidence and ignores minor stylistic
preferences.

Dialogue context belongs in the common typed `stimulus` field, as ordered dialogue
blocks containing speaker-labelled turns. Speaker identity and utterance text are
authored data; prose such as “Lea says” and “Jonas replies” is not a rendering
contract. This lets consumers render accessible dialogue without parsing punctuation
or inventing narration. Prompts retain task instructions, while consuming applications
own visual treatment. Packet schema 4.1 introduced this optional structure; exercises
without stimulus retain their prior public shape.

Normalization adds compiler structure while preserving reviewed wording, order,
examples, translations, and checkpoint intent. It does not repair pedagogy. Exercise
markers remain at their authored positions; request metadata stays in the compact
handoff or exercise source and never becomes learner-facing prose. Exact syntax and
forbidden forms are owned by schemas, validators, and generated operation guidance.

## Trustworthy review and evaluation

Deterministic checks own knowable correctness: parsing, schemas, identifiers, source
audits, routing, state transitions, and handoff invariants. Model review advises on
naturalness, pedagogy, factual premises, valid alternatives, task/rubric alignment,
and whether a claimed skill is actually demonstrated. Humans retain final authority.

Provider-free evidence scans are triage, not semantic approval. A clean scan records
semantics as unevaluated. A focused practice step may support a broader outcome
without proving it alone; report a mismatch only when the authored claim exceeds
what the learner actually does. Grounded semantic defects block strict verification
for human review even when the mechanical answer key is solvable.

Semantic exercise findings must quote a contiguous passage from the exercise reviewed
and explain the affected learner decision. The verifier checks that the quote is
present; this grounding check does not decide whether the criticism is correct.
Unsupported findings stay visible for human review and never trigger automatic
content repair. Grounded exercise findings may enter the existing bounded,
handle-scoped repair path; unaffected exercises remain intact. Whole-lesson
progression is judged across the sequence, so a useful early drill is not treated
as a failed transfer task in isolation.

For closed options, compare each distractor with the misconception its rationale
claims to test. A Bokmål lexicon can surface suspicious spellings for review, but
absence is not a failure by itself: deliberate learner errors may be nonwords.
Reconstruct filled text and read dialogue turns in order. Check that audio targets,
visible answers, speaker turns, and explanations describe the same situation.
Metalinguistic explanation is useful support; an exercise claiming evidence of
using a form must make the learner use that form in context.

Evaluation must distinguish invalid responses, provider outages, missing identities,
and unevaluated cases from passes. A model judge is never its own sole oracle; pair it
with deterministic checks and human-audited samples. Calibration requires labeled
minimal pairs, provenance, held-out cases, and repeated baselines before numerical
quality claims are trustworthy. The authoring side exports its write-judge bank as a
deterministic, schema-versioned JSON projection with dev-only anchors and held-out
evaluation cases; export fails closed while labels remain seed-pending, and an explicit
override still records a non-approvable label state.
Every negative write-judge case names one primary failed gating criterion; positive
cases name none, and export rejects missing, non-gating, or polarity-inconsistent
failure metadata. The 2026-09 calibration approval is a regression-bank result: the
same adaptive holdouts tuned the evidence and instructions, so it demonstrates
agreement on this bank, not error-rate generalization to unseen learner writing;
fresh frozen-case batches are required before production quality claims.

Live evaluation is opt-in and is not an ordinary deterministic pull-request gate.
Exact commands, suites, cases, budgets, and adapter mechanics are owned by
`package.json`, `evals/promptfoo/`, and evaluator code rather than this decision note.
