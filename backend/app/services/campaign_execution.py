"""Campaign Execution Engine.

Shared dispatch core used by campaign lifecycle operations.
InstantCallService dispatches through the same OmniDimensionCallProvider;
this service handles the campaign-specific orchestration layer.

Lifecycle:
  draft  ──start──►  running  ──pause──►  paused  ──resume──►  running
  running ──stop──►  stopped
  running ──(all contacts terminal)──►  completed
  running ──(provider error, no contacts left)──►  failed
  paused  ──stop──►  stopped
  failed/stopped ──retry──►  running  (resets failed contacts to pending)
"""
from __future__ import annotations

import logging
from datetime import datetime, time as dt_time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.integrations.omnidimension import (
    OmniDimensionCallProvider,
    OmniDimensionClient,
    normalize_omni_call_status,
)
from app.integrations.omnidimension.exceptions import OmniDimensionError
from app.models.ai_employee import AIEmployee
from app.models.call import Call
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.campaign_execution_slot import CampaignExecutionSlot
from app.models.enums import (
    CallDirection,
    CallStatus,
    CampaignStatus,
    ContactStatus,
    EmployeeStatus,
    NumberStatus,
)
from app.models.phone_number import PhoneNumber
from app.core.config import get_settings
from app.services.wallets import InsufficientBalanceError, require_minimum_balance
from app.services.phone_numbers import PhoneNumberService

logger = logging.getLogger(__name__)


def _mask_phone(value: str) -> str:
    return value[:3] + "***" + value[-2:] if len(value) > 5 else "***"


# ── Errors ────────────────────────────────────────────────────────────────────

class CampaignExecutionError(Exception):
    """Safe validation failure — surface to the API layer as 422."""


class CampaignNotFoundError(CampaignExecutionError):
    pass


class CampaignStateError(CampaignExecutionError):
    """Invalid state transition."""


# ── Validation helpers ────────────────────────────────────────────────────────

def _require_campaign(db: Session, campaign_id: UUID, tenant_id: UUID) -> Campaign:
    campaign = db.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.tenant_id == tenant_id,
        )
    )
    if campaign is None:
        raise CampaignNotFoundError("Campaign not found.")
    return campaign


def _require_published_employee(db: Session, employee_id: UUID, tenant_id: UUID) -> AIEmployee:
    employee = db.scalar(
        select(AIEmployee).where(
            AIEmployee.id == employee_id,
            AIEmployee.tenant_id == tenant_id,
        )
    )
    if employee is None:
        raise CampaignExecutionError("The campaign's employee no longer exists.")
    if employee.status != EmployeeStatus.published.value or employee.published_version is None:
        raise CampaignExecutionError(
            "The selected employee must be published before the campaign can make calls."
        )
    if not employee.published_version.provider_agent_id:
        raise CampaignExecutionError(
            "The employee is published but has not been deployed to the call provider. "
            "Please re-publish the employee."
        )
    return employee


def _require_usable_phone(db: Session, phone_number_id: UUID, tenant_id: UUID) -> PhoneNumber:
    phone = db.get(PhoneNumber, phone_number_id)
    if phone is None or not PhoneNumberService.can_use(db, phone, tenant_id):
        raise CampaignExecutionError("Phone number not found.")
    if phone.status != NumberStatus.active.value or not phone.provider_phone_number_id:
        raise CampaignExecutionError("The selected phone number is not available for calling.")
    return phone


def _parse_provider_ids(employee: AIEmployee, phone: PhoneNumber) -> tuple[int, int]:
    try:
        agent_id = int(employee.published_version.provider_agent_id)
        from_number_id = int(phone.provider_phone_number_id)
    except (TypeError, ValueError) as exc:
        raise CampaignExecutionError(
            "Invalid provider identifier on employee or phone number."
        ) from exc
    return agent_id, from_number_id


# ── Progress helper ───────────────────────────────────────────────────────────

TERMINAL_CONTACT_STATUSES = {
    ContactStatus.called.value,
    ContactStatus.completed.value,
    ContactStatus.no_answer.value,
    ContactStatus.busy.value,
    ContactStatus.failed.value,
    ContactStatus.do_not_call.value,
    ContactStatus.skipped.value,
    ContactStatus.cancelled.value,
    ContactStatus.voicemail.value,
    ContactStatus.timed_out.value,
}

ACTIVE_CONTACT_STATUSES = {
    ContactStatus.claimed.value,
    ContactStatus.dispatching.value,
    ContactStatus.queued.value,
    ContactStatus.calling.value,
    ContactStatus.in_progress.value,
}

ACTIVE_CALL_STATUSES = {
    CallStatus.queued.value,
    CallStatus.ringing.value,
    CallStatus.in_progress.value,
}

TERMINAL_CALL_STATUSES = {
    CallStatus.completed.value,
    CallStatus.failed.value,
    CallStatus.no_answer.value,
    CallStatus.busy.value,
    CallStatus.voicemail.value,
    CallStatus.canceled.value,
    CallStatus.skipped.value,
    CallStatus.timed_out.value,
}


def _update_campaign_progress(db: Session, campaign: Campaign) -> None:
    """Complete only when every contact is terminal and no call/slot is active."""
    if campaign.status not in {CampaignStatus.running.value, CampaignStatus.paused.value, CampaignStatus.paused_credits.value}:
        return
    contacts = db.scalars(
        select(CampaignContact).where(CampaignContact.campaign_id == campaign.id)
    ).all()
    if not contacts:
        return
    active_call = db.scalar(select(Call.id).where(
        Call.campaign_id == campaign.id,
        Call.status.in_(ACTIVE_CALL_STATUSES),
    ))
    active_slot = db.scalar(select(CampaignExecutionSlot.id).where(
        CampaignExecutionSlot.campaign_id == campaign.id,
        CampaignExecutionSlot.released_at.is_(None),
    ))
    if (
        all(c.status in TERMINAL_CONTACT_STATUSES for c in contacts)
        and active_call is None
        and active_slot is None
    ):
        campaign.status = CampaignStatus.completed.value
        campaign.ends_at = utc_now()


