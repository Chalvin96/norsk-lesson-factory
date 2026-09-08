"""Not a check itself — typed behavior fakes for the Google TTS adapter tests."""

from __future__ import annotations

import httpx


class FakeHttpClient:
    """Return successful OAuth and synthesis responses."""

    def __init__(self, encoded_audio: str) -> None:
        self.encoded_audio = encoded_audio
        self.urls: list[str] = []
        self.json_payloads: list[object] = []

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        self.urls.append(url)
        self.json_payloads.append(kwargs.get("json"))
        if "oauth2" in url:
            return httpx.Response(
                200,
                json={"access_token": "test-token"},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={"audioContent": self.encoded_audio},
            request=httpx.Request("POST", url),
        )


class UnauthorizedThenWavHttpClient:
    """Return 401 for the first N TTS calls, then succeed."""

    def __init__(self, encoded_audio: str, *, failures: int) -> None:
        self.encoded_audio = encoded_audio
        self.failures = failures
        self.tokens_issued = 0
        self.tts_calls = 0
        self.tts_authorizations: list[str | None] = []

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        if "oauth2" in url:
            self.tokens_issued += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{self.tokens_issued}"},
                request=httpx.Request("POST", url),
            )
        headers = kwargs.get("headers") or {}
        authorization = headers.get("Authorization") if isinstance(headers, dict) else None
        self.tts_authorizations.append(authorization)
        self.tts_calls += 1
        if self.tts_calls <= self.failures:
            return httpx.Response(
                401,
                json={"error": "unauthorized"},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={"audioContent": self.encoded_audio},
            request=httpx.Request("POST", url),
        )
