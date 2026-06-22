# Golden lessons — the definition of "ideal", and the eval answer key

These are **hand-authored ideal lessons**, one per archetype. They are NOT pipeline output. They serve
two roles (per `plans/2026-06-15-retrospective-why-still-not-ideal.md`):

1. **The definition of done** — what a great lesson looks like, concretely, so quality stops being
   vibes. Every axis the LLM-judge eval scores is demonstrated here.
2. **The eval answer key** — the LLM-judge gate scores compiled `dist/<slug>.json` against the
   matching golden + the rubric below. Authored as the *ideal form of an existing lesson* so the judge
   has a real before/after (cheaper and less abstract than greenfield goldens).

## Golden set

The Phase 1 bake-off is blocked until this set exists. It covers A1-B2 and every supported exercise
operation at least once without forcing every lesson to use every operation.

| File | CEFR | Role |
|---|---:|---|
| `word_order_main_clauses.json` | A1 | Syntax and V2 word order; simple production and error repair. |
| `past_tense.json` | A2 | Morphology/formation; weak and strong verbs; mixed recognition and production. |
| `preterite_vs_present_perfect.json` | B1 | Usage contrast; tense choice by time reference, result, duration, and narrative frame. |
| `formal_vs_informal_register.json` | B2 | Register, audience, directness, and pragmatic fit where dictionary checks are weak. |
| `collocations.json` | B2 | Lexical naturalness and L1-transfer traps with nuanced, non-absolute feedback. |

Operation coverage across the set: `recall_fill`, `match_pairs`, `judge`, `choose`, `categorize`,
`build`, and `find_fix`.

## Exercise grading contract

Goldens must model deterministic app grading. The learner should never need to type an open-ended
answer that requires an LLM to judge.

| Operation | Learner action | Deterministic key | Feedback-only fields |
|---|---|---|---|
| `choose` | Pick one option. | `payload.answer_id` | Per-option `why`. |
| `recall_fill` | Pick/fill one of the supplied blank options. | `blank.answer_index` | `explanation`. |
| `match_pairs` | Match left items to right items. | `payload.pairs`. | `explanation`. |
| `categorize` | Put each item in a bucket. | Each item's `bucket_id`. | `explanation`. |
| `build` | Arrange token chips. Some chips may be pre-fixed with `fixed: true`. | `payload.answer_order`. | `explanation`. |
| `judge` | Choose true/false. | `payload.is_correct`. | `payload.feedback` is reveal text only. |
| `find_fix` | Select/tap the wrong token. | `payload.error_token_id`. | `payload.feedback` is reveal text only. |

`judge.payload.feedback` and `find_fix.payload.feedback` are not learner answer fields. They are plain
Norwegian strings shown after grading, like an explanation.

`build` covers all chip ordering. Sentence assembly and ordered chunk drills use the same payload shape;
chips may be locked with `fixed: true` when the exercise needs scaffolding.

## Variant coverage

The golden set covers the main payload shape for every supported operation. It also intentionally includes a
`build` exercise with a pre-fixed token (`fixed: true`) so renderers and graders exercise the locked-token path.

Known schema variants not currently represented by the goldens:

- `judge` with `is_correct: true` and `feedback: null`.
- `recall_fill` with multiple blanks in one exercise.
- `choose` without `stem`.
- `choose.options[].why: null`.

Those are valid schema branches, but not currently required for the Phase 1 pedagogical golden set. Add them
as separate renderer/schema fixtures if the app needs exhaustive component-state coverage.

## Rubric axes (what the judge scores)
- **On-concept** — teaches what the title promises (past_tense teaches verb *formation*, not only modals).
- **Complete** — covers the core paradigm(s): regular weak classes AND strong vowel-change, in Bokmål.
- **Bokmål-clean** — zero Nynorsk forms (gikk not gjekk, fikk not fekk, var not vore).
- **Sequenced** — declares prerequisites; builds pattern → why → example; no jargon dropped cold.
- **Presentable** — learner-facing titles (NOT the internal objective string); metalinguistic terms
  glossed on first use (`preteritum` = "the simple past"); no `paradigm`-style meta-vocabulary.
- **Answerable** — every `choose`/cloze has a carrier `stem`; exactly one correct answer; per-option `why`.
- **Depth** — includes production exercises (`build`, `find_fix`, `judge`), not recognition-only.

## Deltas from current `dist/*.json` — example target worklist
`past_tense.json` deliberately encodes what the pipeline does NOT yet produce. Each delta
is a gap to close, validated against the schema where possible:

| Delta | Current pipeline | Golden (target) |
|---|---|---|
| Regular weak verb classes (`-et/-te/-de/-dde`) | absent (Nynorsk strong table was C-dropped) | present, Bokmål, with worked table |
| Strong verbs in Bokmål | Nynorsk (`gjekk/fekk`), graded C, dropped | `gikk/fikk/kom/så/var` |
| Section titles | the internal objective string ("Learners can…") | short learner-facing headers |
| Jargon | `preteritum`/`paradigm` unglossed | glossed on first use; `paradigm` removed |
| `choose.stem` (carrier sentence) | built but **stripped at export** (never reaches wire) | present — answerable |
| Production ops (`build`/`find_fix`/`judge`) | none (CLASS_OPS caps to recognition/assembly) | three included |
| `prerequisites` | none (no concept-ordering model) | `["present_tense","modal_verbs"]` |
| `foreign_term` inline span | mostly plain `text` | used for all Norwegian terms (renderer styles it) |

## Notes
- `grounding_mode: "authored_golden"` marks these as hand-authored (not corpus-grounded). The answer
  keys here are authored by a human reviewer, not deterministically verified — that is acceptable for a
  reference artifact, NOT for shipped pipeline output (shipped keys stay deterministic).
- Golden files use the exported lesson-style `kind` discriminator for readability and eval reference, not
  the internal `Lesson` fixture shape. Some fields (`prerequisites`, `choose.stem` with a `blank` span)
  intentionally extend the current export shape — they are the gaps above.
- In these reference files, `find_fix.payload.feedback` is the reveal text for the marked
  `error_token_id` token or token group, not a typed learner answer. Build exercises include punctuation
  when the prompt asks for a full sentence.
- Goldens are quality references, not rigid templates. A generated lesson should match the relevant
  quality bar while choosing exercise types that fit the concept.
