---
type: Architecture
title: Codebase Shape and Placement
description: Route repository responsibilities to their current owner and layer.
tags: [architecture, modules, ownership]
timestamp: 2026-10-22
---

Read this router before adding or moving production code, then read the
[dependency rationale](codebase-architecture.md). Existing placement and file
size do not establish ownership. For each new or moved responsibility, record
`responsibility -> code kind -> owner -> layer -> target module` in the ignored
local plan.

## Repository ownership

| Path | Responsibility |
| --- | --- |
| `content/` | Human-edited catalog, curriculum, terminology, and lesson source |
| `dist/` | Derived learner distribution and export schemas |
| `src/lesson_builder/` | Production Python package |
| `tests/` | Runtime behavior evidence, organized by production owner |
| `evals/` | Opt-in prompt evaluations and labeled cases |
| `scripts/` | Repository development and validation entry points |
| `knowledge/` | Durable current-state decisions and rationale |
| `plans/` | Ignored local task plans |

Dependency and development commands belong in `pyproject.toml`, `uv.lock`, and
`package.json`; exact schemas, paths, and settings are implementation-owned.

## Production responsibility router

Python package paths are relative to `src/lesson_builder/`. Paths beginning
with `workbench/` or `scripts/` are relative to the repository root.

| Responsibility | Owner and placement |
| --- | --- |
| Catalog, curriculum, lesson, and distribution contracts | Existing context models; public contracts stay with their owning context |
| Model invariants | The model whose values they constrain |
| Boundary validation or serialization | The owning context's `validation/` package |
| Reusable deterministic policy over domain values | Flat `services/` package in the owning context |
| Non-checkpointed use cases, file/provider effects, or cross-context coordination | `application/operations/`; operation results stay there |
| Checkpointed sequencing, retries, owner gates, and recovery | `workflow/`; workflow state and results stay with the flow |
| External provider transport and transport types | `clients/` |
| Markdown/YAML parsing and format conversion | `formats/`; direct Pandoc-shaped AST mapping into lesson model contracts is the narrow exception described in [architecture](codebase-architecture.md) |
| Workspace paths, local records, caches, locks, and atomic writes | `workspace/` |
| Command registration and operator interaction | `cli/`; supported commands are `check`, `regenerate-dist`, `release`, `release-verify`, and `workbench` |
| Workbench HTTP transport, request guards, and JSON projection | `web/` |
| Workbench browser UI and design tokens | `workbench/`; the built app is ignored output served by `web/` |

Markdown parsing and its format-to-domain exception are described in
[architecture boundaries](codebase-architecture.md#markdown-parsing).

Specific routing anchors:

- `application/operations/compile_lesson_package.py` is the canonical
  application boundary for compiling and auditing one authored package.
- `application/operations/workbench_review.py` owns source-bound review
  coverage and evidence; `application/operations/workbench_lesson_gate.py`
  owns approval and promotion blockers.
- `application/operations/workbench_exercise_alignment.py` owns exercise
  section pins and staleness; checkpointed regeneration lives in
  `workflow/workbench_regeneration/`.
- Codex chat transport and profile credentials live in `clients/codex/`;
  OpenCode transport and scoped provider credentials live in
  `clients/llm/`.
- Workbench wire contracts are in `web/workbench_wire.py`.
  `scripts/export_workbench_schema.py` exports
  `workbench/schema/workbench-api.schema.json`, which generates
  `workbench/src/api/types.generated.ts`; `workbench/src/api/contracts.ts`
  provides transport aliases derived from that projection.

Workbench frontend responsibilities follow feature ownership:

| Responsibility | Owner and placement |
| --- | --- |
| HTTP transport and generated contract aliases | `workbench/src/api/http.ts`, `api/contracts.ts`, and `api/types.generated.ts` |
| Lesson, curriculum, and settings queries and writes | `lesson/api.ts`, `curriculum/api.ts`, and `settings/api.ts` |
| Shared asynchronous job state and regeneration polling | `jobs/` |
| Chat transcript and composer types | `chat/types.ts` |
| Curriculum draft and save input types | `curriculum/types.ts` |
| Lesson route loading and workspace composition | `lesson/lesson-page.tsx` and `lesson/lesson-workspace.tsx` |
| Lesson chat, review, audio, and lifecycle workflows | Their owning `chat/`, `lesson/review/`, `lesson/audio/`, and `lesson/lifecycle/` feature modules |

Domain services are deterministic. Their restrictions and the permitted
dependency edges are in [architecture boundaries](codebase-architecture.md).
Expose a cohesive service API only when callers need one.
