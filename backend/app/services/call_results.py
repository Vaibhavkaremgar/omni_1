from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.integrations.omnidimension.webhooks import TERMINAL_STATUSES, parse_post_call
from app.models.call import Call
from app.models.campaign_contact import CampaignContact
from app.models.enums import CallStatus, ContactStatus
from app.services.integration_dispatcher import IntegrationDispatcher
from app.services.call_analysis import CallAnalysisService
from app.services.usage_billing import charge_completed_call
from app.services.wallets import WalletError
from app.services.voice_latency import NOT_OBSERVABLE, measure_provider_timing, utc_now_iso
from app.services.campaign_execution import release_campaign_slot, next_calling_window_time
from app.integrations.omnidimension.calls import OmniDimensionCallProvider

logger = logging.getLogger(__name__)


class CallResultService:
    def __init__(self) -> None:
        self._dispatcher = IntegrationDispatcher()

    def process_post_call(self, db: Session, payload: dict) -> Call | None:
        webhook_received_at = utc_now_iso()
        event = parse_post_call(payload)
        call = self._find_call(db, event)
        if call is None:
            logger.warning("Call termination event did not match a local call provider_call_id=%s status=%s", event.get("provider_call_id"), event.get("provider_status"))
            return None
        if event["metadata_tenant_id"] and str(call.tenant_id) != str(event["metadata_tenant_id"]):
            logger.warning("Ignored cross-tenant call event local_call_id=%s provider_call_id=%s", call.id, event.get("provider_call_id"))
            return None
        was_terminal = call.status in TERMINAL_STATUSES

        if event.get("status") in TERMINAL_STATUSES:
            logger.info(
                "Call terminated local_call_id=%s provider_call_id=%s termination_source=%s "
                "employee_id=%s employee_version_id=%s provider_agent_id=%s "
                "provider_end_reason=%s final_status=%s",
                call.id, event.get("provider_call_id") or call.provider_call_id,
                call.employee_id, call.employee_version_id,
                (call.dispatch_metadata or {}).get("provider_agent_id"),
                event.get("termination_source") or "provider_webhook",
                event.get("termination_reason") or "<not_provided>", event.get("provider_status"),
            )

        if event["provider_call_id"]:
            call.provider_call_id = call.provider_call_id or event["provider_call_id"]
            logger.info(
                "[OMNI_CALL_CORRELATION] local_call_id=%s provider_request_id=%s provider_call_id=%s",
                call.id, event.get("provider_request_id") or (call.dispatch_metadata or {}).get("provider_request_id") or "unknown",
                call.provider_call_id,
            )
        if event["status"]:
            call.status = event["status"]
        for field in ("duration_seconds", "transcript", "summary", "recording_url", "sentiment", "outcome", "customer_intent", "key_points", "action_items", "follow_up_required", "follow_up_notes", "started_at"):
            if event[field] is not None:
                setattr(call, field, event[field])
        if event.get("transcript_data") is not None:
            call.transcript_data = event["transcript_data"]
        if isinstance(event["extracted_attributes"], dict):
            call.extracted_attributes = event["extracted_attributes"]
        if event["ended_at"] is not None:
            call.ended_at = event["ended_at"]
        elif event["status"] in TERMINAL_STATUSES:
            call.ended_at = call.ended_at or utc_now()
        if event["status"] in TERMINAL_STATUSES:
            call.completed_at = call.completed_at or call.ended_at or utc_now()
            if call.campaign_id:
                release_campaign_slot(db, call_id=call.id)
        call.raw_payload = payload
        provider_latency = OmniDimensionCallProvider.normalize_provider_latency(payload)
        call.dispatch_metadata = {
            **(call.dispatch_metadata or {}),
            "provider_status": event["provider_status"],
            "provider_request_id": event.get("provider_request_id") or (call.dispatch_metadata or {}).get("provider_request_id"),
            "post_call_webhook_processed": True,
            "voice_latency": measure_provider_timing(payload, webhook_received_at),
            "provider_latency": provider_latency or {
                "source": "omnidimension_call_logs",
                "measurement_type": "provider_reported",
                "status": "unavailable",
            },
        }
        terminal_reason = str(event.get("termination_reason") or "").casefold().replace("-", "_").replace(" ", "_")
        terminal_outcome = str(event.get("outcome") or "").casefold().replace("-", "_").replace(" ", "_")
        non_chargeable_outcome = terminal_reason in {"wrong_number", "wrongnumber", "do_not_call", "donotcall", "dnc"} or terminal_outcome in {"wrong_number", "wrongnumber", "do_not_call", "donotcall", "dnc"}
        if call.status == CallStatus.completed.value and call.duration_seconds is not None and not non_chargeable_outcome:
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
        if call.campaign_contact_id and event["status"] in TERMINAL_STATUSES and not was_terminal:
            contact = db.get(CampaignContact, call.campaign_contact_id)
            if contact is not None and contact.campaign_id == call.campaign_id:
                if event["status"] == CallStatus.completed.value:
                    reason = str(event.get("termination_reason") or "").casefold().replace("-", "_").replace(" ", "_")
                    outcome = str(event.get("outcome") or "").casefold().replace("-", "_").replace(" ", "_")
                    if reason in {"wrong_number", "wrongnumber"} or outcome in {"wrong_number", "wrongnumber"}:
                        contact.status = ContactStatus.failed.value
                        contact.completed_at = utc_now()
                    elif reason in {"do_not_call", "donotcall", "dnc"} or outcome in {"do_not_call", "donotcall", "dnc"}:
                        contact.status = ContactStatus.do_not_call.value
                        contact.completed_at = utc_now()
                    else:
                        contact.status = ContactStatus.completed.value
                        contact.completed_at = utc_now()
                    contact.lease_token = None
                elif event["status"] in {
                    CallStatus.failed.value,
                    CallStatus.no_answer.value,
                    CallStatus.busy.value,
                    CallStatus.canceled.value,
                }:
                    retryable = event["status"] in {CallStatus.no_answer.value, CallStatus.busy.value, CallStatus.failed.value}
                    campaign = contact.campaign
                    # Older campaigns/databases may have NULL until the additive
                    # migration runs; treat that safely as the legacy enabled behavior.
                    retry_enabled = campaign.retry_enabled is not False if campaign is not None else True
                    retry_intervals = list(campaign.retry_intervals or [2]) if campaign is not None else [2]
                    retry_index = max(0, contact.attempt_count - 1)
                    if retryable and retry_enabled and contact.attempt_count < (campaign.max_attempts if campaign else 3) and retry_index < len(retry_intervals):
                        contact.status = ContactStatus.retry_scheduled.value
                        contact.retry_at = utc_now() + timedelta(minutes=max(1, int(retry_intervals[retry_index])))
                    else:
                        contact.status = ContactStatus.no_answer.value if event["status"] == CallStatus.no_answer.value else ContactStatus.busy.value if event["status"] == CallStatus.busy.value else ContactStatus.failed.value
                    contact.completed_at = utc_now() if contact.status not in {ContactStatus.retry_scheduled.value} else None
                    contact.lease_token = None
                elif event["status"] == CallStatus.voicemail.value:
                    contact.status = ContactStatus.called.value
                outcome = str(event.get("outcome") or "").casefold().replace(" ", "_")
                if outcome in {"callback_requested", "callback_request"} and event.get("callback_at") is not None:
                    contact.status = ContactStatus.retry_scheduled.value
                    contact.callback_at = event["callback_at"]
                    campaign = contact.campaign
                    contact.retry_at = next_calling_window_time(campaign, event["callback_at"])
                    contact.completed_at = None
                    contact.lease_token = None
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
        if event.get("status") in {CallStatus.ringing.value, CallStatus.in_progress.value}:
            logger.info("[CALL_LIFECYCLE_PROVIDER_STARTED] local_call_id=%s provider_request_id=%s provider_call_id=%s provider_event_type=%s event_timestamp=%s", call.id, (call.dispatch_metadata or {}).get("provider_request_id") or "unknown", call.provider_call_id or "unknown", event.get("event_type") or "unknown", event.get("event_timestamp") or "unknown")
        latency = (call.dispatch_metadata or {}).get("voice_latency") or {}
        logger.info(
            "[CALL_VOICE_LATENCY] local_call_id=%s provider_call_id=%s timestamps=%s durations=%s "
            "speech_end=%s stt_final=%s llm_start=%s llm_first_token=%s llm_complete=%s "
            "tts_start=%s tts_first_audio=%s response_complete=%s",
            call.id, call.provider_call_id or "unknown", latency.get("timestamps", {}), latency.get("durations", {}),
            *(latency.get("timestamps", {}).get(key) or NOT_OBSERVABLE for key in (
                "speech_end", "stt_final", "llm_start", "llm_first_token", "llm_complete",
                "tts_start", "tts_first_audio", "response_complete",
            )),
        )
        logger.info("[CALL_LIFECYCLE_PROVIDER_EVENT] local_call_id=%s provider_request_id=%s provider_call_id=%s provider_event_type=%s provider_status=%s termination_source=%s termination_reason=%s event_timestamp=%s", call.id, (call.dispatch_metadata or {}).get("provider_request_id") or "unknown", call.provider_call_id or "unknown", event.get("event_type") or "unknown", event.get("provider_status") or "unknown", event.get("termination_source") or "unknown", event.get("termination_reason") or "unknown", event.get("event_timestamp") or "unknown")
        if event.get("status") in TERMINAL_STATUSES:
            turns = event.get("transcript_data") if isinstance(event.get("transcript_data"), list) else []
            user_spoke = any(isinstance(turn, dict) and turn.get("speaker") == "customer" for turn in turns)
            agent_spoke = any(isinstance(turn, dict) and turn.get("speaker") == "assistant" for turn in turns)
            reason_text = str(event.get("termination_reason") or "").casefold()
            logger.info(
                "[CALL_FINAL_DIAGNOSTIC] local_call_id=%s employee_id=%s employee_version_id=%s "
                "provider_agent_id=%s provider_call_id=%s provider_request_id=%s started_at=%s ended_at=%s "
                "duration_seconds=%s final_local_status=%s provider_status=%s termination_source=%s "
                "termination_reason=%s last_event_type=%s last_event_timestamp=%s user_spoke=%s "
                "agent_spoke=%s end_call_event_received=%s backend_termination_attempted=%s "
                "timeout_triggered=%s exception_occurred=%s",
                call.id, call.employee_id, call.employee_version_id,
                (call.dispatch_metadata or {}).get("provider_agent_id"), call.provider_call_id,
                (call.dispatch_metadata or {}).get("provider_request_id"), call.started_at, call.ended_at,
                call.duration_seconds, call.status, event.get("provider_status"),
                event.get("termination_source") or "unknown", event.get("termination_reason") or "unknown",
                event.get("event_type") or "post_call", event.get("event_timestamp") or call.ended_at,
                user_spoke, agent_spoke, str(event.get("event_type") or "").casefold() == "end_call",
                False, "timeout" in reason_text or "idle" in reason_text, False,
            )
            logger.info("[CALL_LIFECYCLE_TERMINAL] local_call_id=%s provider_request_id=%s provider_call_id=%s terminal_event_at=%s terminal_status=%s termination_source=%s termination_reason=%s", call.id, (call.dispatch_metadata or {}).get("provider_request_id") or "unknown", call.provider_call_id or "unknown", event.get("event_timestamp") or call.ended_at or "unknown", call.status, event.get("termination_source") or "unknown", event.get("termination_reason") or "unknown")
            logger.info("[CALL_LIFECYCLE_FINAL] local_call_id=%s provider_request_id=%s provider_call_id=%s terminal_status=%s termination_source=%s termination_reason=%s", call.id, (call.dispatch_metadata or {}).get("provider_request_id") or "unknown", call.provider_call_id or "unknown", call.status, event.get("termination_source") or "unknown", event.get("termination_reason") or "unknown")
        if call.transcript and call.analysis_status not in {"pending", "running", "completed"}:
            call.analysis_status = "pending"
            db.commit()
            CallAnalysisService.schedule(call.id)
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
        provider_request_id = event.get("provider_request_id")
        if provider_request_id:
            return db.scalar(select(Call).where(
                Call.dispatch_metadata["provider_request_id"].as_string() == str(provider_request_id)
            ))
        return None
