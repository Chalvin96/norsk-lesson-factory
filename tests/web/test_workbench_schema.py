"""Entry point: test_real_serialized_lesson_state_given_sample_lesson_expect_schema_validation_passes."""

import json
from pathlib import Path

from jsonschema import Draft202012Validator

from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.web.workbench_api import build_lesson_payload

K_TEST_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
K_TEST_SCHEMA_PATH = K_TEST_REPOSITORY_ROOT / "workbench" / "schema" / "workbench-api.schema.json"


def test_real_serialized_lesson_state_given_sample_lesson_expect_schema_validation_passes() -> None:
    schema = json.loads(K_TEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    lesson_package = resolve_lesson_package(K_TEST_REPOSITORY_ROOT, "answering_ja_nei_jo", canonical_only=True)
    lesson_payload = build_lesson_payload(K_TEST_REPOSITORY_ROOT, lesson_package)
    instance = {"requests": {}, "responses": {"lesson": lesson_payload}, "components": {}}

    Draft202012Validator(schema).validate(instance)
