"""Application settings shared across lesson_builder modules."""

from __future__ import annotations

# ── Codex CLI ───────────────────────────────────────────────────────────────

K_CODEX_DEFAULT_MODEL = "gpt-5.5"
K_CODEX_DEFAULT_TIMEOUT = 200
K_CODEX_TIMEOUT_ENV = "CODEX_TIMEOUT_SECONDS"
K_CODEX_QUOTA_PATTERNS = (
    "rate limit",
    "quota",
    "usage limit",
    "too many requests",
    "try again later",
)

# ── Opencode CLI ────────────────────────────────────────────────────────────

K_OPENCODE_DEFAULT_MODEL = "opencode-go/glm-5.2"
K_OPENCODE_DEFAULT_TIMEOUT = 200
K_OPENCODE_TIMEOUT_ENV = "OPENCODE_TIMEOUT_SECONDS"
K_OPENCODE_QUOTA_PATTERNS = (
    "rate limit",
    "quota",
    "usage limit",
    "usage cap",
    "request limit",
    "too many requests",
    "try again later",
)

# ── OpenRouter HTTP client ───────────────────────────────────────────────────

K_OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
K_OPENROUTER_DEFAULT_MODEL = "google/gemini-2.5-flash"
K_OPENROUTER_DEFAULT_TIMEOUT = 200.0
K_OPENROUTER_QUOTA_PATTERNS = (
    "rate limit",
    "quota",
    "usage limit",
    "too many requests",
    "credits",
    "payment required",
)

# ── Z.AI GLM (Anthropic-compatible messages API) ─────────────────────────────

K_ZAI_BASE_URL_ENV = "GLM_BASE_URL"
K_ZAI_API_KEY_ENV = "GLM_API_KEY"
K_ZAI_DEFAULT_BASE_URL = "https://api.z.ai/api/anthropic"
K_ZAI_DEFAULT_MODEL = "glm-5.2"
K_ZAI_DEFAULT_TIMEOUT = 200.0
K_ZAI_TIMEOUT_ENV = "ZAI_TIMEOUT_SECONDS"
K_ZAI_MAX_TOKENS = 8000
K_ZAI_QUOTA_PATTERNS = (
    "rate limit",
    "quota",
    "usage limit",
    "too many requests",
    "credits",
    "payment required",
)
