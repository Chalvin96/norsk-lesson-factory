"""Entry points: local workbench settings persistence and workspace containment tests."""

from pathlib import Path

import pytest

from lesson_builder.workspace.workbench_settings_store import read_workbench_settings_text
from lesson_builder.workspace.workbench_settings_store import reset_workbench_settings
from lesson_builder.workspace.workbench_settings_store import write_workbench_settings_text


def test_workbench_settings_given_write_and_reset_expect_atomic_local_override_lifecycle(tmp_path: Path) -> None:
    write_workbench_settings_text(tmp_path, "roles: {}\n")

    assert read_workbench_settings_text(tmp_path) == "roles: {}\n"
    assert (tmp_path / ".workbench" / "settings.yaml").is_file()

    reset_workbench_settings(tmp_path)

    assert read_workbench_settings_text(tmp_path) is None


def test_workbench_settings_given_symlink_directory_expect_write_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".workbench").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        write_workbench_settings_text(tmp_path, "roles: {}\n")

    assert not (outside / "settings.yaml").exists()
