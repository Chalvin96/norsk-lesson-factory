# Lesson schema & contract

The authoring contract every lesson must satisfy, plus the export boundary and its
inverse-completeness limits. This is the code-authoritative reference for the lesson shape; the gate
in [PIPELINE.md](../PIPELINE.md) enforces it.

Authoritative code: `src/lesson_builder/schema/lesson.py`, `elements.py`, `export.py`,
`src/lesson_builder/gen/construct.py`.

## Two shapes, one release boundary

| Shape | Type | Role |
|-------|------|------|
| Internal authoring | `Lesson` | what the pipeline authors and repairs |
| Shipped wire | `ExportedLesson` | what lands in `dist/lessons/<slug>.json` |

The export projection is the release boundary — arbitrary internal JSON is never shipped. A lesson
must pass, in order:

1. `Lesson.model_validate(...)`
2. `to_export_dict(lesson)`
3. `ExportedLesson.model_validate(...)`

Current export schema version: **3.0**.

## Required lesson fields

`key`, `concept_slug`, `grounding_mode`, `title`, `cefr_level`, `goal`, `objectives`, `elements`,
`review_pool`.

`grounding_mode` is required by the schema but the pipeline does not use wiki as prompt context by
default: use `fallback_no_wiki` for normal generation, `grounded` only if an explicit workflow
reintroduces an approved grounding source.

## Elements

`elements` holds teaching `section`s and practice `exercise`s.

Allowed section roles: `orient`, `model`, `contrast`, `recap`. Recommended shape: `orient` → one or
more `model`/`contrast` → mixed exercises → `recap`. Do not emit empty decorative sections to satisfy
the pattern.

## The seven exercise operations

Only these are allowed (`order` is retired — ordered-chip tasks use `build`):

`recall_fill` · `match_pairs` · `judge` · `choose` · `categorize` · `build` · `find_fix`

**Operation rules** (gate/generation logic must preserve these):

- **`judge`** — learner decides if a Norwegian sentence is correct. `payload.is_correct` is boolean;
  if false, `payload.feedback` must be non-empty (reveal text, not a learner answer field).
- **`choose`** — exactly one correct `answer_id`; option `why` explains the real error, not generic
  noise.
- **`recall_fill`** — closed-set blanks (not free-text judged); each blank has options + one answer;
  the number of `___` markers matches the blank list.
- **`match_pairs`** — left/right texts unique; pairs teach real contrasts, not trivial synonyms.
- **`categorize`** — bucket labels unique; items belong to exactly one bucket unless the prompt
  explicitly teaches ambiguity.
- **`build`** — owns all chip-ordering; answer is `answer_order`. `sentence` input → deterministic
  tokenization; `items` input → chunks preserved; fixed chips must exist in the token list.
- **`find_fix`** — learner selects the wrong token; `error_word` occurs exactly once in the
  tokenized sentence; `feedback` explains the correction (not typed correction).

## Language split

Lessons target English-speaking learners of Norwegian. Keep the split consistent:

- **Norwegian** in example sentences, answer material, target forms, contrast carriers
- **English** in prompts, explanations, option `why`, teaching feedback

Do not drift into Norwegian explanatory prose (a known failure mode when prompt context gets too
Norwegian-heavy).

## Review pool

Internal and exported lessons both carry `review_pool`. Every pool card points to an existing
`exercise_id`; pool keys match objective ids.

## What the gate rejects

**Hard rejects:** invalid `Lesson`/`ExportedLesson`; any operation outside the seven; non-determin­
istically gradable answer formats; malformed `build.answer_order` / `find_fix.error_token_id` /
`judge`-false items with empty feedback; Nynorsk leakage outside intentional wrong-answer slots;
missing required concept anchors for gated topics; requests for unavailable media (e.g. audio);
failed independent answer review before export.

**Soft flags (advisory):** thin lessons, repetitive operation mix, weak distractor rationale, likely
usage/collocation disputes, suspiciously Norwegian explanatory text.

## Quality bar (beyond schema-valid)

On-concept · correct · Bokmål-clean · deterministically answerable · complete for the concept ·
coherently sequenced · varied across operations. Hard error classes that survive structural
validation (and why the human gate stays): subtle usage judgments, collocations, register fit, V2
after fronting, `ikke` placement, tense-choice contrasts.

---

## Export inverse-completeness (`to_export_dict`)

`to_export_dict` is **lossy** at the lesson, section, and exercise level. That is acceptable because
`dist/` is now a derived serving projection regenerated from `data/lessons/`; recovery does not invert
exports back into the internal authoring model.

**Not recoverable** from a dist export (placeholder/sentinel on import):

- `Lesson.objectives[].statement` and `.bloom_targets`
- `Section.objective_ids` — export has no section→objective link
- `Section.id` — synthesized on import
- per-exercise `bloom_level` — exported exercise carries none

**Recoverable:** top-level `key`/`concept_slug`/`grounding_mode`/`title`/`cefr_level`/`goal`;
`elements` (section blocks + exercise prompt/explanation/payload); `review_pool` (verbatim);
objective ids + per-exercise `objective_id` — **only when `review_pool.pools` is non-empty**.

**Not recoverable when `review_pool.pools` is empty** (e.g. the 5 goldens: `objectives: 1, pools: 0`):
objective ids live nowhere except `review_pool.pools[].objective_id`. Import synthesizes a single
`o1` placeholder and assigns it to every exercise / `model` / `contrast` section. This is round-trip
safe because `objectives`, exercise `objective_id`, and section `objective_ids` are all
export-dropped — re-export is **validated-dict-equal** to the original dist
(`ExportedLesson.model_validate(...).model_dump()` on both sides; not byte equality).

**Ambiguous `objective_id` tie-break:** three lessons (`indefinite_articles`, `hvor_vs_der`,
`passive_bli_and_s`) place the same exercise in multiple review-pool objectives. Import resolves
deterministically — first pool in document order wins (`_exercise_objective_map` uses `setdefault`).
Unaffected on re-export since `objective_id` is export-dropped.

**Consequence:** the export boundary stays intentionally one-way for serving. Internal lesson fidelity
must be preserved in git-tracked `data/lessons/`; dist exports are regenerated from that source rather
than treated as recovery inputs.
