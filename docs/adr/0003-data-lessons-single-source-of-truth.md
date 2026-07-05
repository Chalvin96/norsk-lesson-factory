# ADR 0003: `data/lessons` is the single source of truth; `dist` is derived-only

- Status: Accepted
- Date: 2026-06-21

## Context

The pipeline previously carried a recovery journey that re-imported `dist/lessons/*.json` back into
`data/lessons/*.json`. That path depended on a lossy inverse of `to_export_dict`, stamped recovered
entries as `accepted`, and treated serving artifacts as if they were safe authoring state.

At the same time, both `data/lessons/` and `data/concept_requirements/` are git-tracked in this
repository. That means the repo already has a durable recovery mechanism for full-fidelity authoring
state: git history.

## Decision

`data/lessons/<slug>.json` plus `data/concept_requirements/<slug>.json` is the single source of truth
for lesson authoring and recovery.

`dist/lessons/<slug>.json` is a derived serving projection regenerated from `data/lessons/` via
`lesson_to_export`. It is not imported back into the authoring model.

Journey 2 imports genuinely external lesson files into `data/lessons/` plus the acceptance ledger.
Recovery of tracked lessons comes from git, not from inverting dist exports.

## Consequences

- The import-from-dist recovery journey is retired.
- Backlog WI 17 is deleted because recovery no longer depends on reconstructing dropped fields from a
  lossy export.
- `regenerate_dist` becomes the explicit data -> dist projection path.
- `import_lesson` no longer writes `dist/`; accepted dist artifacts continue to come from the QA/export
  pipeline or from explicit regeneration.
- Docs should describe git as the recovery story and dist as a serving cache/projection.
