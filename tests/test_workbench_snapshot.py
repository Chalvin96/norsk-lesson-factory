"""Entry point: snapshot CLI tests cover startup output without a real server."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import workbench_snapshot


def test_snapshot_given_warning_and_url_in_one_write_expect_browser_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "source"
    workspace.mkdir()
    browser_api = MagicMock()
    page = browser_api.return_value.__enter__.return_value.chromium.launch.return_value.new_context.return_value.new_page.return_value
    page.evaluate.return_value = {"scroll_width": 320, "viewport_width": 320, "body_width": 320}
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import os, time; "
            "os.write(1, b'warning: x\\nAuthoring workbench available at http://127.0.0.1:43210/\\n'); "
            "time.sleep(30)",
        ],
        cwd=tmp_path,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    monkeypatch.setattr(workbench_snapshot.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(workbench_snapshot, "sync_playwright", browser_api)
    monkeypatch.setattr(workbench_snapshot, "K_SERVER_TIMEOUT_SECONDS", 1.0)
    try:
        result = workbench_snapshot.main(["--repo-root", str(workspace), "--route", "/settings"])

        assert result == 0, capsys.readouterr().out
        assert page.goto.call_count == 2
        page.goto.assert_called_with("http://127.0.0.1:43210/settings", wait_until="networkidle")
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait(timeout=5)
        if child.stdout is not None:
            child.stdout.close()
