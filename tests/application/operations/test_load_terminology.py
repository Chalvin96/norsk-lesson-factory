"""Entry points: ``load_terminology_registry`` and ``load_terminology_bans``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from lesson_builder.application.operations.load_terminology import load_terminology_bans
from lesson_builder.application.operations.load_terminology import load_terminology_registry


def test_load_terminology_registry_given_valid_yaml_expect_resolves_labels(tmp_path: Path):
    registry = load_terminology_registry(_write_registry(tmp_path))

    finite = registry.find_concept_by_id("finite-verb")
    assert finite is not None
    assert finite.preferred_label == "finite verb"
    assert finite.norwegian_label == "finitt verb"
    assert "verb that shows tense" in finite.alternative_labels

    demonstrative = registry.find_concept_by_id("demonstrative")
    assert demonstrative is not None
    assert demonstrative.preferred_label == "demonstrative"
    assert demonstrative.norwegian_label == "demonstrativ"


def test_load_terminology_registry_given_duplicate_ids_expect_validation_error(tmp_path: Path):
    data = {
        "schema_version": "1",
        "concepts": [
            {"id": "finite-verb", "preferred_label": "finite verb"},
            {"id": "finite-verb", "preferred_label": "another term"},
        ],
    }

    with pytest.raises(ValidationError, match="duplicate"):
        load_terminology_registry(_write_registry(tmp_path, data))


def test_load_terminology_registry_given_missing_file_expect_file_not_found_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_terminology_registry(tmp_path / "nonexistent.yaml")


def test_load_terminology_registry_given_invalid_preferred_label_expect_validation_error(tmp_path: Path):
    data = {"schema_version": "1", "concepts": [{"id": "finite-verb", "preferred_label": ""}]}

    with pytest.raises(ValidationError):
        load_terminology_registry(_write_registry(tmp_path, data))


def test_load_terminology_bans_given_missing_registry_expect_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="terminology registry not found"):
        load_terminology_bans(tmp_path / "glossary.yaml")


def _write_registry(tmp_path: Path, data: dict | None = None) -> Path:
    payload = data or {
        "schema_version": "1",
        "concepts": [
            {
                "id": "finite-verb",
                "preferred_label": "finite verb",
                "norwegian_label": "finitt verb",
                "alternative_labels": ["verb that shows tense"],
                "scope": "active",
            },
            {
                "id": "demonstrative",
                "preferred_label": "demonstrative",
                "norwegian_label": "demonstrativ",
                "alternative_labels": ["demonstrative pronoun"],
                "scope": "reference",
            },
        ],
    }
    path = tmp_path / "glossary.yaml"
    path.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
