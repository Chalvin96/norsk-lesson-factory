"""Entry point: immutable approval and source-only promotion behavior."""

from pathlib import Path

import pytest
import yaml

from lesson_builder.workflow.lesson_generation.approval import LessonApprovalError
from lesson_builder.workflow.lesson_generation.approval import create_lesson_approval
from lesson_builder.workflow.lesson_generation.promotion import LessonPromotionError
from lesson_builder.workflow.lesson_generation.promotion import promote_lesson_approval
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT


def test_create_lesson_approval_given_same_source_twice_expect_one_reusable_snapshot(tmp_path: Path) -> None:
    source = _source(tmp_path)
    first = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    second = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    assert first == second
    assert sorted(path.name for path in (first / "source").iterdir()) == ["exercises.yaml", "lesson.md", "plan.md"]


def test_promote_lesson_approval_given_scratch_deleted_expect_canonical_source(tmp_path: Path) -> None:
    source = _source(tmp_path)
    approval = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    for path in source.iterdir():
        path.unlink()
    source.rmdir()
    promote_lesson_approval(repo_root=tmp_path, approval_path=approval)
    assert (tmp_path / "content" / "lessons" / "question_word_order" / "lesson.md").is_file()


def test_promote_lesson_approval_given_interrupted_previous_residue_expect_cleanup(tmp_path: Path) -> None:
    source = _source(tmp_path)
    approval = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    promote_lesson_approval(repo_root=tmp_path, approval_path=approval)
    destination = tmp_path / "content" / "lessons" / "question_word_order"
    residue = destination.parent / f".{destination.name}.previous-{approval.name}"
    residue.mkdir()
    promote_lesson_approval(repo_root=tmp_path, approval_path=approval)
    assert not residue.exists()


def test_create_lesson_approval_given_corrupt_existing_snapshot_expect_conflict(tmp_path: Path) -> None:
    source = _source(tmp_path)
    approval = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    (approval / "source" / "lesson.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(LessonApprovalError, match="different source"):
        create_lesson_approval(
            repo_root=tmp_path,
            source_dir=source,
            lesson_id="question_word_order",
            curriculum_slot_sha256="sha256:" + "1" * 64,
            evidence_sha256="sha256:" + "2" * 64,
        )


def test_create_lesson_approval_given_incomplete_canonical_expect_refusal(tmp_path: Path) -> None:
    source = _source(tmp_path)
    canonical = tmp_path / "content" / "lessons" / "question_word_order"
    canonical.mkdir(parents=True)
    (canonical / "lesson.md").write_bytes(b"partial")
    with pytest.raises(LessonApprovalError, match="incomplete"):
        create_lesson_approval(
            repo_root=tmp_path,
            source_dir=source,
            lesson_id="question_word_order",
            curriculum_slot_sha256="sha256:" + "1" * 64,
            evidence_sha256="sha256:" + "2" * 64,
        )


def test_promote_lesson_approval_given_predecessor_residue_and_concurrent_version_expect_refusal(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path)
    approval_a = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    promote_lesson_approval(repo_root=tmp_path, approval_path=approval_a)
    (source / "lesson.md").write_text("concurrent", encoding="utf-8")
    approval_b = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "3" * 64,
        evidence_sha256="sha256:" + "4" * 64,
    )
    destination = tmp_path / "content" / "lessons" / "question_word_order"
    previous = destination.parent / f".{destination.name}.previous-{approval_b.name}"
    destination.rename(previous)
    destination.mkdir()
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        (destination / name).write_bytes((source / name).read_bytes())
    (destination / "lesson.md").write_text("version-c", encoding="utf-8")
    current_bytes = {name: (destination / name).read_bytes() for name in ("plan.md", "lesson.md", "exercises.yaml")}
    with pytest.raises(LessonPromotionError, match="changed since approval"):
        promote_lesson_approval(repo_root=tmp_path, approval_path=approval_b)
    assert {name: (destination / name).read_bytes() for name in current_bytes} == current_bytes
    assert previous.is_dir()


def test_promote_lesson_approval_given_path_outside_approval_root_expect_refusal(tmp_path: Path) -> None:
    with pytest.raises(LessonPromotionError, match="outside canonical approval root"):
        promote_lesson_approval(repo_root=tmp_path, approval_path=tmp_path / "untrusted")


def test_promote_lesson_approval_given_unsafe_metadata_lesson_id_expect_refusal(tmp_path: Path) -> None:
    source = _source(tmp_path)
    approval = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    record_path = approval / "approval.yaml"
    record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    record["lesson_id"] = "../outside"
    record_path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")

    with pytest.raises(LessonPromotionError, match="invalid immutable lesson approval"):
        promote_lesson_approval(repo_root=tmp_path, approval_path=approval)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewer", "another-reviewer"),
        ("replaces_source_sha256", "sha256:" + "9" * 64),
        ("curriculum_slot_sha256", "sha256:" + "8" * 64),
        ("evidence_sha256", "sha256:" + "7" * 64),
        ("approved_at", "2026-08-23T00:00:00"),
    ],
)
def test_create_lesson_approval_given_changed_record_field_expect_reuse_refused(
    tmp_path: Path, field: str, value: str
) -> None:
    source = _source(tmp_path)
    approval = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    record_path = approval / "approval.yaml"
    record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    record[field] = value
    record_path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    with pytest.raises(LessonApprovalError):
        create_lesson_approval(
            repo_root=tmp_path,
            source_dir=source,
            lesson_id="question_word_order",
            curriculum_slot_sha256="sha256:" + "1" * 64,
            evidence_sha256="sha256:" + "2" * 64,
        )


def test_create_lesson_approval_given_extra_snapshot_file_expect_reuse_refused(tmp_path: Path) -> None:
    source = _source(tmp_path)
    approval = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    (approval / "source" / "unexpected.txt").write_text("unexpected", encoding="utf-8")
    with pytest.raises(LessonApprovalError):
        create_lesson_approval(
            repo_root=tmp_path,
            source_dir=source,
            lesson_id="question_word_order",
            curriculum_slot_sha256="sha256:" + "1" * 64,
            evidence_sha256="sha256:" + "2" * 64,
        )


def test_promote_lesson_approval_given_tampered_record_expect_consumption_refused(tmp_path: Path) -> None:
    source = _source(tmp_path)
    approval = create_lesson_approval(
        repo_root=tmp_path,
        source_dir=source,
        lesson_id="question_word_order",
        curriculum_slot_sha256="sha256:" + "1" * 64,
        evidence_sha256="sha256:" + "2" * 64,
    )
    record_path = approval / "approval.yaml"
    record = yaml.safe_load(record_path.read_text(encoding="utf-8"))
    record["approved_at"] = "2026-08-23T00:00:00"
    record_path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
    with pytest.raises(LessonPromotionError, match="invalid immutable lesson approval"):
        promote_lesson_approval(repo_root=tmp_path, approval_path=approval)


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "scratch" / "source"
    source.mkdir(parents=True)
    for name in ("plan.md", "lesson.md", "exercises.yaml"):
        (source / name).write_bytes((K_CATALOG_PACKAGE_FIXTURE_ROOT / name).read_bytes())
    return source
