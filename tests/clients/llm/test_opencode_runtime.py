"""Behavior tests for OpenCode temporary directory liveness and cleanup."""

from __future__ import annotations

import fcntl
import json
import os
import select
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import lesson_builder.clients.llm.opencode as opencode
import lesson_builder.clients.llm.opencode_credentials as opencode_credentials
import lesson_builder.clients.llm.opencode_runtime as opencode_runtime
from lesson_builder.clients.llm.exceptions import BackendDownException
from tests.clients.llm.fakes import FakeOpencodeProcess
from tests.settings import wait_for_test_condition


def test_process_home_given_inherited_opencode_paths_expect_runtime_overrides_removed() -> None:
    assert not any(name.startswith("OPENCODE_") for name in os.environ)


def test_opencode_environment_given_unrelated_temp_entries_expect_sweep_within_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for index in range(100):
        (tmp_path / f"unrelated-{index}").touch()
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    clock = [0.0]

    def advance_clock() -> float:
        clock[0] += 0.01
        return clock[0]

    monkeypatch.setattr(time, "monotonic", advance_clock)

    with opencode_credentials.opencode_process_environment("opencode-go/glm-5.2", deadline=0.1, timeout=1) as env:
        assert Path(env["XDG_DATA_HOME"]).is_dir()


@pytest.mark.parametrize("failure", ["return", "raise"])
@pytest.mark.parametrize("warm", [False, True])
def test_hold_opencode_cache_given_handled_failure_expect_packages_and_warm_markers_preserved(
    tmp_path: Path, warm: bool, failure: str
) -> None:
    cache = tmp_path / "cache"
    package = cache / "opencode/packages/complete"
    model = "opencode-go/glm-5.2"
    if warm:
        with opencode_runtime.hold_opencode_cache(
            cache, model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
        ) as mark:
            package.mkdir(parents=True)
            mark()
    else:
        package.mkdir(parents=True)
    original_markers = set(cache.glob(".norsk-opencode-cache-warm-*"))

    try:
        with opencode_runtime.hold_opencode_cache(
            cache, model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
        ):
            if failure == "raise":
                raise BackendDownException("handled provider failure")
    except BackendDownException:
        pass

    with opencode_runtime.hold_opencode_cache(
        cache, model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
    ):
        assert package.is_dir()
        assert set(cache.glob(".norsk-opencode-cache-warm-*")) == original_markers


def test_hold_opencode_cache_given_record_creation_race_expect_no_unlocked_pending_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    original_mkstemp = opencode_runtime.tempfile.mkstemp
    visible_unlocked: list[Path] = []

    def observe_created_file(*args, **kwargs):
        result = original_mkstemp(*args, **kwargs)
        for pending in tmp_path.glob(".norsk-opencode-cache-pending-*"):
            with pending.open() as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                visible_unlocked.append(pending)
        return result

    monkeypatch.setattr(opencode_runtime.tempfile, "mkstemp", observe_created_file)

    with opencode_runtime.hold_opencode_cache(
        tmp_path, "opencode-go/glm-5.2", deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
    ) as mark:
        mark()

    assert visible_unlocked == []


