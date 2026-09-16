from uuid import UUID
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.ai_employee import AIEmployee
from app.models.ai_employee_version import AIEmployeeVersion
from app.models.enums import EmployeeStatus, VersionStatus
from app.schemas.ai_employee import AIEmployeeCreate, AIEmployeeRead, AIEmployeeUpdate
from app.schemas.ai_employee_version import AIEmployeeVersionRead
from app.schemas.employee_options import EmployeeOptionsRead
from app.services.auth import AuthenticatedUser
from app.services.llm_registry import llm_registry
from app.core.config import get_settings
from app.integrations.omnidimension import OmniDimensionClient, OmniDimensionAgentProvider
from app.integrations.omnidimension.exceptions import OmniDimensionError
from app.services.omnidimension_agents import OmniDimensionAgentService
from app.services.employee_configuration import (
    normalize_employee_llm_configuration,
    public_employee_configuration,
    strip_customer_internal_configuration,
)
from app.services.voice_catalog import public_voice_catalog
from app.services.employee_templates import template_library, render_template, get_template


router = APIRouter(prefix="/employees", tags=["employees"])
logger = logging.getLogger(__name__)
agent_service: OmniDimensionAgentService | None = None


def employee_response(employee: AIEmployee) -> AIEmployeeRead:
    draft = next((version for version in employee.versions if version.status == VersionStatus.draft.value), None)
    published = employee.published_version
    active = draft or published or max(employee.versions, key=lambda version: version.version_number, default=None)
    configuration = public_employee_configuration(active.configuration) if active else None
    fields = configuration or {}
    provider_version = published
    return AIEmployeeRead(
        id=employee.id,
        name=fields.get("name", employee.name),
        description=employee.description,
        purpose=fields.get("purpose", employee.purpose),
        call_type=fields.get("call_type", employee.call_type),
        language=fields.get("language", employee.language),
        creation_mode=fields.get("creation_mode", employee.creation_mode),
        status=employee.status,
        configuration=configuration,
        created_at=employee.created_at,
        updated_at=employee.updated_at,
        draft_version=draft,
        published_version=published,
        provider_name=provider_version.provider_name if provider_version else None,
        provider_agent_id=provider_version.provider_agent_id if provider_version else None,
        provider_status=provider_version.provider_status if provider_version else None,
    )


def get_agent_service() -> OmniDimensionAgentService:
    global agent_service
    if agent_service is None:
        agent_service = OmniDimensionAgentService(
            OmniDimensionAgentProvider(OmniDimensionClient())
        )
    return agent_service


def _copy_configuration(version: AIEmployeeVersion | None, employee: AIEmployee) -> dict:
    return dict(version.configuration or {}) if version else {
        "name": employee.name,
        "purpose": employee.purpose,
        "call_type": employee.call_type,
        "llm_provider": employee.llm_provider,
        "llm_model": employee.llm_model,
        "language": employee.language,
        "creation_mode": employee.creation_mode,
    }


def _draft_for(employee: AIEmployee) -> AIEmployeeVersion | None:
    return next((version for version in employee.versions if version.status == VersionStatus.draft.value), None)


def _ensure_draft(employee: AIEmployee, current_user: AuthenticatedUser) -> AIEmployeeVersion:
    draft = _draft_for(employee)
    if draft:
        return draft
    latest_number = max((version.version_number for version in employee.versions), default=0)
    draft = AIEmployeeVersion(
        tenant_id=employee.tenant_id,
        employee_id=employee.id,
        version_number=latest_number + 1,
        status=VersionStatus.draft.value,
        configuration=_copy_configuration(employee.published_version, employee),
        change_summary="Draft created for editing",
        created_by_user_id=current_user.user.id,
        # Local edits create a version, not a new voice agent. Retaining this
        # identity means publishing the draft updates the deployed employee.
        provider_name=employee.published_version.provider_name if employee.published_version else None,
        provider_agent_id=employee.published_version.provider_agent_id if employee.published_version else None,
        provider_status=employee.published_version.provider_status if employee.published_version else None,
        provider_metadata=employee.published_version.provider_metadata if employee.published_version else None,
    )
    employee.versions.append(draft)
    return draft


