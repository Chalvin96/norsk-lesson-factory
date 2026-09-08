"""Entry point: generation log behavior tests."""

import json
from pathlib import Path

import pytest

from lesson_builder.clients.llm.base import AttemptOutcome
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.workflow.lesson_generation.generation_log import assert_file_matches_hash
from lesson_builder.workflow.lesson_generation.generation_log import build_attempt_stage_log
from lesson_builder.workflow.lesson_generation.generation_log import build_stage_log
from lesson_builder.workflow.lesson_generation.generation_log import hash_package
from lesson_builder.workflow.lesson_generation.generation_log import hash_text
from lesson_builder.workflow.lesson_generation.generation_log import write_generation_log


class NormalizedSourceStub:
    def __init__(self, lesson_md: str, exercise_requests_yaml: str) -> None:
        self.lesson_md = lesson_md
        self.exercise_requests_yaml = exercise_requests_yaml


def test_build_stage_log_given_response_with_attempt_metadata_expect_hashed_non_content_log() -> None:
    response = LlmResponse(
        text="generated response",
        client="fake",
        model="test-model",
        input_tokens=12,
        output_tokens=8,
        attempts=(AttemptOutcome(client="fake", model="test-model", outcome="ok"),),
    )

    record = build_stage_log("draft_author", "author prompt", response)

    assert record["name"] == "draft_author"
    assert record["prompt_hash"].startswith("sha256:")
    assert record["response"]["response_hash"].startswith("sha256:")
    assert record["response"]["input_tokens"] == 12
    assert record["response"]["attempts"][0]["outcome"] == "ok"
    assert "generated response" not in json.dumps(record)


def test_build_attempt_stage_log_given_distinct_retry_prompts_expect_each_response_has_its_prompt_hash() -> None:
    attempts = [
        ("initial prompt", LlmResponse(text="first response", client="fake", model="test-model")),
        ("retry prompt", LlmResponse(text="second response", client="fake", model="test-model")),
    ]

    records = [build_attempt_stage_log(name, attempts) for name in ("source_normalizer", "exercise_author")]

    assert [record["name"] for record in records] == ["source_normalizer", "exercise_author"]
    for record in records:
        assert [attempt["prompt_hash"] for attempt in record["attempts"]] == [
            hash_text("initial prompt"),
            hash_text("retry prompt"),
        ]
        assert [attempt["response"]["response_hash"] for attempt in record["attempts"]] == [
            hash_text("first response"),
            hash_text("second response"),
        ]
        assert "initial prompt" not in json.dumps(record)
        assert "retry prompt" not in json.dumps(record)
        assert "first response" not in json.dumps(record)
        assert "second response" not in json.dumps(record)


def test_write_generation_log_given_scratch_root_expect_json_record_with_newline(tmp_path: Path) -> None:
    path = write_generation_log(tmp_path / "run", {"status": "valid", "stages": []})

    assert path == tmp_path / "run" / "llm_receipt.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"status": "valid", "stages": []}
    assert path.read_bytes().endswith(b"\n")


def test_assert_file_matches_hash_given_changed_file_expect_fail_closed_error(tmp_path: Path) -> None:
    source = tmp_path / "lesson.md"
    source.write_text("original", encoding="utf-8")
    expected = hash_text("original")
    source.write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="immutable lesson prose"):
        assert_file_matches_hash(source, expected, "immutable lesson prose")


def test_hash_package_given_changed_exercise_requests_expect_distinct_hash() -> None:
    first = NormalizedSourceStub("lesson", "requests-a")
    second = NormalizedSourceStub("lesson", "requests-b")

    assert hash_package(first) != hash_package(second)
