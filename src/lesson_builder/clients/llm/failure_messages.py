"""Entry points: ``format_provider_failure`` and ``provider_name_for_model`` map provider errors to author guidance."""

from __future__ import annotations

import re
from datetime import UTC
from datetime import datetime

from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.clients.llm.settings import K_ACTIONABLE_BACKEND_PATTERNS
from lesson_builder.clients.llm.settings import K_AUTHENTICATION_PATTERN
from lesson_builder.clients.llm.settings import K_FUNDS_PATTERNS
from lesson_builder.clients.llm.settings import K_OPENCODE_QUOTA_PATTERNS
from lesson_builder.clients.llm.settings import K_PROVIDER_NAMES_BY_MODEL_PREFIX
from lesson_builder.clients.llm.settings import K_RESET_TIME_PATTERN


def provider_name_for_model(model: str) -> str:
    """Return the author-facing provider name from a configured model id."""
    prefix, separator, _model_name = model.partition("/")
    if not separator:
        prefix = model
    provider = K_PROVIDER_NAMES_BY_MODEL_PREFIX.get(prefix.casefold())
    if provider is not None:
        return provider
    readable = " ".join(part.capitalize() for part in prefix.replace("_", "-").split("-") if part)
    return readable or "OpenCode"


def format_provider_failure(exception: Exception, provider_name: str) -> str:
    """Return plain-English retry guidance while leaving exception text untouched."""
    provider = provider_name.strip()
    provider_label = provider or "provider"
    detail = str(exception)
    if isinstance(exception, LlmParseException):
        return "The model returned output the workbench could not read; try again."
    if any(pattern.search(detail) for pattern in K_FUNDS_PATTERNS):
        return f"{provider_label} has no credit left. Top up the account, then retry."
    if isinstance(exception, BackendDownException) and any(
        pattern.search(detail) for pattern in K_ACTIONABLE_BACKEND_PATTERNS
    ):
        return detail
    if K_AUTHENTICATION_PATTERN.search(detail):
        if provider.casefold() == "codex":
            return "Codex rejected the login. Run `codex login` and retry."
        if provider:
            return f"{provider} authentication failed. Check its credentials, then retry."
        return "Authentication failed. Check the configured credentials, then retry."
    if isinstance(exception, LlmQuotaException) or any(
        pattern.casefold() in detail.casefold() for pattern in K_OPENCODE_QUOTA_PATTERNS
    ):
        reset_time = _find_reset_time(detail)
        if reset_time is None:
            return f"{provider_label} usage limit reached. Wait for the limit to reset."
        return f"{provider_label} usage limit reached. {reset_time}"
    return f"The {provider_label} call failed."


def _find_reset_time(detail: str) -> str | None:
    """Extract a reset time from a quota response without exposing unrelated text."""
    match = K_RESET_TIME_PATTERN.search(detail)
    if match is None:
        return None
    value = match.group("value").strip()
    if re.fullmatch(r"\d+(?:\.\d+)?\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|days?)", value, re.IGNORECASE):
        return f"It resets in {value}."
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[T ].*)?", value):
        date_value = value.replace(" UTC", "+00:00").replace(" GMT", "+00:00").replace("Z", "+00:00")
        try:
            reset_at = datetime.fromisoformat(date_value)
        except ValueError:
            return f"It resets on {value}."
        if reset_at.tzinfo is not None:
            reset_at = reset_at.astimezone(UTC)
            timezone_label = " UTC"
        else:
            timezone_label = ""
        if "T" not in value and " " not in value:
            return f"It resets on {reset_at.strftime('%B')} {reset_at.day}, {reset_at.year}."
        clock_time = reset_at.strftime("%I:%M %p").lstrip("0")
        return (
            f"It resets on {reset_at.strftime('%B')} {reset_at.day}, {reset_at.year} at {clock_time}{timezone_label}."
        )
    return f"It resets at {value}."


__all__ = ["format_provider_failure", "provider_name_for_model"]
