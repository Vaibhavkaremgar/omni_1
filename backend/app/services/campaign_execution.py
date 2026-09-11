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

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.integrations.omnidimension import OmniDimensionCallProvider, OmniDimensionClient
from app.integrations.omnidimension.exceptions import OmniDimensionError
from app.models.ai_employee import AIEmployee
from app.models.call import Call
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
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
    phone = db.scalar(
        select(PhoneNumber).where(
            PhoneNumber.id == phone_number_id,
            PhoneNumber.tenant_id == tenant_id,
        )
    )
    if phone is None:
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
    ContactStatus.failed.value,
    ContactStatus.do_not_call.value,
    ContactStatus.skipped.value,
}


def _update_campaign_progress(db: Session, campaign: Campaign) -> None:
    """Transition campaign to completed if all contacts are terminal."""
    if campaign.status not in {CampaignStatus.running.value, CampaignStatus.paused.value}:
        return
    contacts = db.scalars(
        select(CampaignContact).where(CampaignContact.campaign_id == campaign.id)
    ).all()
    if not contacts:
        return
    if all(c.status in TERMINAL_CONTACT_STATUSES for c in contacts):
        campaign.status = CampaignStatus.completed.value
        campaign.ends_at = utc_now()


# ── Core dispatch (shared with InstantCallService boundary) ───────────────────

def dispatch_single_contact(
    db: Session,
    campaign: Campaign,
    contact: CampaignContact,
    employee: AIEmployee,
    phone: PhoneNumber,
    agent_id: int,
    from_number_id: int,
) -> Call:
    """
    Create a Call record and dispatch it to OmniDimension.
    Idempotent: if a queued/in-progress call already exists for this contact
    in this campaign, returns it without re-dispatching.
    """
    # Duplicate guard — contact already being called
    existing = db.scalar(
        select(Call).where(
            Call.campaign_contact_id == contact.id,
            Call.status.in_([CallStatus.queued.value, CallStatus.in_progress.value]),
        )
    )
    if existing is not None:
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
    db.add(call)
    contact.status = ContactStatus.in_progress.value
    contact.attempt_count = (contact.attempt_count or 0) + 1
    contact.last_called_at = utc_now()
    db.commit()
    db.refresh(call)

    call_context: dict[str, str] = {}
    if call.customer_name:
        call_context["customer_name"] = call.customer_name
    metadata = {
        "local_call_id": str(call.id),
        "campaign_id": str(campaign.id),
        "tenant_id": str(campaign.tenant_id),
        "employee_id": str(employee.id),
    }

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
        call.status = CallStatus.failed.value
        contact.status = ContactStatus.failed.value
        db.commit()
        raise

    call.provider_call_id = result.provider_call_id
    call.dispatch_metadata = {
        **(call.dispatch_metadata or {}),
        "provider_status": result.status,
    }
    contact.last_call_id = call.id
    db.commit()
    db.refresh(call)
    return call


# ── Campaign lifecycle service ────────────────────────────────────────────────

class CampaignExecutionService:

    def start(
        self,
        db: Session,
        campaign_id: UUID,
        tenant_id: UUID,
        phone_number_id: UUID,
    ) -> Campaign:
        """Transition draft/ready campaign to running and dispatch the first batch."""
        campaign = _require_campaign(db, campaign_id, tenant_id)
        if campaign.status not in {CampaignStatus.draft.value, CampaignStatus.scheduled.value}:
            raise CampaignStateError(
                f"Cannot start a campaign in '{campaign.status}' status. "
                "Only draft or scheduled campaigns can be started."
            )
        employee = _require_published_employee(db, campaign.employee_id, tenant_id)
        phone = _require_usable_phone(db, phone_number_id, tenant_id)
        try:
            require_minimum_balance(db, tenant_id, get_settings().minimum_call_balance_inr)
        except InsufficientBalanceError as exc:
            raise CampaignExecutionError(str(exc)) from exc

        pending = db.scalar(
            select(CampaignContact).where(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.status == ContactStatus.pending.value,
            )
        )
        if pending is None:
            raise CampaignExecutionError("No pending contacts in this campaign.")

        campaign.phone_number_id = phone.id
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
        if campaign.status != CampaignStatus.paused.value:
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
            return None
        if campaign.phone_number_id is None:
            return None

        employee = _require_published_employee(db, campaign.employee_id, tenant_id)
        phone = _require_usable_phone(db, campaign.phone_number_id, tenant_id)
        agent_id, from_number_id = _parse_provider_ids(employee, phone)

        contact = db.scalar(
            select(CampaignContact).where(
                CampaignContact.campaign_id == campaign.id,
                CampaignContact.status == ContactStatus.pending.value,
            ).order_by(CampaignContact.created_at)
        )
        if contact is None:
            _update_campaign_progress(db, campaign)
            db.commit()
            return None

        try:
            require_minimum_balance(db, tenant_id, get_settings().minimum_call_balance_inr)
        except InsufficientBalanceError:
            campaign.status = CampaignStatus.paused.value
            db.commit()
            return None

        return dispatch_single_contact(
            db, campaign, contact, employee, phone, agent_id, from_number_id
        )


campaign_execution_service = CampaignExecutionService()
