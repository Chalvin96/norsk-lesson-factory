"""Entry point: `main` captures disposable workbench browser evidence at two widths."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from queue import Empty
from queue import Queue
from threading import Thread
from typing import TextIO
from typing import cast
from urllib.parse import parse_qsl
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.parse import urlunsplit

from playwright.sync_api import Browser
from playwright.sync_api import Page
from playwright.sync_api import sync_playwright

K_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
K_VIEWPORTS = (1280, 320)
K_NEW_LESSON_DESKTOP_WIDTH = 1440
K_TOOLBAR_ONE_ROW_MAX_HEIGHT = 60
K_COMPACT_VIEWPORT_MAX_WIDTH = 360
K_SERVER_TIMEOUT_SECONDS = 120.0
K_BROWSER_TIMEOUT_MILLISECONDS = 30_000
K_SERVER_URL_PATTERN = re.compile(r"Authoring workbench available at (?P<url>http://127\.0\.0\.1:\d+/)")
K_IGNORED_WORKSPACE_NAMES = (
    ".git",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "dist",
    "node_modules",
    "reports",
)


class SnapshotError(RuntimeError):
    """A browser snapshot could not be started or verified."""


def main(argv: list[str] | None = None) -> int:
    """Parse snapshot options, run the headless browser checks, and report failures."""
    arguments = _parse_arguments(argv)
    try:
        _run_snapshot(arguments)
    except SnapshotError as error:
        print(f"FAIL snapshot {error}")
        return 1
    return 0


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    """Parse route, lesson, URL parameter, history, and output options."""
    parser = argparse.ArgumentParser(description="Capture workbench screenshots at desktop and mobile widths.")
    route_group = parser.add_mutually_exclusive_group()
    route_group.add_argument("--lesson", help="lesson id to open as /lessons/<id>")
    route_group.add_argument("--route", help="workbench route path, such as / or /lessons/<id>")
    parser.add_argument("--mode", help="optional mode URL parameter")
    parser.add_argument("--view", help="optional view URL parameter")
    parser.add_argument("--history", action="store_true", help="exercise browser back and forward")
    scenarios = parser.add_mutually_exclusive_group()
    scenarios.add_argument(
        "--new-lesson-job",
        action="store_true",
        help="capture a restored new-lesson job at 1440 px, 1280 px, and 320 px",
    )
    scenarios.add_argument(
        "--failed-review",
        action="store_true",
        help="capture a fake failed lesson review with Details closed and open",
    )
    scenarios.add_argument(
        "--broken-lesson-review",
        action="store_true",
        help="capture the author-readable review error from an invalid temporary lesson source",
    )
    scenarios.add_argument(
        "--failed-regeneration",
        action="store_true",
        help="capture a failed regeneration request with Details closed and open",
    )
    parser.add_argument("--repo-root", default=str(K_REPOSITORY_ROOT), help="repository to copy and inspect")
    parser.add_argument("--reports-dir", default="reports", help="directory for ignored screenshot evidence")
    return parser.parse_args(argv)


def _run_snapshot(arguments: argparse.Namespace) -> None:
    """Copy the workspace, serve it, and run both viewport and optional history checks."""
    repo_root = Path(arguments.repo_root).resolve()
    reports_dir = (repo_root / arguments.reports_dir).resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)
    route = _route(arguments.lesson, arguments.route)
    scenario = _snapshot_scenario(arguments)
    if scenario in ("broken-lesson-review", "failed-regeneration") and not arguments.lesson:
        raise SnapshotError(f"--{scenario} requires --lesson")
    with tempfile.TemporaryDirectory(prefix="workbench-snapshot-") as temporary_root:
        temporary_path = Path(temporary_root)
        workspace = temporary_path / "workspace"
        _copy_workspace(repo_root, workspace)
        if scenario == "broken-lesson-review":
            _write_broken_lesson_source(workspace, arguments.lesson)
        environment = _isolated_environment(temporary_path)
        server = _start_server(workspace, environment)
        try:
            base_url = server[0]
            target_url = _build_url(base_url, route, arguments.mode, arguments.view)
            _capture_viewports(base_url, target_url, route, reports_dir, scenario=scenario)
            if arguments.history:
                _check_history(base_url, target_url, route)
        finally:
            _stop_server(server[1])


def _route(lesson: str | None, route: str | None) -> str:
    """Return one normalized workbench path from the lesson or route option."""
    selected = route or (f"/lessons/{lesson}" if lesson else "/")
    return selected if selected.startswith("/") else f"/{selected}"


def _copy_workspace(repo_root: Path, destination: Path) -> None:
    """Copy source needed by the workbench while omitting generated and cache trees."""
    ignore = shutil.ignore_patterns(*K_IGNORED_WORKSPACE_NAMES)
    try:
        shutil.copytree(repo_root, destination, ignore=ignore)
    except OSError as error:
        raise SnapshotError(f"could not copy workspace: {error}") from error


def _isolated_environment(temporary_root: Path) -> dict[str, str]:
    """Give the server private home and XDG directories for this snapshot run."""
    environment = os.environ.copy()
    for name in (
        "HOME",
        "CODEX_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
        "UV_CACHE_DIR",
    ):
        directory = temporary_root / name.lower()
        directory.mkdir(parents=True, exist_ok=True)
        environment[name] = str(directory)
    return environment


def _start_server(workspace: Path, environment: dict[str, str]) -> tuple[str, subprocess.Popen[str]]:
    """Start the loopback workbench command and read its printed URL."""
    source_path = str(workspace / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        path for path in (source_path, environment.get("PYTHONPATH", "")) if path
    )
    try:
        process = subprocess.Popen(
            (
                "uv",
                "run",
                "lesson-data",
                "workbench",
                "--workspace-root",
                str(workspace),
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--no-browser",
            ),
            cwd=K_REPOSITORY_ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as error:
        raise SnapshotError(f"could not start workbench: {error}") from error
    if process.stdout is None:
        process.terminate()
        raise SnapshotError("workbench did not provide a startup stream")
    lines: Queue[str | None] = Queue()
    Thread(target=_read_startup_lines, args=(process.stdout, lines), daemon=True).start()
    output: list[str] = []
    deadline = time.monotonic() + K_SERVER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except Empty:
            break
        if line is None:
            break
        output.append(line)
        match = K_SERVER_URL_PATTERN.search(line)
        if match:
            return match.group("url"), process
    _stop_server(process)
    details = "".join(output).strip()
    raise SnapshotError(
        f"workbench did not start within {K_SERVER_TIMEOUT_SECONDS:.0f}s{': ' + details if details else ''}"
    )


def _read_startup_lines(stream: TextIO, lines: Queue[str | None]) -> None:
    """Drain complete buffered lines independently of descriptor readiness."""
    try:
        for line in stream:
            lines.put(line)
    finally:
        lines.put(None)


def _stop_server(process: subprocess.Popen[str]) -> None:
    """Stop the disposable workbench process and reap it."""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _build_url(base_url: str, route: str, mode: str | None, view: str | None) -> str:
    """Append optional mode and view parameters without discarding route queries."""
    parsed = urlsplit(base_url.rstrip("/") + route)
    query = list(parse_qsl(parsed.query, keep_blank_values=True))
    if mode is not None:
        query.append(("mode", mode))
    if view is not None:
        query.append(("view", view))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _capture_viewports(
    base_url: str,
    target_url: str,
    route: str,
    reports_dir: Path,
    *,
    scenario: str | None = None,
) -> None:
    """Capture and inspect one page at each required viewport width."""
    timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    name = re.sub(r"[^A-Za-z0-9]+", "-", route.strip("/") or "curriculum").strip("-")
    failures: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                for width in (
                    (K_NEW_LESSON_DESKTOP_WIDTH, *K_VIEWPORTS) if scenario == "new-lesson-job" else K_VIEWPORTS
                ):
                    if scenario is not None and scenario != "new-lesson-job":
                        screenshot = reports_dir / (
                            f"workbench-{name or 'curriculum'}-{timestamp}-{scenario}-details-closed-{width}px.png"
                        )
                        opened_screenshot = screenshot.with_name(
                            screenshot.name.replace("details-closed", "details-open")
                        )
                    else:
                        screenshot = reports_dir / f"workbench-{name or 'curriculum'}-{timestamp}-{width}px.png"
                        opened_screenshot = None
                    failures.extend(
                        _capture_viewport(
                            browser,
                            target_url,
                            route,
                            width,
                            screenshot,
                            opened_screenshot=opened_screenshot,
                            scenario=scenario,
                        )
                    )
            finally:
                browser.close()
    except SnapshotError:
        raise
    except Exception as error:
        raise SnapshotError(f"browser startup failed: {error}") from error
    if failures:
        raise SnapshotError("; ".join(failures))


def _capture_viewport(
    browser: Browser,
    target_url: str,
    route: str,
    width: int,
    screenshot: Path,
    *,
    opened_screenshot: Path | None = None,
    scenario: str | None = None,
) -> list[str]:
    """Navigate, check overflow, and save one viewport screenshot."""
    context = browser.new_context(viewport={"width": width, "height": 900})
    page: Page = context.new_page()
    page.set_default_timeout(K_BROWSER_TIMEOUT_MILLISECONDS)
    failures: list[str] = []
    try:
        try:
            _install_scenario_routes(page, scenario, route)
            page.goto(target_url, wait_until="networkidle")
            page.wait_for_timeout(250)
            _complete_scenario(page, scenario, width)
            print(f"PASS page-{width}")
        except Exception as error:
            message = f"page-{width}: {error}"
            print(f"FAIL {message}")
            failures.append(message)
            return failures
        overflow = _overflow(page)
        if (
            overflow["scroll_width"] <= overflow["viewport_width"]
            and overflow["body_width"] <= overflow["viewport_width"]
        ):
            print(f"PASS no-horizontal-overflow-{width}")
        else:
            message = f"no-horizontal-overflow-{width}: {overflow}"
            print(f"FAIL {message}")
            failures.append(message)
        page.screenshot(path=str(screenshot), full_page=True)
        print(f"SCREENSHOT {screenshot}")
        if opened_screenshot is not None:
            _capture_details_open(page, width, opened_screenshot, scenario)
    except Exception as error:
        message = f"screenshot-{width}: {error}"
        print(f"FAIL {message}")
        failures.append(message)
    finally:
        context.close()
    return failures


def _install_scenario_routes(page: Page, scenario: str | None, route: str) -> None:
    """Install fake provider-free responses needed by the selected browser state."""
    match scenario:
        case "new-lesson-job":
            page.add_init_script(
                "sessionStorage.setItem('workbench-new-lesson-jobs', "
                + json.dumps(
                    json.dumps(
                        [
                            {
                                "jobId": "snapshot-new-lesson",
                                "request": {
                                    "title": "A long lesson title about asking for directions around a Norwegian town",
                                    "slug": "snapshot_directions",
                                    "learner_outcome": "Ask for directions.",
                                    "cefr_level": "A1",
                                    "after_lesson_id": None,
                                },
                            }
                        ]
                    )
                )
                + ")"
            )
            page.route(
                "**/api/jobs/snapshot-new-lesson",
                lambda request: request.fulfill(status=200, json={"status": "running", "steps": ["drafting lesson"]}),
            )
        case "failed-review":
            _stub_failed_review(page)
        case "failed-regeneration":
            _stub_failed_regeneration(page, route)


def _complete_scenario(page: Page, scenario: str | None, width: int) -> None:
    """Reach the requested failure state after the page has loaded."""
    match scenario:
        case "new-lesson-job":
            toolbar = page.get_by_role("toolbar", name="Curriculum actions")
            toolbar.get_by_role("status").get_by_text("drafting lesson", exact=False).wait_for()
            if width == K_NEW_LESSON_DESKTOP_WIDTH:
                height = toolbar.evaluate("element => element.getBoundingClientRect().height")
                if height > K_TOOLBAR_ONE_ROW_MAX_HEIGHT:
                    raise SnapshotError(f"test_toolbar_given_running_job_at_1440_expect_one_row: height={height}")
                print("PASS test_toolbar_given_running_job_at_1440_expect_one_row")
        case "failed-review":
            _run_fake_failed_review(page, width)
        case "broken-lesson-review":
            _run_broken_lesson_review(page, width)
        case "failed-regeneration":
            page.get_by_role("alert").wait_for()


def _capture_details_open(page: Page, width: int, screenshot: Path, scenario: str | None) -> None:
    """Open the shared error disclosure, verify raw text and overflow, then save it."""
    page.get_by_role("alert").get_by_text("Details", exact=True).click()
    expected_detail = _expected_detail(scenario)
    if not page.get_by_text(expected_detail, exact=True).is_visible():
        raise SnapshotError(f"{scenario} Details disclosure did not reveal the expected error text")
    overflow = _overflow(page)
    if overflow["scroll_width"] > overflow["viewport_width"] or overflow["body_width"] > overflow["viewport_width"]:
        raise SnapshotError(f"no-horizontal-overflow-{width}-details-open: {overflow}")
    print(f"PASS no-horizontal-overflow-{width}-details-open")
    page.screenshot(path=str(screenshot), full_page=True)
    print(f"SCREENSHOT {screenshot}")


def _snapshot_scenario(arguments: argparse.Namespace) -> str | None:
    """Return the selected deterministic failure state, if any."""
    if arguments.new_lesson_job:
        return "new-lesson-job"
    if arguments.failed_review:
        return "failed-review"
    if arguments.broken_lesson_review:
        return "broken-lesson-review"
    if arguments.failed_regeneration:
        return "failed-regeneration"
    return None


def _write_broken_lesson_source(workspace: Path, lesson_id: str) -> None:
    """Break only the snapshot copy of the lesson so review fails before provider work."""
    source = workspace / "content" / "lessons" / lesson_id / "lesson.md"
    if not source.is_file():
        raise SnapshotError(f"cannot prepare broken lesson fixture: {source.relative_to(workspace)} is missing")
    source.write_text("::: {.reading}\nUnclosed", encoding="utf-8")


def _stub_failed_review(page: Page) -> None:
    """Return a local fake failure for lesson review without contacting a provider."""
    page.route(
        "**/api/lessons/*/review",
        lambda route: route.fulfill(status=200, json={"job_id": "fake-failed-review"}),
    )
    page.route(
        "**/api/jobs/fake-failed-review",
        lambda route: route.fulfill(
            status=200,
            json={
                "status": "failed",
                "error": "OpenAI has no credit left. Top up the account, then retry.",
                "error_detail": "BackendDownException: Upstream request failed: Insufficient account funds",
                "can_cancel": False,
            },
        ),
    )


def _stub_failed_regeneration(page: Page, route: str) -> None:
    """Return an API error with separate raw details for a provider-free lifecycle snapshot."""
    lesson_id = route.strip("/").removeprefix("lessons/")
    page.add_init_script(
        "window.sessionStorage.setItem("
        + json.dumps(f"regeneration-{lesson_id}")
        + ", "
        + json.dumps("snapshot-failed-regeneration")
        + ")"
    )
    page.route(
        "**/api/runs/snapshot-failed-regeneration",
        lambda request: request.fulfill(
            status=503,
            json={
                "error": "The regeneration run could not be loaded.",
                "details": "BackendDownException: Regeneration provider request failed",
            },
        ),
    )


def _run_fake_failed_review(page: Page, width: int) -> None:
    """Open the lesson review menu and request the provider-free failed job."""
    if width <= K_COMPACT_VIEWPORT_MAX_WIDTH:
        page.get_by_role("button", name="More lesson actions").click()
        page.get_by_role("menuitem", name="Run AI review", exact=True).click()
    else:
        page.get_by_role("button", name="Run AI review", exact=True).click()
        page.get_by_role("menuitem", name=re.compile(r"^Whole lesson")).click()
    page.get_by_role("alert").get_by_text("OpenAI has no credit left. Top up the account, then retry.").wait_for()


def _run_broken_lesson_review(page: Page, width: int) -> None:
    """Run an actual review against the malformed source in the disposable workspace."""
    if width <= K_COMPACT_VIEWPORT_MAX_WIDTH:
        page.get_by_role("button", name="More lesson actions").click()
        page.get_by_role("menuitem", name="Run AI review", exact=True).click()
    else:
        page.get_by_role("button", name="Run AI review", exact=True).click()
        page.get_by_role("menuitem", name=re.compile(r"^Whole lesson")).click()
    page.get_by_role("alert").get_by_text(
        "AI review requires a compilable lesson; fix check errors first", exact=True
    ).wait_for()


def _expected_detail(scenario: str | None) -> str:
    """Return the raw job text required in the selected disclosure screenshot."""
    match scenario:
        case "failed-review":
            return "BackendDownException: Upstream request failed: Insufficient account funds"
        case "broken-lesson-review":
            return "ValueError: AI review requires a compilable lesson; fix check errors first"
        case "failed-regeneration":
            return "BackendDownException: Regeneration provider request failed"
        case _:
            raise SnapshotError(f"Details screenshots do not support scenario {scenario!r}")


def _overflow(page: Page) -> dict[str, int]:
    """Read document and body widths from the rendered page."""
    value = page.evaluate(
        """() => ({
            viewport_width: window.innerWidth,
            scroll_width: document.documentElement.scrollWidth,
            body_width: document.body ? document.body.scrollWidth : 0,
        })"""
    )
    return cast(dict[str, int], value)


def _check_history(base_url: str, target_url: str, route: str) -> None:
    """Navigate through two entries and verify browser back and forward preserve the route."""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            page.set_default_timeout(K_BROWSER_TIMEOUT_MILLISECONDS)
            try:
                start_route = "/settings" if route == "/" else "/"
                page.goto(base_url.rstrip("/") + start_route, wait_until="networkidle")
                page.goto(target_url, wait_until="networkidle")
                page.go_back(wait_until="networkidle")
                if urlsplit(page.url).path != start_route:
                    raise SnapshotError(f"history-back expected {start_route}, got {page.url}")
                print("PASS history-back")
                page.go_forward(wait_until="networkidle")
                if urlsplit(page.url).path != route:
                    raise SnapshotError(f"history-forward expected {route}, got {page.url}")
                print("PASS history-forward")
            finally:
                context.close()
                browser.close()
    except SnapshotError as error:
        print(f"FAIL history {error}")
        raise
    except Exception as error:
        message = f"history: {error}"
        print(f"FAIL {message}")
        raise SnapshotError(message) from error


if __name__ == "__main__":
    sys.exit(main())
