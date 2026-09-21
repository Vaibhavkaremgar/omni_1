from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, get_db
from app.models.coupon import Coupon
from app.models.coupon_tenant_share import CouponTenantShare
from app.models.tenant import Tenant
from app.services.auth import AuthenticatedUser, require_admin

admin_router = APIRouter(prefix="/admin/coupons", tags=["admin-coupons"])
router = APIRouter(prefix="/coupons", tags=["coupon-offers"])

class ShareRequest(BaseModel):
    tenant_ids: list[UUID] = Field(min_length=1)

def admin(user: AuthenticatedUser = Depends(get_current_user)): return require_admin(user)
def eligible(c: Coupon) -> bool:
    now = datetime.now(timezone.utc)
    return c.is_active and (c.starts_at is None or c.starts_at <= now) and (c.expires_at is None or c.expires_at > now)
def safe(c: Coupon, share: CouponTenantShare):
    return {"id": share.id, "title": c.title, "description": c.description, "code": c.code, "promotional_minutes": c.promotional_minutes, "discount_type": c.discount_type, "discount_value": c.discount_value, "valid_from": c.starts_at, "expires_at": c.expires_at, "status": share.status}

@admin_router.post("/{coupon_id}/share")
def share_coupon(coupon_id: UUID, payload: ShareRequest, _: AuthenticatedUser = Depends(admin), db: Session = Depends(get_db)):
    coupon = db.get(Coupon, coupon_id)
    if not coupon: raise HTTPException(404, "Coupon not found")
    if not coupon.is_active: raise HTTPException(400, "Disabled coupons cannot be shared")
    tenant_ids = list(dict.fromkeys(payload.tenant_ids))
    existing = set(db.scalars(select(CouponTenantShare.tenant_id).where(CouponTenantShare.coupon_id == coupon_id, CouponTenantShare.tenant_id.in_(tenant_ids))).all())
    valid = set(db.scalars(select(Tenant.id).where(Tenant.id.in_(tenant_ids))).all())
    missing = [x for x in tenant_ids if x not in valid]
    if missing: raise HTTPException(422, "One or more customers could not be found")
    now = datetime.now(timezone.utc)
    for tenant_id in tenant_ids:
        if tenant_id not in existing: db.add(CouponTenantShare(coupon_id=coupon_id, tenant_id=tenant_id, shared_at=now, status="SHARED"))
    try: db.commit()
    except IntegrityError: db.rollback()
    return {"newly_shared": len([x for x in tenant_ids if x not in existing]), "already_shared": len(existing), "tenant_ids": [str(x) for x in tenant_ids if x not in existing]}

@router.get("/offers")
def offers(current: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(CouponTenantShare, Coupon).join(Coupon, Coupon.id == CouponTenantShare.coupon_id).where(CouponTenantShare.tenant_id == current_user.tenant.id)).all()
    return [safe(c, s) for s, c in rows if eligible(c)]

@router.post("/offers/{share_id}/view")
def view_offer(share_id: UUID, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)):
    share = db.scalar(select(CouponTenantShare).where(CouponTenantShare.id == share_id, CouponTenantShare.tenant_id == current_user.tenant.id))
    if not share: raise HTTPException(404, "Offer not found")
    if share.viewed_at is None:
        share.viewed_at = datetime.now(timezone.utc); share.status = "VIEWED"; db.commit()
    coupon = db.get(Coupon, share.coupon_id)
    return safe(coupon, share)
