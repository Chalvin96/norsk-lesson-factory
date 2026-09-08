---
type: Architecture
title: Codebase Architecture and Module Boundaries
description: Dependency rationale and remaining boundary debt in the lesson builder.
tags: [architecture, domain, workflows]
timestamp: 2026-09-06
---

The repository is an authoring modular monolith. The
[placement router](codebase.md) owns destination rules; this page explains why the
boundaries exist. The [authoring boundary](boundaries.md) keeps learner interaction,
playback, scoring, and session state outside this system.

## Dependencies and contexts

```text
cli ───────────────┐
                   ├─> application/operations ──┬─> domain
workflow ──────────┘                            └─> clients / formats / workspace
```

Domain code owns deterministic contracts and decisions and does not import
application, workflow, CLI, clients, formats, or workspace. Application operations
and checkpointed workflows compose domain behavior with effects. Infrastructure
translates external formats and mechanisms without deciding catalog or lesson policy.
This keeps pure authoring rules usable by generation, auditing, preview, and export.

| Context | Domain responsibility |
| --- | --- |
| Catalog | Teachable identity, scope, owner references, dependencies, approval policy |
| Curriculum | Plan contracts and deterministic ordering of approved inventory |
| Lesson | Authored content, exercises, transcript projection, deterministic quality rules |
| Distribution | Public lesson-packet and distribution-catalog projections |

Distribution projection has an established read-only dependency on lesson values
and character validation. Curriculum ordering currently consumes catalog dependency
contracts and topological-order policy; this existing edge remains migration debt
until its interface receives an explicit architecture decision. Existing imports
do not authorize new dependency directions. Non-checkpointed cross-context use
cases belong in application operations.

## Boundaries worth preserving

Authored-source loading combines Markdown/YAML mechanics and lesson contracts in
application operations. Transport repair stays at that boundary; deterministic
lesson checks consume parsed values. No compiler service package or intermediate
compiler contract is needed. Character and terminology file loading likewise stays
outside their domain contracts and validation.

`application/operations/prepare_source_package.py` owns the reusable preparation
of one authored lesson package from a single captured source snapshot: audit,
lesson construction, deterministic gates, distribution projection, diagnostics,
transcript projection, and content hashes. The lesson-generation workflow keeps
checkpoint concerns and persists its disposable diagnostics and transcript
sidecars after preparation succeeds.

The checkpointed graph is the single rich-generation orchestrator. Workflow code
owns recovery, checkpoint intent, handoff transport, scratch evidence, and generation
logs. Nodes should add checkpoint-owned behavior; pass-through facades obscure who
actually performs a stage. Rich-authoring prompt builders live in application
operations; catalog-stage prompt builders remain with their workflow. Evaluations
call those same production definitions.
Provider-backed review is an application operation; domain validation interprets
review values deterministically.

One configured OpenCode client serves each job. The job runner retries bounded
transient failures on that same client; quota and parse failures are non-retryable.
It does not select fallback providers or maintain a second provider registry.
Retry attempts remain visible in the response evidence.

Distribution projection stays pure. Source reads, TTS/cache effects, staged-tree
validation, export-schema writes, packaging, and external publication belong in
application operations and clients. A cohesive `DistributionService` is the
existing projection API, not a requirement for every context to grow a service class.

## Remaining placement debt

The lesson context remains broad. Some values currently under its model namespace
are operation results or generation transport, such as source-repair output and
rich-authoring handoffs. Existing placement does not establish that these are
public lesson contracts; classify their audience and lifetime before changing them,
and request the architecture decision required for moving a public contract.

Model namespaces also still contain standalone registry helpers, such as
operation-policy lookup. Route such behavior by its semantic responsibility rather
than preserving a historical location or creating a separate file for every helper.
Review actual callers before a move.

These are current cleanup targets, not reasons to recreate historical layers.
Completed migrations are explained by the router and code; they do not need a
second file-by-file inventory here.
