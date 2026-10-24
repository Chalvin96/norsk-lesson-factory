"""Entry point: `wait_for_test_condition` for bounded test-state observation."""

from collections.abc import Callable
from threading import Condition
from time import monotonic

K_TEST_HANG_GUARD_SECONDS = 30
K_TEST_CONDITION_POLL_SECONDS = 0.01
K_TEST_SUBPROCESS_STARTUP_TIMEOUT_SECONDS = 300
K_TEST_WORKBENCH_SHUTDOWN_EXIT_MARGIN_SECONDS = 10


def wait_for_test_condition(predicate: Callable[[], bool]) -> bool:
    """Wait until a polled test condition becomes true or its hang guard expires."""
    condition = Condition()
    deadline = monotonic() + K_TEST_HANG_GUARD_SECONDS
    with condition:
        while not predicate():
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            condition.wait(timeout=min(K_TEST_CONDITION_POLL_SECONDS, remaining))
        return True
