"""Entry point: `test_ensure_workbench_app_given_missing_modules_and_stale_build_expect_install_then_build` verifies frontend build preparation."""

from __future__ import annotations

from pathlib import Path

import pytest

from lesson_builder.application.operations import workbench_build
from lesson_builder.application.operations.workbench_build import ensure_workbench_app
from tests.application.fakes import FakeWorkbenchBuildRunner

K_WORKBENCH_BUILD_COMMANDS = (("npm", "ci"), ("npm", "run", "build"))


@pytest.fixture
def workbench_root(tmp_path: Path) -> Path:
    """Create the frontend source inputs that define build freshness."""
    root = tmp_path / "workbench"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.tsx").write_text("export const app = true;", encoding="utf-8")
    (root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (root / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (root / "package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
    (root / "vite.config.ts").write_text("export default {};", encoding="utf-8")
    return root.parent


@pytest.fixture
def app_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Direct generated files to a temporary package directory."""
    output = tmp_path / "package" / "web" / "app"
    monkeypatch.setattr(workbench_build, "_built_app_root", lambda: output)
    monkeypatch.setattr(workbench_build.shutil, "which", lambda _tool: "/usr/bin/tool")
    return output


def test_ensure_workbench_app_given_missing_modules_and_stale_build_expect_install_then_build(
    workbench_root: Path, app_root: Path
) -> None:
    runner = FakeWorkbenchBuildRunner(app_root)

    ensure_workbench_app(workbench_root, run_command=runner)

    assert [command for command, _cwd in runner.calls] == list(K_WORKBENCH_BUILD_COMMANDS)
    assert all(cwd == workbench_root / "workbench" for _command, cwd in runner.calls)
    assert (app_root / "index.html").is_file()
    assert (app_root / "workbench-build-source.sha256").is_file()


def test_ensure_workbench_app_given_current_build_expect_no_subprocess_calls(
    workbench_root: Path, app_root: Path
) -> None:
    first_runner = FakeWorkbenchBuildRunner(app_root)
    ensure_workbench_app(workbench_root, run_command=first_runner)
    second_runner = FakeWorkbenchBuildRunner(app_root)

    ensure_workbench_app(workbench_root, run_command=second_runner)

    assert [command for command, _cwd in first_runner.calls] == list(K_WORKBENCH_BUILD_COMMANDS)
    assert second_runner.calls == []


def test_ensure_workbench_app_given_existing_modules_and_changed_lockfile_expect_reinstall_before_build(
    workbench_root: Path, app_root: Path
) -> None:
    initial_runner = FakeWorkbenchBuildRunner(app_root)
    ensure_workbench_app(workbench_root, run_command=initial_runner)
    assert (workbench_root / "workbench" / "node_modules").is_dir()
    lockfile = workbench_root / "workbench" / "package-lock.json"
    lockfile.write_text(lockfile.read_text(encoding="utf-8") + " changed", encoding="utf-8")
    runner = FakeWorkbenchBuildRunner(app_root)

    ensure_workbench_app(workbench_root, run_command=runner)

    assert [command for command, _cwd in runner.calls] == list(K_WORKBENCH_BUILD_COMMANDS)


@pytest.mark.parametrize("changed_file", ["src/main.tsx", "index.html", "package-lock.json", "vite.config.ts"])
def test_ensure_workbench_app_given_build_input_change_expect_rebuild(
    workbench_root: Path, app_root: Path, changed_file: str
) -> None:
    initial_runner = FakeWorkbenchBuildRunner(app_root)
    ensure_workbench_app(workbench_root, run_command=initial_runner)
    changed_path = workbench_root / "workbench" / changed_file
    changed_path.write_text(changed_path.read_text(encoding="utf-8") + " changed", encoding="utf-8")
    runner = FakeWorkbenchBuildRunner(app_root)

    ensure_workbench_app(workbench_root, run_command=runner)

    expected = list(K_WORKBENCH_BUILD_COMMANDS) if changed_file == "package-lock.json" else [("npm", "run", "build")]
    assert [command for command, _cwd in runner.calls] == expected


def test_ensure_workbench_app_given_missing_node_toolchain_expect_clear_error_without_running(
    workbench_root: Path, app_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(workbench_build.shutil, "which", lambda _tool: None)
    runner = FakeWorkbenchBuildRunner(app_root)

    with pytest.raises(RuntimeError, match="Node.js and npm are unavailable and no current app build exists"):
        ensure_workbench_app(workbench_root, run_command=runner)

    assert runner.calls == []


def test_ensure_workbench_app_given_failed_build_expect_no_current_build_marker(
    workbench_root: Path, app_root: Path
) -> None:
    runner = FakeWorkbenchBuildRunner(app_root, fail_on=("npm", "run", "build"))

    with pytest.raises(RuntimeError, match="workbench app build"):
        ensure_workbench_app(workbench_root, run_command=runner)

    assert [command for command, _cwd in runner.calls] == list(K_WORKBENCH_BUILD_COMMANDS)
    assert not (app_root / "workbench-build-source.sha256").exists()


def test_ensure_workbench_app_given_packaged_build_without_source_expect_reuse(tmp_path: Path, app_root: Path) -> None:
    app_root.mkdir(parents=True)
    (app_root / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    runner = FakeWorkbenchBuildRunner(app_root)

    ensure_workbench_app(tmp_path, run_command=runner)

    assert runner.calls == []
