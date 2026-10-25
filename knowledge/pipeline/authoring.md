---
type: Domain Decision
title: Authoring and Evaluation Decisions
description: Curriculum scope, supported source syntax, exercise authoring, drafting, and review.
tags: [authoring, curriculum, evaluation]
timestamp: 2026-10-22
---

## Curriculum and practice

One catalog entry represents one independently teachable learner decision. The
catalog defines identity, outcomes, scope, exclusions, and prerequisites; the
curriculum sequence defines order in `content/curriculum/sequence.yaml`.
Topics and examples are inputs, not catalog units. Use labels from the shared
glossary consistently, keep early titles concise, and introduce technical terms
only when useful; explain them plainly and add a Norwegian label where it helps.

Keep modal verbs and conversational particles as separate teaching owners. Modal
verbs frame a subject's relationship to an action or event. Particles such as
*vel*, *jo*, *nok*, and conversational *da* frame the speaker's relationship to a
claim and listener. B2 hedging builds on these particles to express uncertainty,
inference, shared assumptions, source doubt, opinion, and approximation.
Advanced pragmatics focuses on repairing a misunderstanding by clarifying
intent and establishing an appropriate next step.

Practice should move from focused decisions toward transfer. A final retrieval
task should apply the same target in a changed situation. Lessons do not train
or grade speech production: learners listen to model audio, and roleplay is text
only. Pronunciation outcomes in the approved catalog cover perception and recognition.

## Lesson Markdown syntax

Lesson prose supports paragraphs, **bold**, *italic*, inline code, `[BLANK]`,
bullet lists, and decimal ordered lists (`1.` or `1)`). Letter and Roman
markers such as `a.` or `IV)` are ordinary text. List items must contain a
single inline-only block.

Lesson sections use level-2 headings with explicit attributes on the same
line, such as `## Greetings {#sec-greetings objectives=obj_greet}`.
Identifiers are required; the reader never generates heading ids. Fenced divs
use `::: rule` or `::: {.callout variant=note}` with the attribute set on the
opening line, a body, and a closing `:::`. Attribute sets support `#id`,
`.class`, and `key=value` with bare, single-quoted, or double-quoted values.
Bracketed spans such as `[hei]{lang=nb}` carry attributes. Pipe tables must be
wrapped in a div with `col_langs`, such as `::: {col_langs="nb,en"}`.

The lesson mappers reject links, images, raw HTML (including `<span>` and
`<div>`), hard line breaks, strikethrough, footnotes, definition lists, block
quotes, code blocks, horizontal rules, and unwrapped tables. Unknown div kinds
and unsupported heading levels also fail validation. Math and citation syntax
remain literal text; they have no dedicated authoring support.

The reader deliberately follows CommonMark where Pandoc differs: headings
and bullet lists can start without a preceding blank line, a decimal list can
interrupt prose only when it starts at 1, and intraword `___` blanks remain
literal underscores. Explicit heading ids remain mandatory.

## Exercise authoring

Exercises stand alone: include every fact and situation needed to answer in the
learner-visible prompt or stimulus. The consuming application owns presentation,
interaction, and learner scoring.

An exercise normally has one primary evidence goal and one cohesive submission.
Split it only when its outputs can be attempted, omitted, judged, and remediated
independently. Repeated homogeneous items and cohesive multi-turn products remain
one exercise.

Instructions, structured bounds, answers, criteria, and judge instructions must
agree. Put word limits in `min_words` and `max_words`; learner prompts state the
task without repeating a numeric budget. Examples do not limit valid answers, and
feedback should explain a useful contrast or likely error rather than repeat the
key.

Learner prompts and stems use concise textbook wording: name the task, give
context once, and leave rubric reasoning to criteria, judge instructions, and
feedback. A1 and A2 directions use concise English; B1 and higher directions use
natural Bokmål. Norwegian targets, dialogue, examples, and answer options remain
in the language needed for the task. Keep helpful English glosses and
translations when they do not state the distinction being tested. Exercise
prompts, stimuli, stems, options, and recall segments must not disclose a target
answer through a translation or a copied worked form. Answer-revealing
translations belong in post-answer explanations or feedback. This follows the
reviewer's `supplied_answer_retrieval` criterion.

