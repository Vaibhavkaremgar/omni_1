from fastapi import APIRouter, Depends
import base64
import binascii
import re

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.auth import AuthenticatedUser

router = APIRouter(prefix="/settings", tags=["settings"])


class TenantSettingsRead(BaseModel):
    business_name: str
    logo_data_url: str | None
    instant_leads_enabled: bool
    # Notification preferences — only options with real backend behavior
    notify_campaign_completed: bool
    notify_low_balance: bool


class TenantSettingsUpdate(BaseModel):
    business_name: str | None = Field(default=None, min_length=1, max_length=255)
    logo_data_url: str | None = Field(default=None, max_length=1_000_000)
    instant_leads_enabled: bool | None = None
    notify_campaign_completed: bool | None = None
    notify_low_balance: bool | None = None

    @field_validator("business_name")
    @classmethod
    def normalize_business_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Business name cannot be empty")
        return value

    @field_validator("logo_data_url")
    @classmethod
    def validate_logo_data_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)", value)
        if not match:
            raise ValueError("Logo must be a PNG, JPEG, or WebP image")
        try:
            decoded = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Logo image is not valid base64 data") from exc
        if len(decoded) > 700_000:
            raise ValueError("Logo image must be smaller than 700 KB")
        return value


@router.get("", response_model=TenantSettingsRead)
def get_settings_endpoint(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> TenantSettingsRead:
    tenant = current_user.tenant
    return TenantSettingsRead(
        business_name=tenant.name,
        logo_data_url=tenant.brand_logo_data_url,
        instant_leads_enabled=tenant.instant_leads_enabled,
        notify_campaign_completed=getattr(tenant, "notify_campaign_completed", True),
        notify_low_balance=getattr(tenant, "notify_low_balance", True),
    )


@router.patch("", response_model=TenantSettingsRead)
def update_settings(
    payload: TenantSettingsUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TenantSettingsRead:
    tenant = current_user.tenant
    if payload.business_name is not None:
        tenant.name = payload.business_name
    if "logo_data_url" in payload.model_fields_set:
        tenant.brand_logo_data_url = payload.logo_data_url
    if payload.instant_leads_enabled is not None:
        tenant.instant_leads_enabled = payload.instant_leads_enabled
    if payload.notify_campaign_completed is not None and hasattr(tenant, "notify_campaign_completed"):
        tenant.notify_campaign_completed = payload.notify_campaign_completed
    if payload.notify_low_balance is not None and hasattr(tenant, "notify_low_balance"):
        tenant.notify_low_balance = payload.notify_low_balance
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return TenantSettingsRead(
        business_name=tenant.name,
        logo_data_url=tenant.brand_logo_data_url,
        instant_leads_enabled=tenant.instant_leads_enabled,
        notify_campaign_completed=getattr(tenant, "notify_campaign_completed", True),
        notify_low_balance=getattr(tenant, "notify_low_balance", True),
    )
