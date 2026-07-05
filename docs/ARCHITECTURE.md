# Architecture

A conceptual map of how a Norwegian lesson goes from a curriculum entry to a
validated, exported artifact — and the design decisions that shape that path.

This is the mid-level view. For the high-level pitch see the
[README](../README.md); for node-by-node implementation detail (with source
anchors) see [PIPELINE.md](../PIPELINE.md).

## The core idea

The interesting problem is not "call an LLM to write a lesson." It is building a
system that treats the LLM as an **unreliable component** and engineers around
its failure modes:

- a reviewer that over-reports defects (high recall, low precision),
- a repair loop that may not converge,
- a model backend that can silently go down and return nothing.

Everything below follows from taking those three failure modes seriously.

## Design principles

- **Generate-then-verify.** The LLM authors freely; correctness is established
  afterwards by independent checks, not trusted at generation time. An answer
  reviewer even re-solves each exercise with the answer key hidden, then compares
  its answer against the authored key.
- **Split trust.** Deterministic code owns everything with a real oracle (schema
  validity, answer-key invariants, anchor coverage). The LLM owns everything that
  needs judgment (pedagogy, explanation quality, sequencing). Neither is asked to
  do the other's job.
- **Calibrated judgment, not vibes.** LLM-judge *issue lists* are advisory
  (they over-flag). LLM-judge *scores* are calibrated against hand-authored
  golden lessons and become load-bearing only when deliberately promoted. See
  [CALIBRATION.md](CALIBRATION.md).
- **Bounded repair.** Fix/regenerate loops run under an explicit budget with
  no-progress guards (`lesson_hash`, `blocking_fingerprint`); a loop that stops
  improving escalates to a human instead of spinning.
- **Failure-aware state.** Backend status is first-class. A reviewer that failed
  because its backend was down is never mistaken for a clean review.
- **Dependency injection everywhere.** Graph nodes are pure `(state, deps)`
  functions; IO and LLM calls live behind injected collaborators. The whole test
  suite runs offline.

## Data lifecycle

```text
curriculum/structure.json          # the syllabus: concepts × CEFR levels
        │
        ▼
data/concept_requirements/         # per-concept teaching requirements
        │
        ▼
cold authoring  ─────►  data/lessons/*.json      # internal source of truth
        │                        │
        │                        ▼
        │                 lesson-QA graph         # judge · check · fix · re-judge
        │                        │
        │                        ▼
        └──────────────►  dist/lessons/*.json     # exported, schema-validated
                          dist/manifest.json       # machine-readable index
```

`data/lessons` is the single source of truth. `dist/` is a deterministic export
of it; CI regenerates the export and fails on any diff, so the two cannot drift
unnoticed.

## The three flows

The lesson-QA loop is built on [LangGraph](https://langchain-ai.github.io/langgraph/);
the improvement flow is deliberately imperative (see below). All three flows
share the same checks, judges, and LLM-invocation layer.

### 1. Cold authoring

Turns a curriculum entry with no existing lesson into a first draft
(`cold_author/`). This is the "nothing → dist" path: given only the syllabus and
concept requirements, produce a lesson that can enter the QA graph.

### 2. Lesson-QA graph

The heart of the system. A draft lesson flows through a state graph:

```text
load → judge → run_checks → fix / regenerate → re-judge → converged?
                   │                                          │
                   └── deterministic gate + LLM judge panel   ├─ yes → sign-off → human gate → export
                                                              └─ no / no-progress → park thread → human
```

- **`run_checks`** applies the deterministic gate (schema, anchors, coverage,
  answer-key invariants) — blocking.
- **`judge`** runs the LLM judge panel (pedagogy, alignment, answer) — scores are
  calibrated; issue findings are advisory.
- **`fix` / `regenerate`** attempt repair under budget.
- **`regression`** flags (advisory, warning-only) when a repair looks worse than
  the trusted baseline; it surfaces the diff for a human but does not block.
- **`park_thread`** persists a stuck lesson for later human resumption rather than
  shipping or discarding it.
- **`export`** writes the validated `dist/` artifact.

### 3. Improvement flow

Handles targeted change requests against an already-exported lesson
(`improvement_steps/`): `triage → parse_request → feasibility →
add_exercises / add_explanation / revise_lesson`. These are plain functions
orchestrated imperatively — deliberately *not* graph nodes, because the control
flow is a short request-handling sequence, not a converging loop.

## Trust model

Three gates, in order, each with a different authority:

| Gate | Owner | Authority |
|------|-------|-----------|
| Deterministic gate | Code | Blocking. Oracle-checkable invariants only. |
| LLM judge panel | Model | Scores gate (when calibrated); issues advise. |
| Human gate | Person | Final sign-off; resumes parked lessons. |

A lesson only reaches export after the deterministic gate passes and either the
calibrated scores clear their floors or — for a below-floor lesson the loop could
not lift (scores flag it for revision, they do not hard-reject it) — a human has
signed off.

## LLM invocation and failover

Every model call goes through one failover layer (`pipeline/llm/`). An agent
binds to an ordered **backend chain**; the invoker tries each backend in turn:

- `LlmQuotaException` / `BackendDownException` → fail over to the next backend.
- `LlmParseException` → propagate immediately (a parse failure is not a backend
  problem; one bounded re-ask handles transient malformed output).
- Chain exhausted → re-raise the last error with the full per-attempt trace.

Because failover and outage are explicit, a "clean review" can never actually be
a silent backend failure. Structured output is JSON-instruction plus extraction
plus Pydantic validation, not native tool-calling, so it works uniformly across
heterogeneous backends.

## Where to read next

- [PIPELINE.md](../PIPELINE.md) — node-by-node wiki with source anchors and a
  live-vs-stubbed ledger.
- [SCHEMA.md](SCHEMA.md) — the exported lesson contract and the seven exercise
  operations.
- [CALIBRATION.md](CALIBRATION.md) — how the judge scores are calibrated against
  goldens.
- [research/](research/) — design findings and retrospectives.
- [adr/](adr/) — architecture decision records.