def _validate_publish(employee: AIEmployee, draft: AIEmployeeVersion | None) -> None:
    if draft is None or not isinstance(draft.configuration, dict):
        raise HTTPException(status_code=422, detail="A draft configuration is required before publishing")
    required = ("name", "llm_provider", "llm_model", "language")
    missing = [field for field in required if not str(draft.configuration.get(field, "")).strip()]
    if not str(draft.configuration.get("purpose", "")).strip() and not str(draft.configuration.get("direct_prompt", "")).strip():
        missing.append("purpose or direct_prompt")
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required configuration: {', '.join(missing)}")


@router.get("/options", response_model=EmployeeOptionsRead)
def get_employee_options(
    _: AuthenticatedUser = Depends(get_current_user),
) -> EmployeeOptionsRead:
    return llm_registry.options()


@router.get("/voice-catalog")
def get_voice_catalog(current_user: AuthenticatedUser = Depends(get_current_user)) -> list[dict]:
    catalog = public_voice_catalog()
    logger.info("Voice catalog requested tenant_id=%s count=%d", current_user.tenant.id, len(catalog))
    return catalog


@router.get("/templates")
def get_employee_templates(
    _: AuthenticatedUser = Depends(get_current_user),
) -> list[dict]:
    """Return platform-owned template definitions; no tenant data is included."""
    return template_library()


