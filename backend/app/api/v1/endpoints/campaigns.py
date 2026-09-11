from __future__ import annotations

import threading
import time
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.db.base import utc_now
from app.models.ai_employee import AIEmployee
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
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
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    employee_id: UUID


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    employee_id: UUID | None = None


class CampaignContactCreate(BaseModel):
    first_name: str | None = Field(default=None, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    phone_number: str = Field(min_length=7, max_length=32)
    email: str | None = Field(default=None, max_length=255)


class CampaignStartRequest(BaseModel):
    phone_number_id: UUID


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

_running_campaigns: set[str] = set()
_lock = threading.Lock()


def _run_campaign_loop(campaign_id: str, tenant_id: str) -> None:
    """Background thread: dispatch contacts one by one until paused/stopped/done."""
    from app.db.session import SessionLocal

    with _lock:
        if campaign_id in _running_campaigns:
            return
        _running_campaigns.add(campaign_id)

    db = SessionLocal()
    try:
        cid = UUID(campaign_id)
        tid = UUID(tenant_id)
        while True:
            try:
                call = campaign_execution_service.dispatch_next_pending(db, cid, tid)
            except CampaignExecutionError:
                # Validation failure (e.g. employee unpublished mid-run) — pause
                campaign = db.scalar(
                    select(Campaign).where(Campaign.id == cid, Campaign.tenant_id == tid)
                )
                if campaign and campaign.status == CampaignStatus.running.value:
                    campaign.status = CampaignStatus.failed.value
                    db.commit()
                break
            except Exception:
                break

            if call is None:
                # No more pending contacts or campaign paused/stopped
                break

            # Brief pause between dispatches — avoids hammering the provider
            time.sleep(1)

            # Re-check campaign status (may have been paused/stopped externally)
            db.expire_all()
            campaign = db.scalar(
                select(Campaign).where(Campaign.id == cid, Campaign.tenant_id == tid)
            )
            if campaign is None or campaign.status != CampaignStatus.running.value:
                break
    finally:
        db.close()
        with _lock:
            _running_campaigns.discard(campaign_id)


def _start_execution_background(campaign_id: UUID, tenant_id: UUID) -> None:
    t = threading.Thread(
        target=_run_campaign_loop,
        args=(str(campaign_id), str(tenant_id)),
        daemon=True,
    )
    t.start()


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
        name=payload.name.strip(),
        description=payload.description,
        employee_id=employee.id,
        status=CampaignStatus.draft.value,
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
        email=payload.email,
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
        }
        for c in contacts
    ]


# ── Lifecycle routes ──────────────────────────────────────────────────────────

@router.post("/{campaign_id}/start", response_model=CampaignDetail)
def start_campaign(
    campaign_id: UUID,
    payload: CampaignStartRequest,
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(
        _start_execution_background, campaign.id, current_user.tenant.id
    )
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
    background_tasks: BackgroundTasks,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignDetail:
    try:
        campaign = campaign_execution_service.resume(db, campaign_id, current_user.tenant.id)
    except CampaignExecutionError as exc:
        raise _execution_error_to_http(exc) from exc
    background_tasks.add_task(
        _start_execution_background, campaign.id, current_user.tenant.id
    )
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


@router.post("/{campaign_id}/retry", response_model=CampaignDetail)
def retry_campaign(
    campaign_id: UUID,
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(
        _start_execution_background, campaign.id, current_user.tenant.id
    )
    return _campaign_detail(campaign, db)


@router.get("/{campaign_id}/progress", response_model=CampaignProgress)
def get_campaign_progress(
    campaign_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CampaignProgress:
    _get_campaign(campaign_id, current_user.tenant.id, db)
    return _campaign_progress(campaign_id, db)
