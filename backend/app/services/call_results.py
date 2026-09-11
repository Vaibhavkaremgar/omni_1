from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.integrations.omnidimension.webhooks import TERMINAL_STATUSES, parse_post_call
from app.models.call import Call
from app.models.campaign_contact import CampaignContact
from app.models.enums import CallStatus, ContactStatus
from app.services.integration_dispatcher import IntegrationDispatcher
from app.services.usage_billing import charge_completed_call
from app.services.wallets import WalletError


class CallResultService:
    def __init__(self) -> None:
        self._dispatcher = IntegrationDispatcher()

    def process_post_call(self, db: Session, payload: dict) -> Call | None:
        event = parse_post_call(payload)
        call = self._find_call(db, event)
        if call is None:
            return None
        if event["metadata_tenant_id"] and str(call.tenant_id) != str(event["metadata_tenant_id"]):
            return None

        if event["provider_call_id"]:
            call.provider_call_id = call.provider_call_id or event["provider_call_id"]
        if event["status"]:
            call.status = event["status"]
        for field in ("duration_seconds", "transcript", "summary", "recording_url", "sentiment"):
            if event[field] is not None:
                setattr(call, field, event[field])
        if isinstance(event["extracted_attributes"], dict):
            call.extracted_attributes = event["extracted_attributes"]
        if event["ended_at"] is not None:
            call.ended_at = event["ended_at"]
        elif event["status"] in TERMINAL_STATUSES:
            call.ended_at = call.ended_at or utc_now()
        call.dispatch_metadata = {
            **(call.dispatch_metadata or {}),
            "provider_status": event["provider_status"],
            "post_call_webhook_processed": True,
        }
        if call.status == CallStatus.completed.value and call.duration_seconds is not None:
            metadata = {**(call.dispatch_metadata or {})}
            if not metadata.get("billing_attempted"):
                metadata["billing_attempted"] = True
                try:
                    with db.begin_nested():
                        charge_completed_call(db, call)
                except WalletError as exc:
                    metadata["billing_error"] = str(exc)
                except Exception:
                    metadata["billing_error"] = "Unable to record call usage."
                call.dispatch_metadata = metadata
        # Update campaign contact progress
        if call.campaign_contact_id and event["status"] in TERMINAL_STATUSES:
            contact = db.get(CampaignContact, call.campaign_contact_id)
            if contact is not None and contact.campaign_id == call.campaign_id:
                if event["status"] == CallStatus.completed.value:
                    contact.status = ContactStatus.completed.value
                elif event["status"] in {
                    CallStatus.failed.value,
                    CallStatus.no_answer.value,
                    CallStatus.busy.value,
                    CallStatus.canceled.value,
                }:
                    contact.status = ContactStatus.failed.value
                elif event["status"] == CallStatus.voicemail.value:
                    contact.status = ContactStatus.called.value
                contact.last_call_id = call.id
                # Check if campaign is now complete
                if call.campaign_id:
                    from app.services.campaign_execution import _update_campaign_progress
                    from app.models.campaign import Campaign
                    campaign = db.get(Campaign, call.campaign_id)
                    if campaign is not None:
                        _update_campaign_progress(db, campaign)
        db.commit()
        db.refresh(call)
        # Dispatch to connected integrations after commit — never blocks webhook response.
        try:
            self._dispatcher.dispatch(db, call)
        except Exception:
            pass
        return call

    @staticmethod
    def _find_call(db: Session, event: dict) -> Call | None:
        local_call_id = event.get("local_call_id")
        if local_call_id:
            try:
                call = db.get(Call, UUID(str(local_call_id)))
            except ValueError:
                call = None
            if call is not None:
                return call
        provider_call_id = event.get("provider_call_id")
        if provider_call_id:
            return db.scalar(select(Call).where(Call.provider_call_id == provider_call_id))
        return None
