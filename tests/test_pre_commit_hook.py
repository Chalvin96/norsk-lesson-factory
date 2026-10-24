"""Regression tests for the repository pre-commit hook."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def test_pre_commit_given_staged_invalid_knowledge_and_valid_worktree_expect_failure(tmp_path: Path) -> None:
    """Validate the index snapshot even when the worktree hides its invalid blob."""
    hook = tmp_path / ".githooks" / "pre-commit"
    hook.parent.mkdir()
    shutil.copy(Path(__file__).parents[1] / ".githooks" / "pre-commit", hook)
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy(Path(__file__).parents[1] / "scripts" / "okf_validate.py", scripts / "okf_validate.py")

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.name", "Test Author")
    _git(tmp_path, "config", "user.email", "author@example.test")
    _git(tmp_path, "config", "core.hooksPath", ".githooks")
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "index.md").write_text("# Knowledge\n", encoding="utf-8")
    concept = tmp_path / "knowledge" / "concept.md"
    concept.write_text("not valid frontmatter\n", encoding="utf-8")
    _git(tmp_path, "add", "knowledge")
    concept.write_text(
        "---\n"
        "type: Concept\n"
        "title: Valid\n"
        "description: Valid\n"
        "tags: [test]\n"
        "timestamp: 2026-10-15\n"
        "---\n\n# Valid in worktree\n",
        encoding="utf-8",
    )

    fake_uv = tmp_path / "fake-bin" / "uv"
    fake_uv.parent.mkdir()
    fake_uv.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = run ] && [ "$2" = pre-commit ]; then exit 0; fi\n'
        "shift 2\n"
        f'exec {shutil.which("python") or os.fspath(Path("/usr/bin/python3"))} "$@"\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    result = subprocess.run(
        [os.fspath(hook)],
        cwd=tmp_path,
        env={
            "PATH": os.pathsep.join((os.fspath(fake_uv.parent), "/usr/bin", "/bin")),
            "HOME": os.fspath(tmp_path / "home"),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "requires parseable YAML frontmatter" in result.stdout


def test_pre_commit_given_large_staged_change_with_knowledge_expect_validation_runs(tmp_path: Path) -> None:
    """A long staged name list must not hide knowledge changes through SIGPIPE."""
    hook = tmp_path / ".githooks" / "pre-commit"
    hook.parent.mkdir()
    shutil.copy(Path(__file__).parents[1] / ".githooks" / "pre-commit", hook)
    _git(tmp_path, "init", "-b", "main")
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge" / "index.md").write_text("# Knowledge\n", encoding="utf-8")
    other_files = tmp_path / "z-other-files"
    other_files.mkdir()
    for number in range(2000):
        (other_files / f"{number:04d}-{'x' * 180}").touch()
    _git(tmp_path, "add", ".")
    fake_uv = tmp_path / "fake-bin" / "uv"
    fake_uv.parent.mkdir()
    fake_uv.write_text(
        '#!/bin/sh\nif [ "$1" = run ] && [ "$2" = pre-commit ]; then exit 0; fi\necho knowledge-validation-ran\n',
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)

    result = subprocess.run(
        [os.fspath(hook)],
        cwd=tmp_path,
        env={**os.environ, "PATH": os.pathsep.join((os.fspath(fake_uv.parent), "/usr/bin", "/bin"))},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "knowledge-validation-ran" in result.stdout


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", os.fspath(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
