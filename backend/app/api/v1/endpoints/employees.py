from uuid import UUID
import logging
import base64
from io import BytesIO
from pypdf import PdfReader

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.ai_employee import AIEmployee
from app.models.ai_employee_version import AIEmployeeVersion
from app.models.enums import EmployeeStatus, VersionStatus
from app.models.employee_knowledge_file import EmployeeKnowledgeFile
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
from app.services.voice_catalog import voice_catalog
from app.services.voice_recommendations import recommend_voices
from app.services.employee_templates import template_library, render_template, get_template
from app.services.employee_prompt import compose_employee_configuration
from app.services.script_language_validation import (
    assert_customer_facing_script_language,
    extract_call_script_from_prompt,
    validation_summary,
)


MAX_KNOWLEDGE_FILE_SIZE = 4 * 1024 * 1024
from app.services.employee_interview import RealLLMService, render_call_script_sections
from app.services.business_research import ensure_business_research, validate_public_research_url
from app.services.knowledge_base_files import to_pdf


router = APIRouter(prefix="/employees", tags=["employees"])
logger = logging.getLogger(__name__)
agent_service: OmniDimensionAgentService | None = None

_LEGACY_ASSEMBLED_PURPOSE = "Purpose: Deliver the requested call objective using the saved user context."
_LEGACY_ASSEMBLED_INSTRUCTIONS = "Instructions: Use only the saved user context and approved call rules. Do not invent missing details."
_LEGACY_ASSEMBLED_HANDLING = "Handling: Use the configured guardrails, disclose that the caller is an AI assistant when asked, and stop respectfully if asked to stop."


def employee_response(employee: AIEmployee) -> AIEmployeeRead:
    draft = next((version for version in employee.versions if version.status == VersionStatus.draft.value), None)
    published = employee.published_version
    active = draft or published or max(employee.versions, key=lambda version: version.version_number, default=None)
    configuration = public_employee_configuration(active.configuration) if active else None
    if configuration:
        configuration = {**configuration, "script_language_validation": validation_summary(configuration)}
    fields = configuration or {}
    provider_version = published
    provider_metadata = provider_version.provider_metadata if provider_version and isinstance(provider_version.provider_metadata, dict) else {}
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
        provider_agent_id=None,
        provider_status=provider_version.provider_status if provider_version else None,
        provider_verification=provider_metadata.get("provider_verification"),
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


def _upgrade_legacy_assembled_script(configuration: dict) -> dict:
    """Refresh only the exact legacy deterministic fallback before publishing.

    It avoids touching reviewed/model-authored scripts while making an old
    generic fallback render with the current contextual and language rules.
    """
    if configuration.get("final_prompt_overridden"):
        return configuration
    call_script = configuration.get("call_script")
    if not isinstance(call_script, dict) or len(call_script) != 6:
        return configuration
    legacy_cards = all(
        _LEGACY_ASSEMBLED_PURPOSE in str(card)
        and _LEGACY_ASSEMBLED_INSTRUCTIONS in str(card)
        and _LEGACY_ASSEMBLED_HANDLING in str(card)
        and "Spoken example:" in str(card)
        for card in call_script.values()
    )
    if not legacy_cards or configuration.get("script_source") not in {"assembled", None}:
        return configuration
    source_context = str(
        configuration.get("assembled_source_context")
        or configuration.get("original_requirement")
        or configuration.get("purpose")
        or ""
    ).strip()
    if not source_context:
        return configuration
    upgraded = dict(configuration)
    context = {
        "original_requirement": source_context,
        "purpose": str(upgraded.get("purpose") or source_context),
    }
    language = str(upgraded.get("language") or "English")
    call_direction = str(upgraded.get("call_type") or "inbound")
    sections = RealLLMService._assemble_script_sections(context, language, call_direction, upgraded)
    upgraded["conversation_sections"] = sections
    upgraded["call_script"] = render_call_script_sections(sections)
    upgraded["script_source"] = "assembled"
    upgraded["assembled_script_version"] = 2
    logger.info("Upgraded legacy assembled employee script before publish")
    return upgraded


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
        # The provider identity is unique in the version table. A draft is a
        # configuration revision, not a second owner of the live agent. The
        # publisher resolves the current published agent when synchronizing;
        # historical identity remains available on the archived version.
    )
    employee.versions.append(draft)
    return draft


