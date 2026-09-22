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
