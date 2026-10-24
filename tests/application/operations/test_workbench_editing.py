"""Entry point: draft write, provenance, containment and check behavior tests."""

import shutil
from pathlib import Path

import pytest
import yaml

from lesson_builder.application.operations.compile_lesson_package import digest_authored_source
from lesson_builder.application.operations.compile_lesson_package import load_authored_source_snapshot
from lesson_builder.application.operations.workbench_editing import WorkbenchSourceNotFound
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_editing import write_workbench_block
from lesson_builder.application.operations.workbench_editing import write_workbench_file


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    canonical = Path(__file__).parents[3] / "content" / "lessons" / "greet_and_introduce_yourself"
    shutil.copytree(canonical, tmp_path / "content" / "lessons" / canonical.name)
    return tmp_path


def test_draft_write_given_canonical_source_expect_all_files_copied_and_provenance_retained(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    canonical = workspace / "content" / "lessons" / lesson_id
    (canonical / "extra.txt").write_text("extra", encoding="utf-8")
    digest = digest_authored_source(load_authored_source_snapshot(canonical))
    before = (canonical / "lesson.md").read_bytes()
    view = write_workbench_file(
        workspace, lesson_id, "lesson.md", before.decode().replace("Hi!", "Hello!") + " \r\n\r\n"
    )
    draft = workspace / "drafts" / lesson_id
    metadata = (draft / "draft.yaml").read_bytes()
    assert yaml.safe_load(metadata) == {
        "lesson_id": lesson_id,
        "started_from_sha256": digest,
        "created_at": yaml.safe_load(metadata)["created_at"],
    }
    assert (draft / "extra.txt").read_text() == "extra"
    assert (draft / "plan.md").read_bytes() == (canonical / "plan.md").read_bytes()
    assert (canonical / "lesson.md").read_bytes() == before
    assert not (draft / "lesson.md").read_bytes().endswith(b"\n\n")
    assert "\r" not in view.lesson_source
    write_workbench_file(workspace, lesson_id, "plan.md", "invalid  \r\n")
    assert (draft / "draft.yaml").read_bytes() == metadata
    assert (draft / "plan.md").read_bytes() == b"invalid\n"


def test_block_write_given_translation_edit_expect_only_attribute_changed_and_real_checks(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    source = read_workbench_source(workspace, lesson_id)
    reading = next(block for block in source.blocks if block.kind == "reading")
    replacement = "Hello **Mina**."
    view = write_workbench_block(workspace, lesson_id, reading.key, fields={"translation": replacement})
    assert view.lesson_source == source.files["lesson.md"].replace(reading.fields["translation"], replacement, 1)
    assert view.packet is not None
    assert read_workbench_source(workspace, lesson_id).exists is not False


def test_whole_file_write_given_malformed_source_expect_written_source_and_compile_errors(workspace: Path) -> None:
    view = write_workbench_file(workspace, "greet_and_introduce_yourself", "lesson.md", "::: {.reading}\nUnclosed")
    assert view.errors
    assert view.lesson_source == "::: {.reading}\nUnclosed\n"


@pytest.mark.parametrize("name", ["draft.yaml", "../../secret", "audio.wav", "../lesson.md"])
def test_file_write_given_disallowed_filename_expect_no_draft_created(workspace: Path, name: str) -> None:
    with pytest.raises(WorkbenchSourceNotFound):
        write_workbench_file(workspace, "greet_and_introduce_yourself", name, "secret")
    assert not (workspace / "drafts").exists()


def test_block_write_given_unknown_key_expect_no_draft_created(workspace: Path) -> None:
    with pytest.raises(WorkbenchSourceNotFound):
        write_workbench_block(workspace, "greet_and_introduce_yourself", "unknown#0", source="text")
    assert not (workspace / "drafts").exists()


def test_file_write_given_symlink_to_other_draft_expect_no_other_lesson_changed(workspace: Path) -> None:
    lesson_id = "greet_and_introduce_yourself"
    draft = workspace / "drafts" / lesson_id
    shutil.copytree(workspace / "content" / "lessons" / lesson_id, draft)
    other = workspace / "drafts" / "other.md"
    other.write_text("untouched", encoding="utf-8")
    (draft / "lesson.md").unlink()
    (draft / "lesson.md").symlink_to(other)
    with pytest.raises(ValueError, match="escapes"):
        write_workbench_file(workspace, lesson_id, "lesson.md", "overwrite")
    assert other.read_text() == "untouched"