def _within_calling_window(campaign: Campaign) -> bool:
    """Return whether new calls may start right now in the campaign timezone."""
    if not campaign.calling_window_start or not campaign.calling_window_end:
        return True
    try:
        zone = ZoneInfo(campaign.timezone or "UTC")
        now = datetime.now(zone).time()
        start = dt_time.fromisoformat(campaign.calling_window_start)
        end = dt_time.fromisoformat(campaign.calling_window_end)
        return start <= now <= end if start <= end else now >= start or now <= end
    except (ValueError, KeyError):
        return False


def next_calling_window_time(campaign: Campaign, requested_at: datetime) -> datetime:
    """Return the first instant at/after requested_at inside the campaign window."""
    if not campaign.calling_window_start or not campaign.calling_window_end:
        return requested_at
    zone = ZoneInfo(campaign.timezone or "UTC")
    local = requested_at.astimezone(zone)
    start = dt_time.fromisoformat(campaign.calling_window_start)
    end = dt_time.fromisoformat(campaign.calling_window_end)
    if start <= end:
        if start <= local.time() <= end:
            return requested_at
        day = local.date() if local.time() < start else local.date() + timedelta(days=1)
        return datetime.combine(day, start, tzinfo=zone).astimezone(requested_at.tzinfo)
    # Overnight windows: only shift values in the closed daytime gap.
    if local.time() >= start or local.time() <= end:
        return requested_at
    return datetime.combine(local.date() + timedelta(days=1), start, tzinfo=zone).astimezone(requested_at.tzinfo)


def recover_stale_contacts(db: Session, *, lease_seconds: int = 900) -> int:
    """Recover only pre-dispatch claims; active provider calls are polled safely."""
    cutoff = utc_now() - timedelta(seconds=lease_seconds)
    rows = db.scalars(select(CampaignContact).where(
        CampaignContact.status == ContactStatus.dispatching.value,
        CampaignContact.claimed_at < cutoff,
    )).all()
    for contact in rows:
        active_call = db.scalar(select(Call).where(
            Call.campaign_contact_id == contact.id,
            Call.status.in_(ACTIVE_CALL_STATUSES),
        ).order_by(Call.created_at.desc()))
        if active_call is not None or contact.provider_bulk_call_id or contact.provider_request_id or contact.provider_call_id:
            contact.status = ContactStatus.in_progress.value
            contact.polling_started_at = contact.polling_started_at or contact.attempt_started_at or contact.claimed_at
            continue
        contact.status = ContactStatus.retry_scheduled.value if contact.attempt_count < (contact.campaign.max_attempts if contact.campaign else 3) else ContactStatus.failed.value
        contact.retry_at = utc_now() if contact.status == ContactStatus.retry_scheduled.value else None
        contact.lease_token = None
        contact.error_message = "Recovered after an abandoned worker lease."
    if rows:
        db.commit()
    return len(rows)


def recover_stale_slots(db: Session, *, lease_seconds: int = 900) -> int:
    """Release abandoned/terminal slots without guessing that an active call ended."""
    rows = db.scalars(select(CampaignExecutionSlot).where(
        CampaignExecutionSlot.released_at.is_(None),
    )).all()
    released = 0
    for slot in rows:
        call = db.get(Call, slot.call_id) if slot.call_id else None
        contact = db.get(CampaignContact, slot.campaign_contact_id) if slot.campaign_contact_id else None
        terminal = bool(call and call.status in TERMINAL_CALL_STATUSES) or bool(
            contact and contact.status in TERMINAL_CONTACT_STATUSES
        )
        abandoned = _elapsed_seconds(slot.reserved_at, utc_now()) >= lease_seconds and call is None and (
            contact is None or contact.status not in ACTIVE_CONTACT_STATUSES
        )
        if terminal or abandoned:
            slot.released_at = utc_now()
            released += 1
    if released:
        db.commit()
    return released


def reserve_campaign_slot(db: Session, campaign: Campaign, contact: CampaignContact) -> CampaignExecutionSlot | None:
    recover_stale_slots(db)
    for number in range(max(1, int(campaign.concurrency or 1))):
        token = uuid4().hex
        changed = db.execute(update(CampaignExecutionSlot).where(
            CampaignExecutionSlot.campaign_id == campaign.id,
            CampaignExecutionSlot.slot_number == number,
            CampaignExecutionSlot.released_at.is_not(None),
        ).values(
            campaign_contact_id=contact.id,
            call_id=None,
            lease_token=token,
            reserved_at=utc_now(),
            released_at=None,
        )).rowcount
        if changed == 1:
            db.commit()
            return db.scalar(select(CampaignExecutionSlot).where(CampaignExecutionSlot.lease_token == token))
        slot = CampaignExecutionSlot(
            tenant_id=campaign.tenant_id,
            campaign_id=campaign.id,
            campaign_contact_id=contact.id,
            slot_number=number,
            lease_token=uuid4().hex,
            reserved_at=utc_now(),
        )
        try:
            with db.begin_nested():
                db.add(slot)
                db.flush()
            return slot
        except IntegrityError:
            continue
    return None


def release_campaign_slot(db: Session, *, call_id: UUID | None = None, contact_id: UUID | None = None) -> int:
    query = select(CampaignExecutionSlot).where(CampaignExecutionSlot.released_at.is_(None))
    query = query.where(CampaignExecutionSlot.call_id == call_id) if call_id is not None else query.where(CampaignExecutionSlot.campaign_contact_id == contact_id)
    rows = db.scalars(query).all()
    for slot in rows:
        slot.released_at = utc_now()
    if rows:
        db.commit()
    return len(rows)


