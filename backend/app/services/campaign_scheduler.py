"""Database-backed campaign polling and recovery."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.enums import CampaignStatus
from app.services.campaign_execution import CampaignExecutionError, campaign_execution_service, recover_stale_contacts, recover_stale_slots

logger = logging.getLogger(__name__)


class CampaignScheduler:
    def run_once(self, db: Session) -> int:
        now = datetime.now(timezone.utc)
        recover_stale_contacts(db)
        recover_stale_slots(db)
        campaigns = db.scalars(select(Campaign).where(
            Campaign.status.in_([CampaignStatus.scheduled.value, CampaignStatus.running.value]),
            (Campaign.scheduled_at.is_(None) | (Campaign.scheduled_at <= now)),
        )).all()
        dispatched = 0
        for campaign in campaigns:
            if campaign.status == CampaignStatus.scheduled.value:
                campaign.status = CampaignStatus.running.value
                campaign.starts_at = campaign.starts_at or now
                db.commit()
            for _ in range(max(1, int(campaign.concurrency or 1))):
                try:
                    call = campaign_execution_service.dispatch_next_pending(db, campaign.id, campaign.tenant_id)
                except CampaignExecutionError as exc:
                    logger.warning("Campaign scheduler validation failure campaign_id=%s reason=%s", campaign.id, str(exc)[:160])
                    break
                except Exception:
                    db.rollback()
                    logger.exception("Campaign scheduler cycle failed campaign_id=%s", campaign.id)
                    break
                if call is None:
                    break
                dispatched += 1
                logger.info("Campaign scheduler dispatched campaign_id=%s campaign_contact_id=%s call_id=%s", campaign.id, call.campaign_contact_id, call.id)
        return dispatched


campaign_scheduler = CampaignScheduler()
