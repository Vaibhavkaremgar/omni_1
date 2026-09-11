from __future__ import annotations

import logging
from datetime import datetime, timezone
from secrets import token_urlsafe
from typing import Any

from sqlalchemy.orm import Session

from app.integrations.omnidimension import OmniDimensionResellerProvider
from app.integrations.omnidimension.exceptions import (
    OmniDimensionAuthenticationError,
    OmniDimensionClientError,
    OmniDimensionError,
    OmniDimensionNetworkError,
    OmniDimensionServerError,
)
from app.models.tenant import Tenant
from app.models.user import User

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = {"pan", "aadhaar", "aadhar", "gst", "gstin", "gst_num", "otp", "mobile_otp", "email_otp"}

# Carrier values accepted from the frontend — must match the marketplace search carrier param.
_CARRIER_PATTERN = r"^[A-Za-z0-9-]+$"
_MAX_CARRIER_LEN = 80


def _validate_carrier(carrier: str) -> str:
    import re
    carrier = carrier.strip()
    if not carrier or len(carrier) > _MAX_CARRIER_LEN or not re.match(_CARRIER_PATTERN, carrier):
        raise ValueError(f"Invalid carrier value: {carrier!r}")
    return carrier


class ResellerKycService:
    def __init__(self, provider: OmniDimensionResellerProvider):
        self.provider = provider

    def initialize(self, db: Session, *, tenant: Tenant, user: User, phone: str) -> dict[str, Any]:
        if tenant.omni_reseller_user_id is None:
            response = self.provider.create_child_user(
                name=(user.full_name or tenant.name).strip(),
                email=user.email,
                phone=phone.strip(),
                password=token_urlsafe(32),
            )
            child_user_id = self.provider._user_id(response)
            if child_user_id is None:
                raise ValueError("Provider did not return a child user ID.")
            tenant.omni_reseller_user_id = child_user_id
            tenant.omni_reseller_status = "active"
            tenant.omni_reseller_contact_phone = phone.strip()
            db.commit()
        return self.status(db, tenant=tenant)

    def status(self, db: Session, *, tenant: Tenant, region: str = "IN", carrier: str | None = None) -> dict[str, Any]:
        if not tenant.omni_reseller_user_id:
            return {
                "region": region, "carrier": carrier, "status": "not_started", "next_step": None,
                "can_purchase": False, "needs_contact_phone": True,
            }
        response = self.provider.get_kyc_status(
            user_id=tenant.omni_reseller_user_id,
        )
        selected = _region(response, region, carrier)
        safe = _safe_region(selected)
        if carrier:
            safe["carrier"] = carrier
        self._save_status(db, tenant, safe)
        return safe

    def requirements(self, *, region: str, carrier: str) -> dict[str, Any]:
        carrier = _validate_carrier(carrier)
        response = self.provider.get_kyc_requirements(region=region, carrier=carrier)
        steps = []
        for item in response.get("steps", []):
            if isinstance(item, dict) and isinstance(item.get("step"), str):
                required = item.get("required", [])
                steps.append({
                    "step": item["step"],
                    "required": [x for x in required if isinstance(x, str)],
                    "cooldown": bool(item.get("cooldown", False)),
                    "type": item.get("type") if isinstance(item.get("type"), str) else None,
                    "redirect_url": item.get("redirect_url") if isinstance(item.get("redirect_url"), str) else None,
                })
        return {
            "region": response.get("region") if isinstance(response.get("region"), str) else region,
            "carrier": carrier,
            "steps": steps,
        }

    def submit(
        self, db: Session, *, tenant: Tenant, step: str, region: str, carrier: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        if not tenant.omni_reseller_user_id:
            raise LookupError("KYC has not been initialized.")
        carrier = _validate_carrier(carrier)
        # Values are forwarded only; this service neither logs nor persists them.
        response = self.provider.submit_kyc_step(
            user_id=tenant.omni_reseller_user_id,
            region=region,
            carrier=carrier,
            step=step,
            values=values,
        )
        safe = {k: v for k, v in response.items() if k in {"success", "status", "next_step", "message", "preview", "redirect_url"}}
        safe["preview"] = redact_sensitive(safe.get("preview")) if "preview" in safe else None
        safe["region"] = region
        safe["carrier"] = carrier
        self._save_status(db, tenant, safe)
        return safe

    @staticmethod
    def _save_status(db: Session, tenant: Tenant, status: dict[str, Any]) -> None:
        value = status.get("status")
        if isinstance(value, str):
            tenant.omni_reseller_kyc_status = value
            tenant.omni_reseller_region = (
                status.get("region") if isinstance(status.get("region"), str) else "IN"
            )
            if value.lower() == "completed" or status.get("can_purchase") is True:
                tenant.omni_reseller_verified_at = datetime.now(timezone.utc)
        db.commit()


def map_kyc_error(error: OmniDimensionError) -> tuple[int, str]:
    """Map a provider error to a safe (http_status, customer_message) pair.

    Never exposes provider internals or API keys in the returned message.
    Server-side logging of safe diagnostic info is the caller's responsibility.
    """
    if isinstance(error, OmniDimensionAuthenticationError):
        logger.error("KYC: provider authentication failed HTTP %s message=%s", error.status_code, error.provider_message)
        return 502, f"Omni provider authentication failure (upstream HTTP {error.status_code})."
    if isinstance(error, OmniDimensionNetworkError):
        logger.warning("KYC: provider network error class=%s message=%s", error.exception_class, error.exception_message)
        suffix = f": {error.exception_class}: {error.exception_message}" if error.exception_class else ""
        return 502, f"Omni provider connection failed{suffix}"
    if isinstance(error, OmniDimensionClientError):
        code = error.status_code
        logger.info("KYC: provider rejected request with HTTP %s code=%s message=%s", code, error.provider_code, error.provider_message)
        labels = {404: "provider endpoint/resource failure", 422: "provider rejected the request", 429: "provider rate limit", 400: "provider rejected the request", 409: "provider conflict"}
        return 502, f"Omni provider {labels.get(code, 'client error')} (upstream HTTP {code})."
    if isinstance(error, OmniDimensionServerError):
        logger.error("KYC: provider server error HTTP %s code=%s message=%s", error.status_code, error.provider_code, error.provider_message)
        return 502, f"Omni provider server error (upstream HTTP {error.status_code})."
    logger.error("KYC: unexpected provider error: %s", type(error).__name__)
    return 502, "Omni provider request failed."


def _region(payload: dict[str, Any], desired: str, carrier: str | None = None) -> dict[str, Any]:
    for item in payload.get("regions", []):
        if not isinstance(item, dict) or item.get("region") != desired:
            continue
        if carrier is None:
            return item
        if item.get("carrier") == carrier:
            return item
        for carrier_item in item.get("carriers", []):
            if isinstance(carrier_item, dict) and carrier_item.get("carrier") == carrier:
                return {**item, **carrier_item, "region": desired}
    if isinstance(payload.get("status"), str):
        return {
            "region": desired,
            "status": payload["status"],
            "next_step": payload.get("next_step"),
            "can_purchase": payload.get("can_purchase", False),
            "kyc_required": payload.get("kyc_required", True),
        }
    if carrier:
        raise ValueError("No KYC status is available for the selected carrier.")
    return {"region": desired, "status": "not_required", "next_step": None, "can_purchase": True, "kyc_required": False}


def _safe_region(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value.get(key) for key in ("region", "status", "next_step", "can_purchase", "kyc_required")}


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower() in SENSITIVE_KEYS else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value
