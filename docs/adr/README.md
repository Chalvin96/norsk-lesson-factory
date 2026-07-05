# Architecture Decision Records

Short, dated records of decisions with lasting architectural consequence — the *why* behind choices
that aren't obvious from the code. Deferred-scope decisions live here too, so a reader can tell a
deliberate cut from an unfinished edge.

| ADR | Title | Status |
|-----|-------|--------|
| [0001](0001-defer-pronunciation-track.md) | Defer the pronunciation track + spaced-review nodes | Deferred |
| [0002](0002-human-review-persona-split.md) | Human-review surface: persona split, defer the visual editor | Accepted |
| [0003](0003-data-lessons-single-source-of-truth.md) | `data/lessons` is the single source of truth; `dist` is derived-only | Accepted |

Related design docs (not ADRs): [PIPELINE.md](../../PIPELINE.md) (architecture wiki),
[CALIBRATION.md](../CALIBRATION.md) (LLM-judge calibration), [SCHEMA.md](../SCHEMA.md) (lesson contract).
