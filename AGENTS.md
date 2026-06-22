# Agent Conventions

Guidelines for humans and AI agents working in this repository.

## Test naming

All test functions must follow the pattern:

```
test_<what_is_being_tested>_given_<condition>_expect_<output>
```

- **what_is_being_tested**: the unit or behavior under test (e.g. `call_llm`, `codex_client`, `lesson_acceptance_log`)
- **given (condition)**: the input, state, or mock setup (e.g. `quota_on_first_step`, `missing_cli`, `empty_chain`)
- **expect (output)**: the asserted outcome (e.g. `failover_to_second`, `backend_down_exception`, `validated_object`)

Example:

```python
def test_call_llm_given_quota_on_first_step_expect_failover_to_second():
    ...
```

Keep names readable: use snake_case, avoid abbreviations, and prefer specifics over vague terms like `happy_path` or `sad_path`.

## Test structure

Each test should follow a clear setup, execute, assert shape:

```python
def test_call_llm_given_quota_on_first_step_expect_failover_to_second():
    # setup
    chain = [...]

    # execute
    result = call_llm(chain, "prompt", clients=clients)

    # assert
    assert result == "recovered"
```

This does not require literal comments in every test. The important rule is that setup happens first, the behavior under test is executed once in the middle, and assertions come last. Avoid mixing assertions into setup unless the assertion is the behavior being tested.

## File organization

Every module must open with a docstring whose first line names its entry point
function by name: `` Entry point: `thing_check`. `` If a module's public API is
a data type rather than a function (e.g. a shared result/context model), say so
instead: `` Not a check itself — ... `` If a module genuinely has more than one
entry point for different callers, name all of them and say which caller uses
which.

Within a module, public functions/classes (no leading underscore) come first, in the
order a reader should encounter them — the primary entry point (the function most
callers import, e.g. a `*_check`) goes first, other public helpers follow. Private
`_`-prefixed helpers go last, each placed near where it's first used by the public
code above it.

This means a reader opening any file can find "what does this module do, and where
do I start reading" in the first three lines, without scrolling past implementation
details first.

```python
def thing_check(lesson: dict) -> list[CheckResult]: # public entry point — first
    ...

def other_public_helper(...):                        # other public API — next
    ...

def _private_helper(...):                            # implementation detail — last
    ...
```

## Package organization (`__init__.py`)

When a package has a clear entry point (the function/class most callers should
start from), `__init__.py` must surface it in the module docstring: name the
entry point explicitly and say one sentence about what it does and what it calls.

Imports themselves stay ruff/isort-sorted (alphabetical by module) — don't fight
the formatter with manual import ordering or per-import comments, they get
silently reshuffled. Put the role-based grouping (e.g. "deterministic checks",
"advisory checks", "standalone utilities") in `__all__` instead, as a comment
header per group; `__all__` is a plain list literal so isort won't touch it.

A reader opening `__init__.py` should be able to answer "where do I start" (from
the docstring) and "what's in this package, and how does it relate" (from the
grouped `__all__`) without opening any other file.

## Variable naming

Avoid vague container names like `data` for parameters or local variables. Name
the variable after what it actually contains: use `lesson`, `manifest`,
`requirements`, `payload`, `review`, `exported_lesson`, etc. When a helper is
generic and truly accepts any mapping, choose a role-based name such as
`mapping`, `record`, or `raw_payload` rather than `data`.

## Constant naming

All module-level constants must be prefixed with `K_` and use `UPPER_SNAKE_CASE`, grouped by the component they belong to:

```
K_<COMPONENT>_<NAME>
```

- Prefix the constant name with the component/owner so it is greppable and self-documenting
- All config (models, timeouts, env-var names, quota patterns, URLs) lives in one `settings.py` per layer

Example:

```python
# settings.py
K_CODEX_DEFAULT_MODEL = "gpt-5.5"
K_CODEX_QUOTA_PATTERNS = ("rate limit", "quota", "too many requests")

K_API_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
K_API_KEY_ENV = "OPENROUTER_API_KEY"
```

## Testing guidelines

### What to test

