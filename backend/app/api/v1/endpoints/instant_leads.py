from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.v1.endpoints.campaign_upload import MAX_UPLOAD_BYTES, _process_rows
from app.models.ai_employee import AIEmployee
from app.models.enums import EmployeeStatus, NumberStatus
from app.models.instant_lead_source import InstantLeadSource
from app.models.phone_number import PhoneNumber
from app.services.auth import AuthenticatedUser
from app.services.instant_leads import InstantLeadService, _parse_csv, _parse_xlsx

router = APIRouter(prefix="/instant-leads", tags=["instant-leads"])


def _source(source_id, tenant_id, db):
    source = db.scalar(select(InstantLeadSource).where(InstantLeadSource.id == source_id, InstantLeadSource.tenant_id == tenant_id))
    if source is None:
        raise HTTPException(status_code=404, detail="Instant Leads source not found.")
    return source


def _validate_setup(employee_id, phone_number_id, tenant_id, db):
    employee = db.scalar(select(AIEmployee).where(AIEmployee.id == employee_id, AIEmployee.tenant_id == tenant_id))
    if employee is None or employee.status != EmployeeStatus.published.value or employee.published_version is None or not employee.published_version.provider_agent_id:
        raise HTTPException(status_code=422, detail="Select a published employee connected to the provider.")
    phone = db.scalar(select(PhoneNumber).where(PhoneNumber.id == phone_number_id, PhoneNumber.tenant_id == tenant_id))
    if phone is None or phone.status != NumberStatus.active.value or not phone.provider_phone_number_id:
        raise HTTPException(status_code=422, detail="Select an active assigned phone number.")


@router.post("/source")
async def upload_source(file: UploadFile = File(...), employee_id: str = Form(...), phone_number_id: str = Form(...), current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from uuid import UUID
    try:
        employee_uuid, phone_uuid = UUID(employee_id), UUID(phone_number_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid employee or phone number.") from None
    _validate_setup(employee_uuid, phone_uuid, current_user.tenant.id, db)
    filename = (file.filename or "").lower()
    if not (filename.endswith(".csv") or filename.endswith(".xlsx")):
        raise HTTPException(status_code=422, detail="Only CSV and XLSX files are supported.")
    content = await file.read()
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=422, detail="File is empty or exceeds the 5 MB limit.")
    try:
        raw = _parse_xlsx(content) if filename.endswith(".xlsx") else _parse_csv(content)
        preview = _process_rows(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Unable to parse file: {exc}") from exc
    if not preview["valid"]:
        raise HTTPException(status_code=422, detail="File contains no valid lead rows.")
    source = InstantLeadSource(tenant_id=current_user.tenant.id, employee_id=employee_uuid, phone_number_id=phone_uuid,
        filename=file.filename or "leads.csv", content_type=file.content_type, content=content, enabled=False,
        last_status=f"uploaded; {len(preview['valid'])} valid row(s) ready")
    db.add(source); db.commit(); db.refresh(source)
    return InstantLeadService.status(source)


@router.get("/source")
def get_source(current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    source = db.scalar(select(InstantLeadSource).where(InstantLeadSource.tenant_id == current_user.tenant.id).order_by(InstantLeadSource.created_at.desc()))
    return InstantLeadService.status(source) if source else None


class SourceUpdate(BaseModel):
    enabled: bool


@router.patch("/source/{source_id}")
def update_source(source_id: str, payload: SourceUpdate, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from uuid import UUID
    source = _source(UUID(source_id), current_user.tenant.id, db)
    source.enabled = payload.enabled
    db.commit(); db.refresh(source)
    return InstantLeadService.status(source)


@router.post("/source/{source_id}/check")
def check_source(source_id: str, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from uuid import UUID
    try:
        return InstantLeadService().check(db, UUID(source_id), current_user.tenant.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