def _validate_publish(employee: AIEmployee, draft: AIEmployeeVersion | None) -> None:
    if draft is None or not isinstance(draft.configuration, dict):
        raise HTTPException(status_code=422, detail="A draft configuration is required before publishing")
    required = ("name", "llm_provider", "llm_model", "language", "call_type")
    missing = [field for field in required if not str(draft.configuration.get(field, "")).strip()]
    if not str(draft.configuration.get("purpose", "")).strip() and not str(draft.configuration.get("direct_prompt", "")).strip() and not str(draft.configuration.get("final_prompt", "")).strip():
        missing.append("purpose, direct_prompt, or final_prompt")
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required configuration: {', '.join(missing)}")
    if draft.configuration.get("final_prompt_overridden") and str(draft.configuration.get("final_prompt") or "").strip():
        return
    script = draft.configuration.get("call_script") or {}
    if len(script) != 6 or not all(isinstance(v, str) and v.strip() for v in script.values()):
        raise HTTPException(422, "Generate an employee prompt before publishing")
    _validate_script_language_or_422(draft.configuration)


def _validate_script_language_or_422(configuration: dict) -> None:
    if configuration.get("conversation_design"):
        script = configuration.get("call_script") or {}
        if len(script) != 6 or not all(isinstance(v, str) and v.strip() for v in script.values()):
            raise HTTPException(422, "Exactly six populated employee sections are required")
        summary = validation_summary(configuration)
        if not summary["valid"]:
            raise HTTPException(422, detail={"message": "Spoken language validation failed", "validation": summary})
        return
    # Configuration fields (business name, purpose, requirement, etc.) are
    # owner-authored context and may be written in any language. Only validate
    # when a customer-facing script has actually been supplied/generated.
    call_script = configuration.get("call_script") if isinstance(configuration.get("call_script"), dict) else {}
    has_spoken_script = any(str(value or "").strip() for value in call_script.values())
    if not has_spoken_script and not (configuration.get("final_prompt_overridden") and str(configuration.get("final_prompt") or "").strip()):
        return
    assert_customer_facing_script_language(
        call_script,
        str(configuration.get("language") or "English"),
    )
    if configuration.get("final_prompt_overridden") and str(configuration.get("final_prompt") or "").strip():
        prompt_script = extract_call_script_from_prompt(str(configuration.get("final_prompt") or ""))
        if prompt_script:
            assert_customer_facing_script_language(prompt_script, str(configuration.get("language") or "English"))


def _has_customer_script(configuration: dict) -> bool:
    script = configuration.get("call_script") if isinstance(configuration.get("call_script"), dict) else {}
    return bool(script) or (
        bool(configuration.get("final_prompt_overridden")) and bool(str(configuration.get("final_prompt") or "").strip())
    )


def _apply_final_prompt_override_to_call_script(configuration: dict) -> dict:
    if not configuration.get("final_prompt_overridden") or not str(configuration.get("final_prompt") or "").strip():
        return configuration
    prompt_script = extract_call_script_from_prompt(str(configuration.get("final_prompt") or ""))
    if not prompt_script:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Final prompt must contain the six call-script sections before it can be saved.",
                "validation": {
                    "valid": False,
                    "language": str(configuration.get("language") or "English"),
                    "issues": [
                        {
                            "section": title,
                            "type": "missing_section",
                            "message": f"Final prompt must include {title}.",
                        }
                        for title in ("six numbered, uniquely titled sections",)
                    ],
                },
            },
        )
    return {**configuration, "call_script": prompt_script}


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


