# Calibrating the LLM judge

> Why the quality gate trusts the reviewer's **scores** but not its **issue lists** — and how those floors are derived.

This is the load-bearing design decision in the pipeline. An LLM reviewer ("the pedagogy
judge") scores every lesson, but an LLM reviewer is itself an unreliable component. Before its
output is allowed to *block* a lesson, we measure **which parts of that output are trustworthy**
and gate only on those. The rest stays advisory.

## The finding

We evaluated the reviewer two ways against the validated golden lessons:

| Signal | What it is | Measured behaviour | Verdict |
|--------|------------|--------------------|---------|
| **Issue lists** | the discrete defects the reviewer reports | over-flags badly — recall ≈ **1.0**, precision ≈ **0.25** (≈3 of every 4 reported defects are false positives) | **advisory only** — never blocks |
| **Rubric scores** | 7 axis scores, 0–5 each | stable and well-separated — goldens score **86–97 %** overall, tight per-axis spread | **load-bearing** — drives the gate |

The reviewer is good at *grading* and bad at *enumerating*. So the gate is built on the scores,
and the issue lists are surfaced to the human at the review gate as hints, not blockers. This is
the difference between "the LLM said there's a problem, so reject" (which would reject ~75 % of
good lessons) and "the LLM scored this axis below the worst accepted golden, so flag for revision."

## How the floors are derived (`calibration/rubric_floors.py`)

Corpus-free, because seeding a defect corpus only works for the *shadow* judges, not the live
pedagogy judge (you can't synthesize "slightly-too-shallow" the way you can synthesize a wrong
answer key). Instead the floor is anchored to the reviewer's own scores on lessons we already
trust:

1. Score each **golden** lesson with the real pedagogy reviewer → 7 axis scores.
2. **Exclude** any golden scoring below `exclude_below` (60 %) overall — a weak golden would drag
   every floor down.
3. **Floor for each axis = the minimum score across the kept goldens**, minus a tolerance of 1
   (gate a *collapse*, not a one-point dip).
4. A new lesson scoring **below a floor on any axis** is flagged for revision (not hard-rejected —
   see [PIPELINE.md](../PIPELINE.md) §8).

Promotion to load-bearing is **explicit** (`--promote`): floors are computed and inspected before
they are allowed to gate, so a recalibration can't silently change what ships.

## Current calibration

7 axes (each 0–5): `on_concept`, `complete`, `bokmal`, `sequencing`, `presentable`, `answerable`,
`depth`. Benchmark = 5 validated goldens (`collocations`, `formal_vs_informal_register`,
`past_tense`, `preterite_vs_present_perfect`, `word_order_main_clauses`); 0 excluded.

```
overall: min 91.4 %  mean 97.1 %
axis floors:  on_concept 4  complete 4  bokmal 4  sequencing 3
              presentable 2  answerable 4  depth 4
```

(Persisted at `data/calibration/rubric_floors.json`.)

## Reproduce

```bash
# Re-score the goldens with the live reviewer and recompute floors (does NOT promote):
uv run python -m lesson_builder.pipeline.calibration.rubric_run

# Inspect, then promote to load-bearing:
uv run python -m lesson_builder.pipeline.calibration.rubric_run --promote
```

The floor math is a pure function (`compute_rubric_floors`); the live reviewer is injected, so the
calibration is unit-tested offline (`tests/pipeline/calibration/`).

## Why this matters

A naive "LLM-as-judge" gate rejects most good content because LLM reviewers over-report defects.
The pipeline measures that failure mode explicitly, keeps the unreliable signal advisory, and gates
only on the signal that calibration shows is stable — with promotion gated behind human inspection
so the gate can't drift. See also the related design notes in `PIPELINE.md` §8.