@pytest.mark.parametrize("crash_phase", ["install", "before-marker", "after-marker"])
def test_hold_opencode_cache_given_killed_owner_expect_only_unfinished_new_packages_removed(
    tmp_path: Path, crash_phase: str
) -> None:
    cache = tmp_path / "cache"
    existing = cache / "opencode/packages/complete"
    with opencode_runtime.hold_opencode_cache(
        cache, "openai/existing", deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
    ) as mark:
        existing.mkdir(parents=True)
        mark()
    original_markers = set(cache.glob(".norsk-opencode-cache-warm-*"))
    script = (
        "import os, pathlib, sys, time\n"
        "from lesson_builder.clients.llm.opencode_runtime import hold_opencode_cache\n"
        "cache = pathlib.Path(sys.argv[1])\n"
        "def pause(*args, **kwargs):\n"
        "    print('ready', flush=True)\n"
        "    while True: time.sleep(1)\n"
        "with hold_opencode_cache(cache, 'openai/new', deadline=time.monotonic()+10, timeout=10, is_cancelled=None) as mark:\n"
        "    (cache / 'opencode/packages/new').mkdir()\n"
        "    if sys.argv[2] != 'install':\n"
        "        original_replace = os.replace\n"
        "        def publish_marker(source, target):\n"
        "            if sys.argv[2] == 'after-marker': original_replace(source, target)\n"
        "            pause()\n"
        "        os.replace = publish_marker\n"
        "        mark()\n"
        "    else: pause()\n"
    )
    owner = subprocess.Popen([sys.executable, "-c", script, str(cache), crash_phase], stdout=subprocess.PIPE, text=True)
    try:
        assert owner.stdout is not None
        assert select.select([owner.stdout], [], [], 5)[0]
        assert owner.stdout.readline().strip() == "ready"
    finally:
        if owner.poll() is None:
            os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=5)

    with opencode_runtime.hold_opencode_cache(
        cache, "openai/new", deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
    ):
        assert existing.is_dir()
        assert original_markers <= set(cache.glob(".norsk-opencode-cache-warm-*"))
        assert (cache / "opencode/packages/new").exists() == (crash_phase == "after-marker")


def test_hold_opencode_cache_given_queued_cold_call_expect_new_warm_calls_wait_for_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    warm_model = "opencode-go/glm-5.2"
    cold_waiting = threading.Event()
    late_warm_entered = threading.Event()
    cold_entered = threading.Event()
    original_flock = fcntl.flock

    def observe_cold_wait(descriptor: int, operation: int) -> None:
        if threading.current_thread().name == "cold" and operation == fcntl.LOCK_EX | fcntl.LOCK_NB:
            try:
                original_flock(descriptor, operation)
            except BlockingIOError:
                cold_waiting.set()
                raise
            return
        original_flock(descriptor, operation)

    def run_call(model: str, entered: threading.Event) -> None:
        threading.current_thread().name = "cold" if model != warm_model else "warm"
        with opencode_runtime.hold_opencode_cache(
            tmp_path, model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
        ) as mark:
            entered.set()
            mark()

    with opencode_runtime.hold_opencode_cache(
        tmp_path, warm_model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
    ) as mark:
        mark()
    monkeypatch.setattr(fcntl, "flock", observe_cold_wait)

    with ThreadPoolExecutor(max_workers=2) as pool:
        with opencode_runtime.hold_opencode_cache(
            tmp_path, warm_model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
        ):
            cold = pool.submit(run_call, "openai/new-model", cold_entered)
            assert cold_waiting.wait(timeout=5)
            warm = pool.submit(run_call, warm_model, late_warm_entered)
            overtook_writer = late_warm_entered.wait(timeout=0.2)
        cold.result(timeout=5)
        warm.result(timeout=5)

    assert not overtook_writer
    assert cold_entered.is_set()
    assert late_warm_entered.is_set()


def test_hold_opencode_cache_given_overlapping_success_then_killed_owner_expect_completed_addition_preserved(
    tmp_path: Path,
) -> None:
    model = "openai/existing"
    with opencode_runtime.hold_opencode_cache(
        tmp_path, model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
    ) as mark:
        mark()
    script = (
        "import pathlib, sys, time\n"
        "from lesson_builder.clients.llm.opencode_runtime import hold_opencode_cache\n"
        "with hold_opencode_cache(pathlib.Path(sys.argv[1]), 'openai/existing', deadline=time.monotonic()+10, timeout=10, is_cancelled=None):\n"
        "    print('ready', flush=True)\n"
        "    while True: time.sleep(1)\n"
    )
    owner = subprocess.Popen([sys.executable, "-c", script, str(tmp_path)], stdout=subprocess.PIPE, text=True)
    completed = tmp_path / "opencode/packages/new-complete"
    try:
        assert owner.stdout is not None
        assert select.select([owner.stdout], [], [], 5)[0]
        assert owner.stdout.readline().strip() == "ready"
        with opencode_runtime.hold_opencode_cache(
            tmp_path, model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
        ) as mark:
            completed.mkdir(parents=True)
            mark()
    finally:
        if owner.poll() is None:
            os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=5)

    with opencode_runtime.hold_opencode_cache(
        tmp_path, model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
    ):
        assert completed.is_dir()


