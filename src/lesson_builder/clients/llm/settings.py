"""Not a check itself — LLM transport retry and OpenCode process constants.

These constants are the fixed transport behaviors and provider failure matching
rules of the ``clients/llm`` layer. ``config.yaml`` remains the routing authority
for tiers, jobs, and call deadlines.
"""

from __future__ import annotations

import re

# Author-facing provider failure matching and naming.
K_PROVIDER_NAMES_BY_MODEL_PREFIX = {
    "anthropic": "Anthropic",
    "google": "Google",
    "openai": "OpenAI",
    "opencode-go": "OpenCode Go",
    "zai": "Z.AI",
    "zai-coding-plan": "Z.AI",
}
K_ACTIONABLE_BACKEND_PATTERNS = (
    re.compile(r"\btimed out after\s+\d+(?:\.\d+)?\s*(?:s|seconds?)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:backend\s+)?(?:CLI|command(?:-line)? tool)\s+(?:not found|not installed|could not start)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bRun\s+['`]?opencode auth login\b", re.IGNORECASE),
)
K_FUNDS_PATTERNS = (
    re.compile(r"\binsufficient\b.*\b(?:funds?|credits?|balance)\b", re.IGNORECASE),
    re.compile(r"\b(?:no|out of|low)\s+(?:account\s+)?(?:funds?|credits?|balance)\b", re.IGNORECASE),
    re.compile(r"\b(?:credits?|balance)\s+(?:depleted|exhausted|insufficient|low|empty)\b", re.IGNORECASE),
    re.compile(r"\bbalance(?: is)?\s+(?:too low|insufficient|negative)\b", re.IGNORECASE),
)
K_AUTHENTICATION_PATTERN = re.compile(
    r"\b401\b|\bunauthori[sz]ed\b|\bunauthenticated\b|\bauthentication required\b"
    r"|\binvalid (?:api )?(?:key|token)\b|\bexpired (?:api )?(?:key|token)\b|\blog[ -]?in required\b",
    re.IGNORECASE,
)
K_RESET_TIME_VALUE_PATTERN = (
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|\s*(?:UTC|GMT)|[+-]\d{2}:?\d{2})?)?"
    r"|\d{1,2}:\d{2}\s*(?:AM|PM)?(?:\s*(?:UTC|GMT|[A-Z]{2,5}))?"
    r"|\d+(?:\.\d+)?\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)"
)
K_RESET_TIME_PATTERN = re.compile(
    rf"\b(?:reset(?:s)?(?:\s+time)?|retry|available|try again)\s*(?:(?:is|:|at|in|after)\s*)+"
    rf"(?P<value>{K_RESET_TIME_VALUE_PATTERN})",
    re.IGNORECASE,
)

# Shared OpenCode invocation retry and process lifecycle settings.
K_LLM_BACKEND_RETRIES = 3
K_LLM_BACKEND_RETRY_BACKOFF_SECONDS = 2.0
K_LLM_CLI_TERMINATE_GRACE_SECONDS = 2.0

# ── Opencode CLI ────────────────────────────────────────────────────────────

