"""Scheduler liveness diagnostics are intentionally unit-tested without a database."""
from __future__ import annotations

import threading

from app.services.campaign_scheduler import CampaignScheduler


class _Session:
    def close(self):
        pass


def test_scheduler_starts_and_performs_immediate_tick(monkeypatch):
    scheduler = CampaignScheduler(_Session, interval_seconds=60)
    ticked = threading.Event()
    monkeypatch.setattr(scheduler, "run_once", lambda _db: ticked.set())

    scheduler.start()
    try:
        assert ticked.wait(1), "scheduler did not perform its immediate first tick"
    finally:
        scheduler.stop()
    assert scheduler._thread is not None
    assert not scheduler._thread.is_alive()


def test_scheduler_continues_after_iteration_exception(monkeypatch):
    scheduler = CampaignScheduler(_Session, interval_seconds=0.01)
    attempts = []
    finished = threading.Event()

    def run_once(_db):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("test iteration failure")
        finished.set()

    monkeypatch.setattr(scheduler, "run_once", run_once)
    scheduler.start()
    try:
        assert finished.wait(1), "scheduler stopped after an iteration exception"
        assert len(attempts) >= 2
    finally:
        scheduler.stop()


def test_wake_restarts_a_stopped_worker(monkeypatch):
    scheduler = CampaignScheduler(_Session, interval_seconds=60)
    first_tick = threading.Event()
    monkeypatch.setattr(scheduler, "run_once", lambda _db: first_tick.set())

    scheduler.start()
    assert first_tick.wait(1)
    scheduler.stop()
    stopped_thread = scheduler._thread
    assert stopped_thread is not None and not stopped_thread.is_alive()

    first_tick.clear()
    scheduler.wake()
    try:
        assert scheduler._thread is not stopped_thread
        assert first_tick.wait(1), "wake did not restart the stopped worker"
    finally:
        scheduler.stop()
