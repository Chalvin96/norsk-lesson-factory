---
type: Architecture
title: Codebase Shape and Placement
description: Authoritative responsibility routing for repository code.
tags: [architecture, modules, ownership]
timestamp: 2026-09-06
---

Read this router before adding or moving production code, then read the
[dependency rationale](codebase-architecture.md). Existing placement is evidence,
not authority: file size and similar names do not establish ownership.

For each new or moved responsibility, record in the local plan:

```text
responsibility -> code kind -> owner -> layer -> target module
```

Choose by semantic owner, contract audience and lifetime, side effects, and layer.
A Pydantic model, dataclass, or enum does not automatically belong in the domain.

## Repository ownership

| Path | Responsibility |
| --- | --- |
| `content/` | Human-editable catalog, curriculum, terminology, and lesson source |
| `dist/` | Derived learner distribution and export schemas |
| `src/lesson_builder/` | Production Python code |
| `tests/` | Behavior evidence mirroring production ownership |
| `evals/` | Opt-in prompt evaluations and labeled cases |
| `scripts/` | Repository development and validation entry points |
| `knowledge/` | Current durable decisions and rationale |
| `plans/`, `store/` | Ignored task plans and scratch runs, caches, checkpoints, archives |
| `.github/` | CI and contribution configuration |
| `AGENTS.md` | Mandatory contributor rules; detailed architecture stays here |

Dependencies and development commands belong in `pyproject.toml`, `uv.lock`, and
`package.json`; exact schemas, paths, and settings remain implementation-owned.

## Production responsibility router

All paths below are relative to `src/lesson_builder/`.

| Responsibility | Owner and target |
| --- | --- |
| Public contract owned by an existing domain context | That context's existing `domain/<context>/models` namespace |
| Invariant depending only on a model or its nested contract values | The model |
| Serializer-like boundary check or projection | `domain/<context>/validation/` |
| Reusable deterministic policy or transformation spanning domain values | Flat `domain/<context>/services/<responsibility>.py` |
| Stable context mutation or projection API, when needed | Optional cohesive `domain/<context>/services/<context>_service.py` |
| Non-checkpointed use case, file/provider effects, or cross-context coordination | `application/operations/`; operation results stay with this layer |
| Checkpointed sequencing, retry, human gates, recovery, or workflow transport | `workflow/<flow>/`; state and results stay with this flow |
| External provider adaptation | `clients/`; transport types stay with the client |
| Markdown, YAML, or Pandoc mechanics | `formats/`; source-format types stay here |
| Repository paths, scratch, cache, locks, or atomic writes | `workspace/`; workspace values stay here |
| Command registration and operator interaction | `cli/`; handlers delegate and parser construction does not initialize providers |
| Private implementation record | The module or layer owning its lifetime |

The existing domain contexts are `catalog`, `curriculum`, `lesson`, and
`distribution`. A capability name does not create another context.

Domain services are flat and contain deterministic logic only: no directories
below `services/`, file reads or writes, format parsing, providers, environment
inspection, checkpoints, or coordination of other contexts, even through injected
collaborators. Standalone validators, serializers, registries, and policy helpers
do not become models because they return typed data. A cohesive service is useful
only when callers need its API; do not add a catch-all facade for naming symmetry.

Examples: authored lesson loading composes formats and lesson values in the
application layer; graph stages own checkpoint recovery; audio synthesis owns
provider calls and assets in application operations; export-schema writing is an
application effect even though the schema derives from a domain contract.
`workspace.paths.WorkspacePaths` owns canonical source, distribution, approval,
and scratch locations; layer settings hold configuration, not a competing path map.

Mirror test ownership under `tests/`. Shared model factories live in
`tests/<layer>/factories.py`, typed behavior fakes in `tests/<layer>/fakes.py`, and
`conftest.py` only injects common fixture defaults. Naming, import, settings, and
reader-order conventions are defined in `AGENTS.md` and checked mechanically by
`npm run conventions:check`.

## Architecture decisions

Stop and review the router and rationale read-only when ownership or dependency
routing is unresolved. Request an architecture decision before creating or changing
a domain context, moving a public contract between contexts or layers, adding a
dependency direction, or placing code whose route is not established here. Nested
service packages are not an escape hatch.

Review the routing line before implementation details. Passing tests does not make
a wrong owner correct. Consolidate files when they share responsibility and
lifetime; preserve boundaries that separate policy, orchestration, and effects.
