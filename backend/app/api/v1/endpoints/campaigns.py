from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.base import utc_now
from app.models.ai_employee import AIEmployee
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.call import Call
from app.models.enums import (
    CampaignStatus,
    ContactStatus,
    EmployeeStatus,
)
from app.schemas.campaign import CampaignRead
from app.services.auth import AuthenticatedUser
from app.services.campaign_execution import (
    CampaignExecutionError,
    CampaignExecutionService,
    CampaignNotFoundError,
    CampaignStateError,
    campaign_execution_service,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    employee_id: UUID
    scheduled_at: datetime | None = None
    timezone: str | None = None
    calling_window_start: str | None = None
    calling_window_end: str | None = None
    max_attempts: int = Field(default=3, ge=1, le=10)
    concurrency: int = Field(default=1, ge=1, le=50)
    retry_enabled: bool = True
    retry_intervals: list[int] = Field(default_factory=lambda: [10, 30], min_length=0, max_length=9)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    employee_id: UUID | None = None
    scheduled_at: datetime | None = None
    timezone: str | None = None
    calling_window_start: str | None = None
    calling_window_end: str | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    concurrency: int | None = Field(default=None, ge=1, le=50)
    retry_enabled: bool | None = None
    retry_intervals: list[int] | None = Field(default=None, min_length=0, max_length=9)


class CampaignContactCreate(BaseModel):
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    phone_number: str = Field(min_length=7, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    customer_data: dict = Field(default_factory=dict)


class CampaignStartRequest(BaseModel):
    phone_number_id: UUID | None = None


class EmployeeSummary(BaseModel):
    id: UUID
    name: str
    purpose: str
    language: str
    status: str
    is_ready: bool


class CampaignProgress(BaseModel):
    total: int
    pending: int
    in_progress: int
    completed: int
    failed: int
    skipped: int
    queued: int = 0
    dispatching: int = 0
    retry_pending: int = 0
    cancelled: int = 0


class CampaignDetail(BaseModel):
    id: UUID
    name: str
    description: str | None
    status: str
    employee_id: UUID
    employee: EmployeeSummary | None = None
    phone_number_id: UUID | None = None
    contact_count: int = 0
    progress: CampaignProgress
    created_at: str
    updated_at: str
    scheduled_at: str | None = None
    timezone: str | None = None
    calling_window_start: str | None = None
    calling_window_end: str | None = None
    max_attempts: int = 3
    concurrency: int = 1
    retry_enabled: bool = True
    retry_intervals: list[int] = []


class CampaignAttemptRead(BaseModel):
    id: UUID
    attempt_number: int
    started_at: datetime | None = None
    completed_at: datetime | None = None
    status: str
    outcome: str | None = None
    duration_seconds: int | None = None
    callback_at: datetime | None = None
    summary: str | None = None
    recording_available: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_campaign(campaign_id: UUID, tenant_id: UUID, db: Session) -> Campaign:
    campaign = db.scalar(
        select(Campaign).where(
            Campaign.id == campaign_id,
            Campaign.tenant_id == tenant_id,
        )
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign


def _get_published_employee(employee_id: UUID, tenant_id: UUID, db: Session) -> AIEmployee:
    employee = db.scalar(
        select(AIEmployee).where(
            AIEmployee.id == employee_id,
            AIEmployee.tenant_id == tenant_id,
        )
    )
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


def _employee_summary(employee: AIEmployee) -> EmployeeSummary:
    published = employee.published_version
    is_ready = (
        employee.status == EmployeeStatus.published.value
        and published is not None
        and bool(published.provider_agent_id)
    )
    return EmployeeSummary(
        id=employee.id,
        name=employee.name,
        purpose=employee.purpose,
        language=employee.language,
        status=employee.status,
        is_ready=is_ready,
    )


def _campaign_progress(campaign_id: UUID, db: Session) -> CampaignProgress:
    contacts = db.scalars(
        select(CampaignContact).where(CampaignContact.campaign_id == campaign_id)
    ).all()
    return CampaignProgress(
        total=len(contacts),
        pending=sum(1 for c in contacts if c.status == ContactStatus.pending.value),
        in_progress=sum(1 for c in contacts if c.status == ContactStatus.in_progress.value),
        completed=sum(1 for c in contacts if c.status in {
            ContactStatus.completed.value, ContactStatus.called.value
        }),
        failed=sum(1 for c in contacts if c.status == ContactStatus.failed.value),
        skipped=sum(1 for c in contacts if c.status in {
            ContactStatus.skipped.value, ContactStatus.do_not_call.value
        }),
        queued=sum(1 for c in contacts if c.status == ContactStatus.queued.value),
        dispatching=sum(1 for c in contacts if c.status == ContactStatus.dispatching.value),
        retry_pending=sum(1 for c in contacts if c.status == ContactStatus.retry_pending.value),
        cancelled=sum(1 for c in contacts if c.status == ContactStatus.cancelled.value),
    )


def _campaign_detail(campaign: Campaign, db: Session) -> CampaignDetail:
    employee = db.scalar(select(AIEmployee).where(AIEmployee.id == campaign.employee_id))
    progress = _campaign_progress(campaign.id, db)
    return CampaignDetail(
        id=campaign.id,
        name=campaign.name,
        description=campaign.description,
        status=campaign.status,
        employee_id=campaign.employee_id,
        employee=_employee_summary(employee) if employee else None,
        phone_number_id=campaign.phone_number_id,
        contact_count=progress.total,
        progress=progress,
        created_at=campaign.created_at.isoformat(),
        updated_at=campaign.updated_at.isoformat(),
        scheduled_at=campaign.scheduled_at.isoformat() if campaign.scheduled_at else None,
        timezone=campaign.timezone,
        calling_window_start=campaign.calling_window_start,
        calling_window_end=campaign.calling_window_end,
        max_attempts=campaign.max_attempts,
        concurrency=campaign.concurrency,
        retry_enabled=campaign.retry_enabled is not False,
        retry_intervals=list(campaign.retry_intervals or []),
    )


def _execution_error_to_http(exc: CampaignExecutionError) -> HTTPException:
    if isinstance(exc, CampaignNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, CampaignStateError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


# ── Background execution loop ─────────────────────────────────────────────────
# Simple in-process loop: dispatches one contact at a time with a short sleep
# between calls to respect provider rate limits. No Redis/Celery required for
# the current SQLite/FastAPI development environment.

# ── CRUD routes ───────────────────────────────────────────────────────────────

@router.get("", response_model=list[CampaignDetail])
def list_campaigns(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CampaignDetail]:
    campaigns = db.scalars(
        select(Campaign)
        .where(
            Campaign.tenant_id == current_user.tenant.id,
            Campaign.status != CampaignStatus.archived.value,
        )
        .order_by(Campaign.created_at.desc())
    ).all()
    return [_campaign_detail(c, db) for c in campaigns]


@router.post("", response_model=CampaignDetail, status_code=status.HTTP_201_CREATED)
def create_campaign(
    payload: CampaignCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    employee = _get_published_employee(payload.employee_id, current_user.tenant.id, db)
    campaign = Campaign(
        tenant_id=current_user.tenant.id,
        name=(payload.name or f"{employee.name} - Bulk Campaign - {utc_now():%Y-%m-%d %H:%M}").strip(),
        description=payload.description,
        employee_id=employee.id,
        status=CampaignStatus.draft.value,
        scheduled_at=payload.scheduled_at,
        timezone=payload.timezone,
        calling_window_start=payload.calling_window_start,
        calling_window_end=payload.calling_window_end,
        max_attempts=payload.max_attempts,
        concurrency=payload.concurrency,
        retry_enabled=payload.retry_enabled,
        retry_intervals=payload.retry_intervals,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return _campaign_detail(campaign, db)


@router.get("/{campaign_id}", response_model=CampaignDetail)
def get_campaign(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    campaign = _get_campaign(campaign_id, current_user.tenant.id, db)
    return _campaign_detail(campaign, db)


@router.patch("/{campaign_id}", response_model=CampaignDetail)
def update_campaign(
    campaign_id: UUID,
    payload: CampaignUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    campaign = _get_campaign(campaign_id, current_user.tenant.id, db)
    if campaign.status not in {CampaignStatus.draft.value, CampaignStatus.scheduled.value}:
        raise HTTPException(status_code=409, detail="Only draft campaigns can be edited.")
    if payload.name is not None:
        campaign.name = payload.name.strip()
    if payload.description is not None:
        campaign.description = payload.description
    if payload.employee_id is not None:
        employee = _get_published_employee(payload.employee_id, current_user.tenant.id, db)
        campaign.employee_id = employee.id
    for field in ("scheduled_at", "timezone", "calling_window_start", "calling_window_end", "max_attempts", "concurrency", "retry_enabled", "retry_intervals"):
        value = getattr(payload, field)
        if value is not None:
            setattr(campaign, field, value)
    if campaign.scheduled_at and campaign.scheduled_at > utc_now():
        campaign.status = CampaignStatus.scheduled.value
    db.commit()
    db.refresh(campaign)
    return _campaign_detail(campaign, db)


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_campaign(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    campaign = _get_campaign(campaign_id, current_user.tenant.id, db)
    campaign.status = CampaignStatus.archived.value
    db.commit()


# ── Contact routes ────────────────────────────────────────────────────────────

@router.post("/{campaign_id}/contacts", status_code=status.HTTP_201_CREATED)
def add_contact(
    campaign_id: UUID,
    payload: CampaignContactCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    campaign = _get_campaign(campaign_id, current_user.tenant.id, db)
    contact = CampaignContact(
        tenant_id=current_user.tenant.id,
        campaign_id=campaign.id,
        first_name=payload.first_name,
        last_name=payload.last_name,
        phone_number=payload.phone_number,
        normalized_phone=payload.phone_number,
        email=payload.email,
        customer_data=payload.customer_data,
        status=ContactStatus.pending.value,
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return {"id": str(contact.id), "phone_number": contact.phone_number, "status": contact.status}


@router.get("/{campaign_id}/contacts")
def list_contacts(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    _get_campaign(campaign_id, current_user.tenant.id, db)
    contacts = db.scalars(
        select(CampaignContact).where(CampaignContact.campaign_id == campaign_id)
    ).all()
    return [
        {
            "id": str(c.id),
            "first_name": c.first_name,
            "last_name": c.last_name,
            "phone_number": c.phone_number,
            "status": c.status,
            "attempt_count": c.attempt_count,
            "last_called_at": c.last_called_at.isoformat() if c.last_called_at else None,
            "retry_at": c.retry_at.isoformat() if c.retry_at else None,
            "callback_at": c.callback_at.isoformat() if c.callback_at else None,
            "customer_data": c.customer_data or {},
        }
        for c in contacts
    ]


@router.get("/{campaign_id}/contacts/{contact_id}/attempts", response_model=list[CampaignAttemptRead])
def list_contact_attempts(
    campaign_id: UUID,
    contact_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[CampaignAttemptRead]:
    """Return real call records for one tenant-owned campaign contact."""
    _get_campaign(campaign_id, current_user.tenant.id, db)
    contact = db.scalar(
        select(CampaignContact).where(
            CampaignContact.id == contact_id,
            CampaignContact.campaign_id == campaign_id,
            CampaignContact.tenant_id == current_user.tenant.id,
        )
    )
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    calls = db.scalars(
        select(Call)
        .where(
            Call.campaign_id == campaign_id,
            Call.campaign_contact_id == contact_id,
            Call.tenant_id == current_user.tenant.id,
        )
        .order_by(Call.started_at.asc().nulls_last(), Call.created_at.asc(), Call.id.asc())
    ).all()
    # Numbering follows execution chronology; response is newest first for the UI.
    attempts = [
        CampaignAttemptRead(
            id=call.id,
            attempt_number=index,
            started_at=call.started_at or call.created_at,
            completed_at=call.completed_at or call.ended_at,
            status=call.status,
            outcome=call.outcome,
            duration_seconds=call.duration_seconds,
            callback_at=contact.callback_at if call.id == contact.last_call_id else None,
            summary=call.summary,
            recording_available=bool(call.recording_url),
        )
        for index, call in enumerate(calls, start=1)
    ]
    return list(reversed(attempts))


# ── Lifecycle routes ──────────────────────────────────────────────────────────

@router.post("/{campaign_id}/start", response_model=CampaignDetail)
def start_campaign(
    campaign_id: UUID,
    payload: CampaignStartRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    """Start a draft campaign. Selects the phone number and begins dispatching contacts."""
    try:
        campaign = campaign_execution_service.start(
            db, campaign_id, current_user.tenant.id, payload.phone_number_id
        )
    except CampaignExecutionError as exc:
        raise _execution_error_to_http(exc) from exc
    return _campaign_detail(campaign, db)


@router.post("/{campaign_id}/pause", response_model=CampaignDetail)
def pause_campaign(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    try:
        campaign = campaign_execution_service.pause(db, campaign_id, current_user.tenant.id)
    except CampaignExecutionError as exc:
        raise _execution_error_to_http(exc) from exc
    return _campaign_detail(campaign, db)


@router.post("/{campaign_id}/resume", response_model=CampaignDetail)
def resume_campaign(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    try:
        campaign = campaign_execution_service.resume(db, campaign_id, current_user.tenant.id)
    except CampaignExecutionError as exc:
        raise _execution_error_to_http(exc) from exc
    return _campaign_detail(campaign, db)


@router.post("/{campaign_id}/stop", response_model=CampaignDetail)
def stop_campaign(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    try:
        campaign = campaign_execution_service.stop(db, campaign_id, current_user.tenant.id)
    except CampaignExecutionError as exc:
        raise _execution_error_to_http(exc) from exc
    return _campaign_detail(campaign, db)

@router.post("/{campaign_id}/cancel", response_model=CampaignDetail)
def cancel_campaign(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    try:
        campaign = campaign_execution_service.cancel(db, campaign_id, current_user.tenant.id)
    except CampaignExecutionError as exc:
        raise _execution_error_to_http(exc) from exc
    return _campaign_detail(campaign, db)


@router.post("/{campaign_id}/retry", response_model=CampaignDetail)
def retry_campaign(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    """Reset failed contacts to pending and resume execution."""
    try:
        campaign = campaign_execution_service.retry_failed(
            db, campaign_id, current_user.tenant.id
        )
    except CampaignExecutionError as exc:
        raise _execution_error_to_http(exc) from exc
    return _campaign_detail(campaign, db)


@router.get("/{campaign_id}/progress", response_model=CampaignProgress)
def get_campaign_progress(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignProgress:
    _get_campaign(campaign_id, current_user.tenant.id, db)
    return _campaign_progress(campaign_id, db)