@router.get("/voice-recommendations")
def get_voice_recommendations(
    language: str = Query(default=""),
    gender: str = Query(default=""),
    requirement: str = Query(default=""),
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> dict[str, list[dict[str, str]]]:
    recommendations = recommend_voices(
        voice_catalog(), language=language, gender=gender, requirement=requirement,
    )
    logger.info(
        "Voice recommendations requested tenant_id=%s language=%s gender=%s count=%d",
        current_user.tenant.id, language, gender or None, len(recommendations),
    )
    return {"recommended_voices": recommendations}


def _public_knowledge_file(row: EmployeeKnowledgeFile) -> dict:
    return {"id": row.id, "filename": row.filename, "content_type": row.content_type,
            "file_size": row.file_size, "status": row.status, "error_message": row.error_message,
            "created_at": row.created_at, "updated_at": row.updated_at}


@router.get("/{employee_id}/knowledge-files")
def list_knowledge_files(employee_id: UUID, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    get_employee_or_404(employee_id, current_user.tenant.id, db)
    return [_public_knowledge_file(row) for row in db.scalars(select(EmployeeKnowledgeFile).where(EmployeeKnowledgeFile.employee_id == employee_id, EmployeeKnowledgeFile.tenant_id == current_user.tenant.id).order_by(EmployeeKnowledgeFile.created_at)).all()]


@router.post("/{employee_id}/knowledge-files", status_code=status.HTTP_201_CREATED)
async def upload_knowledge_file(employee_id: UUID, file: UploadFile = File(...), current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    filename = (file.filename or "").strip()
    content = await file.read()
    if not content:
        raise HTTPException(status_code=422, detail="The uploaded file is empty.")
    try:
        pdf_content, pdf_filename = to_pdf(content, filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(pdf_content) >= MAX_KNOWLEDGE_FILE_SIZE:
        raise HTTPException(status_code=413, detail="Knowledge files must be smaller than 4 MB.")
    try:
        knowledge_text = "\n\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(pdf_content)).pages).strip()
    except Exception as exc:
        raise HTTPException(status_code=422, detail="The PDF text could not be extracted.") from exc
    version = employee.published_version
    agent_id = version.provider_agent_id if version else None
    row = EmployeeKnowledgeFile(tenant_id=current_user.tenant.id, employee_id=employee_id, filename=pdf_filename[:255], content_type="application/pdf", file_size=len(pdf_content), knowledge_text=knowledge_text, status="uploading")
    db.add(row); db.commit(); db.refresh(row)
    provider = get_agent_service().provider
    preserve_uploaded_provider_file = False
    try:
        logger.info("Knowledge Base create started employee_id=%s filename=%s", employee_id, row.filename)
        row.provider_file_id = provider.upload_knowledge_file(base64.b64encode(pdf_content).decode("ascii"), row.filename)
        if agent_id:
            row.status = "attaching"; db.commit()
            try:
                provider.attach_knowledge_file(row.provider_file_id, agent_id, f"Use this document to answer questions about {employee.name}'s configured business, products, services, and policies.")
            except Exception:
                preserve_uploaded_provider_file = True
                row.status = "uploaded_not_attached"
                row.error_message = "The file was uploaded to OmniDimension but could not be attached. Retry attachment."
                db.commit()
                raise
            row.status = "ready"
        else:
            # Files uploaded during initial employee creation are attached
            # automatically when the employee is first published.
            row.status = "pending"
        row.error_message = None; db.commit(); db.refresh(row)
        if version is not None:
            version.configuration = compose_employee_configuration({
                **(version.configuration or {}),
                "knowledge_base_configured": True,
                "knowledge_files": [
                    {"filename": item.filename, "status": item.status}
                    for item in db.scalars(select(EmployeeKnowledgeFile).where(EmployeeKnowledgeFile.employee_id == employee_id, EmployeeKnowledgeFile.tenant_id == current_user.tenant.id, EmployeeKnowledgeFile.status == "ready")).all()
                ],
            })
        db.commit()
        return _public_knowledge_file(row)
    except Exception as exc:
        if row.provider_file_id and not preserve_uploaded_provider_file:
            try:
                if agent_id:
                    provider.detach_knowledge_file(row.provider_file_id, agent_id)
                provider.delete_knowledge_file(row.provider_file_id)
            except Exception:
                logger.warning("Knowledge provider cleanup failed file_id=%s", row.id)
        if not preserve_uploaded_provider_file:
            row.provider_file_id = None
        row.status = "failed"; row.error_message = "Knowledge file processing failed. Please retry."; db.commit()
        logger.warning("Knowledge file processing failed employee_id=%s file_id=%s exception_class=%s", employee_id, row.id, type(exc).__name__)
        raise HTTPException(status_code=502, detail=row.error_message) from exc


@router.delete("/{employee_id}/knowledge-files/{file_id}")
def delete_knowledge_file(employee_id: UUID, file_id: UUID, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    row = db.scalar(select(EmployeeKnowledgeFile).where(EmployeeKnowledgeFile.id == file_id, EmployeeKnowledgeFile.employee_id == employee_id, EmployeeKnowledgeFile.tenant_id == current_user.tenant.id))
    if row is None:
        raise HTTPException(status_code=404, detail="Knowledge file not found.")
    if row.provider_file_id and employee.published_version and employee.published_version.provider_agent_id:
        provider = get_agent_service().provider
        try:
            provider.detach_knowledge_file(row.provider_file_id, employee.published_version.provider_agent_id)
            provider.delete_knowledge_file(row.provider_file_id)
        except Exception as exc:
            row.status = "failed"; row.error_message = "The provider could not remove this file. Please retry."; db.commit()
            logger.warning("Knowledge file removal failed file_id=%s exception_class=%s", row.id, type(exc).__name__)
            raise HTTPException(status_code=502, detail=row.error_message) from exc
    db.delete(row); db.commit()
    return {"status": "removed"}


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
    template = None
    if payload.selected_template_id:
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
            configuration=compose_employee_configuration({
                "name": employee.name,
                "purpose": employee.purpose,
                "call_type": employee.call_type,
                "llm_provider": employee.llm_provider,
                "llm_model": employee.llm_model,
                "language": employee.language,
                "creation_mode": employee.creation_mode,
                **({"host_name": payload.host_name.strip()} if payload.host_name and payload.host_name.strip() else {}),
                **({"selected_template_id": template["id"], "selected_template_version": template["template_version"], "template_values": payload.template_values or {}} if template else {}),
                **({"direct_prompt": payload.direct_prompt} if payload.direct_prompt else {}),
            }),
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
    if "call_type" in normalized and normalized["call_type"] not in {"inbound", "outbound"}:
        raise HTTPException(status_code=422, detail="Only inbound or outbound employees are supported")
    draft.configuration = {**(draft.configuration or {}), **normalized}
    if configuration is not None:
        if not isinstance(configuration, dict):
            raise HTTPException(status_code=422, detail="Configuration must be an object")
        if configuration.get("call_type") not in (None, "inbound", "outbound"):
            raise HTTPException(status_code=422, detail="Only inbound or outbound employees are supported")
        if "website_url" in configuration:
            try:
                configuration = {**configuration, "website_url": validate_public_research_url(configuration.get("website_url"))}
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        draft.configuration = {
            **(draft.configuration or {}),
            **strip_customer_internal_configuration(configuration),
        }
    # Composition creates a fallback six-section prompt from the owner's
    # business brief. That fallback is not generated spoken content and must
    # not be subjected to Telugu/Hindi script validation during a normal edit.
    had_customer_script = _has_customer_script(draft.configuration)
    draft.configuration = compose_employee_configuration(_apply_final_prompt_override_to_call_script(draft.configuration))
    if had_customer_script:
        _validate_script_language_or_422(draft.configuration)
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


@router.post("/{employee_id}/generate-script", response_model=AIEmployeeVersionRead)
def generate_employee_script(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AIEmployeeVersionRead:
    """Generate the editable six-section script from the canonical employee context."""
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    draft = _draft_for(employee)
    if draft is None:
        raise HTTPException(status_code=404, detail="Employee draft not found")
    knowledge = db.scalars(select(EmployeeKnowledgeFile).where(EmployeeKnowledgeFile.employee_id == employee_id, EmployeeKnowledgeFile.tenant_id == current_user.tenant.id)).all()
    configuration = {**(draft.configuration or {}), "knowledge_files": [{"filename": item.filename, "status": item.status} for item in knowledge]}
    if str(configuration.get("call_type") or employee.call_type) not in {"inbound", "outbound"}:
        raise HTTPException(status_code=422, detail="Call type must be inbound or outbound")
    if str(configuration.get("language") or employee.language).casefold() not in {"english", "hindi", "telugu", "en", "hi", "te", "en-us", "hi-in", "te-in"}:
        raise HTTPException(status_code=422, detail="Selected language is not supported for employee script generation")
    configuration = ensure_business_research(configuration)
    generated_script = RealLLMService().generate_call_script(employee, configuration)
    draft.configuration = compose_employee_configuration({
        **configuration, "call_script": generated_script, "conversation_sections": configuration.get("conversation_sections"),
        "spoken_script_generated": True, "conversation_design": None,
    })
    draft.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(draft)
    return draft


@router.post("/{employee_id}/suggest-conversation-variables")
def suggest_conversation_variables(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, list[dict]]:
    """Return reviewable extraction suggestions without changing the employee draft."""
    employee = get_employee_or_404(employee_id, current_user.tenant.id, db)
    draft = _draft_for(employee)
    source = draft.configuration if draft is not None else (employee.published_version.configuration if employee.published_version else {})
    configuration = dict(source or {})
    knowledge = db.scalars(select(EmployeeKnowledgeFile).where(
        EmployeeKnowledgeFile.employee_id == employee_id,
        EmployeeKnowledgeFile.tenant_id == current_user.tenant.id,
    )).all()
    configuration["knowledge_files"] = [{"filename": item.filename, "status": item.status} for item in knowledge]
    return {"variables": RealLLMService().suggest_conversation_variables(employee, configuration)}


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
        version.configuration = {
            "name": employee.name,
            "purpose": employee.purpose,
            "call_type": employee.call_type,
            "llm_provider": employee.llm_provider,
            "llm_model": employee.llm_model,
            "language": employee.language,
            "creation_mode": employee.creation_mode,
            **(version.configuration or {}),
        }
        version.configuration = _upgrade_legacy_assembled_script(version.configuration)
        version.configuration = compose_employee_configuration(_apply_final_prompt_override_to_call_script(version.configuration or {}))
        normalize_employee_llm_configuration(employee, version)
        version.configuration = ensure_business_research(version.configuration)
        version.configuration = compose_employee_configuration(_apply_final_prompt_override_to_call_script(version.configuration))
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
    # Re-attach existing files so the knowledge base follows the published
    # Omni voice assistant when its provider identity changes.
    knowledge_files = db.scalars(select(EmployeeKnowledgeFile).where(
        EmployeeKnowledgeFile.employee_id == employee_id,
        EmployeeKnowledgeFile.tenant_id == current_user.tenant.id,
        EmployeeKnowledgeFile.provider_file_id.is_not(None),
    )).all()
    if knowledge_files:
        try:
            provider = get_agent_service().provider
            for knowledge_file in knowledge_files:
                provider.attach_knowledge_file(
                    knowledge_file.provider_file_id,
                    provider_result.provider_id,
                    f"Use this document to answer questions about {employee.name}'s configured business, products, services, and policies.",
                )
        except OmniDimensionError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Unable to attach the knowledge base to the Omni voice assistant. Please retry.",
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
            if version.provider_agent_id and version.provider_name:
                version.provider_metadata = {
                    **(version.provider_metadata or {}),
                    "historical_provider_name": version.provider_name,
                    "historical_provider_agent_id": version.provider_agent_id,
                }
                version.provider_name = None
                version.provider_agent_id = None
                version.provider_status = None
            version.status = VersionStatus.archived.value
    draft.status = VersionStatus.published.value
    draft.published_at = datetime.now(timezone.utc)
    employee.published_version = draft
    employee.status = EmployeeStatus.published.value
    for field in ("name", "purpose", "call_type", "language", "creation_mode"):
        if field in draft.configuration:
            setattr(employee, field, draft.configuration[field])
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        logger.exception("Employee publish persistence failed employee_id=%s version_id=%s provider_agent_id=%s", employee.id, draft.id, provider_result.provider_id)
        raise HTTPException(status_code=409, detail="This employee version could not be persisted because its provider agent is already linked to another version. Please retry.") from exc
    db.refresh(employee)
    return employee_response(employee)
