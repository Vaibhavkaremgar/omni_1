"""Database-backed campaign polling and recovery."""
from __future__ import annotations

import logging
import os
import socket
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus, ContactStatus
from app.models.campaign_contact import CampaignContact
from app.services.campaign_execution import CampaignExecutionError, campaign_execution_service, recover_stale_contacts, recover_stale_slots
from app.core.config import get_settings

logger = logging.getLogger(__name__)


class CampaignScheduler:
    def __init__(self, session_factory=None, interval_seconds: int = 10):
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._tick_number = 0
        self._last_heartbeat: datetime | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.info("[SCHEDULER_START_SKIPPED] reason=already_running pid=%s thread=%s", os.getpid(), threading.get_ident())
            return
        if self.session_factory is None:
            logger.error("[SCHEDULER_START_SKIPPED] reason=no_session_factory pid=%s thread=%s", os.getpid(), threading.get_ident())
            raise RuntimeError("Campaign scheduler requires a database session factory.")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="campaign-scheduler", daemon=True)
        self._thread.start()
        logger.info("[SCHEDULER_START] pid=%s thread=%s interval=%s environment=%s timestamp=%s", os.getpid(), self._thread.ident, self.interval_seconds, get_settings().environment, datetime.now(timezone.utc).isoformat())
        logger.info("[SCHEDULER_RUNTIME] pid=%s thread=%s hostname=%s worker_configuration=process_local_thread", os.getpid(), self._thread.ident, socket.gethostname())

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("[SCHEDULER_STOP] pid=%s thread=%s timestamp=%s", os.getpid(), threading.get_ident(), datetime.now(timezone.utc).isoformat())

    def _run(self) -> None:
        first_tick = True
        while first_tick or not self._stop.wait(self.interval_seconds):
            first_tick = False
            try:
                db = self.session_factory()
                try:
                    self.run_once(db)
                finally:
                    db.close()
            except Exception:
                logger.exception("[SCHEDULER_FATAL_ITERATION_ERROR] pid=%s thread=%s tick=%s", os.getpid(), threading.get_ident(), self._tick_number)

    def run_once(self, db: Session) -> int:
        now = datetime.now(timezone.utc)
        self._tick_number += 1
        logger.info("[SCHEDULER_TICK] tick=%s pid=%s thread=%s timestamp=%s", self._tick_number, os.getpid(), threading.get_ident(), now.isoformat())
        if self._last_heartbeat is None or (now - self._last_heartbeat).total_seconds() >= 60:
            self._last_heartbeat = now
            logger.info("[SCHEDULER_HEARTBEAT] tick=%s pid=%s thread=%s timestamp=%s", self._tick_number, os.getpid(), threading.get_ident(), now.isoformat())
        recover_stale_contacts(db)
        recover_stale_slots(db)
        campaigns = db.scalars(select(Campaign).where(
            Campaign.status.in_([CampaignStatus.scheduled.value, CampaignStatus.running.value]),
            (Campaign.scheduled_at.is_(None) | (Campaign.scheduled_at <= now)),
        )).all()
        logger.info("[SCHEDULER_CAMPAIGN_SCAN] running_campaigns=%s campaign_ids=%s", len(campaigns), [str(c.id) for c in campaigns])
        dispatched = 0
        for campaign in campaigns:
            logger.info("[SCHEDULER_CAMPAIGN] campaign_id=%s tenant_id=%s status=%s employee_id=%s phone_number_id=%s schedule_type=%s calling_window=%s-%s current_time=%s timezone=%s concurrency=%s", campaign.id, campaign.tenant_id, campaign.status, campaign.employee_id, campaign.phone_number_id, (campaign.schedule_config or {}).get("type", "immediate"), campaign.calling_window_start, campaign.calling_window_end, now.isoformat(), campaign.timezone or "UTC", campaign.concurrency)
            if campaign.status == CampaignStatus.scheduled.value:
                campaign.status = CampaignStatus.running.value
                campaign.starts_at = campaign.starts_at or now
                db.commit()
            logger.info("[SCHEDULER_CAMPAIGN_ELIGIBLE] campaign_id=%s reason=status_and_schedule_due", campaign.id)
            for _ in range(max(1, int(campaign.concurrency or 1))):
                try:
                    contacts = db.scalars(select(CampaignContact).where(CampaignContact.campaign_id == campaign.id)).all()
                    counts = {status: sum(c.status == status for c in contacts) for status in (ContactStatus.pending.value, ContactStatus.retry_scheduled.value, ContactStatus.in_progress.value, ContactStatus.completed.value, ContactStatus.failed.value)}
                    logger.info("[SCHEDULER_CONTACT_SCAN] campaign_id=%s total_contacts=%s pending=%s retry_scheduled=%s in_progress=%s completed=%s failed=%s", campaign.id, len(contacts), counts[ContactStatus.pending.value], counts[ContactStatus.retry_scheduled.value], counts[ContactStatus.in_progress.value], counts[ContactStatus.completed.value], counts[ContactStatus.failed.value])
                    eligible = db.scalar(select(CampaignContact).where(
                        CampaignContact.campaign_id == campaign.id,
                        CampaignContact.status.in_([ContactStatus.pending.value, ContactStatus.retry_scheduled.value]),
                        (CampaignContact.retry_at.is_(None) | (CampaignContact.retry_at <= now)),
                    ))
                    if eligible is None:
                        logger.info("[SCHEDULER_NO_ELIGIBLE_CONTACT] campaign_id=%s reason=no_pending_or_due_retry", campaign.id)
                    else:
                        logger.info("[SCHEDULER_ELIGIBLE_CONTACT] campaign_id=%s contact_id=%s status=%s attempts=%s", campaign.id, eligible.id, eligible.status, eligible.attempt_count)
                    logger.info("[DISPATCH_NEXT_PENDING_START] campaign_id=%s tenant_id=%s", campaign.id, campaign.tenant_id)
                    call = campaign_execution_service.dispatch_next_pending(db, campaign.id, campaign.tenant_id)
                    logger.info("[DISPATCH_NEXT_PENDING_RESULT] campaign_id=%s result_type=%s contact_id=%s local_call_id=%s reason=%s", campaign.id, type(call).__name__ if call else "None", getattr(call, "campaign_contact_id", None), getattr(call, "id", None), "not_dispatched" if call is None else "dispatched")
                except CampaignExecutionError as exc:
                    logger.warning("[DISPATCH_NEXT_PENDING_EXCEPTION] campaign_id=%s exception_type=%s reason=%s", campaign.id, type(exc).__name__, str(exc)[:500])
                    break
                except Exception:
                    db.rollback()
                    logger.exception("[SCHEDULER_EXCEPTION] campaign_id=%s", campaign.id)
                    break
                if call is None:
                    logger.info("Campaign scheduler contact skipped or no slot available campaign_id=%s", campaign.id)
                    break
                dispatched += 1
                logger.info("[SCHEDULER_DISPATCHED] campaign_id=%s contact_id=%s call_id=%s", campaign.id, call.campaign_contact_id, call.id)
            db.refresh(campaign)
            if campaign.status == CampaignStatus.completed.value:
                logger.info("[CAMPAIGN_COMPLETED] campaign_id=%s", campaign.id)
            elif campaign.status in {CampaignStatus.paused.value, CampaignStatus.cancelled.value}:
                logger.info("Campaign scheduler campaign paused_or_cancelled campaign_id=%s status=%s", campaign.id, campaign.status)
        logger.info("Campaign scheduler tick finished dispatched=%s", dispatched)
        return dispatched


from app.db.session import SessionLocal

campaign_scheduler = CampaignScheduler(SessionLocal)
