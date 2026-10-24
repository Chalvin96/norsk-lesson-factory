# clients/llm

LLM client boundary: typed errors, one OpenCode CLI transport, structured
(schema-validated) output, and the jobs that compose it. Production jobs are
single-client OpenCode calls with bounded retry for transient transport errors.

## Changing the model

`config.yaml` is the single source of truth for this routing layer. Tiers name
model/variant pairs (`cheap`, `standard`, `deep_alt`, and `workbench`); jobs
bind stable workflow names (`author`, `reviewer`, and so on) to exactly one
tier plus one agent template, so workflow code does not change when a model
moves. Change a tier's `model` to select another model or provider routed
through OpenCode; there is no adapter field and no backward-compatible alias
for retired ones.

Provider authentication and model availability belong to OpenCode. The model
ID is forwarded unchanged, so configured IDs such as `openai/gpt-5.6-sol` and
`zai-coding-plan/glm-5.3` remain selectable without adding provider-specific
clients to this package. There is deliberately no supported direct Claude,
Codex, OpenRouter, or Z.AI adapter.

## Layout

```
llm/
├── base.py              contracts, JSON extraction, and owned-process cancellation
├── exceptions.py        the typed error hierarchy
├── failure_messages.py  author-facing provider names and failure guidance
├── settings.py          transport and provider failure matching constants
├── config.py            strict root config.yaml tier/job loader
├── invocation.py        one-client JobRunner, structured output, bounded retry
├── role_jobs.py       role composition boundary: config-selected jobs
├── opencode_credentials.py scoped provider credentials and refresh writeback
├── opencode_runtime.py  private runtime paths, liveness, sweeping, and cache leases
└── opencode.py          OpencodeClient (`opencode run`) process transport
```

## What goes where

**`base.py`** contains the shared `BaseLlmClient` contract, case-insensitive
quota matching, the process registry used by batch cancellation, and tolerant
JSON extraction used by structured invocation.
`opencode_credentials.py` owns scoped provider credentials and refresh writeback.
`opencode_runtime.py` owns per-call runtime paths, directory liveness and
sweeping, and shared cache coordination. Its file-lock API also protects
credential refresh and writeback.
`opencode.py` owns subprocess streaming, prompt attachment, quota-event parsing,
and provider-specific command flags.

**`config.py`** strictly loads `config.yaml`. Each tier contains only `model`
and `variant`; each job contains only `tier` and `agent_template`. Unknown or
missing fields, unknown tier references, duplicate tier/job names, bad scalar
types, duplicate YAML keys, a missing `.opencode/agents/<template>.md`
file, or a template that resolves outside the resolved `.opencode/agents`
directory (including an in-repository symlink such as one to the repository
README) all fail before an LLM call starts. `llm.timeout_seconds` is a
positive integer default deadline with no upper bound.

**`invocation.py`** owns `JobRunner`, `StructuredJobRunner`, and the bounded
retry loop for one configured client. Production jobs do not select a fallback
client. Tests inject fake clients to exercise retry and failure handling.

**`role_jobs.py`** is the composition boundary. `runner_for_job()` and
`client_for_job()` load a validated job, build its OpenCode client with the
configured `llm.timeout_seconds` default (or an explicit finite positive
override), and expose cancellation for active calls. Production callers use
this boundary rather than constructing transports or model profiles directly.

## OpenCode runtime policy

The adapter emits `--agent` and `--variant` from the job's agent template and
tier, and keeps `--pure` enabled so global plugins cannot silently change an
authoring run. The task prompt always follows the `--` end-of-options marker,
so prompt text (including prompts that begin with `-` or equal a flag such as
`--help`) is positional data and can never be parsed as a CLI option. Each
call runs with the validated repository root as the subprocess working
directory. Agent templates under `.opencode/agents/` are reusable policy
artifacts: stable instructions, tool permissions, and side-effect rules.
Prompt builders live with their owning application operation; the invocation
layer owns schema/repair instructions, and callers cannot override tools.
Authoring/review templates disable tools. A model response never approves or
publishes an artifact; LangGraph remains the checkpoint and human-gate
authority.

Each call copies only the selected provider's `opencode/auth.json` entry into
private XDG data. XDG state is per call except for the installation locks described
below; `TMPDIR` is private. Database, history, logs, and other providers' credentials
are not copied. Missing credentials fail before spawning OpenCode and point to
`opencode auth login`. Inherited
`OPENCODE_` policy overrides are stripped; `OPENCODE_API_KEY` and provider-owned
credential variables are preserved.

