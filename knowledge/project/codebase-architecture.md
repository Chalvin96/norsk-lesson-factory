---
type: Architecture
title: Codebase Architecture and Module Boundaries
description: Dependency rationale and established cross-context edges.
tags: [architecture, domain, workflows]
timestamp: 2026-10-22
---

This repository is an authoring modular monolith. The
[placement router](codebase.md) defines code ownership; this page records the
dependency rules and established exceptions. The
[authoring boundary](boundaries.md) keeps learner presentation and interaction
outside the system.

Package-internal paths below are relative to `src/lesson_builder/`.

## Dependency directions

CLI and web are inbound adapters. They delegate to application operations;
inner layers do not import either adapter. Application operations compose
domain decisions with file, provider, and cross-context effects. Checkpointed
flows own their sequencing and recovery in `workflow/`. Domain code owns
deterministic contracts and decisions and does not import application,
workflow, CLI, clients, formats, or workspace. Domain services also do not
read or write files, parse external formats, inspect environment state, or
coordinate other contexts through injected collaborators.

The domain contexts are catalog, curriculum, lesson, and distribution.
Distribution reads lesson values and character validation. Curriculum ordering
uses catalog dependency contracts, ordering constants, and topological-order
policy. This edge does not authorize new dependency directions. Put
non-checkpointed cross-context use cases in application operations.

## Markdown parsing

Markdown parsing uses markdown-it-py and mdit-py-plugins in-process, emitting
Pandoc-shaped JSON for panflute. Pandoc itself is not a dependency. Syntax
compatibility belongs to `formats/markdown/`; lesson policy stays in the lesson context.

One narrow format-to-domain edge is permitted: the Pandoc-shaped AST mappers
`formats/markdown/pandoc_blocks.py` and
`formats/markdown/pandoc_inlines.py` map parser AST values into
`domain/lesson/models/` contracts. Format modules do not call domain services
or decide lesson policy.

## Stable composition boundaries

`application/operations/compile_lesson_package.py` is the canonical application
boundary for loading and auditing one authored package. It composes Markdown
and YAML parsing, Pandoc-shaped AST mapping, lesson values, and deterministic source
policy. Export, distribution checks, and workbench previews use this boundary
so a caller cannot bypass the source audit by loading the parts independently.

The [placement router](codebase.md) lists workbench wire contracts, schema and
TypeScript generation, HTTP transport, feature hooks, and workspace composition.
Run `npm run workbench:types:check` to check generated contract freshness.
The lesson toolbar groups navigation, review, draft, actions, and audio inputs.

Provider adapters in `clients/` own external transport. OpenCode serves
configured pipeline jobs; Codex app-server serves workbench chat. Their
credential boundaries and the workbench's review and promotion
rules are described in [authoring boundaries](boundaries.md).

Distribution projection is deterministic. Source reads, audio synthesis and
cache access, complete-tree validation, schema writes, packaging, and external
publication belong to application operations and provider clients. Keep policy,
orchestration, transport, and file effects in their respective layers.
