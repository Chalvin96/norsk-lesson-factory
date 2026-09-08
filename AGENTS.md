# Agent Conventions

Project-specific rules for humans and agents working in this repository.

## Repository scope: authoring only

This repository owns the human-editable authoring and generation inputs for
lesson content. Frame design, implementation, and review questions as
authoring questions first.

The authoring surface may define lesson objectives, explanations, examples,
transcripts, translations, model audio,
evidence kinds, answers, and feedback or rationales. It may also generate and
export the audio artifacts required by those authored transcripts.

The consuming learner application owns presentation and interaction behavior.
Do not put transcript visibility, replay controls, reveal timing, player state,
session state, or learner-facing scoring behavior into the lesson authoring
contract. The supported local preview is an author inspection tool; see
[Authoring, Distribution, and Release Boundaries](knowledge/project/boundaries.md).
A scratch browser POC may demonstrate one possible downstream rendering, but it
must be clearly labeled as such and must not be treated as the production
authoring schema.

## Knowledge and planning

Read `knowledge/index.md` and the relevant linked concepts before planning work.
For any change involving package layout, imports, module boundaries, or
services, read [Codebase Shape and Placement](knowledge/project/codebase.md)
immediately after the index, then read
[Codebase Architecture and Module Boundaries](knowledge/project/codebase-architecture.md)
for dependency rationale and known migration debt.
`knowledge/` is the only committed current-state source: durable product, domain,
data, architecture, platform, operations, and reference facts belong there.
Use an ignored file under `plans/` for local planning when a task needs a detailed
work plan. Any `openspec/` files that remain in the repository are dated historical
context only; they are not authoritative, are not required for new work, and are not
validated by CI. Never copy proposal, design, or task history into `knowledge/`.

Update the affected knowledge concepts whenever behavior or architecture changes.
Use `uv` for Python dependencies and commands. Enable the repository hook with
`git config core.hooksPath .githooks` when you want local checks.

## Authoring preview

Use `uv run lesson-data preview <lesson-id> --no-browser` and open the printed
loopback URL to inspect current source. Reload after edits; preview does not
regenerate `dist/`, call providers, or establish learner-app behavior.

- Browser inspection is required for changes to preview rendering, templates,
  styles, or interactions, and for lesson edits that change section/exercise
  structure, order, or formatting. Inspect a representative affected lesson;
  for shared renderer changes, cover the affected block and operation types.
  Preview is optional for plain wording, translation, metadata, dependency, or
  curriculum-sequence edits unless the change raises a rendering concern.
- Check authored content order, exercise context, and author-only answer/source
  disclosures. For visible UI changes, check desktop and a 320 px viewport for
  clipping/overflow, keyboard navigation and focus, heading hierarchy, language
  scoping, and contrast. Exercise affected findings, unavailable-audio, malformed
  source, and reload/recovery states when those paths change; use temporary
  fixtures for invalid source rather than altering canonical lessons.
- Keep the preview read-only, provider-free, and loopback-only. Renderer/server
  changes must retain regression coverage for escaped untrusted content, source
  path containment, and Host/Origin validation as applicable. Do not weaken
  these boundaries to make a demo accessible.
- For visible UI changes, capture and inspect a screenshot of the actual browser
  result. Keep screenshots and requested demo recordings in ignored `reports/`
  or outside the repository, and link them in the handoff with the lesson and
  tested state. They are review evidence, not lesson/distribution artifacts.
  A recording must demonstrate the interactions it claims to verify.
- Preview complements the relevant automated checks in `npm run check`, source
  validation, and deterministic export regeneration; it does not replace them,
  semantic review, or human acceptance. If browser access is unavailable, report
  the unverified visual behavior and complete the checks that can run.

## Executable conventions

`npm run conventions:check` checks introduced mechanical violations against the branch
base. `scripts/test_review_prompt.md` contains the semantic review rubric for test
qualities that cannot be decided reliably by syntax. Review changes to packages,
imports, models, or services against `knowledge/project/codebase.md` before reviewing
implementation details.

The enforced test-name shape is:

