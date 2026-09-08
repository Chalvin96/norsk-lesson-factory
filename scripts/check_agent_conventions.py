"""Entry points: `main` runs the CLI; tests call `convention_issues` for one source file."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

K_CONVENTIONS_TEST_NAME = re.compile(r"^test_.+_given_.+_expect_.+$")
K_CONVENTIONS_DOCSTRING_PREFIXES = ("Entry point:", "Entry points:", "Not a check itself")
K_CONVENTIONS_SOURCE_ROOTS = (Path("src"), Path("scripts"))
K_CONVENTIONS_QUALIFIED_MIN_PARTS = 2
K_CONVENTIONS_RENAME_STATUS_FIELDS = 3
K_CONVENTIONS_FLAT_SERVICE_PATH_PARTS = 3
# Keep this mechanical allowlist aligned with knowledge/project/codebase.md.
K_CONVENTIONS_APPROVED_DOMAIN_CONTEXTS = frozenset({"catalog", "curriculum", "distribution", "lesson"})
K_CONVENTIONS_MODEL_BASES = frozenset(
    {"BaseModel", "RootModel", "TypedDict", "NamedTuple", "Enum", "IntEnum", "StrEnum"}
)
K_CONVENTIONS_ALLOWED_IMPORTS = {
    "cli": frozenset({"cli", "workflow", "application", "domain", "workspace", "clients", "formats"}),
    "workflow": frozenset({"workflow", "application", "domain", "workspace", "clients", "formats"}),
    "application": frozenset({"application", "domain", "workspace", "clients", "formats"}),
    "domain": frozenset({"domain"}),
    "workspace": frozenset({"workspace", "clients", "formats"}),
    "clients": frozenset({"clients", "formats"}),
    "formats": frozenset({"formats"}),
}
K_CONVENTIONS_IMPORT_EXCEPTIONS = frozenset()
K_CONVENTIONS_ACCEPTED_IMPORT_EXCEPTIONS = frozenset()


@dataclass(frozen=True)
class ConventionIssue:
    path: Path
    line: int
    rule: str
    message: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (str(self.path), self.rule, self.message)


def convention_issues(path: Path, source: str) -> list[ConventionIssue]:
    """Return mechanical convention violations for one Python source file."""
    tree = _parse_source(path, source)
    if tree is None:
        try:
            ast.parse(source)
        except SyntaxError as exc:
            return [ConventionIssue(path, exc.lineno or 1, "syntax", exc.msg)]
        return []
    issues = _test_name_issues(path, tree)
    if any(path.is_relative_to(root) for root in K_CONVENTIONS_SOURCE_ROOTS):
        issues.extend(_source_issues(path, tree))
    issues.extend(_test_helper_placement_issues(path, tree))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="report the full existing backlog")
    parser.add_argument("--changed-from", help="only fail on issues introduced after this git revision")
    args = parser.parse_args()
    base = None if args.all else (args.changed_from or _default_base())
    paths = _python_paths() if base is None else _changed_python_paths(base)
    current = [issue for path in paths for issue in convention_issues(path, path.read_text(encoding="utf-8"))]
    if base is not None:
        previous = _previous_issue_identities(base, paths, _renamed_paths(base))
        current = [issue for issue in current if issue.identity not in previous]
    for issue in sorted(current, key=lambda item: (str(item.path), item.line, item.rule)):
        print(f"{issue.path}:{issue.line}: {issue.rule}: {issue.message}")
    return 1 if current else 0


def _previous_issue_identities(
    base: str, paths: list[Path], renamed_paths: dict[Path, Path]
) -> set[tuple[str, str, str]]:
    """Return baseline issues under current paths, preserving path-sensitive checks."""
    previous: set[tuple[str, str, str]] = set()
    for path in paths:
        previous_path = renamed_paths.get(path, path)
        source = _git_source(base, previous_path)
        if source is None:
            continue
        previous.update((str(path), issue.rule, issue.message) for issue in convention_issues(previous_path, source))
    return previous


def _parse_source(path: Path, source: str) -> ast.Module | None:
    """Parse source, returning ``None`` when syntax errors need reporting."""
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _test_name_issues(path: Path, tree: ast.Module) -> list[ConventionIssue]:
    """Find test functions that do not use the repository's naming shape."""
    if not (path.name.startswith("test_") and "tests" in path.parts):
        return []
    return [
        ConventionIssue(path, node.lineno, "test-name", node.name)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        and not K_CONVENTIONS_TEST_NAME.fullmatch(node.name)
    ]


