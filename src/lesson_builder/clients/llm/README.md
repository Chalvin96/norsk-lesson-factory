# clients/llm

LLM client boundary: typed errors, one OpenCode CLI transport, structured
(schema-validated) output, and the jobs that compose it. Production jobs are
single-client OpenCode calls with bounded retry for transient transport errors.

## Changing the model

`config.yaml` is the single source of truth for this routing layer. Tiers
name cheap model/variant pairs by relative cost (`cheap`, `standard`, `deep`,
`deep_alt`); jobs bind stable workflow names (`author`, `reviewer`, and so on)
to exactly one tier plus one agent template, so workflow code does not change
when a model moves. Change a tier's `model` to select another model or
provider routed through OpenCode; there is no adapter field and no
backward-compatible alias for retired ones.

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
├── config.py            strict root config.yaml tier/job loader
├── invocation.py        one-client JobRunner, structured output, bounded retry
├── jobs.py              role composition boundary: config-selected jobs
└── opencode.py          OpencodeClient (`opencode run`)
```

## What goes where

**`base.py`** contains the shared `BaseLlmClient` contract, case-insensitive
quota matching, the process registry used by batch cancellation, and tolerant
JSON extraction used by structured invocation.
OpenCode owns its subprocess streaming, temporary state, prompt attachment,
quota-event parsing, and provider-specific command flags in `opencode.py`.

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

**`jobs.py`** is the composition boundary. `runner_for_job()` and
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
Nodes own task/user prompts (prompt builders live beside their nodes), the
invocation layer owns schema/repair instructions, and nodes cannot override
tools. Authoring/review templates disable tools; `catalog-research` allows
only `websearch`/`webfetch`. A model response never approves or publishes an
artifact; LangGraph remains the checkpoint and human-gate authority.

Each call receives temporary XDG data/state/cache directories and copies only
`opencode/auth.json`. The shared OpenCode database, history, logs, and tool
output are not copied. Inherited `OPENCODE_`-prefixed override variables
(config paths, permissions, routing) are stripped from the child environment
so they cannot bypass the repository's validated configuration; credential
variables are preserved, and the only allowlisted OpenCode-owned name is
`OPENCODE_API_KEY`. Calls run in parallel by default; set
`OPENCODE_EXECUTION_MODE=serial` only as an emergency rollback when diagnosing
a runtime-specific concurrency problem. Large prompts use a temporary file
attachment before the operating-system argument limit is reached. Disposable
state uses `OPENCODE_TEMP_ROOT` when set, then `XDG_RUNTIME_DIR`, so concurrent
calls do not depend on a large shared `/tmp`.

Every retry attempt runs with a finite deadline that starts at public call entry and
covers serial-slot acquisition, environment setup, process startup, and output
collection: the configured `llm.timeout_seconds` default (1800 recommended)
or an explicit finite positive override supplied by the caller. The shared
invocation retry policy may retry the same configured client with a fresh deadline
after a retryable failure, so this is not a job-wide retry budget. Environment
and prompt setup check that deadline before the execution slot is acquired or
the subprocess starts, so an expired budget fails the call without launching
OpenCode. A busy serial execution slot fails the call at that same deadline
instead of blocking without bound. The default is not a ceiling and no
maximum-bound policy exists; there is no unlimited-time option. Explicit
batch cancellation via `cancel_active_clients()` is the additional escape path
for interrupted runs, not a replacement for the deadline.

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

Adding another transport would be an intentional contract change. It would
require a new job/configuration policy, focused adapter tests, and an updated
operator migration note; do not leave dormant vendor modules in the package as
an implicit fallback.
