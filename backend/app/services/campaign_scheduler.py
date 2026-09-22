"""Database-backed campaign polling and recovery."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus, ContactStatus
from app.models.campaign_contact import CampaignContact
from app.services.campaign_execution import CampaignExecutionError, campaign_execution_service, recover_stale_contacts, recover_stale_slots

logger = logging.getLogger(__name__)


class CampaignScheduler:
    def __init__(self, session_factory=None, interval_seconds: int = 10):
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            logger.info("Campaign scheduler already running")
            return
        if self.session_factory is None:
            raise RuntimeError("Campaign scheduler requires a database session factory.")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="campaign-scheduler", daemon=True)
        self._thread.start()
        logger.info("[SCHEDULER_START] interval_seconds=%s", self.interval_seconds)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Campaign scheduler stopped")

    def _run(self) -> None:
        first_tick = True
        while first_tick or not self._stop.wait(self.interval_seconds):
            first_tick = False
            db = self.session_factory()
            try:
                self.run_once(db)
            except Exception:
                db.rollback()
                logger.exception("Campaign scheduler exception")
            finally:
                db.close()

    def run_once(self, db: Session) -> int:
        now = datetime.now(timezone.utc)
        logger.info("[SCHEDULER_TICK] at=%s", now.isoformat())
        recover_stale_contacts(db)
        recover_stale_slots(db)
        campaigns = db.scalars(select(Campaign).where(
            Campaign.status.in_([CampaignStatus.scheduled.value, CampaignStatus.running.value]),
            (Campaign.scheduled_at.is_(None) | (Campaign.scheduled_at <= now)),
        )).all()
        logger.info("[SCHEDULER_CAMPAIGNS] count=%s ids=%s", len(campaigns), [str(c.id) for c in campaigns])
        dispatched = 0
        for campaign in campaigns:
            logger.info("Campaign scheduler processing campaign_id=%s status=%s concurrency=%s", campaign.id, campaign.status, campaign.concurrency)
            if campaign.status == CampaignStatus.scheduled.value:
                campaign.status = CampaignStatus.running.value
                campaign.starts_at = campaign.starts_at or now
                db.commit()
            for _ in range(max(1, int(campaign.concurrency or 1))):
                try:
                    eligible = db.scalar(select(CampaignContact.id).where(
                        CampaignContact.campaign_id == campaign.id,
                        CampaignContact.status.in_([ContactStatus.pending.value, ContactStatus.retry_scheduled.value]),
                    ))
                    if eligible is None:
                        logger.info("Campaign scheduler no eligible contacts campaign_id=%s", campaign.id)
                    else:
                        logger.info("[SCHEDULER_ELIGIBLE_CONTACT] campaign_id=%s contact_id=%s", campaign.id, eligible)
                    call = campaign_execution_service.dispatch_next_pending(db, campaign.id, campaign.tenant_id)
                except CampaignExecutionError as exc:
                    logger.warning("Campaign scheduler validation failure campaign_id=%s reason=%s", campaign.id, str(exc)[:160])
                    break
                except Exception:
                    db.rollback()
                    logger.exception("Campaign scheduler exception campaign_id=%s", campaign.id)
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
