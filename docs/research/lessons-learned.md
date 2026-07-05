# Research Notes: Lessons Learned Building an LLM Lesson Pipeline

Findings from iterating on this pipeline across four rebuilds (v1, v2, v3, and the
2026-06 reset). These are the design conclusions that shaped the current
architecture — what worked, what failed, and why.

The lesson schema/authoring contract these findings informed now lives in
[../SCHEMA.md](../SCHEMA.md).

## 1. LLM authoring is the strength

The central mistake was treating "LLM made factual mistakes" as "LLM should stop authoring."

That diagnosis was wrong.

The model is good at:

- pedagogy
- explanation
- exercise variation
- sequencing
- readable scaffolding

The failure was using the model as an unverified factual authority at scale.

Current rule: keep LLM authoring, add better verification and human read.

## 2. Determinism only belongs where there is a real oracle

Deterministic checks are excellent for:

- schema validity
- answer-key invariants
- token/order consistency
- Bokmal form checks
- banned-operation checks
- Nynorsk scans

Deterministic systems are not a substitute for pedagogy. The old KB/compiler direction spent too much machinery trying to make pedagogy deterministic.

Current rule: deterministic where the answer is closed; human or model judgment where it is not.

## 3. "Grounded" is not the same thing as "good"

A lesson can be grounded and still be bad.

The real quality surface includes:

- correctness
- concept fit
- completeness
- sequencing
- register control
- natural Norwegian
- learner-facing clarity

Current rule: treat grounding as one signal only, not a quality verdict.

## 4. Circular verification is fake verification

One of the worst earlier failures was checking LLM-generated content against other LLM-generated content and calling that verification.

Current rule:

- do not use archived wiki as default prompt context
- do not verify generated facts against generated artifacts
- use lexicon/corpus tools as checks, not as self-referential proof

## 5. Single-pass strong author beats cheap-author plus review

Our internal bake-off and review-fix experiment settled this for Phase 1 (these
are our own results on our golden set, not a general benchmark).

Findings:

- single-pass strong-author (GPT-5.5 via Codex, at the time) produced the best
  cost/quality result
- cheap-author plus strong-review can rescue some errors
- review-fix costs roughly like a second authoring pass
- review-fix preserves the weaker draft's shallower structure

Current rule: use single-pass GPT-5.5 via Codex CLI, then gate, then human read.

## 6. Wiki context did not improve the strong-author path

The last wiki-context tests did not beat strong single-pass generation. In one case, the lesson drifted into Norwegian explanatory prose.

Current rule:

- no wiki context by default for Phase 1 prompts
- archived wiki may be consulted manually while strengthening requirements or reviewing edge cases

## 7. Human read is still load-bearing

The remaining errors were not malformed JSON or broken morphology. They were subtle issues like:

- collocation disputes
- register fit
- `ikke` placement
- V2 slips
- tense-choice teaching errors

These are exactly the errors a beginner cannot self-correct.

Current rule: every lesson still gets a read, with more scrutiny on B1/B2 usage topics.

## 8. The scoreboard must come before architecture

The project re-architected too quickly and outran its own evaluation. Metrics moved, but the outcome stayed unclear.

Current rule:

- keep the eval surface fixed
- compare new workflows on the same golden set
- do not merge a new architecture because it sounds cleaner

## 9. Requirements are useful, but narrow

`data/concept_requirements/` is worth keeping, but only as a lightweight guardrail.

Requirements are good for:

- must-cover contrasts
- known learner traps
- banned false claims
- minimal anchor examples

Requirements are not a knowledge base and not a replacement for review.

Current rule: use them as prompt/gate hints for the relevant concept only.

## 10. Keep the active repo small

The active tree is now intentionally narrow:

- curriculum
- goldens
- schema
- constructors
- lexicon checks
- concept requirements
- tests
- the new Phase 1 scripts

Everything else is archived until deliberately re-adopted.

Current rule: new generated run artifacts live under ignored `tmp/`, not as new root clutter.

## 11. Curriculum had to be fixed before batch generation

The original active curriculum was too fragmented in A1/A2 and too thin at the top end.

Applied fixes:

- repaired the A1 sentence spine
- added explicit `ikke`, basic modals, and `det er` vs `det finnes`
- merged fragmented adverb/time/preposition/pronunciation clusters
- moved the token C1/C2 tails into B2
- audited concept requirements topic-by-topic

Curriculum count at the time of this fix: 106 concepts across A1, A2, B1, and B2.
(The current release exports 104 validated lessons.)

## 12. The export schema is the shipping contract

Internal shapes can evolve, but the exported lesson contract is the surface that matters to downstream consumers.

Current rule:

- validate internal `Lesson`
- project with `to_export_dict(...)`
- validate `ExportedLesson`

Any script that skips that sequence is building the wrong system.
