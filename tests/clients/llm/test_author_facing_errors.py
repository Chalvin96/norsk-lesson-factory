"""Entry point: provider failures become useful author-facing guidance."""

from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.clients.llm.failure_messages import format_provider_failure
from lesson_builder.clients.llm.failure_messages import provider_name_for_model


def test_provider_failure_given_insufficient_account_funds_expect_top_up_guidance() -> None:
    exception = BackendDownException("Upstream request failed: Insufficient account funds")

    assert format_provider_failure(exception, "Z.AI") == "Z.AI has no credit left. Top up the account, then retry."


def test_provider_failure_given_http_401_unauthorized_expect_login_guidance() -> None:
    exception = BackendDownException("HTTP 401: Unauthorized")

    assert format_provider_failure(exception, "OpenAI") == (
        "OpenAI authentication failed. Check its credentials, then retry."
    )


def test_provider_failure_given_codex_authentication_error_expect_codex_login_guidance() -> None:
    exception = BackendDownException("Login required: expired token")

    assert format_provider_failure(exception, "Codex") == "Codex rejected the login. Run `codex login` and retry."


def test_provider_failure_given_google_tts_authentication_error_expect_google_credentials_guidance() -> None:
    exception = BackendDownException("HTTP 401: Unauthorized")

    assert format_provider_failure(exception, "Google Cloud Text-to-Speech") == (
        "Google Cloud Text-to-Speech authentication failed. Check its credentials, then retry."
    )


def test_provider_failure_given_timeout_expect_actionable_backend_message() -> None:
    message = "backend timed out after 8s: 'opencode'"

    assert format_provider_failure(BackendDownException(message), "OpenCode Go") == message


def test_provider_failure_given_backend_cli_not_installed_expect_actionable_backend_message() -> None:
    message = "backend CLI not found: 'opencode'"

    assert format_provider_failure(BackendDownException(message), "OpenCode Go") == message


def test_provider_failure_given_missing_opencode_login_expect_actionable_backend_message() -> None:
    message = "No OpenCode credential found for provider 'google'. Run 'opencode auth login' and select that provider."

    assert format_provider_failure(BackendDownException(message), "Google") == message


def test_provider_failure_given_llm_parse_exception_expect_unreadable_output_guidance() -> None:
    exception = LlmParseException("no JSON in response; response was: '<untrusted output>'")

    assert format_provider_failure(exception, "OpenAI") == (
        "The model returned output the workbench could not read; try again."
    )


def test_provider_failure_given_llm_quota_exception_without_reset_expect_wait_guidance() -> None:
    exception = LlmQuotaException("request rejected")

    assert format_provider_failure(exception, "OpenCode Go") == (
        "OpenCode Go usage limit reached. Wait for the limit to reset."
    )


def test_provider_failure_given_quota_pattern_with_reset_time_expect_reset_guidance() -> None:
    exception = BackendDownException("429: rate limit reached; resets at 2026-10-16T14:30:00Z")

    assert format_provider_failure(exception, "OpenAI") == (
        "OpenAI usage limit reached. It resets on October 16, 2026 at 2:30 PM UTC."
    )


def test_provider_failure_given_unknown_exception_expect_provider_fallback() -> None:
    exception = RuntimeError("")

    assert format_provider_failure(exception, "Z.AI") == "The Z.AI call failed."


def test_provider_name_given_configured_model_id_expect_provider_display_name() -> None:
    assert provider_name_for_model("zai-coding-plan/glm-5.3") == "Z.AI"
    assert provider_name_for_model("opencode-go/glm-5.2") == "OpenCode Go"
    assert provider_name_for_model("openai/gpt-6.1-sol") == "OpenAI"