# ── Core dispatch (shared with InstantCallService boundary) ───────────────────

def dispatch_single_contact(
    db: Session,
    campaign: Campaign,
    contact: CampaignContact,
    employee: AIEmployee,
    phone: PhoneNumber,
    agent_id: int,
    from_number_id: int,
    slot: CampaignExecutionSlot | None = None,
) -> Call:
    """
    Create a Call record and dispatch it to OmniDimension.
    Idempotent: if a queued/in-progress call already exists for this contact
    in this campaign, returns it without re-dispatching.
    """
    # Claim is a single conditional UPDATE. This is the cross-worker idempotency
    # boundary; a worker which loses the race must never reach the provider.
    lease_token = uuid4().hex
    previous_status = contact.status
    claimed = db.execute(
        update(CampaignContact)
        .where(CampaignContact.id == contact.id, CampaignContact.status.in_([ContactStatus.pending.value, ContactStatus.retry_scheduled.value]))
        .values(status=ContactStatus.dispatching.value, attempt_count=(CampaignContact.attempt_count + 1), last_called_at=utc_now(), claimed_at=utc_now(), attempt_started_at=utc_now(), retry_at=None, lease_token=lease_token)
    ).rowcount
    try:
        db.commit()
    except Exception:
        logger.exception("[CONTACT_CLAIM_FAILED] campaign_id=%s contact_id=%s reason=database_commit_failed", campaign.id, contact.id)
        raise
    if claimed != 1:
        logger.info("[CONTACT_CLAIM_SKIPPED] campaign_id=%s contact_id=%s reason=already_claimed", campaign.id, contact.id)
        existing = db.scalar(select(Call).where(Call.campaign_contact_id == contact.id).order_by(Call.created_at.desc()))
        if existing is not None:
            return existing
        raise CampaignExecutionError("Contact is already claimed by another worker.")
    logger.info("[CONTACT_CLAIMED] [CAMPAIGN_CONTACT_CLAIMED] campaign_id=%s contact_id=%s previous_status=%s new_status=%s attempt_number=%s", campaign.id, contact.id, previous_status, ContactStatus.dispatching.value, (contact.attempt_count or 0) + 1)

    # Duplicate guard — an accepted or active call is never dispatched again.
    existing = db.scalar(
        select(Call).where(
            Call.campaign_contact_id == contact.id,
            Call.status.in_([CallStatus.queued.value, CallStatus.in_progress.value]),
        )
    )
    if existing is not None:
        logger.info("[CONTACT_CLAIM_SKIPPED] campaign_id=%s contact_id=%s reason=existing_active_call local_call_id=%s", campaign.id, contact.id, existing.id)
        return existing

    call = Call(
        tenant_id=campaign.tenant_id,
        employee_id=employee.id,
        employee_version_id=employee.published_version.id,
        campaign_id=campaign.id,
        campaign_contact_id=contact.id,
        phone_number_id=phone.id,
        direction=CallDirection.outbound.value,
        status=CallStatus.queued.value,
        customer_name=(
            f"{contact.first_name or ''} {contact.last_name or ''}".strip() or None
        ),
        customer_phone_number=contact.phone_number,
        dispatch_metadata={
            "source": "campaign",
            "campaign_id": str(campaign.id),
            "employee_id": str(employee.id),
            "employee_version_id": str(employee.published_version.id),
            "provider_agent_id": employee.published_version.provider_agent_id,
        },
    )
    try:
        db.add(call)
        db.flush()
    except Exception:
        logger.exception("[CALL_CREATE_FAILED] campaign_id=%s contact_id=%s", campaign.id, contact.id)
        raise
    if slot is not None:
        slot.call_id = call.id
    contact.status = ContactStatus.in_progress.value
    db.commit()
    db.refresh(call)
    logger.info("[CALL_CREATED] campaign_id=%s contact_id=%s call_id=%s employee_id=%s provider_agent_id=%s masked_phone=%s provider_phone_id=%s", campaign.id, contact.id, call.id, employee.id, agent_id, _mask_phone(contact.phone_number), from_number_id)
    logger.info("[CAMPAIGN_CALL_CREATED] campaign_id=%s contact_id=%s local_call_id=%s employee_id=%s provider_agent_id=%s provider_phone_number_id=%s attempt_number=%s masked_destination=%s", campaign.id, contact.id, call.id, employee.id, agent_id, from_number_id, contact.attempt_count, _mask_phone(contact.phone_number))

    call_context: dict[str, str] = {str(k): str(v) for k, v in (contact.customer_data or {}).items()}
    canonical_name = f"{contact.first_name or ''} {contact.last_name or ''}".strip()
    if canonical_name:
        call_context["name"] = canonical_name
    # Outbound agents ask the caller for their name first; do not pre-seed
    # customer_name with a contact or employee display name.
    metadata = {
        "local_call_id": str(call.id),
        "campaign_id": str(campaign.id),
        "campaign_contact_id": str(contact.id),
        "contact_id": str(contact.id),
        "tenant_id": str(campaign.tenant_id),
        "employee_id": str(employee.id),
    }

    # Safe correlation diagnostics: expose context keys and only a masked
    # destination so dispatch issues can be traced without logging secrets or
    # full customer phone numbers.
    masked_number = contact.phone_number[:3] + "***" + contact.phone_number[-2:] if len(contact.phone_number) > 5 else "***"
    if not call_context.get("name"):
        logger.warning("[OMNI_CONTEXT_NAME_MISSING] campaign_id=%s contact_id=%s local_call_id=%s", campaign.id, contact.id, call.id)
    logger.info(
        "[OMNI_DISPATCH_REQUEST] campaign_id=%s contact_id=%s local_call_id=%s employee_id=%s "
        "provider_agent_id=%s masked_to_number=%s from_number_id=%s context_keys=%s request_timestamp=%s",
        campaign.id, contact.id, call.id, employee.id, agent_id, masked_number, from_number_id,
        sorted(call_context.keys()), utc_now().isoformat(),
    )
    logger.info("[CAMPAIGN_OMNI_DISPATCH] campaign_id=%s contact_id=%s local_call_id=%s employee_id=%s provider_agent_id=%s provider_phone_number_id=%s attempt_number=%s masked_destination=%s", campaign.id, contact.id, call.id, employee.id, agent_id, from_number_id, contact.attempt_count, masked_number)

    settings = get_settings()
    call_provider = OmniDimensionCallProvider(OmniDimensionClient(settings))
    try:
        result = call_provider.dispatch(
            agent_id=agent_id,
            to_number=contact.phone_number,
            from_number_id=from_number_id,
            call_context=call_context,
            metadata=metadata,
        )
    except OmniDimensionError:
        logger.exception("[OMNI_DISPATCH_FAILED] campaign_id=%s contact_id=%s local_call_id=%s", campaign.id, contact.id, call.id)
        call.status = CallStatus.failed.value
        contact.status = ContactStatus.failed.value
        if slot is not None:
            slot.released_at = utc_now()
        db.commit()
        raise

    provider_request_id = getattr(result, "provider_request_id", None)
    provider_bulk_call_id = getattr(result, "provider_bulk_call_id", None)
    provider_line_id = getattr(result, "provider_line_id", None)
    call.provider_call_id = result.provider_call_id
    call.dispatch_metadata = {
        **(call.dispatch_metadata or {}),
        "provider_status": result.status,
        "provider_request_id": provider_request_id,
        "provider_bulk_call_id": provider_bulk_call_id,
        "provider_line_id": provider_line_id,
        "provider_call_id_received_at_dispatch": bool(result.provider_call_id),
    }
    contact.provider_request_id = provider_request_id
    contact.provider_bulk_call_id = provider_bulk_call_id
    contact.provider_line_id = provider_line_id
    contact.provider_call_id = result.provider_call_id
    contact.polling_started_at = contact.polling_started_at or utc_now()
    contact.status_checked_at = None
    contact.last_call_id = call.id
    try:
        db.commit()
    except Exception:
        logger.exception("[CAMPAIGN_CONTACT_UPDATE_FAILED] campaign_id=%s contact_id=%s local_call_id=%s", campaign.id, contact.id, call.id)
        raise
    logger.info("[CAMPAIGN_CONTACT_UPDATED] campaign_id=%s contact_id=%s old_status=%s new_status=%s attempt_count=%s provider_request_id=%s provider_call_id=%s", campaign.id, contact.id, ContactStatus.dispatching.value, contact.status, contact.attempt_count, provider_request_id or "unknown", result.provider_call_id or "unknown")
    logger.info("[CAMPAIGN_CONTACT_DISPATCHED] campaign_id=%s contact_id=%s local_call_id=%s provider_request_id=%s", campaign.id, contact.id, call.id, provider_request_id or "unknown")
    db.refresh(call)
    return call


