"""Regression tests for the staged Biome wrapper."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_check_staged_biome_given_missing_installation_expect_setup_message(tmp_path: Path) -> None:
    (tmp_path / "workbench").mkdir()
    script = Path(__file__).parents[1] / "scripts" / "check_staged_biome.sh"

    result = subprocess.run(
        [os.fspath(script), "workbench/src/example.ts"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "npm --prefix workbench ci" in result.stderr