```text
test_<what_is_being_tested>_given_<condition>_expect_<output>
```

Use readable, specific snake_case. Avoid abbreviations and vague conditions such as
`happy_path` or `sad_path`.

The checker is authoritative for the mechanical portions of module organization,
constants, and helper placement below.

## Module organization

Existing placement is evidence about the current code, not proof of correct ownership.
Before adding or moving production code, classify each responsibility and record
`responsibility -> code kind -> owner -> layer -> target module` in the plan. File
length, similar names, and an existing neighboring module are not placement reasons.

`domain/<context>/services/` is flat and contains deterministic logic only. Do not create
a directory below `services/` or put file/provider effects there, even through injected
collaborators. Domain-owned public contracts belong in the context's existing model
namespace; operation results, workflow state, transport types, and private records stay
with their owning layer. Type syntax does not decide ownership. Standalone validators
and projections belong in validation modules; reusable cross-model policy belongs in
flat services. See the codebase guide for responsibility routing.

Stop and request an architecture decision before creating or changing a domain context,
moving a public contract between contexts or layers, adding a dependency direction, or
placing code whose route is not established by the codebase guide.

A boundary change is complete only when it names the intended interface, narrows what
callers must know, updates repository callers atomically, and deletes the obsolete alias,
wrapper, or facade. A checkpoint stage gets its own node module only when that module owns
routing, state transformation, checkpoint, or error behavior; register a pure call-through
operation directly from the cohesive injected runner.

Every module begins with a docstring whose first line names its entry-point function:
``Entry point: `thing_check`.`` If its public API is a data type, use
``Not a check itself — ...``. If it has multiple entry points, name each and its caller.

Public functions/classes come first in reader order: primary entry point, then other
public helpers. Private `_` helpers follow. A package with a clear entry point names it
and its role in `__init__.py`'s docstring. Group package exports by role with comments
inside `__all__`.

A context may expose one cohesive `<Context>Service` in
`domain/<context>/services/<context>_service.py` when callers need a stable mutation or
projection API; it is an option, not a requirement. Keep methods verb-first and explicit;
use `is_`, `has_`, or `can_` for predicates, `find_` for optional lookups, and `require_`
for raising lookups. Do not create a generic catch-all `rules.py` or a god service merely
for naming symmetry. The architecture map documents the boundary and the distinction
between local distribution artifacts and external releases.

## Constants and settings

All module-level constants use `K_<COMPONENT>_<NAME>` in upper snake case. Provider
model-routing settings, timeouts, environment-variable names, quota patterns, URLs,
and other configuration
belong in one `settings.py` per layer.

## Testing policy

Test observable runtime behavior, routing, state transitions, edge cases, regressions,
and data invariants. Do not test language primitives, type hierarchy by itself, static
pattern tuples, private structure, or duplicated assertions. A behavior test that
exercises exception routing supersedes an `issubclass` test.

Mock external subprocess/HTTP calls with `monkeypatch`; tests that call live
providers or external services require an explicit environment gate such as
`LLM_SMOKE`. Provider-free local CLI and loopback HTTP integration tests do not
require that gate. Filesystem tests use `tmp_path` and never write into the
repository. Tests must pass independently.

## Test data and behavior fakes

Use `factory_boy` for incidental Pydantic/DTO setup, with one `factory.Factory` per
model in `tests/<layer>/factories.py`. Use inline literals when their exact shape or
values are the behavior under test.

Use shared typed fakes for behavior, not factories or ad-hoc per-file classes. Reusable
fakes live in `tests/<layer>/fakes.py` and are imported explicitly. The canonical LLM
fake is `FakeLlmClient(BaseLlmClient)`, driven by an `fn(prompt, model) -> str` closure,
with `responding`, `raising`, and `from_fn` constructors.

Keep `conftest.py` limited to fixtures that inject common defaults. Prefer fakes for
return/raise behavior; use `unittest.mock` only for interaction assertions. Simple fakes
may expose a typed `calls` list. Shared fakes never touch subprocess, network,
environment, or repository files. Patch agent factories explicitly with
`monkeypatch.setattr` at the call site.
