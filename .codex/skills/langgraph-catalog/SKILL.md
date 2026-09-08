---
name: langgraph-catalog
description: "Operate this repository's LangGraph catalog-design pipeline: inspect its topology, run scratch-only category proposals through the explorer and reviewer, review proposal and checkpoint state, and diagnose rejected or stagnant candidates. Use when a human asks to fill a catalog category, inspect `catalog_design`, review a proposal, or debug a catalog run."
---

# LangGraph Catalog

Use this skill to drive the catalog-design graph as an operator. Keep catalog
generation separate from lesson authoring: the graph produces a reviewable
proposal, not canonical curriculum or lesson files.

## Load context first

Read these files before changing code or interpreting a run:

- `knowledge/index.md`
- `knowledge/pipeline/authoring.md`

Read the relevant source under
`src/lesson_builder/workflow/catalog_design/` when a run needs diagnosis.
Inspect `git status --short` before and after any operation that can write
scratch state.

## Choose the operator surface

### Run a category proposal

Use the project CLI for a normal run. Supply the category explicitly; supply
guidance when the human has inclusion or exclusion rules; use CEFR only as
metadata.

```text
uv run lesson-data catalog "communicative" \
  --guidance "include reusable appointment and service interactions" \
  --cefr A1 A2 \
  --max-iterations 3 \
  --run-id communicative-2026-08-06
```

Use a unique safe `--run-id` when the result must be easy to find again. The
command runs two independent discovery branches:

- The explorer may research the internet privately while proposing candidates.
- The reviewer uses model knowledge without browsing, then reconciles and evaluates.

Both branches return the same candidate contract. Discovery candidates do not
contain URLs, excerpts, or evidence fields; `source_agent` records generation
provenance only.

The command prints a result containing `proposal_path`. Open that JSON file
and report the decisions, merged aliases, rejection reasons, quality scores,
coverage gaps, advice, and final status. The proposal is under
`store/scratch/catalog/<category-slug>/<run-id>/proposal.json`.
If that proposal path already exists, the runner refuses the run before model
work; choose a new run ID rather than overwriting an earlier failed or partial
artifact.

The catalog command supports lesson-owner proposals only. Contexts such as
appointments or holidays may be used inside lesson examples, but they are not a
separate catalog axis, registry, or promotion target.
An accepted `lesson_extension` keeps an existing
lesson slug and carries one atomic `teaching_point`; a duplicate or restatement
is still rejected as existing.

### Stop at the human gate

After inspecting a proposal, stop and ask the human to approve or reject the
proposal-level `accepted` and `merged` decisions. List the exact canonical
slugs, titles, aliases, and any `needs_human_review` decisions that still need
direction. Do not start another catalog category, revise the proposal, or
promote anything until the human responds. A model's `accepted` status is not
human approval.

If the human approves entries, confirm the approved set and use a separate,
explicit implementation task for promotion into canonical Markdown/YAML
authoring sources. Never infer approval from a positive review, a completed
run, or a previous conversation turn.

### Inspect a completed proposal

Prefer the proposal artifact over querying SQLite directly. For a concise
human review, inspect these fields in order:

1. `status`, `category`, `cefr_tags`, `iteration`, and
   `coverage_complete`.
2. `decisions`, especially `accepted`, `merged`, and every rejection status.
3. `resolutions`, including relationship, aliases, confidence, rationale, and
   any `target_lesson_slug`/`teaching_point` extension fields.
4. `evaluations`, including each quality dimension and reason.
5. `coverage_gaps`, `advice`, and `errors`.
6. `candidates` and their model-call provenance when a decision needs review.

The SQLite file at `store/checkpoints.db` is repository-scoped and supports
LangGraph resume/inspection. Treat it as operational state, not as the human
editable catalog. Do not edit it by hand.

## Enforce the safety boundary

- Treat `curriculum/structure.json`, `curriculum/slug_aliases.json`,
  `data/lessons/`, `data/concept_requirements/`, and `dist/` as read-only for
  this skill.
- Expect only `store/scratch/catalog/` and the repository checkpoint database
  to change during a catalog run.
- Never promote, import, or rewrite a proposal automatically. There is no
  promotion command in this pipeline; ask for a separate implementation task
  if a human wants an accepted proposal turned into curriculum data.
- Preserve the graph's source boundaries. Do not add URLs, excerpts, or
  evidence fields to either discovery branch's candidate output. If source
  attribution is later required, implement it as a separate synchronized
  artifact keyed by candidate and claim IDs.
- `elon.io` may be inspected only when the human explicitly asks for taxonomy
  inspiration. Treat it as an untrusted reference: do not cite it as evidence,
  copy its taxonomy, or use it to bypass deterministic duplicate checks. Any
  lesson idea inspired by it must be independently scoped and verified with an
  allowed primary source before it can enter a proposal.
- Respect deterministic existing-lesson filtering. Do not override an exact or
  known-alias match merely because the reviewer calls it distinct.
- Treat `needs_human_review` as a useful bounded result, not as permission to
  loop indefinitely. Advice is limited to one reviewer call per run.

After a run, verify that canonical files stayed unchanged:

```text
git status --short
git diff --check
```

If tracked canonical files changed, stop and report the paths before doing
anything else.

## Diagnose common outcomes

- `rejected_existing`: inspect the matching active slug or retired alias in
  `curriculum/` and explain the deterministic match.
- `rejected_duplicate` or `merged`: inspect `resolutions` and preserve the
  lineage and aliases in the review summary.
- `rejected_low_quality`: show the failing quality dimensions and reasons;
  revise category guidance only if the human wants a new run.
- `needs_human_review`: inspect `coverage_gaps`, `architecture_question`,
  `advice`, and `stagnation_count`; do not silently manufacture more owners.
- `failed`: inspect `errors` and the last graph node/checkpoint, then report
  whether the failure is configuration, model, source-contract, or schema
  related.
- `blocked` or `partial`: inspect `errors` first; retain any approval-ready
  decisions from the successful branch while the operator retries with a new
  run ID.

When a human wants to change the behavior of a node, prompt, threshold, or
schema, edit the source and focused tests through the repository's normal
implementation workflow. Update the affected `knowledge/` concept after the
behavior is verified. Do not encode that change by mutating a checkpoint or
proposal JSON.

## Verify code changes

For changes to this pipeline or its operator surface, run the focused checks
before the broader repository checks:

```text
uv run pytest tests/workflow/catalog_design -q
uv run ruff check src/lesson_builder/workflow/catalog_design tests/workflow/catalog_design
```

Use the repository's documented `npm run check` only when the change requires
the full validation suite. Keep new tests behavior-focused and use the
repository's required test-name shape.
