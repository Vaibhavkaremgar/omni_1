from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.services.auth import AuthenticatedUser

router = APIRouter(prefix="/settings", tags=["settings"])


class TenantSettingsRead(BaseModel):
    instant_leads_enabled: bool
    # Notification preferences — only options with real backend behavior
    notify_campaign_completed: bool
    notify_low_balance: bool


class TenantSettingsUpdate(BaseModel):
    instant_leads_enabled: bool | None = None
    notify_campaign_completed: bool | None = None
    notify_low_balance: bool | None = None


@router.get("", response_model=TenantSettingsRead)
def get_settings_endpoint(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> TenantSettingsRead:
    tenant = current_user.tenant
    return TenantSettingsRead(
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
        instant_leads_enabled=tenant.instant_leads_enabled,
        notify_campaign_completed=getattr(tenant, "notify_campaign_completed", True),
        notify_low_balance=getattr(tenant, "notify_low_balance", True),
    )
