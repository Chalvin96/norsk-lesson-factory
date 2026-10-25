# Agent conventions

This repository is the authoring and generation source for Norwegian lesson
content. Treat product, implementation, and review questions as authoring
questions first. The learner application owns presentation, playback, reveal
timing, session state, and learner scoring; those behaviours do not belong in
the authoring contract. The workbench is an author tool, not the learner
runtime. Read [Authoring, Distribution, and Release Boundaries](knowledge/project/boundaries.md)
when a task crosses that boundary. A scratch browser POC can illustrate a
downstream rendering only when it is clearly labelled and never defines the
production authoring schema.

Authoring may define lesson objectives, explanations, examples, transcripts,
translations, model audio, evidence kinds, answers, feedback, rationales, and
the audio artifacts generated from those authored transcripts.

## Start with the current map

Read [`knowledge/index.md`](knowledge/index.md) and the concepts it links before
planning. For package layout, imports, module boundaries, or services, read
[Codebase Shape and Placement](knowledge/project/codebase.md), then
[Codebase Architecture and Module Boundaries](knowledge/project/codebase-architecture.md).
Record `responsibility -> code kind -> owner -> layer -> target module` in an
ignored `plans/` file when a change needs a detailed plan. `knowledge/` is the
committed current-state source; keep proposals and task history out of it.
Remaining `openspec/` files are dated historical context, are not authoritative
or CI-validated, and do not get copied into `knowledge/`.
Update affected knowledge concepts when behaviour or architecture changes.

Use `uv` for Python commands and dependencies. Enable the repository hook with
`git config core.hooksPath .githooks` when local staged checks are useful.

## Authoring preview

Read and follow the [workbench verification skill](.agents/skills/verify/SKILL.md)
when a change affects workbench rendering, templates, styles, interactions, or
lesson section/exercise structure, order, or formatting. It is the canonical
route to browser evidence. Wording, translation, metadata, dependency, and
curriculum-sequence changes usually need no browser run unless they create a
rendering question.

The loopback workbench reads authoring source; opening it does not regenerate
`dist/` or call providers. Chat, review, and regeneration can call configured
providers. Git commits and PR review record acceptance. AI reviews are optional and
advisory; promotion checks valid source, settled proposals, complete audio, and
canonical conflicts. Curriculum chips show source, findings, and proposals.
Exercise regeneration is a per-draft graph: one proposal contains one block
per affected exercise, is checked as a whole, and is decided per item.

The generated app in `src/lesson_builder/web/app/` is ignored output. The
workbench build creates it when required, and it never belongs in a commit.
The frontend's Storybook command is the visual catalogue; its tests and type
checks are the relevant automated evidence for frontend changes.
Browser inspection complements automated checks, source validation, deterministic
export regeneration, semantic review, and human acceptance. If browser access
is unavailable, report the unverified visual behaviour and complete the checks
that can run.

## Release and distribution

Follow the [Release Operations Runbook](knowledge/README.md). Run
`uv run lesson-data release`, review reuse, missing recordings, and estimated
cost, then use its printed `release --approve` continuation only when synthesis
and publication are authorized. Standard releases require complete
source-derived audio and verified uploaded audio before the lesson archive.
Tag CI only verifies an already published release. Provider-free committed
`dist/` is a local authoring artifact, not a standard release.
Confirm release verification passes before reporting a release complete.

The supported top-level CLI commands are `check`, `regenerate-dist`, `release`,
`release-verify`, and `workbench`.

## Code ownership and boundaries

The codebase guide is the placement authority. Existing placement, file length,
similar names, and neighbouring modules are evidence to inspect, not ownership
reasons. Domain services stay flat and
deterministic; application operations own non-checkpointed effects and
coordination; workflows own checkpoints, retries, and recovery; clients own
provider transport; formats own parsing and conversion; workspace owns local
paths, records, caches, locks, and atomic writes; web and CLI are inbound
adapters. Keep public contracts with their owning context and keep operation,
workflow, transport, and private records with their owning layer.
Domain services do not read or write files, parse external formats, call
providers, inspect environment state, own checkpoints, or coordinate contexts,
including through injected collaborators. Expose one cohesive context service
only when callers need a stable mutation or projection API.
Type syntax does not decide ownership. Standalone validators and projections
belong in validation modules, reusable cross-model policy belongs in flat
services, and a catch-all `rules.py` or god service exists only when its
responsibility is actually cohesive.

