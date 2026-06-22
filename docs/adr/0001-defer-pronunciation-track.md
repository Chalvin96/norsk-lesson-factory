# ADR-0001 — Defer the pronunciation track + spaced-review nodes

- **Status:** Deferred (needs a schema spike before implementation)
- **Date:** 2026-06-17
- **Deciders:** curriculum-redesign review (Opus + Codex GPT-5.5)

## Context

The pipeline only ships **deterministically gradable** exercises — the seven operations (`choose`,
`recall_fill`, `build`, `find_fix`, `judge`, `categorize`, `match_pairs`; see [SCHEMA.md](../SCHEMA.md)).
A Babbel comparison flagged **pronunciation** as the single biggest real gap: Babbel's NOR-for-English
course interleaves ~10 pronunciation lessons across A1–A2 (`å`/`o`, `ø`/`o`, `ei`, soft `g`/`k` before
front vowels, silent/final consonants, `kj`/`sj`/`skj`, etc.). This repo has only **2**, both end-loaded
at the close of A1 (`pronunciation_basics`, `kj_sj_skj_contrast`) — not a real phonology track.

Pronunciation ("does the learner produce /ç/?") and spaced-review ("re-surface 3 prior slugs") are not
obviously gradable as text MC/fill-in. Forcing them into the current schema would regenerate **hollow
lessons** — the same failure mode as the earlier `lexical_vocab` zero-exercise problem.

## Decision

**Defer both** until a schema spike resolves how (or whether) they fit the deterministic-grading
contract. Do **not** block the rest of the curriculum redesign on this — the other phases stand alone.

Open questions the spike must answer:

1. **Does pronunciation need new schema at all?** It may reduce to existing operations applied to
   **letters** rather than words: "which spelling makes the /ç/ sound?" (`choose`), "sort words by the
   sound the letter makes" (`categorize`), "find the silent letter" (`find_fix`). Validate before
   authoring.
2. **Audio.** The pipeline is text-first (no audio refs allowed). Decide whether pronunciation stays
   spelling/sound-awareness only, or whether an audio asset field is added later.
3. **Anchors.** `concept_requirements` notes + anchor forms (full verbatim Bokmål words) per lesson.

## Consequences

- The pronunciation gap remains a known, documented limitation rather than a silently missing feature.
- **Spaced-review nodes** are lower-risk and may ship first: Codex judged lightweight
  `review_checkpoint_*` lessons (re-test 2–3 prior slugs, no new semantics) likely fit the existing
  schema — pending one decision (a new lesson "type" vs a normal lesson whose `concept_requirements`
  references prior slugs).
- **When picked up:** run the spike (Q1); if existing operations over letters suffice, no new schema —
  just new slugs + `concept_requirements`; add ~6 phonology lessons interleaved across A1–A2 in
  `curriculum/structure.json`; generate via the normal pipeline; re-derive scope cards for neighbors
  whose position shifts.

Candidate slugs when unblocked: `vowel_contrast_a_o_aa`, `vowel_contrast_o_oe_ae`,
`g_k_softening_front_vowels`, `silent_and_final_consonants`, `tone_and_stress_intro` (+ the existing two,
possibly re-scoped). Keep scope to recognition + spelling/sound awareness; do not claim text-only drills
replace audio.
