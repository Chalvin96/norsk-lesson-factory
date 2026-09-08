"""Entry points: `build_stage_log`, `hash_text`, and `write_generation_log`.

This workflow-owned module contains only scratch generation log serialization and
content-addressing mechanics. Rich-authoring policy and stage sequencing stay
in ``rich_authoring.py``; this module never invokes a provider or interprets
lesson semantics. Rich authoring calls the other exported builders and hash
helpers for stage-specific variants.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from typing import Protocol

from lesson_builder.clients.llm.base import LlmResponse


class NormalizedSource(Protocol):
    """Minimal content contract needed to hash a normalized package."""

    lesson_md: str
    exercise_requests_yaml: str


def build_stage_log(name: str, prompt: str, response: LlmResponse) -> dict[str, Any]:
    """Build the successful log entry for one authoring stage."""
    return {
        "name": name,
        "status": "valid",
        "prompt_hash": hash_text(prompt),
        "prompt_chars": len(prompt),
        "response": build_response_metadata(response),
    }


def build_attempt_stage_log(name: str, attempts: list[tuple[str, LlmResponse]]) -> dict[str, Any]:
    """Serialize all attempts for one named stage without exposing content."""
    return {
        "name": name,
        "status": "valid",
        "attempts": [
            {
                "prompt_hash": hash_text(prompt),
                "prompt_chars": len(prompt),
                "response": build_response_metadata(response),
            }
            for prompt, response in attempts
        ],
    }


def build_failed_stage_log(name: str, prompt: str, exc: Exception) -> dict[str, Any]:
    """Serialize a failed stage while preserving the exception class."""
    return {
        "name": name,
        "status": "failed",
        "prompt_hash": hash_text(prompt),
        "prompt_chars": len(prompt),
        "error": f"{type(exc).__name__}: {exc}",
    }


def build_response_metadata(response: LlmResponse) -> dict[str, Any]:
    """Return stable response metadata for the scratch generation log."""
    return {
        "client": response.client,
        "model": response.model,
        "agent": response.agent,
        "variant": response.variant,
        "latency_ms": response.latency_ms,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "response_hash": hash_text(response.text),
        "attempts": [asdict(attempt) for attempt in response.attempts],
    }


def hash_package(package: NormalizedSource) -> str:
    """Hash normalized source bytes for compile logs."""
    payload = package.lesson_md.encode("utf-8") + b"\0" + package.exercise_requests_yaml.encode("utf-8")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def hash_text(value: str) -> str:
    """Hash one prompt or response without storing its full text."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def hash_config(config: dict[str, Any]) -> str:
    """Hash the effective model configuration used by a cached stage."""
    return hash_text(json.dumps(config, ensure_ascii=False, sort_keys=True, default=str))


def hash_file(path: Path) -> str:
    """Hash one authored UTF-8 file at the artifact boundary."""
    return hash_text(path.read_text(encoding="utf-8"))


def assert_file_matches_hash(path: Path, expected: str, message: str) -> None:
    """Fail closed when a repair or compiler changes immutable lesson prose."""
    actual = hash_file(path)
    if actual != expected:
        raise ValueError(f"{message}: expected {expected}, got {actual}")


def write_generation_log(root: Path, record: dict[str, Any]) -> Path:
    """Write the stage log record for scratch inspection."""
    path = root / "llm_receipt.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_stage_record(path: Path, payload: dict[str, Any]) -> None:
    """Persist one review-stage record beside its scratch evidence."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "assert_file_matches_hash",
    "build_attempt_stage_log",
    "build_failed_stage_log",
    "build_response_metadata",
    "build_stage_log",
    "hash_config",
    "hash_file",
    "hash_package",
    "hash_text",
    "write_generation_log",
    "write_stage_record",
]