Write tests for **observable runtime behavior**, not structural definitions. Good tests:

- **Integration paths**: a client receives mock subprocess/HTTP output and raises the right exception or returns the right text (e.g. codex stderr contains "rate limit" -> `LlmQuotaException`)
- **Routing and failover logic**: quota/backend-down triggers failover; parse errors propagate immediately; chain exhaustion re-raises the last exception
- **State transitions and edge cases**: empty input, missing config, timeout, nonzero exit, persistent failures
- **Regression guards**: non-greedy JSON extraction, round-trip export equality, lesson acceptance baseline exclusions
- **Data invariants**: all committed lesson acceptance log entries are valid baselines; dist == data/lessons == manifest count

### What NOT to test

- **Language primitives**: `issubclass`, `isinstance`, `json.loads('{"a": 1}')`, `"x" in "xyz"` are stdlib behavior, not application logic
- **Type hierarchy for its own sake**: if the routing code already catches `LlmQuotaException` separately from `LlmParseException`, the hierarchy is proven by the routing test, not by an `issubclass` assertion
- **Static pattern tuples**: don't test that `any(p in text for p in K_QUOTA_PATTERNS)` matches a known pattern string; test the full call path that depends on it
- **Redundant assertions**: if test A already checks `len(chain) == 1` and `step.model == "gpt-5.5"`, don't add test B that only re-checks `len(chain) == 1`
- **Vague "happy path" / "sad path" tests**: name the actual condition and outcome

### Test isolation

- Mock external calls (`subprocess.run`, `httpx.post`) via `monkeypatch`; never hit real CLIs or APIs unless gated behind an env flag (see `LLM_SMOKE` in `test_acceptance_smoke.py`)
- Use `tmp_path` for filesystem tests; never write to the repo tree
- Each test must pass independently; no ordering dependencies

### Fakes and factories

There are two distinct needs. Use the right tool for each — do not reach for one
where the other belongs.

**1. Building data objects (Pydantic models / DTOs).** Use `factory_boy`, one
`factory.Factory` per model in `tests/<layer>/factories.py` (e.g.
`LessonAcceptanceEntryFactory`). Override only the fields the test cares about; the
factory supplies the rest. This is *not dogma*: when the specific field values
**are** the behavior under test, write the literal inline. Use a factory when the
object is incidental setup or one already exists; use a literal when the shape is
the point.

**2. Faking behavior (LLM clients, subprocess, network).** `factory_boy` does not
apply — it builds data, not behavior. Use one shared typed fake, not a mix of
ad-hoc per-file classes:

- Reusable fake **classes/helpers** live in `tests/<layer>/fakes.py` and are
  imported explicitly. The canonical LLM fake is
  `FakeLlmClient(BaseLlmClient)`, driven by an `fn(prompt, model) -> str` closure
  so one class covers every case: canned, echo, conditional, raise, malformed
  JSON. Convenience constructors keep call sites clean:
  `FakeLlmClient.responding("text")`, `FakeLlmClient.raising(exc)`,
  `FakeLlmClient.from_fn(fn)`.
- `conftest.py` stays thin: only pytest fixtures that *inject* fakes with common
  defaults. Don't put shared classes there — `conftest` is implicit wiring;
  fakes read better as explicit imports.
- Name fakes by role when they differ meaningfully
  (`FakeLlmClient`, `RecordingLlmClient`, `FailingLlmClient`).
- Prefer a fake for return/raise behavior. Reach for `unittest.mock`
  (incl. `create_autospec`) **only** for interaction assertions ("was `call`
  invoked with this prompt/model?"). For simple call-count needs, expose a
  `calls: list[...]` on the fake instead of swapping in a mock.
- Subclassing the `BaseLlmClient` ABC guarantees the method *exists*, not that its
  signature still matches — it is a cheap guard, not full drift protection. Pair
  it with type annotations.
- Shared fakes must never touch subprocess, network, env, or the repo
  filesystem. Patch agent factories (e.g. `fixers.author`) explicitly at the call
  site with `monkeypatch.setattr`; avoid magical string-target patch helpers.