`XDG_CACHE_HOME` is shared at
`${XDG_CACHE_HOME:-~/.cache}/norsk-lesson-data/opencode`. OpenCode 1.18.x puts
[model metadata and package assets in cache, credentials in data](https://github.com/anomalyco/opencode/blob/v1.18.0/packages/core/src/global.ts).
Its [remote skill downloader](https://github.com/anomalyco/opencode/blob/v1.18.0/packages/opencode/src/skill/discovery.ts)
keys files by skill name and reuses existing files across URLs. The controlled
`OPENCODE_CONFIG_CONTENT` overlay clears project/user `skills.urls` to prevent
that cross-call reuse. It preserves agent, model, and permission configuration;
OpenCode's later system-managed configuration can restore URLs.

Cold calls take a shared-cache exclusive lock through completion. Warm calls
hold shared locks and run in parallel, including callers queued during initial
population. Failed calls remain cold because failure does not prove installation
completed. A writer-intent gate stops new warm readers while a cold call waits
for existing readers to finish; callers for a now-warm model resume in parallel.
OpenCode's internal install-lock directory is linked into the shared cache, so
different model/package mappings coordinate writers too.

Every call retains a pending-record flock because even a warm model can map to
a new package. The record is published only after locking and recording the
pre-call package directory inventory, excluding dependency trees inside
`node_modules`. Every Python return or exception removes it, including quota,
provider, timeout, and empty-output failures. Success atomically publishes its
directory inventory before removing the pending record, so recovery recognizes
a committed installation if the process dies between those steps. Only an abruptly terminated owner and child leave an abandoned
record. Recovery waits for live calls and removes installation directories added
after the abandoned call began, preserving its pre-existing directories,
inventories published by successful overlapping calls, and warm markers.
Success inventories are conservative: directories observed during a successful
call are retained because this wrapper cannot attribute individual package
installations to concurrent OpenCode processes. Legacy records without an inventory
require full package and marker invalidation. A newly mapped package can still make a concurrent reader fail
during installation; normal failure handling belongs to OpenCode.

State/prompt directories and cache ownership locks are inherited by the child,
so they survive Python's death while OpenCode remains alive. Startup and the
first CLI call sweep unlocked leftovers belonging to the current user. Lockless
matching directories are removed regardless of age. Creation and sweeping use
the same temp-root lock; creation holds it until the live lock exists. Calls run
in parallel. Large prompts use a temporary file attachment before the
operating-system argument limit is reached. Disposable state uses
`OPENCODE_TEMP_ROOT` when set, then `XDG_RUNTIME_DIR`, so concurrent calls do
not depend on a large shared `/tmp`.

Every retry attempt runs with a finite deadline that starts at public call entry and
covers environment setup, process startup, and output collection: the
configured `llm.timeout_seconds` default (1800 recommended)
or an explicit finite positive override supplied by the caller. The shared
invocation retry policy may retry the same configured client with a fresh deadline
after a retryable failure, so this is not a job-wide retry budget. Environment
and prompt setup check that deadline before the subprocess starts, so an
expired budget fails the call without launching OpenCode. The default is not a
ceiling and no maximum-bound policy exists; there is no unlimited-time option.
Explicit batch cancellation via `cancel_active_clients()` is the additional
escape path for interrupted runs, not a replacement for the deadline.

OpenCode quota signals are classified before the shared retry loop. Structured
quota/error events on stdout are authoritative; a quota-shaped stderr log only
fails a call that produced no model text, so a completed, valid stdout
response is never rejected by stderr wording. When stdout stays open without
model text (for example a stalling provider retry loop), the stderr quota
fails the call after a bounded grace period rather than waiting for the full
process timeout. Quota exhaustion is never
retried; other backend failures receive the bounded outer retry policy.
`cancel_active_clients()` terminates and reaps every owned OpenCode process
during a batch interrupt. Cancellation is a distinct, non-retryable outcome,
so an interrupted call is never relaunched by the outer retry loop. The
cancellation latch remains set until the worker pool has shut down, which also
stops calls that register after the interrupt; the batch then resets it before
future runs.

## Public contract

Other modules should import only from:

- `invocation`: `JobRunner`, `StructuredJobRunner`, and
  `build_structured_json_prompt`;
- `jobs`: role factories, `runner_for_job`, `client_for_job`, and
  `cancel_active_clients` / `reset_client_cancellation`;
- `config`: `LlmTier`, `LlmJob`, `load_llm_job`, `load_llm_jobs`, and
  `load_llm_timeout_seconds`;
- `exceptions`: the `LlmException` hierarchy;
- `base`: `BaseLlmClient` for typed test doubles and `extract_json_object` when
  tolerant JSON parsing is needed outside the LLM layer.
- `opencode_runtime.sweep_stale_opencode_directories`: startup cleanup for
  abandoned OpenCode state and prompt directories.

Adding another transport would be an intentional contract change. It would
require a new job/configuration policy, focused adapter tests, and an updated
operator migration note; do not leave dormant vendor modules in the package as
an implicit fallback.