def _source_issues(path: Path, tree: ast.Module) -> list[ConventionIssue]:
    """Find module-docstring, declaration-order, and constant-name issues."""
    issues: list[ConventionIssue] = []
    docstring = ast.get_docstring(tree, clean=False) or ""
    first_line = docstring.splitlines()[0].strip() if docstring else ""
    if not first_line.startswith(K_CONVENTIONS_DOCSTRING_PREFIXES):
        issues.append(ConventionIssue(path, 1, "module-docstring", first_line or "missing"))
    issues.extend(_module_order_issues(path, tree))
    issues.extend(_constant_issues(path, tree))
    issues.extend(_import_direction_issues(path, tree))
    issues.extend(_domain_placement_issues(path, tree))
    return issues


def _domain_placement_issues(path: Path, tree: ast.Module) -> list[ConventionIssue]:
    """Find unknown contexts, nested service packages, and service models."""
    domain_root = Path("src/lesson_builder/domain")
    if not path.is_relative_to(domain_root):
        return []
    relative = path.relative_to(domain_root)
    if len(relative.parts) < K_CONVENTIONS_QUALIFIED_MIN_PARTS:
        return []
    context = relative.parts[0]
    issues: list[ConventionIssue] = []
    if context not in K_CONVENTIONS_APPROVED_DOMAIN_CONTEXTS:
        issues.append(
            ConventionIssue(
                path,
                1,
                "unknown-domain-context",
                f"{context} is not an approved domain context; update the codebase guide after explicit approval",
            )
        )
    if len(relative.parts) < K_CONVENTIONS_QUALIFIED_MIN_PARTS or relative.parts[1] != "services":
        return issues
    if len(relative.parts) > K_CONVENTIONS_FLAT_SERVICE_PATH_PARTS:
        service_package = relative.parts[2]
        issues.append(
            ConventionIssue(
                path,
                1,
                "nested-service-package",
                (
                    f"{context}/services/{service_package} is not allowed; route the responsibility "
                    "to models, flat services, validation, application, workflow, or infrastructure"
                ),
            )
        )
    issues.extend(_service_model_issues(path, context, tree))
    return issues


def _service_model_issues(path: Path, context: str, tree: ast.Module) -> list[ConventionIssue]:
    """Find model-shaped classes declared in a domain service module."""
    issues: list[ConventionIssue] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = {_expression_name(base) for base in node.bases}
        decorator_names = {_expression_name(decorator) for decorator in node.decorator_list}
        if not (base_names.intersection(K_CONVENTIONS_MODEL_BASES) or "dataclass" in decorator_names):
            continue
        issues.append(
            ConventionIssue(
                path,
                node.lineno,
                "model-in-service",
                f"{node.name} belongs in domain/{context}/models, not services",
            )
        )
    return issues


def _expression_name(node: ast.expr) -> str:
    """Return the final name in a decorator or base-class expression."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _expression_name(node.func)
    return ""


def _import_direction_issues(path: Path, tree: ast.Module) -> list[ConventionIssue]:
    """Find imports that point upward across the package layer boundary."""
    package_root = Path("src/lesson_builder")
    if not path.is_relative_to(package_root):
        return []
    relative = path.relative_to(package_root).with_suffix("")
    current_layer = relative.parts[0] if relative.parts else ""
    if current_layer not in K_CONVENTIONS_ALLOWED_IMPORTS:
        return []
    current_module = "lesson_builder." + ".".join(relative.parts)
    issues: list[ConventionIssue] = []
    for node in ast.walk(tree):
        imported_modules = _imported_modules(node, current_module)
        if not imported_modules:
            continue
        for imported_module in imported_modules:
            parts = imported_module.split(".")
            if len(parts) < K_CONVENTIONS_QUALIFIED_MIN_PARTS or parts[0] != "lesson_builder":
                continue
            target_layer = parts[1]
            if target_layer not in K_CONVENTIONS_ALLOWED_IMPORTS:
                continue
            if _is_import_allowed(current_module, current_layer, target_layer, imported_module):
                continue
            issues.append(
                ConventionIssue(
                    path,
                    node.lineno,
                    "import-direction",
                    f"{current_layer} may not import {target_layer}: {imported_module}",
                )
            )
    return issues


def _is_import_allowed(current_module: str, current_layer: str, target_layer: str, imported_module: str) -> bool:
    """Return whether one import is inside the current layer boundary."""
    return (
        (current_module, imported_module) in K_CONVENTIONS_ACCEPTED_IMPORT_EXCEPTIONS
        or target_layer in K_CONVENTIONS_ALLOWED_IMPORTS[current_layer]
        or (current_module, target_layer) in K_CONVENTIONS_IMPORT_EXCEPTIONS
    )


def _imported_modules(node: ast.AST, current_module: str) -> list[str]:
    """Resolve absolute and relative imports to lesson-builder modules."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level == 0:
            return [node.module] if node.module else []
        return _resolve_relative_import(node, current_module)
    return []


