"""Regression checks for local pre-commit trigger coverage."""

from __future__ import annotations

import ast
import re
from collections import deque
from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
EXPORTER = REPOSITORY_ROOT / "scripts" / "export_workbench_schema.py"


def test_workbench_schema_hook_given_exporter_transitive_imports_expect_all_are_triggered() -> None:
    config = yaml.safe_load((REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    hook = next(
        hook
        for repository in config["repos"]
        for hook in repository.get("hooks", [])
        if hook["id"] == "workbench-types-check"
    )
    trigger = re.compile(hook["files"])

    uncovered = sorted(
        path.relative_to(REPOSITORY_ROOT).as_posix()
        for path in _exporter_source_imports()
        if not trigger.search(path.relative_to(REPOSITORY_ROOT).as_posix())
    )

    assert not uncovered, f"workbench schema hook misses exporter inputs: {uncovered}"


def _exporter_source_imports() -> set[Path]:
    seen: set[Path] = set()
    pending = deque([EXPORTER])
    while pending:
        path = pending.popleft()
        if path in seen:
            continue
        seen.add(path)
        for module in _source_imports(path):
            imported = _resolve_module(module)
            if imported is not None and imported not in seen:
                pending.append(imported)
    return seen


def _source_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names if alias.name.startswith("lesson_builder"))
        elif isinstance(node, ast.ImportFrom):
            modules.update(_resolved_from_import(node))
    return modules


def _resolved_from_import(node: ast.ImportFrom) -> set[str]:
    if not node.module or not node.module.startswith("lesson_builder"):
        return set()
    return {
        candidate if _resolve_module(candidate) is not None else node.module
        for alias in node.names
        for candidate in (f"{node.module}.{alias.name}",)
    }


def _resolve_module(module: str) -> Path | None:
    relative = Path(*module.split("."))
    for candidate in (SOURCE_ROOT / relative.with_suffix(".py"), SOURCE_ROOT / relative / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None
