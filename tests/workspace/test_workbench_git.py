"""Entry point: isolated local git Save behavior tests."""

import subprocess
from pathlib import Path

import pytest

from lesson_builder.workspace.workbench_git import WorkbenchGitError
from lesson_builder.workspace.workbench_git import commit_draft
from lesson_builder.workspace.workbench_git import read_draft_git_state


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "authoring")
    _git(tmp_path, "config", "user.name", "Test Author")
    _git(tmp_path, "config", "user.email", "author@example.test")
    (tmp_path / "unrelated.md").write_text("original\n", encoding="utf-8")
    _git(tmp_path, "add", "unrelated.md")
    _git(tmp_path, "commit", "--no-verify", "-m", "initial")
    draft = tmp_path / "drafts" / "demo"
    draft.mkdir(parents=True)
    (draft / "lesson.md").write_text("draft\n", encoding="utf-8")
    return tmp_path


def test_save_given_dirty_draft_expect_one_commit_with_only_explicit_paths_and_clean_status(repository: Path) -> None:
    paths = ("drafts/demo/lesson.md",)
    (repository / "unrelated.md").write_text("keep working\n", encoding="utf-8")
    hooks = repository / ".git" / "hooks"
    for name in ("pre-commit", "commit-msg"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
    assert read_draft_git_state(repository, paths).dirty
    commit = commit_draft(repository, "demo", paths)
    assert _git(repository, "log", "-1", "--format=%s").strip() == "workbench: save demo"
    assert _git(repository, "show", "--format=", "--name-only", commit).strip() == paths[0]
    assert _git(repository, "rev-list", "--count", "HEAD").strip() == "2"
    assert read_draft_git_state(repository, paths).last_commit == commit
    assert not read_draft_git_state(repository, paths).dirty
    assert _git(repository, "diff", "--name-only").strip() == "unrelated.md"


@pytest.mark.parametrize("branch", ["main", "master"])
def test_save_given_protected_branch_expect_no_staging_or_commit(repository: Path, branch: str) -> None:
    _git(repository, "branch", "-m", branch)
    with pytest.raises(WorkbenchGitError, match="protected"):
        commit_draft(repository, "demo", ("drafts/demo/lesson.md",))
    assert _git(repository, "diff", "--cached", "--name-only") == ""
    assert _git(repository, "rev-list", "--count", "HEAD").strip() == "1"


def test_save_given_unrelated_staged_path_expect_refusal_and_staged_content_retained(repository: Path) -> None:
    (repository / "unrelated.md").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "unrelated.md")
    with pytest.raises(WorkbenchGitError, match="unrelated"):
        commit_draft(repository, "demo", ("drafts/demo/lesson.md",))
    assert _git(repository, "diff", "--cached", "--name-only").strip() == "unrelated.md"
    assert _git(repository, "rev-list", "--count", "HEAD").strip() == "1"


def test_save_given_unchanged_committed_draft_expect_no_empty_commit(repository: Path) -> None:
    paths = ("drafts/demo/lesson.md",)
    commit_draft(repository, "demo", paths)
    with pytest.raises(WorkbenchGitError, match="no draft changes"):
        commit_draft(repository, "demo", paths)
    assert _git(repository, "rev-list", "--count", "HEAD").strip() == "2"


def test_save_given_head_moves_during_staging_expect_refusal_without_commit(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = subprocess.run

    def move_head(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        result = run(arguments, **kwargs)
        if "add" in arguments:
            run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "commit",
                    "--allow-empty",
                    "--only",
                    "--no-verify",
                    "-m",
                    "external change",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        return result

    monkeypatch.setattr(subprocess, "run", move_head)
    with pytest.raises(WorkbenchGitError, match="HEAD moved"):
        commit_draft(repository, "demo", ("drafts/demo/lesson.md",))
    assert _git(repository, "log", "-1", "--format=%s").strip() == "external change"


def test_save_given_commit_failure_after_staging_expect_original_index_retained(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = repository / "drafts" / "demo" / "lesson.md"
    _git(repository, "add", "--", "drafts/demo/lesson.md")
    original = _git(repository, "ls-files", "--stage")
    path.write_text("changed after staging\n")
    run = subprocess.run

    def fail_commit(arguments: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "commit" in arguments:
            raise subprocess.CalledProcessError(1, arguments, stderr="commit failed")
        return run(arguments, **kwargs)

    monkeypatch.setattr(subprocess, "run", fail_commit)
    with pytest.raises(WorkbenchGitError, match="commit failed"):
        commit_draft(repository, "demo", ("drafts/demo/lesson.md",))
    assert _git(repository, "ls-files", "--stage") == original
    assert path.read_text() == "changed after staging\n"


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True).stdout


def test_save_given_detached_head_expect_branch_precondition_and_no_commit(repository: Path) -> None:
    _git(repository, "checkout", "--detach")
    with pytest.raises(
        WorkbenchGitError, match="Saving drafts needs a checked-out branch; this workspace is on a detached HEAD."
    ):
        commit_draft(repository, "demo", ("drafts/demo/lesson.md",))
    assert _git(repository, "diff", "--cached", "--name-only") == ""
    assert _git(repository, "rev-list", "--count", "HEAD").strip() == "1"