K_OPENCODE_DEFAULT_MODEL = "opencode-go/glm-5.2"
K_OPENCODE_BARE_CLIENT_TIMEOUT = 1800
K_OPENCODE_XDG_DATA_HOME_ENV = "XDG_DATA_HOME"
K_OPENCODE_XDG_STATE_HOME_ENV = "XDG_STATE_HOME"
K_OPENCODE_XDG_CACHE_HOME_ENV = "XDG_CACHE_HOME"
K_OPENCODE_TEMP_ROOT_ENV = "OPENCODE_TEMP_ROOT"
K_OPENCODE_SHARED_CACHE_RELATIVE_PATH = "norsk-lesson-data/opencode"
K_OPENCODE_CONFIG_CONTENT_ENV = "OPENCODE_CONFIG_CONTENT"
K_OPENCODE_CONFIG_CONTENT = '{"skills":{"urls":[]}}'
K_OPENCODE_CACHE_POPULATION_LOCK_FILENAME = ".norsk-opencode-cache-population.lock"
K_OPENCODE_CACHE_INTENT_LOCK_FILENAME = ".norsk-opencode-cache-intent.lock"
K_OPENCODE_CACHE_MARKER_LOCK_FILENAME = ".norsk-opencode-cache-marker.lock"
K_OPENCODE_CACHE_RECORD_PREFIX = ".norsk-opencode-cache-record-"
K_OPENCODE_CACHE_WARM_MARKER_PREFIX = ".norsk-opencode-cache-warm-"
K_OPENCODE_CACHE_PENDING_PREFIX = ".norsk-opencode-cache-pending-"
K_OPENCODE_CACHE_INSTALL_LOCKS_DIR = ".norsk-opencode-install-locks"
K_OPENCODE_TEMP_ROOT_LOCK_FILENAME = ".norsk-opencode-temp-root-{uid}.lock"
K_OPENCODE_TEMP_LIVE_LOCK_FILENAME = ".norsk-opencode-live.lock"
K_OPENCODE_STATE_DIR_PREFIX = "norsk-opencode-state-"
K_OPENCODE_PROMPT_DIR_PREFIX = "norsk-opencode-prompt-"
K_OPENCODE_OAUTH_REFRESH_WINDOW_SECONDS = 300
K_OPENCODE_OAUTH_EXPIRY_MILLISECONDS_THRESHOLD = 100_000_000_000
K_OPENCODE_AUTH_LOCK_POLL_SECONDS = 0.05
K_OPENCODE_STREAM_POLL_SECONDS = 0.05
K_OPENCODE_STREAM_CHANNEL_COUNT = 2
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
    "K_PROVIDER_NAMES_BY_MODEL_PREFIX",
    "K_ACTIONABLE_BACKEND_PATTERNS",
    "K_FUNDS_PATTERNS",
    "K_AUTHENTICATION_PATTERN",
    "K_RESET_TIME_VALUE_PATTERN",
    "K_RESET_TIME_PATTERN",
    "K_LLM_BACKEND_RETRIES",
    "K_LLM_BACKEND_RETRY_BACKOFF_SECONDS",
    "K_LLM_CLI_TERMINATE_GRACE_SECONDS",
    "K_OPENCODE_DEFAULT_MODEL",
    "K_OPENCODE_BARE_CLIENT_TIMEOUT",
    "K_OPENCODE_XDG_DATA_HOME_ENV",
    "K_OPENCODE_XDG_STATE_HOME_ENV",
    "K_OPENCODE_XDG_CACHE_HOME_ENV",
    "K_OPENCODE_TEMP_ROOT_ENV",
    "K_OPENCODE_SHARED_CACHE_RELATIVE_PATH",
    "K_OPENCODE_CONFIG_CONTENT_ENV",
    "K_OPENCODE_CONFIG_CONTENT",
    "K_OPENCODE_CACHE_POPULATION_LOCK_FILENAME",
    "K_OPENCODE_CACHE_INTENT_LOCK_FILENAME",
    "K_OPENCODE_CACHE_MARKER_LOCK_FILENAME",
    "K_OPENCODE_CACHE_RECORD_PREFIX",
    "K_OPENCODE_CACHE_WARM_MARKER_PREFIX",
    "K_OPENCODE_CACHE_PENDING_PREFIX",
    "K_OPENCODE_CACHE_INSTALL_LOCKS_DIR",
    "K_OPENCODE_TEMP_ROOT_LOCK_FILENAME",
    "K_OPENCODE_TEMP_LIVE_LOCK_FILENAME",
    "K_OPENCODE_STATE_DIR_PREFIX",
    "K_OPENCODE_PROMPT_DIR_PREFIX",
    "K_OPENCODE_OAUTH_REFRESH_WINDOW_SECONDS",
    "K_OPENCODE_OAUTH_EXPIRY_MILLISECONDS_THRESHOLD",
    "K_OPENCODE_AUTH_LOCK_POLL_SECONDS",
    "K_OPENCODE_STREAM_POLL_SECONDS",
    "K_OPENCODE_STREAM_CHANNEL_COUNT",
    "K_OPENCODE_TERMINATE_GRACE_SECONDS",
    "K_OPENCODE_STDERR_QUOTA_GRACE_SECONDS",
    "K_OPENCODE_OVERRIDE_ENV_PREFIX",
    "K_OPENCODE_OVERRIDE_ENV_ALLOWLIST",
    "K_OPENCODE_INLINE_PROMPT_MAX_BYTES",
    "K_OPENCODE_QUOTA_PATTERNS",
]
