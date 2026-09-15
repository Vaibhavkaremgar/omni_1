from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.omnidimension import OmniDimensionError, OmniDimensionPhoneNumberProvider
from app.models.phone_number import PhoneNumber
from app.models.tenant import Tenant


class PhoneLifecycleService:
    def __init__(self, provider: OmniDimensionPhoneNumberProvider): self.provider = provider

    def sync(self, db: Session, tenant: Tenant) -> list[PhoneNumber]:
        if not tenant.omni_reseller_user_id: return []
        remote = self.provider.list_phone_numbers(user_id=tenant.omni_reseller_user_id)
        existing = {item.e164_number: item for item in db.scalars(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant.id)).all()}
        for item in remote:
            local = existing.get(item.e164_number)
            if local is None:
                db.add(PhoneNumber(tenant_id=tenant.id, e164_number=item.e164_number, provider_name="omnidimension", provider_phone_number_id=item.provider_id, status=item.status, ownership="tenant", label=item.label, capabilities=item.metadata))
            elif local.status != "released":
                local.provider_phone_number_id, local.status, local.label, local.capabilities = item.provider_id, item.status, item.label, item.metadata
        db.commit()
        return list(db.scalars(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant.id).order_by(PhoneNumber.created_at.desc())).all())

    def release(self, db: Session, tenant: Tenant, number: PhoneNumber) -> PhoneNumber:
        if number.status == "released": return number
        if not tenant.omni_reseller_user_id: raise LookupError
        number.release_idempotency_key = number.release_idempotency_key or str(uuid4())
        number.status, number.release_failure_reason = "release_pending", None; db.commit()
        try:
            response = self.provider.release_number(phone_number=number.e164_number, user_id=tenant.omni_reseller_user_id, idempotency_key=number.release_idempotency_key)
        except OmniDimensionError:
            number.status, number.release_failure_reason = "release_failed", "provider_error"; db.commit(); return number
        if response.get("success") is True or response.get("status") in {"released", "already_released"}:
            number.status, number.release_failure_reason, number.released_at = "released", None, datetime.now(timezone.utc)
        else:
            number.status, number.release_failure_reason = "release_failed", "provider_rejected"
        db.commit(); return number