def test_hold_opencode_cache_given_held_marker_lock_expect_completion_within_deadline(tmp_path: Path) -> None:
    finished = threading.Event()

    def run_call() -> None:
        try:
            with opencode_runtime.hold_opencode_cache(
                tmp_path, "openai/model", deadline=time.monotonic() + 0.1, timeout=1, is_cancelled=None
            ) as mark:
                mark()
        finally:
            finished.set()

    with (tmp_path / ".norsk-opencode-cache-marker.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        with ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(run_call)
            completed_in_budget = finished.wait(timeout=0.5)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            error = worker.exception(timeout=5)

    assert completed_in_budget
    assert isinstance(error, BackendDownException)
    assert "cache warm marker" in str(error)


def test_sweep_stale_opencode_directories_given_killed_lock_owner_expect_directory_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    stale_dir = runtime_root / "norsk-opencode-state-killed"
    stale_dir.mkdir()
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime_root))
    process = _start_lock_holder(stale_dir)

    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=5)

    removed = opencode_runtime.sweep_stale_opencode_directories()

    assert removed == 1
    assert not stale_dir.exists()


def test_sweep_stale_opencode_directories_given_live_lock_owner_expect_directory_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    live_dir = runtime_root / "norsk-opencode-prompt-live"
    live_dir.mkdir()
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime_root))
    process = _start_lock_holder(live_dir)

    try:
        removed = opencode_runtime.sweep_stale_opencode_directories()

        assert removed == 0
        assert live_dir.is_dir()
    finally:
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def test_sweep_stale_opencode_directories_given_lockless_legacy_dir_expect_directory_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    legacy_dir = runtime_root / "norsk-opencode-state-legacy"
    legacy_dir.mkdir()
    old = time.time() - 24 * 60 * 60
    os.utime(legacy_dir, (old, old))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime_root))

    removed = opencode_runtime.sweep_stale_opencode_directories()

    assert removed == 1
    assert not legacy_dir.exists()


@pytest.mark.parametrize("recent_path", [".", "data/opencode/log/current.log", "cache/packages/new"])
def test_sweep_stale_opencode_directories_given_recent_activity_expect_lockless_directory_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, recent_path: str
) -> None:
    legacy_dir = tmp_path / "norsk-opencode-state-active"
    log = legacy_dir / "data/opencode/log/current.log"
    log.parent.mkdir(parents=True)
    log.write_text("active call", encoding="utf-8")
    (legacy_dir / "cache/packages/new").mkdir(parents=True)
    old = time.time() - 24 * 60 * 60
    for path in [legacy_dir, *legacy_dir.rglob("*")]:
        os.utime(path, (old, old))
    os.utime(legacy_dir / recent_path, None)
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))

    removed = opencode_runtime.sweep_stale_opencode_directories()

    assert removed == 1
    assert not legacy_dir.exists()


def test_sweep_stale_opencode_directories_given_foreign_root_lock_expect_independent_startup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    foreign_lock = tmp_path / ".norsk-opencode-temp-root.lock"
    foreign_lock.touch()
    original_open = Path.open

    def guarded_open(path: Path, *args, **kwargs):
        if path == foreign_lock:
            raise PermissionError("lock belongs to another user")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))

    assert opencode_runtime.sweep_stale_opencode_directories() == 0


def test_sweep_stale_opencode_directories_given_foreign_owned_directory_expect_directory_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    foreign_dir = tmp_path / "norsk-opencode-state-foreign"
    foreign_dir.mkdir()
    old = time.time() - 24 * 60 * 60
    os.utime(foreign_dir, (old, old))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    monkeypatch.setattr(os, "getuid", lambda: foreign_dir.stat().st_uid + 1)

    removed = opencode_runtime.sweep_stale_opencode_directories()

    assert removed == 0
    assert foreign_dir.is_dir()


