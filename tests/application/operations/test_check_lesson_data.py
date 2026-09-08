"""Behavior tests for the composed read-only workspace check."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from lesson_builder.application.operations.check_lesson_data import WorkspaceCheckCounts
from lesson_builder.application.operations.check_lesson_data import check_workspace
from tests.workspace.builders import K_LESSON_ID
from tests.workspace.builders import assemble_valid_workspace
from tests.workspace.builders import workspace_snapshot


def test_check_workspace_given_complete_workspace_expect_five_passed_checks_and_no_writes(tmp_path: Path) -> None:
    assemble_valid_workspace(tmp_path)
    snapshot_before = workspace_snapshot(tmp_path)

    report = check_workspace(tmp_path)

    assert report.schema_version == 1
    assert report.workspace_root == str(tmp_path.resolve())
    assert report.status == "valid"
    assert report.read_only is True
    assert [result.check_id for result in report.checks] == [
        "content_layout",
        "character_registry",
        "terminology_registry",
        "curriculum_plan",
        "distribution",
    ]
    assert all(result.status == "passed" for result in report.checks)
    assert all(not result.issues for result in report.checks)
    assert all(not result.blocked_by for result in report.checks)
    assert report.counts == WorkspaceCheckCounts(passed=5, failed=0, skipped=0, error=0)
    assert workspace_snapshot(tmp_path) == snapshot_before


def test_check_workspace_given_malformed_registries_expect_simultaneous_failures_and_distribution_skip(
    tmp_path: Path,
) -> None:
    assemble_valid_workspace(tmp_path)
    (tmp_path / "content" / "authoring" / "characters.yaml").write_text(
        "schema_version: 1\ncharacters: []\n", encoding="utf-8"
    )
    (tmp_path / "content" / "terminology" / "glossary.yaml").write_text(
        "schema_version: '1'\nconcepts: not-a-list\n", encoding="utf-8"
    )

    report = check_workspace(tmp_path)

    assert report.status == "invalid"
    by_id = {result.check_id: result for result in report.checks}
    assert by_id["content_layout"].status == "passed"
    assert by_id["character_registry"].status == "failed"
    character_issue = by_id["character_registry"].issues[0]
    assert character_issue.code == "authoring.character_registry_invalid"
    assert character_issue.paths == ("content/authoring/characters.yaml",)
    assert character_issue.remedy.action == "edit_authored_data"
    assert by_id["terminology_registry"].status == "failed"
    assert by_id["terminology_registry"].issues[0].code == "authoring.terminology_registry_invalid"
    assert by_id["curriculum_plan"].status == "passed"
    assert by_id["distribution"].status == "skipped"
    assert by_id["distribution"].blocked_by == ("character_registry",)
    assert report.counts == WorkspaceCheckCounts(passed=2, failed=2, skipped=1, error=0)


def test_check_workspace_given_glossary_corrupted_after_prior_check_expect_fresh_terminology_failure(
    tmp_path: Path,
) -> None:
    assemble_valid_workspace(tmp_path)
    first_report = check_workspace(tmp_path)
    (tmp_path / "content" / "terminology" / "glossary.yaml").write_text(
        "schema_version: '1'\nconcepts: not-a-list\n",
        encoding="utf-8",
    )

    second_report = check_workspace(tmp_path)

    assert first_report.status == "valid"
    by_id = {result.check_id: result for result in second_report.checks}
    assert second_report.status == "invalid"
    assert by_id["terminology_registry"].status == "failed"
    assert by_id["terminology_registry"].issues[0].code == "authoring.terminology_registry_invalid"


def test_check_workspace_given_edited_catalog_expect_stale_plan_failure_and_distribution_skip(tmp_path: Path) -> None:
    assemble_valid_workspace(tmp_path)
    catalog_path = tmp_path / "content" / "catalog" / "approved" / "catalog.yaml"
    payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8"))
    payload["entries"][0]["title"] = "Edited after planning"
    catalog_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    report = check_workspace(tmp_path)

    assert report.status == "invalid"
    by_id = {result.check_id: result for result in report.checks}
    assert by_id["character_registry"].status == "passed"
    assert by_id["terminology_registry"].status == "passed"
    assert by_id["curriculum_plan"].status == "failed"
    plan_issue = by_id["curriculum_plan"].issues[0]
    assert plan_issue.code == "curriculum.plan_invalid"
    assert plan_issue.paths == ("content/curriculum/plan.yaml", "content/catalog/approved/catalog.yaml")
    assert plan_issue.remedy.action == "request_human_decision"
    assert plan_issue.remedy.command is None
    assert plan_issue.remedy.requires_human_decision is True
    assert by_id["distribution"].status == "skipped"
    assert by_id["distribution"].blocked_by == ("curriculum_plan",)


def test_check_workspace_given_edited_lesson_expect_stale_distribution_failure_with_regeneration_remedy(
    tmp_path: Path,
) -> None:
    assemble_valid_workspace(tmp_path)
    lesson_path = tmp_path / "content" / "lessons" / K_LESSON_ID / "lesson.md"
    lesson_path.write_text(
        lesson_path.read_text(encoding="utf-8").replace("What you will learn in this lesson", "Fresh overview"),
        encoding="utf-8",
    )

    report = check_workspace(tmp_path)

    assert report.status == "invalid"
    by_id = {result.check_id: result for result in report.checks}
    assert by_id["curriculum_plan"].status == "passed"
    assert by_id["distribution"].status == "failed"
    distribution_issue = by_id["distribution"].issues[0]
    assert distribution_issue.code == "distribution.artifacts_invalid"
    assert distribution_issue.paths == ("dist",)
    assert distribution_issue.remedy.action == "run_command"
    assert distribution_issue.remedy.command == (
        "lesson-data",
        "regenerate-dist",
        "--repo-root",
        str(tmp_path.resolve()),
    )
    assert report.counts == WorkspaceCheckCounts(passed=4, failed=1, skipped=0, error=0)


def test_check_workspace_given_legacy_root_directory_expect_layout_failure_without_suppressing_checks(
    tmp_path: Path,
) -> None:
    assemble_valid_workspace(tmp_path)
    (tmp_path / "lessons").mkdir()

    report = check_workspace(tmp_path)

    assert report.status == "invalid"
    layout = report.checks[0]
    assert layout.status == "failed"
    legacy_issue = layout.issues[0]
    assert legacy_issue.code == "workspace.layout_invalid"
    assert legacy_issue.paths == ("lessons",)
    assert legacy_issue.remedy.action == "restore_canonical_path"
    assert legacy_issue.remedy.requires_human_decision is True
    assert all(result.status == "passed" for result in report.checks[1:])


def test_check_workspace_given_missing_character_registry_expect_failed_check_not_skipped(tmp_path: Path) -> None:
    assemble_valid_workspace(tmp_path)
    (tmp_path / "content" / "authoring" / "characters.yaml").unlink()

    report = check_workspace(tmp_path)

    by_id = {result.check_id: result for result in report.checks}
    assert by_id["content_layout"].status == "failed"
    assert by_id["character_registry"].status == "failed"
    assert by_id["character_registry"].issues[0].code == "authoring.character_registry_invalid"
    assert by_id["distribution"].status == "skipped"
    assert "character_registry" in by_id["distribution"].blocked_by


def test_check_workspace_given_missing_content_root_expect_dependent_checks_skipped(tmp_path: Path) -> None:
    report = check_workspace(tmp_path)

    assert report.status == "invalid"
    by_id = {result.check_id: result for result in report.checks}
    layout = by_id["content_layout"]
    assert layout.status == "failed"
    missing_issue = layout.issues[0]
    assert missing_issue.code == "workspace.layout_invalid"
    assert missing_issue.paths == (
        "content/authoring/characters.yaml",
        "content/catalog/approved/catalog.yaml",
        "content/catalog/registry/catalog_families.yaml",
        "content/curriculum/plan.yaml",
        "content/lessons",
    )
    assert missing_issue.remedy.action == "restore_canonical_path"
    assert by_id["character_registry"].status == "skipped"
    assert by_id["character_registry"].blocked_by == ("content_layout",)
    assert by_id["terminology_registry"].status == "skipped"
    assert by_id["curriculum_plan"].status == "skipped"
    assert by_id["distribution"].status == "skipped"
    assert by_id["distribution"].blocked_by == ("content_layout", "character_registry", "curriculum_plan")
    assert report.counts == WorkspaceCheckCounts(passed=0, failed=1, skipped=4, error=0)


def test_check_workspace_given_missing_lessons_dir_expect_only_distribution_skipped(tmp_path: Path) -> None:
    assemble_valid_workspace(tmp_path)
    shutil.rmtree(tmp_path / "content" / "lessons")

    report = check_workspace(tmp_path)

    by_id = {result.check_id: result for result in report.checks}
    assert by_id["content_layout"].status == "failed"
    assert by_id["content_layout"].issues[0].paths == ("content/lessons",)
    assert by_id["character_registry"].status == "passed"
    assert by_id["terminology_registry"].status == "passed"
    assert by_id["curriculum_plan"].status == "passed"
    assert by_id["distribution"].status == "skipped"
    assert by_id["distribution"].blocked_by == ("content_layout",)


def test_check_workspace_given_unexpected_runtime_error_expect_incomplete_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assemble_valid_workspace(tmp_path)

    def _raise_runtime_error(repo_root: Path) -> object:
        raise RuntimeError("unexpected harness failure")

    monkeypatch.setattr(
        "lesson_builder.application.operations.check_lesson_data.load_character_registry",
        _raise_runtime_error,
    )

    report = check_workspace(tmp_path)

    assert report.status == "incomplete"
    by_id = {result.check_id: result for result in report.checks}
    assert by_id["content_layout"].status == "passed"
    assert by_id["character_registry"].status == "error"
    error_issue = by_id["character_registry"].issues[0]
    assert error_issue.code == "workspace.check_error"
    assert error_issue.error_type == "RuntimeError"
    assert error_issue.remedy.action == "inspect_environment"
    assert by_id["terminology_registry"].status == "passed"
    assert by_id["curriculum_plan"].status == "passed"
    assert by_id["distribution"].status == "skipped"
    assert "character_registry" in by_id["distribution"].blocked_by
    assert report.counts == WorkspaceCheckCounts(passed=3, failed=0, skipped=1, error=1)
