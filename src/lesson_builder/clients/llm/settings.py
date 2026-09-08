"""Not a check itself — LLM transport retry and OpenCode process constants.

These constants are the fixed transport behaviors of the ``clients/llm``
layer: shared backend retry policy, subprocess lifecycle bounds, and the
OpenCode CLI execution environment. ``config.yaml`` remains the routing
authority for tiers, jobs, and call deadlines.
"""

from __future__ import annotations

# Shared OpenCode invocation retry and process lifecycle settings.
K_LLM_BACKEND_RETRIES = 3
K_LLM_BACKEND_RETRY_BACKOFF_SECONDS = 2.0
K_LLM_CLI_TERMINATE_GRACE_SECONDS = 2.0

# ── Opencode CLI ────────────────────────────────────────────────────────────

K_OPENCODE_DEFAULT_MODEL = "opencode-go/glm-5.2"
K_OPENCODE_BARE_CLIENT_TIMEOUT = 1800
K_OPENCODE_EXECUTION_MODE_ENV = "OPENCODE_EXECUTION_MODE"
K_OPENCODE_EXECUTION_MODE_PARALLEL = "parallel"
K_OPENCODE_EXECUTION_MODE_SERIAL = "serial"
K_OPENCODE_DEFAULT_EXECUTION_MODE = K_OPENCODE_EXECUTION_MODE_PARALLEL
K_OPENCODE_DEFAULT_LOCK_FILENAME = "norsk-lesson-data-opencode.lock"
K_OPENCODE_XDG_DATA_HOME_ENV = "XDG_DATA_HOME"
K_OPENCODE_XDG_STATE_HOME_ENV = "XDG_STATE_HOME"
K_OPENCODE_XDG_CACHE_HOME_ENV = "XDG_CACHE_HOME"
K_OPENCODE_TEMP_ROOT_ENV = "OPENCODE_TEMP_ROOT"
K_OPENCODE_STATE_DIR_PREFIX = "norsk-opencode-state-"
K_OPENCODE_STREAM_POLL_SECONDS = 0.05
K_OPENCODE_TERMINATE_GRACE_SECONDS = K_LLM_CLI_TERMINATE_GRACE_SECONDS
K_OPENCODE_STDERR_QUOTA_GRACE_SECONDS = 2.0
# OpenCode-owned override variables are stripped from the child environment so
# inherited configuration or policy cannot bypass the validated repository
# boundary. The allowlist keeps credential entry points; provider API keys use
# their own provider-prefixed names and are unaffected.
K_OPENCODE_OVERRIDE_ENV_PREFIX = "OPENCODE_"
K_OPENCODE_OVERRIDE_ENV_ALLOWLIST = frozenset({"OPENCODE_API_KEY"})
K_OPENCODE_INLINE_PROMPT_MAX_BYTES = 128 * 1024
K_OPENCODE_QUOTA_PATTERNS = (
    "rate limit",
    "quota",
    "usage limit",
    "usage cap",
    "request limit",
    "too many requests",
    "try again later",
)

__all__ = [
    "K_LLM_BACKEND_RETRIES",
    "K_LLM_BACKEND_RETRY_BACKOFF_SECONDS",
    "K_LLM_CLI_TERMINATE_GRACE_SECONDS",
    "K_OPENCODE_DEFAULT_MODEL",
    "K_OPENCODE_BARE_CLIENT_TIMEOUT",
    "K_OPENCODE_EXECUTION_MODE_ENV",
    "K_OPENCODE_EXECUTION_MODE_PARALLEL",
    "K_OPENCODE_EXECUTION_MODE_SERIAL",
    "K_OPENCODE_DEFAULT_EXECUTION_MODE",
    "K_OPENCODE_DEFAULT_LOCK_FILENAME",
    "K_OPENCODE_XDG_DATA_HOME_ENV",
    "K_OPENCODE_XDG_STATE_HOME_ENV",
    "K_OPENCODE_XDG_CACHE_HOME_ENV",
    "K_OPENCODE_TEMP_ROOT_ENV",
    "K_OPENCODE_STATE_DIR_PREFIX",
    "K_OPENCODE_STREAM_POLL_SECONDS",
    "K_OPENCODE_TERMINATE_GRACE_SECONDS",
    "K_OPENCODE_STDERR_QUOTA_GRACE_SECONDS",
    "K_OPENCODE_OVERRIDE_ENV_PREFIX",
    "K_OPENCODE_OVERRIDE_ENV_ALLOWLIST",
    "K_OPENCODE_INLINE_PROMPT_MAX_BYTES",
    "K_OPENCODE_QUOTA_PATTERNS",
]