def test_opencode_environment_given_same_process_sweep_expect_live_directory_kept_and_locks_released(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    for _ in range(3):
        with opencode_credentials.opencode_process_environment(
            "opencode-go/glm-5.2", deadline=time.monotonic() + 5, timeout=5
        ) as env:
            data_home = Path(env["XDG_DATA_HOME"])
            assert opencode_runtime.sweep_stale_opencode_directories() == 0
            assert (data_home / "opencode/auth.json").is_file()
        assert not data_home.exists()
    assert opencode_runtime.collect_opencode_lock_fds() == ()


@pytest.mark.parametrize("prompt_size", [6, 128 * 1024 + 1])
def test_opencode_client_given_killed_parent_with_live_child_expect_locks_retained_until_child_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, prompt_size: int
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    ready = tmp_path / "consumer.json"
    executable = tmp_path / "opencode"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "root = pathlib.Path(os.environ['XDG_DATA_HOME']).parent\n"
        "partial = pathlib.Path(os.environ['XDG_CACHE_HOME']) / 'opencode/packages/example/node_modules/example'\n"
        "partial.mkdir(parents=True)\n"
        "ready = pathlib.Path(os.environ['OC362_CONSUMER_READY'])\n"
        "prompt = str(pathlib.Path(sys.argv[sys.argv.index('-f') + 1]).parent) if '-f' in sys.argv else None\n"
        "ready.with_suffix('.tmp').write_text(json.dumps({'pid': os.getpid(), 'root': str(root), 'prompt': prompt, 'partial': str(partial)}))\n"
        "ready.with_suffix('.tmp').replace(ready)\n"
        "while True: time.sleep(0.05)\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime))
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("OC362_CONSUMER_READY", str(ready))
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from lesson_builder.clients.llm.opencode import OpencodeClient; OpencodeClient().call('x' * int(sys.argv[1]))",
            str(prompt_size),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child_pid: int | None = None
    try:
        assert wait_for_test_condition(ready.exists)
        consumer = json.loads(ready.read_text(encoding="utf-8"))
        child_pid = consumer["pid"]
        state_root = Path(consumer["root"])
        os.kill(parent.pid, signal.SIGKILL)
        parent.wait(timeout=5)

        removed = opencode_runtime.sweep_stale_opencode_directories()

        assert removed == 0
        assert state_root.is_dir()
        if consumer["prompt"] is not None:
            assert (Path(consumer["prompt"]) / "prompt.txt").is_file()

        def unexpected_spawn(*args, **kwargs):
            pytest.fail("cache lock was released while its OpenCode child was still alive")

        monkeypatch.setattr(opencode.subprocess, "Popen", unexpected_spawn)
        with pytest.raises(BackendDownException, match="cache population"):
            opencode.OpencodeClient(timeout=1).call("competing cold call")

        os.kill(child_pid, signal.SIGKILL)
        child_pid = None
        directory_count = 2 if consumer["prompt"] is not None else 1
        removed_after_exit = 0

        def sweep_exited_child_state() -> bool:
            nonlocal removed_after_exit
            removed_after_exit += opencode_runtime.sweep_stale_opencode_directories()
            prompt_removed = consumer["prompt"] is None or not Path(consumer["prompt"]).exists()
            return not state_root.exists() and prompt_removed

        assert wait_for_test_condition(sweep_exited_child_state)
        assert removed_after_exit == directory_count

        def recovered_spawn(*args, **kwargs):
            assert not Path(consumer["partial"]).exists()
            return FakeOpencodeProcess(stdout='{"type":"text","part":{"text":"recovered"}}', returncode=0)

        monkeypatch.setattr(opencode.subprocess, "Popen", recovered_spawn)
        assert opencode.OpencodeClient().call("retry").text == "recovered"
    finally:
        if parent.poll() is None:
            os.kill(parent.pid, signal.SIGKILL)
            parent.wait(timeout=5)
        if child_pid is not None:
            os.kill(child_pid, signal.SIGKILL)


def _start_lock_holder(directory: Path) -> subprocess.Popen[str]:
    lock_path = directory / ".norsk-opencode-live.lock"
    script = (
        "import fcntl, sys, time\n"
        "handle = open(sys.argv[1], 'a+')\n"
        "fcntl.flock(handle.fileno(), fcntl.LOCK_EX)\n"
        "print('ready', flush=True)\n"
        "while True: time.sleep(1)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    if not select.select([process.stdout], [], [], 5)[0]:
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        pytest.fail("lock-holder child did not acquire its directory lock")
    assert process.stdout.readline().strip() == "ready"
    return process
