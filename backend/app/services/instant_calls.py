from __future__ import annotations

import logging
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.omnidimension import OmniDimensionCallProvider, ProviderDispatchResult
from app.models.ai_employee import AIEmployee
from app.models.call import Call
from app.models.enums import CallDirection, CallStatus, EmployeeStatus, NumberStatus
from app.models.lead import Lead
from app.models.phone_number import PhoneNumber
from app.db.base import utc_now
from app.schemas.call import InstantCallRequest
from app.core.config import get_settings
from app.services.wallets import InsufficientBalanceError, require_minimum_balance
from app.services.phone_numbers import PhoneNumberService

logger = logging.getLogger(__name__)


class InstantCallError(Exception):
    """Base class for safe instant-call validation failures."""


class InstantCallNotFoundError(InstantCallError):
    pass


class InstantCallValidationError(InstantCallError):
    pass


class DuplicateInstantCallError(InstantCallError):
    pass


class InstantCallService:
    def __init__(self, provider: OmniDimensionCallProvider):
        self.provider = provider

    def dispatch(self, db: Session, tenant_id: UUID, request: InstantCallRequest) -> Call:
        try:
            require_minimum_balance(db, tenant_id, get_settings().minimum_call_balance_inr)
        except InsufficientBalanceError as exc:
            raise InstantCallValidationError(str(exc)) from exc
        # Respect the tenant-level Instant Leads toggle
        from sqlalchemy import select as _select
        from app.models.tenant import Tenant as _Tenant
        tenant = db.get(_Tenant, tenant_id)
        if tenant is not None and not tenant.instant_leads_enabled:
            raise InstantCallValidationError("Instant Leads is currently disabled for your account.")
        employee = db.scalar(
            select(AIEmployee).where(
                AIEmployee.id == request.employee_id,
                AIEmployee.tenant_id == tenant_id,
            )
        )
        if employee is None:
            raise InstantCallNotFoundError("Employee not found.")
        if employee.status != EmployeeStatus.published.value or employee.published_version is None:
            raise InstantCallValidationError("Only published employees can place calls.")
        provider_agent_id = employee.published_version.provider_agent_id
        if not provider_agent_id:
            raise InstantCallValidationError("The published employee is not connected to a provider agent.")

        phone_number = db.scalar(
            select(PhoneNumber).where(
                PhoneNumber.id == request.phone_number_id,
            )
        )
        if phone_number is None or not PhoneNumberService.can_use(db, phone_number, tenant_id):
            raise InstantCallNotFoundError("Phone number not found.")
        if phone_number.status != NumberStatus.active.value or not phone_number.provider_phone_number_id:
            raise InstantCallValidationError("The selected phone number is not available for calling.")

        lead = None
        if request.lead_id is not None:
            lead = db.scalar(select(Lead).where(Lead.id == request.lead_id, Lead.tenant_id == tenant_id))
            if lead is None:
                raise InstantCallNotFoundError("Lead not found.")

        try:
            agent_id = int(provider_agent_id)
            from_number_id = int(phone_number.provider_phone_number_id)
        except (TypeError, ValueError) as exc:
            raise InstantCallValidationError("The selected employee or phone number has an invalid provider identifier.") from exc

        recent_duplicate = db.scalar(
            select(Call).where(
                Call.tenant_id == tenant_id,
                Call.employee_id == employee.id,
                Call.phone_number_id == phone_number.id,
                Call.customer_phone_number == request.destination_phone_number,
                Call.status == CallStatus.queued.value,
                Call.created_at >= utc_now() - timedelta(seconds=30),
            )
        )
        if recent_duplicate is not None:
            raise DuplicateInstantCallError("A matching call is already being dispatched.")

        call = Call(
            tenant_id=tenant_id,
            employee_id=employee.id,
            employee_version_id=employee.published_version.id,
            lead_id=lead.id if lead else None,
            phone_number_id=phone_number.id,
            direction=CallDirection.outbound.value,
            status=CallStatus.queued.value,
            customer_name=(request.customer_name.strip() if request.customer_name and not (request.context or "").startswith("Test call") else None),
            customer_phone_number=request.destination_phone_number,
            dispatch_metadata={
                "source": "instant",
                "employee_version_id": str(employee.published_version.id),
                "context": request.context.strip() if request.context else None,
            },
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        logger.info(
            "[CALL_LIFECYCLE_CREATED] local_call_id=%s employee_id=%s employee_version_id=%s provider_agent_id=%s "
            "provider_request_id=unknown provider_call_id=unknown created_at=%s",
            call.id, employee.id, employee.published_version.id, provider_agent_id, call.created_at,
        )

        call_context = {}
        if call.customer_name and not (request.context or "").startswith("Test call"):
            call_context["customer_name"] = call.customer_name
        if request.context:
            call_context["context"] = request.context.strip()
        if lead:
            if lead.first_name:
                call_context["lead_name"] = " ".join(part for part in (lead.first_name, lead.last_name) if part)
            if lead.company:
                call_context["company"] = lead.company
            if lead.profile_data:
                call_context["lead_data"] = lead.profile_data
        metadata = {
            "local_call_id": str(call.id),
            "tenant_id": str(tenant_id),
            "employee_id": str(employee.id),
            "employee_version_id": str(employee.published_version.id),
            "provider_agent_id": str(provider_agent_id),
            "dispatch_timestamp": utc_now().isoformat(),
        }
        if lead:
            metadata["lead_id"] = str(lead.id)

        try:
            version_config = employee.published_version.configuration or {}
            logger.info(
                "[OMNI_DISPATCH_CONFIG] local_call_id=%s provider_agent_id=%s voice_configured=%s language=%s "
                "phone_configured=%s custom_variables_count=%s call_duration_limit=%s silence_timeout=%s "
                "end_call_config=%s other_termination_config=%s",
                call.id, provider_agent_id, bool(version_config.get("voice")), version_config.get("language", employee.language),
                bool(from_number_id), len(call_context), version_config.get("max_call_duration_in_sec", "none"),
                version_config.get("silence_timeout", "none"), version_config.get("end_call_condition", "none"),
                {key: version_config.get(key) for key in ("is_end_call_enabled", "user_idle_threshold_sec", "speech_start_timeout") if key in version_config},
            )
            logger.info(
                "[CALL_LIFECYCLE_DISPATCH_START] local_call_id=%s employee_id=%s employee_version_id=%s "
                "provider_agent_id=%s provider_request_id=unknown provider_call_id=unknown dispatch_started_at=%s",
                call.id, employee.id, employee.published_version.id, provider_agent_id, metadata["dispatch_timestamp"],
            )
            logger.info(
                "[CALL_RUNTIME_CONFIG] local_call_id=%s provider_agent_id=%s is_end_call_enabled=%s "
                "end_call_condition=%s user_idle_threshold_sec=%s silence_timeout=%s "
                "max_call_duration_in_sec=%s is_interruption_allowed=%s language=%s call_type=%s",
                call.id, provider_agent_id, version_config.get("is_end_call_enabled", "provider_default"),
                str(version_config.get("end_call_condition", "provider_configured"))[:500],
                version_config.get("user_idle_threshold_sec", "provider_default"),
                version_config.get("silence_timeout", "provider_default"),
                version_config.get("max_call_duration_in_sec", "provider_default"),
                version_config.get("is_interruption_allowed", "provider_configured"),
                version_config.get("language", employee.language), version_config.get("call_type", employee.call_type),
            )
            logger.info(
                "[OMNI_DISPATCH_START] local_call_id=%s employee_id=%s employee_version_id=%s "
                "provider_agent_id=%s provider_phone_id=%s provider_request_id=%s timestamp=%s",
                call.id, employee.id, employee.published_version.id, provider_agent_id,
                from_number_id, metadata.get("provider_request_id"), metadata["dispatch_timestamp"],
            )
            result = self.provider.dispatch(
                agent_id=agent_id,
                to_number=request.destination_phone_number,
                from_number_id=from_number_id,
                call_context=call_context,
                metadata=metadata,
            )
        except Exception as exc:
            logger.exception(
                "[CALL_RUNTIME_EXCEPTION] local_call_id=%s employee_id=%s employee_version_id=%s "
                "provider_agent_id=%s provider_call_id=%s stage=provider_dispatch exception_type=%s exception_message=%s",
                call.id, employee.id, employee.published_version.id, provider_agent_id,
                None, type(exc).__name__, str(exc)[:300],
            )
            call.status = CallStatus.failed.value
            call.dispatch_metadata = {**(call.dispatch_metadata or {}), "dispatch_failed": True}
            db.commit()
            raise

        if result.provider_call_id:
            call.provider_call_id = result.provider_call_id
        call.status = CallStatus.queued.value
        call.dispatch_metadata = {
            **(call.dispatch_metadata or {}),
            "provider_status": result.status,
            "provider_agent_id": str(provider_agent_id),
            "dispatch_timestamp": metadata["dispatch_timestamp"],
            "provider_request_id": str(result.provider_request_id or result.provider_call_id or ""),
            "provider_call_id_received_at_dispatch": bool(result.provider_call_id),
        }
        logger.info(
            "[CALL_LIFECYCLE_DISPATCH_RESULT] local_call_id=%s employee_id=%s employee_version_id=%s provider_agent_id=%s "
            "provider_request_id=%s provider_call_id=%s dispatch_completed_at=%s terminal_status=unknown",
            call.id, employee.id, employee.published_version.id, provider_agent_id,
            result.provider_request_id or result.provider_call_id or "unknown", result.provider_call_id or "unknown", utc_now(),
        )
        logger.info(
            "[OMNI_DISPATCH_RESULT] local_call_id=%s employee_id=%s employee_version_id=%s "
            "provider_agent_id=%s provider_call_id=%s dispatch_timestamp=%s final_local_status=%s",
            call.id, employee.id, employee.published_version.id, provider_agent_id,
            result.provider_call_id, metadata["dispatch_timestamp"], call.status,
        )
        db.commit()
        db.refresh(call)
        return call


def provider_dispatch_result(result: ProviderDispatchResult) -> dict[str, str]:
    return {"provider_call_id": result.provider_call_id, "status": result.status}