def _nested_value(value: object, keys: tuple[str, ...]) -> object | None:
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key] not in (None, ""):
                return value[key]
        for child in value.values():
            found = _nested_value(child, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _nested_value(child, keys)
            if found not in (None, ""):
                return found
    return None


def _line_identifier(line: dict, keys: tuple[str, ...]) -> str | None:
    value = _nested_value(line, keys)
    return str(value) if value not in (None, "") else None


def _request_identifier(line: dict) -> str | None:
    value = _nested_value(line, ("call_request_id", "requestId", "request_id"))
    if isinstance(value, dict):
        value = value.get("id")
    return str(value) if value not in (None, "", False) else None


def _normalized_phone(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    digits = "".join(character for character in text if character.isdigit())
    return digits or None


def _unique_line_match(lines: list[dict], predicate) -> dict | None:
    matches = [line for line in lines if predicate(line)]
    return matches[0] if len(matches) == 1 else None


def _select_contact_line(contact: CampaignContact, call: Call, lines: list[dict]) -> dict | None:
    """Match one provider line using identifiers in descending strength."""
    if contact.provider_line_id:
        match = _unique_line_match(lines, lambda line: _line_identifier(
            line, ("line_id", "lineId", "bulk_call_line_id", "bulkCallLineId", "id")
        ) == str(contact.provider_line_id))
        if match is not None:
            return match
    provider_call_id = contact.provider_call_id or call.provider_call_id
    if provider_call_id:
        match = _unique_line_match(lines, lambda line: _line_identifier(
            line, ("call_id", "callId", "call_log_id", "callLogId")
        ) == str(provider_call_id))
        if match is not None:
            return match
    if contact.provider_request_id:
        match = _unique_line_match(
            lines,
            lambda line: _request_identifier(line) == str(contact.provider_request_id),
        )
        if match is not None:
            return match
    match = _unique_line_match(lines, lambda line: (
        _line_identifier(line, ("local_call_id",)) == str(call.id)
        or _line_identifier(line, ("campaign_contact_id", "contact_id")) == str(contact.id)
    ))
    if match is not None:
        return match
    expected_phone = _normalized_phone(contact.normalized_phone or contact.phone_number)
    if expected_phone:
        return _unique_line_match(lines, lambda line: _normalized_phone(
            _nested_value(line, ("to_number", "toNumber", "phone_number", "phoneNumber"))
        ) == expected_phone)
    return None


def _call_log_status_record(provider: OmniDimensionCallProvider, contact: CampaignContact, call: Call) -> dict | None:
    """Resolve a standalone dispatch through its call log without treating requestId as a bulk ID."""
    if contact.provider_call_id or call.provider_call_id:
        return provider.get_call_log(str(contact.provider_call_id or call.provider_call_id))
    if contact.provider_request_id:
        return provider.get_call_log_by_request_id(str(contact.provider_request_id))
    return None


def _elapsed_seconds(started_at: datetime | None, now: datetime) -> float:
    if started_at is None:
        return 0.0
    if started_at.tzinfo is None and now.tzinfo is not None:
        started_at = started_at.replace(tzinfo=now.tzinfo)
    return max(0.0, (now - started_at).total_seconds())


def _retry_or_terminal_status(contact: CampaignContact, campaign: Campaign, call_status: str) -> str:
    if call_status == CallStatus.completed.value:
        return ContactStatus.completed.value
    if call_status == CallStatus.voicemail.value:
        return ContactStatus.voicemail.value
    if call_status == CallStatus.canceled.value:
        return ContactStatus.cancelled.value
    if call_status == CallStatus.skipped.value:
        return ContactStatus.skipped.value
    retryable = call_status in {
        CallStatus.failed.value,
        CallStatus.no_answer.value,
        CallStatus.busy.value,
        CallStatus.timed_out.value,
    }
    retry_intervals = list(campaign.retry_intervals or [2])
    retry_index = max(0, contact.attempt_count - 1)
    if (
        retryable
        and campaign.retry_enabled is not False
        and contact.attempt_count < campaign.max_attempts
        and retry_index < len(retry_intervals)
    ):
        contact.retry_at = utc_now() + timedelta(minutes=max(1, int(retry_intervals[retry_index])))
        return ContactStatus.retry_scheduled.value
    return {
        CallStatus.no_answer.value: ContactStatus.no_answer.value,
        CallStatus.busy.value: ContactStatus.busy.value,
        CallStatus.timed_out.value: ContactStatus.timed_out.value,
    }.get(call_status, ContactStatus.failed.value)


def _finish_polled_call(
    db: Session,
    campaign: Campaign,
    contact: CampaignContact,
    call: Call,
    call_status: str,
    *,
    provider_status: str,
    terminal_status_source: str,
) -> None:
    now = utc_now()
    old_status = contact.status
    call.status = call_status
    call.ended_at = call.ended_at or now
    call.completed_at = call.completed_at or now
    call.dispatch_metadata = {
        **(call.dispatch_metadata or {}),
        "provider_status": provider_status,
        "terminal_status_source": terminal_status_source,
    }
    contact.status = _retry_or_terminal_status(contact, campaign, call_status)
    contact.completed_at = None if contact.status == ContactStatus.retry_scheduled.value else now
    contact.lease_token = None
    if call_status == CallStatus.timed_out.value:
        contact.error_message = "Omni call status did not reach a terminal state before the polling deadline."
    else:
        contact.error_message = None
    release_campaign_slot(db, call_id=call.id)
    _update_campaign_progress(db, campaign)
    db.commit()
    logger.info(
        "[CAMPAIGN_CALL_TERMINAL] campaign_id=%s contact_id=%s local_call_id=%s old_status=%s new_status=%s provider_status=%s",
        campaign.id, contact.id, call.id, old_status, contact.status, provider_status,
    )


def poll_campaign_active_calls(db: Session, campaign: Campaign) -> int:
    """Poll due active calls once. Unknown status keeps the execution slot held."""
    settings = get_settings()
    now = utc_now()
    interval = int(settings.omni_call_status_poll_interval_seconds)
    max_wait = int(settings.omni_call_status_max_wait_seconds)
    contacts = db.scalars(select(CampaignContact).where(
        CampaignContact.campaign_id == campaign.id,
        CampaignContact.status.in_(ACTIVE_CONTACT_STATUSES),
    ).order_by(CampaignContact.created_at)).all()
    if not contacts:
        return 0

    provider: OmniDimensionCallProvider | None = None
    terminal_count = 0
    for contact in contacts:
        started_at = contact.polling_started_at or contact.attempt_started_at or contact.claimed_at
        if _elapsed_seconds(started_at, now) >= max_wait:
            call = db.get(Call, contact.last_call_id) if contact.last_call_id else db.scalar(
                select(Call).where(Call.campaign_contact_id == contact.id).order_by(Call.created_at.desc())
            )
            if call is not None and call.status in ACTIVE_CALL_STATUSES:
                logger.warning(
                    "[CAMPAIGN_CALL_POLL_TIMEOUT] campaign_id=%s contact_id=%s local_call_id=%s max_wait_seconds=%s",
                    campaign.id, contact.id, call.id, max_wait,
                )
                _finish_polled_call(
                    db, campaign, contact, call, CallStatus.timed_out.value,
                    provider_status="polling_timeout",
                    terminal_status_source="polling_timeout",
                )
                terminal_count += 1
            continue
        if contact.status_checked_at and _elapsed_seconds(contact.status_checked_at, now) < interval:
            continue
        call = db.get(Call, contact.last_call_id) if contact.last_call_id else db.scalar(
            select(Call).where(Call.campaign_contact_id == contact.id).order_by(Call.created_at.desc())
        )
        if call is None:
            contact.status_checked_at = now
            contact.error_message = "Active campaign contact has no local call record."
            db.commit()
            continue
        bulk_call_id = contact.provider_bulk_call_id or (call.dispatch_metadata or {}).get("provider_bulk_call_id")
        request_id = contact.provider_request_id or (call.dispatch_metadata or {}).get("provider_request_id")
        contact.status_checked_at = now
        if not bulk_call_id and not (contact.provider_call_id or call.provider_call_id or request_id):
            contact.error_message = "Omni call correlation identifier is not available yet."
            db.commit()
            logger.warning(
                "[CAMPAIGN_CALL_POLL_DEFERRED] campaign_id=%s contact_id=%s local_call_id=%s reason=missing_provider_identifier",
                campaign.id, contact.id, call.id,
            )
            continue
        logger.info(
            "[CAMPAIGN_CALL_POLL_STARTED] campaign_id=%s contact_id=%s local_call_id=%s bulk_call_id=%s",
            campaign.id, contact.id, call.id, bulk_call_id,
        )
        try:
            if provider is None:
                provider = OmniDimensionCallProvider(OmniDimensionClient(settings))
            if bulk_call_id:
                live = provider.get_bulk_call_live_status(str(bulk_call_id))
                logger.info(
                    "[CAMPAIGN_CALL_LIVE_STATUS] campaign_id=%s contact_id=%s local_call_id=%s campaign_status=%s",
                    campaign.id, contact.id, call.id,
                    _nested_value(live, ("campaign_status", "campaignStatus", "status")) or "unknown",
                )
                lines = provider.get_bulk_call_lines(str(bulk_call_id))
                line = _select_contact_line(contact, call, lines)
            else:
                record = _call_log_status_record(provider, contact, call)
                lines = [record] if isinstance(record, dict) else []
                line = record
        except OmniDimensionError as exc:
            contact.error_message = f"Omni status check failed: {type(exc).__name__}"
            db.commit()
            logger.warning(
                "[CAMPAIGN_CALL_POLL_FAILED] campaign_id=%s contact_id=%s local_call_id=%s exception_type=%s",
                campaign.id, contact.id, call.id, type(exc).__name__,
            )
            continue
        if line is None:
            contact.error_message = "Omni line status is not yet available or could not be matched safely."
            db.commit()
            logger.info(
                "[CAMPAIGN_CALL_STATUS_UNKNOWN] campaign_id=%s contact_id=%s local_call_id=%s line_count=%s",
                campaign.id, contact.id, call.id, len(lines),
            )
            continue
        provider_status_value = _nested_value(line, ("call_status", "callStatus", "status", "state"))
        provider_status = str(provider_status_value or "unknown")
        normalized_status = normalize_omni_call_status(provider_status_value)
        line_id = _line_identifier(
            line,
            ("line_id", "lineId", "bulk_call_line_id", "bulkCallLineId") + (("id",) if bulk_call_id else ()),
        )
        provider_call_id = _line_identifier(
            line,
            ("call_id", "callId", "call_log_id", "callLogId") + (() if bulk_call_id else ("id",)),
        )
        contact.provider_line_id = contact.provider_line_id or line_id
        contact.provider_call_id = contact.provider_call_id or provider_call_id
        call.provider_call_id = call.provider_call_id or provider_call_id
        call.dispatch_metadata = {
            **(call.dispatch_metadata or {}),
            "provider_bulk_call_id": str(bulk_call_id) if bulk_call_id else None,
            "provider_line_id": contact.provider_line_id,
            "provider_status": provider_status,
        }
        if normalized_status is None:
            contact.error_message = f"Unknown Omni call status: {provider_status[:120]}"
            db.commit()
            logger.warning(
                "[CAMPAIGN_CALL_STATUS_UNKNOWN] campaign_id=%s contact_id=%s local_call_id=%s provider_status=%s",
                campaign.id, contact.id, call.id, provider_status,
            )
            continue
        contact.error_message = None
        logger.info(
            "[CAMPAIGN_CALL_STATUS_CHECKED] campaign_id=%s contact_id=%s local_call_id=%s provider_status=%s normalized_status=%s",
            campaign.id, contact.id, call.id, provider_status, normalized_status,
        )
        if normalized_status in ACTIVE_CALL_STATUSES:
            call.status = normalized_status
            contact.status = ContactStatus.in_progress.value
            db.commit()
            continue
        if normalized_status in TERMINAL_CALL_STATUSES:
            _finish_polled_call(
                db, campaign, contact, call, normalized_status,
                provider_status=provider_status,
                terminal_status_source=(
                    "bulk_call_status_poll" if bulk_call_id else "call_log_status_poll"
                ),
            )
            terminal_count += 1
    provider_client = getattr(provider, "client", None)
    if provider_client is not None:
        provider_client.close()
    return terminal_count


# ── Campaign lifecycle service ────────────────────────────────────────────────

class CampaignExecutionService:

    def start(
        self,
        db: Session,
        campaign_id: UUID,
        tenant_id: UUID,
        phone_number_id: UUID | None,
    ) -> Campaign:
        """Transition draft/ready campaign to running and dispatch the first batch."""
        campaign = _require_campaign(db, campaign_id, tenant_id)
        logger.info("[CAMPAIGN_START_VALIDATION] campaign_id=%s tenant_id=%s employee_id=%s phone_number_id=%s status_before=%s scheduling_mode=%s calling_window=%s-%s timezone=%s", campaign.id, tenant_id, campaign.employee_id, phone_number_id, campaign.status, (campaign.schedule_config or {}).get("type", "immediate"), campaign.calling_window_start, campaign.calling_window_end, campaign.timezone or "UTC")
        if campaign.status not in {CampaignStatus.draft.value, CampaignStatus.scheduled.value}:
            raise CampaignStateError(
                f"Cannot start a campaign in '{campaign.status}' status. "
                "Only draft or scheduled campaigns can be started."
            )
        employee = _require_published_employee(db, campaign.employee_id, tenant_id)
        if phone_number_id is None:
            phone_number_id = db.scalar(select(PhoneNumber.id).where(
                PhoneNumber.tenant_id == tenant_id,
                PhoneNumber.status == NumberStatus.active.value,
                PhoneNumber.provider_phone_number_id.is_not(None),
            ).order_by(PhoneNumber.created_at))
        if phone_number_id is None:
            raise CampaignExecutionError("No active outbound phone number is configured.")
        phone = _require_usable_phone(db, phone_number_id, tenant_id)
        try:
            require_minimum_balance(db, tenant_id, get_settings().minimum_call_balance_inr)
        except InsufficientBalanceError as exc:
            raise CampaignExecutionError(str(exc)) from exc

        pending = db.scalar(
            select(CampaignContact).where(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.status.in_([ContactStatus.pending.value, ContactStatus.retry_scheduled.value]),
            )
        )
        if pending is None:
            raise CampaignExecutionError("No pending contacts in this campaign.")

        campaign.phone_number_id = phone.id
        if campaign.scheduled_at and campaign.scheduled_at > utc_now():
            campaign.status = CampaignStatus.scheduled.value
        else:
            campaign.status = CampaignStatus.running.value
            campaign.starts_at = campaign.starts_at or utc_now()
        db.commit()
        return campaign

    def pause(self, db: Session, campaign_id: UUID, tenant_id: UUID) -> Campaign:
        campaign = _require_campaign(db, campaign_id, tenant_id)
        if campaign.status != CampaignStatus.running.value:
            raise CampaignStateError("Only a running campaign can be paused.")
        campaign.status = CampaignStatus.paused.value
        db.commit()
        return campaign

    def resume(self, db: Session, campaign_id: UUID, tenant_id: UUID) -> Campaign:
        campaign = _require_campaign(db, campaign_id, tenant_id)
        if campaign.status not in {CampaignStatus.paused.value, CampaignStatus.paused_credits.value}:
            raise CampaignStateError("Only a paused campaign can be resumed.")
        if campaign.phone_number_id is None:
            raise CampaignExecutionError("Campaign has no phone number recorded; cannot resume.")
        employee = _require_published_employee(db, campaign.employee_id, tenant_id)
        phone = _require_usable_phone(db, campaign.phone_number_id, tenant_id)
        try:
            require_minimum_balance(db, tenant_id, get_settings().minimum_call_balance_inr)
        except InsufficientBalanceError as exc:
            raise CampaignExecutionError(str(exc)) from exc
        # Validate phone still belongs to tenant (already done by _require_usable_phone)
        _ = employee, phone
        campaign.status = CampaignStatus.running.value
        db.commit()
        return campaign

    def stop(self, db: Session, campaign_id: UUID, tenant_id: UUID) -> Campaign:
        campaign = _require_campaign(db, campaign_id, tenant_id)
        if campaign.status not in {
            CampaignStatus.running.value,
            CampaignStatus.paused.value,
            CampaignStatus.draft.value,
        }:
            raise CampaignStateError(
                f"Cannot stop a campaign in '{campaign.status}' status."
            )
        # Mark pending/in_progress contacts as skipped
        contacts = db.scalars(
            select(CampaignContact).where(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.status.in_([
                    ContactStatus.pending.value,
                    ContactStatus.in_progress.value,
                ]),
            )
        ).all()
        for c in contacts:
            c.status = ContactStatus.skipped.value
        campaign.status = CampaignStatus.stopped.value
        campaign.ends_at = utc_now()
        db.commit()
        return campaign

    def cancel(self, db: Session, campaign_id: UUID, tenant_id: UUID) -> Campaign:
        """Cancel queued work while allowing already active provider calls to finish."""
        campaign = _require_campaign(db, campaign_id, tenant_id)
        if campaign.status not in {
            CampaignStatus.running.value, CampaignStatus.paused.value,
            CampaignStatus.paused_credits.value, CampaignStatus.draft.value,
            CampaignStatus.scheduled.value,
        }:
            raise CampaignStateError(f"Cannot cancel a campaign in '{campaign.status}' status.")
        contacts = db.scalars(select(CampaignContact).where(
            CampaignContact.campaign_id == campaign.id,
            CampaignContact.status.in_([
                ContactStatus.pending.value, ContactStatus.retry_scheduled.value,
                ContactStatus.retry_pending.value, ContactStatus.queued.value,
            ]),
        )).all()
        for contact in contacts:
            contact.status = ContactStatus.cancelled.value
            contact.retry_at = None
            contact.callback_at = None
            contact.lease_token = None
        campaign.status = CampaignStatus.cancelled.value
        campaign.ends_at = utc_now()
        db.commit()
        return campaign

    def retry_failed(
        self,
        db: Session,
        campaign_id: UUID,
        tenant_id: UUID,
    ) -> Campaign:
        """Reset failed contacts to pending and set campaign back to running."""
        campaign = _require_campaign(db, campaign_id, tenant_id)
        if campaign.status not in {
            CampaignStatus.stopped.value,
            CampaignStatus.failed.value,
            CampaignStatus.paused.value,
            CampaignStatus.completed.value,
        }:
            raise CampaignStateError(
                f"Cannot retry a campaign in '{campaign.status}' status."
            )
        if campaign.phone_number_id is None:
            raise CampaignExecutionError("Campaign has no phone number recorded; cannot retry.")
        _require_published_employee(db, campaign.employee_id, tenant_id)
        _require_usable_phone(db, campaign.phone_number_id, tenant_id)
        try:
            require_minimum_balance(db, tenant_id, get_settings().minimum_call_balance_inr)
        except InsufficientBalanceError as exc:
            raise CampaignExecutionError(str(exc)) from exc

        failed_contacts = db.scalars(
            select(CampaignContact).where(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.status == ContactStatus.failed.value,
            )
        ).all()
        if not failed_contacts:
            raise CampaignExecutionError("No failed contacts to retry.")
        for c in failed_contacts:
            c.status = ContactStatus.pending.value
        campaign.status = CampaignStatus.running.value
        db.commit()
        return campaign

    def restart(self, db: Session, campaign_id: UUID, tenant_id: UUID) -> Campaign:
        """Restart a finished/stopped campaign from its full contact list."""
        campaign = _require_campaign(db, campaign_id, tenant_id)
        if campaign.status not in {
            CampaignStatus.completed.value, CampaignStatus.stopped.value,
            CampaignStatus.failed.value, CampaignStatus.cancelled.value,
        }:
            raise CampaignStateError("Only completed, stopped, failed, or cancelled campaigns can be restarted.")
        _require_published_employee(db, campaign.employee_id, tenant_id)
        if campaign.phone_number_id is None:
            raise CampaignExecutionError("Campaign has no phone number recorded; cannot restart.")
        _require_usable_phone(db, campaign.phone_number_id, tenant_id)
        contacts = db.scalars(select(CampaignContact).where(CampaignContact.campaign_id == campaign.id)).all()
        if not contacts:
            raise CampaignExecutionError("Campaign has no contacts to restart.")
        for contact in contacts:
            contact.status = ContactStatus.pending.value
            contact.attempt_count = 0
            contact.last_called_at = None
            contact.last_call_id = None
            contact.provider_request_id = None
            contact.provider_bulk_call_id = None
            contact.provider_line_id = None
            contact.provider_call_id = None
            contact.error_message = None
            contact.claimed_at = None
            contact.attempt_started_at = None
            contact.completed_at = None
            contact.polling_started_at = None
            contact.status_checked_at = None
            contact.retry_at = None
            contact.callback_at = None
            contact.lease_token = None
        campaign.status = CampaignStatus.running.value
        campaign.starts_at = campaign.starts_at or utc_now()
        campaign.ends_at = None
        db.commit()
        return campaign

    def dispatch_next_pending(
        self,
        db: Session,
        campaign_id: UUID,
        tenant_id: UUID,
    ) -> Call | None:
        """
        Dispatch the next pending contact in a running campaign.
        Returns None if no pending contacts remain (campaign may be completed).
        Called by the background execution loop.
        """
        campaign = _require_campaign(db, campaign_id, tenant_id)
        if campaign.status != CampaignStatus.running.value:
            logger.info("[SCHEDULER_CAMPAIGN_SKIPPED] campaign_id=%s reason=campaign_not_running status=%s", campaign.id, campaign.status)
            return None
        if campaign.phone_number_id is None:
            logger.info("[SCHEDULER_CAMPAIGN_SKIPPED] campaign_id=%s reason=phone_missing", campaign.id)
            return None

        try:
            employee = _require_published_employee(db, campaign.employee_id, tenant_id)
            logger.info("[EMPLOYEE_DISPATCH_VALIDATION] employee_id=%s employee_name=%s published_status=%s published_version_id=%s provider_agent_id=%s", employee.id, employee.name, employee.status, employee.published_version.id if employee.published_version else None, employee.published_version.provider_agent_id if employee.published_version else None)
        except CampaignExecutionError as exc:
            logger.warning("[EMPLOYEE_DISPATCH_BLOCKED] campaign_id=%s employee_id=%s reason=%s", campaign.id, campaign.employee_id, str(exc))
            raise
        try:
            phone = _require_usable_phone(db, campaign.phone_number_id, tenant_id)
            logger.info("[PHONE_DISPATCH_VALIDATION] phone_number_id=%s active_status=%s provider_phone_number_id=%s masked_phone_number=%s employee_association=%s", phone.id, phone.status, phone.provider_phone_number_id, _mask_phone(phone.e164_number or ""), campaign.employee_id)
        except CampaignExecutionError as exc:
            logger.warning("[PHONE_DISPATCH_BLOCKED] campaign_id=%s phone_number_id=%s reason=%s", campaign.id, campaign.phone_number_id, str(exc))
            raise
        agent_id, from_number_id = _parse_provider_ids(employee, phone)

        recover_stale_contacts(db)
        if not _within_calling_window(campaign):
            logger.info("[SCHEDULER_CAMPAIGN_SKIPPED] campaign_id=%s reason=outside_calling_window timezone=%s window=%s-%s", campaign.id, campaign.timezone or "UTC", campaign.calling_window_start, campaign.calling_window_end)
            return None
        if int(campaign.concurrency or 1) == 1:
            active_contact = db.scalar(select(CampaignContact.id).where(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.status.in_(ACTIVE_CONTACT_STATUSES),
            ))
            active_slot = db.scalar(select(CampaignExecutionSlot.id).where(
                CampaignExecutionSlot.campaign_id == campaign.id,
                CampaignExecutionSlot.released_at.is_(None),
            ))
            if active_contact is not None or active_slot is not None:
                logger.info(
                    "[SCHEDULER_CAMPAIGN_SKIPPED] campaign_id=%s reason=sequential_call_still_active active_contact=%s active_slot=%s",
                    campaign.id, active_contact, active_slot,
                )
                return None
        contact = db.scalar(
            select(CampaignContact).where(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.status.in_([ContactStatus.pending.value, ContactStatus.retry_scheduled.value]),
                (CampaignContact.retry_at.is_(None) | (CampaignContact.retry_at <= utc_now())),
            ).order_by(CampaignContact.created_at)
        )
        if contact is None:
            logger.info("[SCHEDULER_NO_ELIGIBLE_CONTACT] campaign_id=%s reason=no_pending_contacts_or_retry_not_due", campaign.id)
            _update_campaign_progress(db, campaign)
            db.commit()
            return None

        try:
            require_minimum_balance(db, tenant_id, get_settings().minimum_call_balance_inr)
        except InsufficientBalanceError:
            campaign.status = CampaignStatus.paused_credits.value
            db.commit()
            logger.info("[SCHEDULER_CAMPAIGN_SKIPPED] campaign_id=%s reason=insufficient_balance", campaign.id)
            return None

        slot = reserve_campaign_slot(db, campaign, contact)
        if slot is None:
            logger.info("[SCHEDULER_CAMPAIGN_SKIPPED] campaign_id=%s reason=no_available_execution_slot", campaign.id)
            return None
        return dispatch_single_contact(
            db, campaign, contact, employee, phone, agent_id, from_number_id, slot
        )


campaign_execution_service = CampaignExecutionService()