Use verb-first, explicit service methods; name predicates `is_`, `has_`, or
`can_`, optional lookups `find_`, and raising lookups `require_`. Keep provider
model routing, timeouts, environment names, quota patterns, URLs, and similar
configuration in the layer's `settings.py`. A checkpoint stage gets its own
module when it owns routing, state transformation, checkpoint, or error
behaviour; pure call-through operations stay on the cohesive injected runner.
Group package exports by role in `__all__`; put the primary public entry point
first, then other public helpers, then private helpers.

Ask for an architecture decision before creating or changing a domain context,
moving a public contract across contexts or layers, adding a dependency edge,
or placing a responsibility the codebase guide does not route. A boundary
change names the intended interface, narrows caller knowledge, migrates callers
atomically, and removes the obsolete alias or wrapper.

The mechanical Python and test conventions are checked by
`scripts/check_agent_conventions.py`; run `npm run conventions:check` and use
its diagnostic as the repair instruction. Keep semantic review guidance in
[`scripts/test_review_prompt.md`](scripts/test_review_prompt.md), including its
state-sync standard for code that persists shared state.

The staged pre-commit hook stays fast and checks only staged workbench files.
Biome owns frontend formatting and lint with its exact pinned dependency. Its
configuration excludes generated TypeScript and JSON schema plus the copied
Flyt learner renderer because those sources belong to their upstream owners.
The disabled array-index rule matches ordered author content with no stable
wire identity; the disabled noninteractive-tabindex rule is limited to the
chat transcript and lesson document, where keyboard scrolling and row focus
are deliberate.

## Tests and evidence

Test observable behaviour, routing, state transitions, edge cases, regressions,
and data invariants. Keep setup incidental, execute behaviour once, and make
assertions follow the action. Use `monkeypatch` for external subprocess or HTTP
calls; gate live providers with an explicit environment flag. Local provider-
free CLI and loopback HTTP tests need no live-service gate. Use `tmp_path` for
filesystem work and keep tests independent of ordering and shared mutable state.
Skip language primitives, type hierarchy by itself, static tuples, private
structure, and duplicated assertions; an exception-routing behaviour test is
stronger than an `issubclass` test.

Use one `factory.Factory` per model for incidental Pydantic or DTO data and
literals for behaviour-defining shapes. Share typed fakes through the test
layer's `fakes.py` and factories through `factories.py`; keep `conftest.py` for
common fixtures. The canonical LLM fake is `FakeLlmClient(BaseLlmClient)` with
an `fn(prompt, model) -> str` closure and `responding`, `raising`, and
`from_fn` constructors.
Use mocks for interaction assertions, and typed `calls` lists for simple fake
recording. The root test fixture isolates HOME and XDG state per test; preserve
that isolation for every test-launched subprocess. Shared fakes do not touch
subprocesses, networks, environments, or repository files; patch agent
factories explicitly at the call site.

## Calibration and authoring review

The repository owner does not read Norwegian. For every seed change in
`evals/promptfoo/cases/write_judge_calibration.yaml`, run the delegated `codex exec`
review, fix until it reports full agreement, and then flip
`label_provenance` to `human_reviewed` in the same change. Keep case IDs stable;
repair stale names through the description and `primary_failed_criterion`.

When lesson content changes, review authored order, exercise context, and
author-only answer/source disclosures. Preserve regression coverage for escaped
untrusted content, source containment, and Host/Origin validation when the
workbench renderer or server changes. Use temporary invalid-source fixtures so
canonical lessons remain valid.

The repository check runner is the full local gate and runs every listed check
even after a failure; use its `--only` and `--skip` filters for focused loops.
The release and workbench runbooks, package scripts, and checker output are the
authoritative mechanics; this file carries the reasons, boundaries, and routing
that those files cannot infer.
