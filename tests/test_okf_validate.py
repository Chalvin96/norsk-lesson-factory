"""Tests for ``scripts/okf_validate.py`` link checking.

Focus on the markdown link forms that must not silently bypass the CI gate:
inline (bare + ``<angle-wrapped>``) and reference-style (``[text][label]``,
implicit ``[text][]``). Builds a throwaway OKF bundle in ``tmp_path`` and runs
``validate_bundle(..., check_links=True)`` (the ``--check-links`` CI mode).
"""

from __future__ import annotations

from pathlib import Path

from scripts.okf_validate import Report
from scripts.okf_validate import validate_bundle

K_FRONTMATTER = "---\ntype: Concept\ntitle: T\ndescription: d\ntags: [x]\ntimestamp: 2026-07-09\n---\n\n"


def test_inline_link_given_bare_missing_target_expect_error(tmp_path: Path) -> None:
    report = validate_bundle(_bundle(tmp_path, "See [x](missing.md).\n"), check_links=True)

    assert _link_errors(report)


def test_inline_link_given_angle_wrapped_missing_target_expect_error(tmp_path: Path) -> None:
    report = validate_bundle(_bundle(tmp_path, "See [x](<missing file.md>).\n"), check_links=True)

    assert _link_errors(report)


def test_inline_link_given_angle_wrapped_valid_target_expect_no_error(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "See [x](<real file.md>).\n", existing=("real file.md",))

    report = validate_bundle(bundle, check_links=True)

    assert not _link_errors(report)


def test_reference_link_given_undefined_label_expect_error(tmp_path: Path) -> None:
    report = validate_bundle(_bundle(tmp_path, "See [x][ref].\n"), check_links=True)

    assert _link_errors(report)


def test_reference_link_given_definition_to_missing_file_expect_error(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "See [x][ref].\n\n[ref]: missing.md\n")

    report = validate_bundle(bundle, check_links=True)

    assert _link_errors(report)


def test_reference_link_given_definition_to_existing_file_expect_no_error(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "See [x][ref].\n\n[ref]: real.md\n", existing=("real.md",))

    report = validate_bundle(bundle, check_links=True)

    assert not _link_errors(report)


def test_reference_link_given_implicit_label_expect_resolved_by_text(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path, "See [real][].\n\n[real]: real.md\n", existing=("real.md",))

    report = validate_bundle(bundle, check_links=True)

    assert not _link_errors(report)


def _bundle(tmp_path: Path, body: str, *, existing: tuple[str, ...] = ()) -> Path:
    """Write a minimal bundle: index.md + a concept containing ``body``."""
    (tmp_path / "index.md").write_text("# B\n", encoding="utf-8")
    for name in existing:
        (tmp_path / name).write_text(K_FRONTMATTER + "# real\n", encoding="utf-8")
    (tmp_path / "concept.md").write_text(K_FRONTMATTER + body, encoding="utf-8")
    return tmp_path


def _link_errors(report: Report) -> list[str]:
    return [e for e in report.errors if "link" in e or "reference-style" in e]
