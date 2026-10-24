"""Entry point: `ensure_workbench_app` builds the local React app when its source changed."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_COMMAND
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_INSTALL_COMMAND
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_INSTALLED_LOCKFILE_DIGEST_FILENAME
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_METADATA_FILENAME
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_NODE_BINARY
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_NODE_MODULES_DIR
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_NPM_BINARY
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_PACKAGE_LOCKFILE
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_SOURCE_DIR
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_BUILD_SOURCE_FILES


def ensure_workbench_app(repo_root: Path, *, run_command: _BuildCommandRunner | None = None) -> None:
    """Build the packaged workbench app when its source digest is missing or stale."""
    source_root = Path(repo_root).resolve() / K_APPLICATION_WORKBENCH_BUILD_SOURCE_DIR
    app_root = _built_app_root()
    app_index = app_root / "index.html"
    if not source_root.is_dir():
        _require_packaged_build(app_index)
        return

    source_digest = _source_digest_or_raise(source_root)
    if _is_current_build(app_root, app_index, source_digest):
        return
    _require_build_toolchain()
    _build_workbench_app(source_root, app_index, run_command or _run_build_command)
    (app_root / K_APPLICATION_WORKBENCH_BUILD_METADATA_FILENAME).write_text(source_digest + "\n", encoding="utf-8")


def _require_packaged_build(app_index: Path) -> None:
    """Raise when an installed package has neither frontend source nor built HTML."""
    if not app_index.is_file():
        raise RuntimeError(
            "The workbench source and built app are unavailable. Run this command from the project checkout."
        )


def _source_digest_or_raise(source_root: Path) -> str:
    """Return the frontend digest or an actionable error when a required input is absent."""
    try:
        return _workbench_source_digest(source_root)
    except OSError as exc:
        raise RuntimeError(f"Cannot inspect workbench build inputs: {exc}") from exc


def _require_build_toolchain() -> None:
    """Raise a clear error when a stale or missing app cannot be rebuilt locally."""
    missing_tools = [
        tool
        for tool in (K_APPLICATION_WORKBENCH_BUILD_NODE_BINARY, K_APPLICATION_WORKBENCH_BUILD_NPM_BINARY)
        if shutil.which(tool) is None
    ]
    if not missing_tools:
        return
    tool_names = ["Node.js" if tool == K_APPLICATION_WORKBENCH_BUILD_NODE_BINARY else "npm" for tool in missing_tools]
    tools = " and ".join(tool_names)
    verb = "are" if tool_names[1:] else "is"
    raise RuntimeError(
        f"Cannot build the workbench because {tools} {verb} unavailable and no current app build exists. "
        "Install Node.js and npm, then run `uv run lesson-data workbench` again."
    )


def _build_workbench_app(source_root: Path, app_index: Path, command_runner: _BuildCommandRunner) -> None:
    """Install locked frontend dependencies when absent or stale, then run the Vite build."""
    try:
        node_modules = source_root / K_APPLICATION_WORKBENCH_BUILD_NODE_MODULES_DIR
        lockfile_digest = _file_digest(source_root / K_APPLICATION_WORKBENCH_BUILD_PACKAGE_LOCKFILE)
        if not node_modules.is_dir() or not _has_installed_lockfile_digest(node_modules, lockfile_digest):
            command_runner(K_APPLICATION_WORKBENCH_BUILD_INSTALL_COMMAND, cwd=source_root)
            (node_modules / K_APPLICATION_WORKBENCH_BUILD_INSTALLED_LOCKFILE_DIGEST_FILENAME).write_text(
                lockfile_digest + "\n", encoding="utf-8"
            )
        command_runner(K_APPLICATION_WORKBENCH_BUILD_COMMAND, cwd=source_root)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise _workbench_build_error(exc) from exc
    if not app_index.is_file():
        raise RuntimeError("The workbench build finished without creating web/app/index.html.")


def _workbench_build_error(error: OSError | subprocess.CalledProcessError) -> RuntimeError:
    """Return an actionable message for a failed npm install or Vite build."""
    if isinstance(error, subprocess.CalledProcessError):
        command = error.cmd if isinstance(error.cmd, str) else " ".join(error.cmd)
        details = f" `{command}` failed with exit code {error.returncode}"
    else:
        details = " failed to start"
    return RuntimeError(
        f"The workbench app build{details}. Check the Node.js and npm output, then retry "
        "`uv run lesson-data workbench`."
    )


def _built_app_root() -> Path:
    """Return the package-local Vite output directory."""
    package_root = Path(__file__).resolve().parents[2]
    return package_root / "web" / "app"


def _workbench_source_digest(source_root: Path) -> str:
    """Hash sorted source paths and bytes so additions, removals and edits invalidate the build."""
    digest = hashlib.sha256()
    paths = [path for path in (source_root / "src").rglob("*") if path.is_file()]
    paths.extend(source_root / filename for filename in K_APPLICATION_WORKBENCH_BUILD_SOURCE_FILES)
    for path in sorted(paths, key=lambda item: item.relative_to(source_root).as_posix()):
        relative_path = path.relative_to(source_root).as_posix().encode("utf-8")
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _file_digest(path: Path) -> str:
    """Return the SHA-256 digest of one dependency lockfile."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _has_installed_lockfile_digest(node_modules: Path, expected_digest: str) -> bool:
    """Return whether the installed dependency tree records this exact lockfile."""
    try:
        stored_digest = (node_modules / K_APPLICATION_WORKBENCH_BUILD_INSTALLED_LOCKFILE_DIGEST_FILENAME).read_text(
            encoding="utf-8"
        )
    except OSError:
        return False
    return stored_digest.strip() == expected_digest


def _is_current_build(app_root: Path, app_index: Path, source_digest: str) -> bool:
    """Return whether the generated shell records the digest of the current frontend inputs."""
    try:
        stored_digest = (app_root / K_APPLICATION_WORKBENCH_BUILD_METADATA_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return app_index.is_file() and stored_digest == source_digest


def _run_build_command(command: tuple[str, ...], *, cwd: Path) -> None:
    """Run one locked npm install or Vite build in the workbench package."""
    subprocess.run(command, cwd=cwd, check=True)


class _BuildCommandRunner(Protocol):
    def __call__(self, command: tuple[str, ...], *, cwd: Path) -> None: ...


__all__ = ["ensure_workbench_app"]
