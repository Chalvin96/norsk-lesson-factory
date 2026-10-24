"""Not a check itself — typed behavior fakes shared by application tests."""

from __future__ import annotations

import json
import math
import wave
from array import array
from collections.abc import Callable
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path

from tests.clients.llm.fakes import FakeLlmClient


class FakeTtsClient:
    """Behavior fake for the injected audio synthesis seam."""

    def __init__(self, audio: bytes) -> None:
        self.audio = audio
        self._fn: Callable[[str, str], bytes] = lambda text, voice: self.audio
        self.calls: list[tuple[str, str]] = []

    @classmethod
    def from_fn(cls, fn: Callable[[str, str], bytes]) -> FakeTtsClient:
        """Build a fake whose WAV response depends on the requested clip."""
        client = cls(b"")
        client._fn = fn
        return client

    def synthesize(self, *, text: str, voice: str) -> bytes:
        """Record one request and return the configured WAV bytes."""
        self.calls.append((text, voice))
        return self._fn(text, voice)

    def close(self) -> None:
        """Match the owned provider client's cleanup interface."""


class FakeWorkbenchBuildRunner:
    """Behavior fake for the injected npm install and Vite build commands."""

    def __init__(self, app_root: Path, *, fail_on: tuple[str, ...] | None = None) -> None:
        self.app_root = app_root
        self.fail_on = fail_on
        self.calls: list[tuple[tuple[str, ...], Path]] = []

    def __call__(self, command: Sequence[str], *, cwd: Path) -> None:
        """Record an npm command and simulate its filesystem result."""
        normalized_command = tuple(command)
        self.calls.append((normalized_command, cwd))
        if normalized_command == self.fail_on:
            raise OSError("simulated npm failure")
        if normalized_command == ("npm", "ci"):
            (cwd / "node_modules").mkdir(exist_ok=True)
        if normalized_command == ("npm", "run", "build"):
            self.app_root.mkdir(parents=True, exist_ok=True)
            (self.app_root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")


def fake_audio_for_text(text: str, voice: str) -> bytes:
    """Return a valid, audible-length 24 kHz WAV for offline workbench flows."""
    sample_rate = 24_000
    duration = max(1.0, len(text) / 30)
    frame_count = int(sample_rate * duration)
    tail_count = int(sample_rate * 0.03)
    samples = array(
        "h",
        (int(5_000 * math.sin(2 * math.pi * 220 * index / sample_rate)) for index in range(frame_count - tail_count)),
    )
    samples.extend([0] * tail_count)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples.tobytes())
    return buffer.getvalue()


def workbench_review_reply(prompt: str, model: str | None) -> str:
    """Return schema-valid review responses for offline lifecycle tests."""
    if "PedagogyReview" in prompt:
        return json.dumps(
            {
                "scores": dict.fromkeys(
                    ["on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"], 4
                ),
                "summary": "Clear",
                "passes": [],
                "issues": [],
            }
        )
    if "NaturalnessReview" in prompt:
        return (
            '{"scores":{"idiomatic_phrasing":4,"register_appropriateness":4,"terminology_consistency":4},"issues":[]}'
        )
    if "ObjectiveAlignmentReview" in prompt:
        return '{"passed":true,"summary":"Aligned","issues":[]}'
    if "StandaloneExerciseReview" in prompt:
        payload = json.JSONDecoder().raw_decode(prompt.split("EXERCISES:\n", 1)[1])[0]
        return json.dumps({"results": [{"id": item["id"], "verdict": "standalone"} for item in payload]})
    if "AnswerReview" in prompt:
        return _build_answer_reply(prompt)
    if "AttemptReview" in prompt:
        return _build_check_reply(prompt, "EXERCISES:", field="status", value="clear")
    if "OpenRubricReview" in prompt:
        return _build_check_reply(prompt, "WRITE TASKS WITH RUBRICS:", field="status", value="clear")
    if "OpenSemanticReview" in prompt:
        return _build_check_reply(prompt, "OPEN TASKS AND EVIDENCE CLAIMS:", field="semantic_issues", value=[])
    return '{"missing":[],"extra":[],"mismatches":[],"ambiguous":[],"unanswerable":[],"duplicates":[],"semantic_issues":[],"standalone_review":[],"status":"ok"}'


def _build_check_reply(prompt: str, section: str, *, field: str, value: object) -> str:
    payload = json.JSONDecoder().raw_decode(prompt.split(f"{section}\n", 1)[1])[0]
    return json.dumps({"checks": [{"id": item["id"], field: value} for item in payload]})


def _build_answer_reply(prompt: str) -> str:
    payload = json.JSONDecoder().raw_decode(prompt.split("EXERCISES:\n", 1)[1])[0]
    answers = [
        {
            "id": item["id"],
            "answer": None,
            "status": "ambiguous",
            "reason": "The test reviewer leaves answer judgments unresolved.",
        }
        for item in payload
    ]
    return json.dumps({"answers": answers})


__all__ = [
    "FakeLlmClient",
    "FakeTtsClient",
    "FakeWorkbenchBuildRunner",
    "fake_audio_for_text",
    "workbench_review_reply",
]
