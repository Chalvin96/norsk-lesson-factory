# pipeline/llm

LLM invocation layer: typed errors, vendor clients, failover, and structured
(schema-validated) output. This README defines the boundaries between the
files here so they don't drift back into per-vendor `config.py` files,
duplicated quota matchers, or speculative unused clients.

## Layout

```
llm/
├── base.py         contracts + shared mechanics every client needs
├── exceptions.py   the typed error hierarchy
├── invocation.py   orchestration: Agent, failover chain, structured output
└── clients/
    ├── codex.py       CodexClient      (CLI: `codex exec`)
    ├── opencode.py    OpencodeClient   (CLI: `opencode run`)
    ├── openrouter.py  OpenrouterClient (HTTP: OpenAI-compatible chat-completions)
    └── zai.py         ZaiClient        (HTTP: Z.AI GLM, Anthropic-messages format)
```

## What goes where

**`base.py`** — anything a vendor client needs that ISN'T specific to one
vendor: the abstract `BaseLlmClient`/`BaseLlmCli`/`BaseLlmApi` contracts,
`matches_quota` (concrete on `BaseLlmClient`, not abstract — it only needs
`self.quota_patterns`), `exec_subprocess` (CLI process invocation +
timeout/not-found mapping to `BackendDownException`, used by the CLI
clients), `BaseLlmApi.call()` (the shared OpenAI-compatible chat-completions
HTTP request/response handling, used by HTTP clients), and
`extract_json_object` (tolerant JSON extraction used by
`invocation.StructuredAgent`).

Rule: if a helper is used by *more than one* client, or by `invocation.py`,
it lives in `base.py`. It does not get its own file. We tried `structured.py`
and `clients/utils.py` as separate "shared helper" modules and it just split
one concern (shared client mechanics) across three files with no real
boundary between them — don't recreate that split.

**`exceptions.py`** — the `LlmException` hierarchy only. Kept separate from
`base.py` because it's imported from far more places (every client, every
test) than the abstract base classes are, and it should never need to import
anything else — keeping it dependency-free avoids import cycles as the
module grows.

**`invocation.py`** — orchestration only: `BackendStep` (one hop in a
fallback chain), `Agent`/`StructuredAgent` (bind a chain + run/repair-loop
structured calls), `AgentRegistry`, `call_llm` (the failover loop), and
`default_clients()` (the name → client wiring). This file knows about every
concrete client by name, by design — it's the one place that's allowed to.

**`clients/<vendor>.py`** — one file per vendor, each holding:
- a class implementing `call()` (CLI clients implement it directly via
  `exec_subprocess`; HTTP clients inherit `BaseLlmApi.call()` and only
  implement `api_key()`)
- its own `__init__` defaults pulled from `lesson_builder.settings`
  constants, with the *one* real env-var override read inline (do not add a
  `config.py` — there is no reasoning layer between "env var" and "field on
  the client" that earns its own file/dataclass)
- `quota_patterns` set as an instance attribute, written independently per
  vendor even where the wording happens to overlap (see below);
  `matches_quota` is inherited from `base.py`, never reimplemented
- HTTP vendors only: `quota_status_codes` (a `BaseLlmApi` class attribute,
  default `(429,)`) for cases where the vendor's quota/payment signal is the
  status code itself rather than reliably-matchable body text — e.g.
  OpenRouter returns `402 Payment Required` with no guaranteed body, so
  `OpenrouterClient` overrides it to `(429, 402)`. Same independence rule as
  `quota_patterns`: don't hoist this onto `BaseLlmApi`'s default just because
  one vendor needs it.

`zai.py` (`ZaiClient`) is the primary reviewer backend; `openrouter.py`
(`OpenrouterClient`) is the shared last-resort HTTP fallback in both the author
and reviewer chains (`pipeline/agents.py`). Both are wired through
`default_clients()`.

## Quota patterns are NOT shared data

`settings.K_CODEX_QUOTA_PATTERNS` and `K_OPENCODE_QUOTA_PATTERNS` look
similar but are independent domain knowledge: each tuple encodes "what does
*this specific* CLI actually print when *its* vendor rate-limits it." That
they currently share some English words is coincidence, not a contract. Do
not factor a `_COMMON_QUOTA_PATTERNS` base tuple — editing it to fix codex's
detection would silently change opencode's matching too. Keep each vendor's
tuple fully spelled out, even with duplicate-looking strings.

## When does something get a shared base vs. live only on one client?

- **Two clients need identical *logic*** (e.g. "match these substrings
  case-insensitively") → it belongs on `BaseLlmClient` in `base.py` as a
  concrete method, not duplicated, and not extracted into a free function in
  a new module.
- **Two clients have similar-looking *data*** (e.g. quota pattern strings)
  → do NOT merge it just because it looks the same today. Data dedup is only
  correct when the values are actually the same fact, not when they
  coincidentally match. See above.
- **Only one client needs it** (e.g. opencode's JSON-event-line parsing,
  codex's tempfile-output convention) → it stays inside that vendor's file,
  even if it's "shared-helper-shaped." A helper used by exactly one caller
  is not shared.
- **Transport mechanics differ structurally** (subprocess+files vs.
  subprocess+stdout-streaming vs. HTTP+JSON-body) → that's what
  `BaseLlmCli` vs. `BaseLlmApi` are for. Don't add a third transport base
  speculatively; add one when a vendor with a genuinely different transport
  shape is actually being wired in.

## When do we add a new vendor file under `clients/`?

When there's a concrete plan to use it — either an `Agent` profile in
`pipeline/agents.py` already points at it, or it's staged for near-term work
(see `openrouter.py` above). Don't add a vendor client purely speculatively
with no plan at all; that's the difference between "staged for next cycle"
and "might be nice someday." If you're adding a new HTTP-transport vendor,
reuse `BaseLlmApi` rather than rebuilding the request/response handling per
vendor.

## Public contract — what's safe to import from outside this package

Other modules (`pipeline/agents.py`, tests, future graph nodes) should only
import from:
- `invocation` — `Agent`, `AgentRegistry`, `BackendStep`, `call_llm`,
  `default_clients`
- `exceptions` — the `LlmException` hierarchy
- `base` — `BaseLlmClient` (for typing test doubles), `extract_json_object`
  (if tolerant JSON parsing is needed outside the LLM layer)

Nothing under `clients/` should be imported from outside `pipeline/llm/`
except by name through `default_clients()`/the registry. If you find
yourself importing `CodexClient` directly elsewhere, that's a sign the
caller should be going through an `Agent`/`BackendStep` instead.

## Registry keys

`default_clients()` keys the dict by each client's own `.name` attribute
(`{c.name: c for c in (...)}`) — `name` is the single source of truth for a
client's wire identity. Don't hand-write the dict key separately from the
class's `name`; if they drift, lookups fail silently with a `KeyError` that
looks like a missing-client bug instead of a typo.