def _resolve_relative_import(node: ast.ImportFrom, current_module: str) -> list[str]:
    """Resolve an ``ImportFrom`` level against its declaring package."""
    module_parts = current_module.split(".")
    package_parts = module_parts if module_parts[-1] == "__init__" else module_parts[:-1]
    drop = node.level - 1
    if drop >= len(package_parts):
        return []
    prefix = package_parts[: len(package_parts) - drop]
    if node.module:
        return [".".join((*prefix, *node.module.split(".")))]
    return [".".join((*prefix, alias.name)) for alias in node.names]


def _test_helper_placement_issues(path: Path, tree: ast.Module) -> list[ConventionIssue]:
    """Find classes that are in the wrong test helper module."""
    issues: list[ConventionIssue] = []
    if path.name == "conftest.py":
        issues.extend(
            ConventionIssue(path, node.lineno, "conftest-class", node.name)
            for node in tree.body
            if isinstance(node, ast.ClassDef)
        )
    if "tests" not in path.parts:
        return issues
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not (node.name.endswith("Factory") or node.name.startswith("Fake")):
            continue
        expected = "factories.py" if node.name.endswith("Factory") else "fakes.py"
        if path.name != expected:
            issues.append(ConventionIssue(path, node.lineno, "test-helper-placement", node.name))
    return issues


def _module_order_issues(path: Path, tree: ast.Module) -> list[ConventionIssue]:
    seen_private = False
    issues: list[ConventionIssue] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_"):
            seen_private = True
        elif seen_private and not node.name.startswith("_"):
            issues.append(ConventionIssue(path, node.lineno, "public-before-private", node.name))
    return issues


def _constant_issues(path: Path, tree: ast.Module) -> list[ConventionIssue]:
    issues: list[ConventionIssue] = []
    for node in tree.body:
        names: list[str] = []
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id for target in targets if isinstance(target, ast.Name)]
        for name in names:
            if name.isupper() and not name.startswith("K_"):
                issues.append(ConventionIssue(path, node.lineno, "constant-name", name))
    return issues


def _python_paths() -> list[Path]:
    return sorted(path for root in (*K_CONVENTIONS_SOURCE_ROOTS, Path("tests")) for path in root.rglob("*.py"))


def _default_base() -> str:
    result = subprocess.run(["git", "merge-base", "HEAD", "origin/master"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "HEAD^"


def _changed_python_paths(base: str) -> list[Path]:
    names: set[str] = set()
    for command in (
        ["git", "diff", "--name-only", "--diff-filter=ACMR", f"{base}...HEAD"],
        ["git", "diff", "--name-only", "--diff-filter=ACMR"],
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
    ):
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        names.update(result.stdout.splitlines())
    return sorted(Path(name) for name in names if name.endswith(".py") and Path(name).exists())


def _git_source(base: str, path: Path) -> str | None:
    result = subprocess.run(["git", "show", f"{base}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else None


def _renamed_paths(base: str) -> dict[Path, Path]:
    """Map a changed path to its pre-migration path when Git detected a rename."""
    result = subprocess.run(
        ["git", "diff", "--find-renames", "--name-status", base],
        check=True,
        capture_output=True,
        text=True,
    )
    renamed: dict[Path, Path] = {}
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != K_CONVENTIONS_RENAME_STATUS_FIELDS or not fields[0].startswith("R"):
            continue
        old_path, new_path = fields[1:]
        renamed[Path(new_path)] = Path(old_path)
    return renamed


if __name__ == "__main__":
    sys.exit(main())
