"""Scheduler survives transient failures and stops between bounded checks."""

import pytest

from app.scheduler import run_scheduler


class StopAfter:
    def __init__(self, count):
        self.count = count
        self.waits = []

    def is_set(self):
        return len(self.waits) >= self.count

    def wait(self, seconds):
        self.waits.append(seconds)


def test_transient_failure_does_not_disable_future_reminders(caplog):
    stop = StopAfter(3)
    calls = []

    def generate():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("Sensitive database connection information")
        return 1

    run_scheduler(stop, interval=60, generate=generate)
    assert len(calls) == 3
    assert stop.waits == [60, 60, 60]
    assert "RuntimeError" in caplog.text
    assert "Sensitive" not in caplog.text


def test_stopped_scheduler_does_not_generate():
    def unexpected():
        pytest.fail("A stopped scheduler must not generate notifications.")

    run_scheduler(StopAfter(0), generate=unexpected)


def test_nonpositive_interval_is_rejected():
    with pytest.raises(ValueError):
        run_scheduler(StopAfter(0), interval=0)
