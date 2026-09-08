"""Not a check itself — typed behavior fakes shared by application tests."""

from __future__ import annotations


class FakeSynthesisClient:
    """Behavior fake for the injected audio synthesis seam."""

    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self.calls: list[tuple[str, str]] = []

    def synthesize(self, *, text: str, voice: str) -> bytes:
        """Record one request and return the configured WAV bytes."""
        self.calls.append((text, voice))
        return self.audio


__all__ = ["FakeSynthesisClient"]