@router.post("/templates/render")
def render_employee_template(
    payload: dict,
    _: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """Render a tenant's draft locally. Publishing remains the only provider mutation."""
    try:
        return render_template(
            str(payload.get("template_id", "")),
            payload.get("values") or {},
            str(payload.get("language") or "Telugu"),
            str(payload.get("custom_instructions") or ""),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("", response_model=list[AIEmployeeRead])
def list_employees(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AIEmployeeRead]:
    employees = db.scalars(
        select(AIEmployee)
        .where(AIEmployee.tenant_id == current_user.tenant.id, AIEmployee.status != EmployeeStatus.archived.value)
        .order_by(AIEmployee.created_at.desc())
    ).all()
    return [employee_response(employee) for employee in employees]


@router.post("", response_model=AIEmployeeRead, status_code=status.HTTP_201_CREATED)
def create_employee(
    payload: AIEmployeeCreate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIEmployeeRead:
    try:
        template = get_template(payload.selected_template_id)
    except KeyError as exc:
        raise HTTPException(status_code=422, detail="A valid Pontis employee template is required") from exc
    if payload.selected_template_version != template["template_version"]:
        raise HTTPException(status_code=422, detail="The selected template version is not available")
    settings = get_settings()
    provider = settings.effective_llm_provider
    model = settings.effective_llm_model
    if not provider or not model:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI employee deployment is not configured. Please contact support.",
        )
    employee = AIEmployee(
        tenant_id=current_user.tenant.id,
        name=payload.name.strip(),
        purpose=payload.purpose.strip(),
        call_type=payload.call_type,
        llm_provider=provider,
        llm_model=model,
        language=payload.language,
        creation_mode=payload.creation_mode,
        status=EmployeeStatus.draft.value,
    )
    db.add(employee)
    db.flush()
    db.add(
        AIEmployeeVersion(
            tenant_id=current_user.tenant.id,
            employee_id=employee.id,
            version_number=1,
            status=VersionStatus.draft.value,
            configuration={
                "name": employee.name,
                "purpose": employee.purpose,
                "call_type": employee.call_type,
                "llm_provider": employee.llm_provider,
                "llm_model": employee.llm_model,
                "language": employee.language,
                "creation_mode": employee.creation_mode,
                "selected_template_id": template["id"],
                "selected_template_version": template["template_version"],
                "template_values": payload.template_values or {},
                **({"direct_prompt": payload.direct_prompt, "system_prompt": payload.direct_prompt} if payload.direct_prompt else {}),
            },
            change_summary="Initial employee draft",
            created_by_user_id=current_user.user.id,
        )
    )
    db.commit()
    db.refresh(employee)
    return employee_response(employee)


def get_employee_or_404(employee_id: UUID, tenant_id: UUID, db: Session) -> AIEmployee:
    employee = db.scalar(
        select(AIEmployee).where(
            AIEmployee.id == employee_id,
            AIEmployee.tenant_id == tenant_id,
        )
    )
    if employee is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
    return employee


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    provider_linked = bool(employee.published_version_id) or any(version.provider_agent_id for version in employee.versions)
    if provider_linked or employee.status == EmployeeStatus.published.value:
        employee.status = EmployeeStatus.archived.value
        for version in employee.versions:
            if version.status == VersionStatus.draft.value:
                version.status = VersionStatus.archived.value
    else:
        db.delete(employee)
    db.commit()


@router.get("/{employee_id}", response_model=AIEmployeeRead)
def get_employee(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIEmployeeRead:
    return employee_response(get_employee_or_404(employee_id, current_user.tenant.id, db))


@router.patch("/{employee_id}", response_model=AIEmployeeRead)
def update_employee(
    employee_id: UUID,
    payload: AIEmployeeUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIEmployeeRead:
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    changes = payload.model_dump(exclude_unset=True)
    configuration = changes.pop("configuration", None)
    draft = _ensure_draft(employee, current_user)
    normalized = {field: value.strip() if isinstance(value, str) else value for field, value in changes.items()}
    # Enforce self-service call_type restriction at the configuration level too
    if "call_type" in normalized and normalized["call_type"] not in ("inbound", "both"):
        raise HTTPException(status_code=422, detail="Outbound calling is available by request. Contact us to enable it.")
    draft.configuration = {**(draft.configuration or {}), **normalized}
    if configuration is not None:
        if not isinstance(configuration, dict):
            raise HTTPException(status_code=422, detail="Configuration must be an object")
        if configuration.get("call_type") not in (None, "inbound", "both"):
            raise HTTPException(status_code=422, detail="Outbound calling is available by request. Contact us to enable it.")
        draft.configuration = strip_customer_internal_configuration(configuration)
    normalize_employee_llm_configuration(employee, draft)
    for field in ("name", "purpose", "call_type", "language", "creation_mode"):
        if field in draft.configuration:
            setattr(employee, field, draft.configuration[field])
    draft.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(employee)
    return employee_response(employee)


@router.get("/{employee_id}/versions", response_model=list[AIEmployeeVersionRead])
def list_employee_versions(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[AIEmployeeVersionRead]:
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    return sorted(employee.versions, key=lambda version: version.version_number)


@router.get("/{employee_id}/draft", response_model=AIEmployeeVersionRead)
def get_employee_draft(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIEmployeeVersionRead:
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    draft = _draft_for(employee)
    if draft is None:
        raise HTTPException(status_code=404, detail="Employee draft not found")
    return draft


@router.get("/{employee_id}/published", response_model=AIEmployeeVersionRead)
def get_published_employee_version(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIEmployeeVersionRead:
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    if employee.published_version is None:
        raise HTTPException(status_code=404, detail="Published employee version not found")
    return employee.published_version


@router.post("/{employee_id}/publish", response_model=AIEmployeeRead)
def publish_employee(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIEmployeeRead:
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    draft = _draft_for(employee)
    version = draft or (employee.published_version if employee.status == EmployeeStatus.published.value else None)
    if version is not None:
        normalize_employee_llm_configuration(employee, version)
    _validate_publish(employee, version)
    assert version is not None
    try:
        provider_result = get_agent_service().synchronize(employee, version)
    except OmniDimensionError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to synchronize the AI employee with the provider. Please retry.",
        ) from exc
    version.provider_name = "omnidimension"
    version.provider_agent_id = provider_result.provider_id
    version.provider_status = provider_result.status
    version.provider_metadata = provider_result.metadata
    if draft is None:
        db.commit()
        db.refresh(employee)
        return employee_response(employee)
    for version in employee.versions:
        if version.status == VersionStatus.published.value and version.id != draft.id:
            version.status = VersionStatus.archived.value
    draft.status = VersionStatus.published.value
    draft.published_at = datetime.now(timezone.utc)
    employee.published_version = draft
    employee.status = EmployeeStatus.published.value
    for field in ("name", "purpose", "call_type", "language", "creation_mode"):
        if field in draft.configuration:
            setattr(employee, field, draft.configuration[field])
    db.commit()
    db.refresh(employee)
    return employee_response(employee)