Dialogue context belongs in the typed `stimulus` data as ordered dialogue blocks
with speaker-labelled turns. Consumers can render the authored speaker and text
accessibly without inferring dialogue structure from prose or punctuation.

Learners produce Norwegian only in writing. Check a productive objective with
`write` (type a response) or `build` (arrange supplied words). The loader
rejects any operation outside the registry.

## New lesson drafting

Workbench New lesson uses a complete same-level package as a format exemplar.
Its family starts as Unassigned, with a plan note and provisional slot flag.
The author may approve Unassigned or choose a family by learner outcome from
`content/catalog/registry/catalog_families.yaml`. The exemplar establishes format,
not family fit.

The provider supplies teaching points, terminology, objectives, coverage, success
criteria, exclusions, and the plan body. The application writes identity, kind,
title, CEFR, outcome, family, and prerequisites from the curriculum slot into
`plan.md`, and `type`, `slug`, `title`, `cefr_level`, and `goal` into `lesson.md`
frontmatter, keeping the provider's other header fields. Echoed slot identity and
server-owned metadata are discarded, including plan objective `bloom_targets` and
the document `type` marker. Other authored fields remain strictly validated;
lesson objectives and exercise Bloom levels retain their own validation.

Drafts require 2–5 concrete teaching points, each naming a teachable form,
contrast, or decision derived from the outcome. Invalid counts enter the complete
package repair loop. An outcome-copy fallback in a repair prompt must be expanded.
The checked points must match in the catalog, slot, and plan, including after a
concurrent insertion changes the slot position.

The combined `workbench_author` prompt uses CEFR size guidance from
`K_APPLICATION_NEW_LESSON_SIZE_TARGETS` in `application/settings.py`, overriding
the separate prose stage's omission of size targets. Each range is the middle
half of canonical lessons at that level. Source words count
whitespace-separated tokens in the Markdown body, including markup and bilingual
examples. These are guidance, not compiler limits or quotas. Every section and
exercise must serve the outcome; narrow outcomes may justify shorter drafts.
Do not pad or split cohesive exercises to reach a count.

## Review and evaluation

Deterministic checks own knowable correctness such as parsing, schemas,
identifiers, source audits, routing, and state transitions. Model review can
advise on naturalness, pedagogy, factual premises, valid alternatives, and
task-to-rubric alignment. Its findings are advisory; humans retain final
authority. See [authoring and publication boundaries](../project/boundaries.md)
for save and promotion requirements.

Exercise review findings combine nested issues for one exercise/check in report
order. Each reason has an English category label; evidence quotes, English
glosses, and suggested changes stay separate from the raw report in Details.
Cards, Fix, and Ask chat show the reason and evidence.
Owner decisions carry over only for the same normalized issue set,
using category and normalized evidence together with the original cited excerpt;
added issues or changes to category, evidence, or citation reopen the card.
Rewording reasons, suggested changes, or English glosses preserves the decision.
Issue order and whitespace do not change that set. Resolution matching retains
the original cited excerpt separately from the display excerpt, and uses finding
identity when there is no citation. Without evidence, it uses a short source quote
from the reason when available and otherwise omits the excerpt; it never uses
the serialized exercise as an excerpt.

Workbench review runs its independent lesson and exercise checks concurrently
within a bounded worker pool. Results keep a stable finding and summary order
regardless of completion order. Each call owns a runner and
inherits the review cancellation context; a failed call cancels its siblings.

Evaluation must distinguish invalid responses, provider outages, missing
identities, and unevaluated cases from passes. A model judge is never its own
sole oracle; pair it with deterministic checks and human-audited samples.
Numerical quality claims require labeled cases, held-out examples, and repeated
baselines.

For reviewer model, reasoning, or prompt changes, run
`npm run eval:promptfoo -- -c evals/promptfoo/reviews.yaml`. This provider-backed
suite checks controlled regressions; it has no expert labels and cannot approve
content.
