from datetime import datetime, time, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.ai_employee import AIEmployee
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.call import Call
from app.models.credit_wallet import CreditWallet
from app.models.enums import CampaignStatus, CallStatus, EmployeeStatus, ContactStatus, LeadStatus
from app.services.auth import AuthenticatedUser
from app.services.wallets import LOW_BALANCE_THRESHOLD_MINUTES
from app.services.pricing import call_price_inr

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def dashboard_summary(current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    tenant_id = current_user.tenant.id
    today_start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    calls_today = db.scalars(select(Call).where(Call.tenant_id == tenant_id, Call.created_at >= today_start)).all()
    connected = sum(1 for call in calls_today if call.status == CallStatus.completed.value)
    recent_calls = db.scalars(select(Call).where(Call.tenant_id == tenant_id).order_by(Call.created_at.desc()).limit(8)).all()
    campaigns = db.scalars(select(Campaign).where(Campaign.tenant_id == tenant_id, Campaign.status != CampaignStatus.archived.value).order_by(Campaign.created_at.desc()).limit(10)).all()
    contacts = db.scalars(select(CampaignContact).where(CampaignContact.tenant_id == tenant_id)).all()
    contacts_by_campaign: dict[str, list[CampaignContact]] = {}
    for contact in contacts:
        contacts_by_campaign.setdefault(str(contact.campaign_id), []).append(contact)
    employees = db.scalars(select(AIEmployee).where(AIEmployee.tenant_id == tenant_id, AIEmployee.status != EmployeeStatus.archived.value).order_by(AIEmployee.created_at.desc())).all()
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_id))
    alerts = []
    available_value = Decimal(wallet.balance_credits) if wallet is not None else Decimal("0")
    available_minutes = available_value / call_price_inr()
    if available_minutes <= 0:
        alerts.append({"type": "low_credits", "title": "Calling balance exhausted", "message": "Recharge to continue making calls.", "severity": "error", "href": "/billing"})
    elif available_minutes <= LOW_BALANCE_THRESHOLD_MINUTES:
        alerts.append({"type": "low_credits", "title": "Calling balance is running low", "message": f"{available_minutes.quantize(Decimal('0.01'))} min · ₹{available_value.quantize(Decimal('0.01'))} remaining.", "severity": "warning", "href": "/billing"})
    for employee in employees:
        if employee.status != EmployeeStatus.published.value:
            alerts.append({"type": "employee_unpublished", "title": "Employee needs publishing", "message": f"{employee.name} is not published.", "severity": "warning", "href": f"/employees/{employee.id}"})
    for campaign in campaigns:
        if campaign.status in {CampaignStatus.paused.value, CampaignStatus.paused_credits.value}:
            message = "Insufficient calling balance. Recharge before resuming." if campaign.status == CampaignStatus.paused_credits.value else f"{campaign.name} is paused."
            alerts.append({"type": "campaign_paused", "title": "Campaign paused", "message": message, "severity": "warning", "href": f"/campaigns/{campaign.id}"})
    return {
        "calls_today": len(calls_today), "connected_today": connected,
        "recent_calls": [{"id": call.id, "customer_phone_number": call.customer_phone_number, "status": call.status, "duration_seconds": call.duration_seconds, "direction": call.direction, "started_at": call.started_at or call.created_at, "employee_id": call.employee_id, "outcome": call.outcome} for call in recent_calls],
        "employees": [{"id": e.id, "name": e.name, "purpose": e.purpose, "language": e.language, "status": e.status, "is_ready": e.status == EmployeeStatus.published.value and bool(e.published_version and e.published_version.provider_agent_id)} for e in employees],
        "campaigns": [{"id": c.id, "name": c.name, "status": c.status, "scheduled_at": c.scheduled_at, "completed": sum(1 for x in contacts_by_campaign.get(str(c.id), []) if x.status in {ContactStatus.completed.value, ContactStatus.called.value}), "total": len(contacts_by_campaign.get(str(c.id), []))} for c in campaigns if c.status in {CampaignStatus.scheduled.value, CampaignStatus.running.value, CampaignStatus.paused.value, CampaignStatus.paused_credits.value}],
        "credits": float(wallet.balance_credits) if wallet is not None else 0,
        "alerts": alerts,
    }
