from datetime import datetime, timezone
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, get_db
from app.models.coupon import Coupon
from app.schemas.coupon import CouponPayload, CouponRead
from app.services.auth import AuthenticatedUser, require_admin

router = APIRouter(prefix="/admin/coupons", tags=["admin-coupons"])

def _admin(user: AuthenticatedUser = Depends(get_current_user)):
    return require_admin(user)

def _status(coupon: Coupon) -> str:
    if not coupon.is_active: return "disabled"
    now = datetime.now(timezone.utc)
    if coupon.starts_at and coupon.starts_at > now: return "scheduled"
    if coupon.expires_at and coupon.expires_at <= now: return "expired"
    return "active"

def _read(c: Coupon) -> CouponRead:
    return CouponRead.model_validate({**{k: getattr(c, k) for k in CouponPayload.model_fields}, "id": c.id, "created_at": c.created_at, "updated_at": c.updated_at, "status": _status(c)})

@router.get("", response_model=list[CouponRead])
def list_coupons(_: AuthenticatedUser = Depends(_admin), db: Session = Depends(get_db)):
    return [_read(c) for c in db.scalars(select(Coupon).order_by(Coupon.created_at.desc())).all()]

@router.post("", response_model=CouponRead, status_code=status.HTTP_201_CREATED)
def create_coupon(payload: CouponPayload, _: AuthenticatedUser = Depends(_admin), db: Session = Depends(get_db)):
    coupon = Coupon(**payload.model_dump())
    db.add(coupon)
    try:
        db.commit(); db.refresh(coupon)
    except IntegrityError:
        db.rollback(); raise HTTPException(status_code=409, detail="Coupon code already exists")
    return _read(coupon)

@router.patch("/{coupon_id}", response_model=CouponRead)
def update_coupon(coupon_id: UUID, payload: CouponPayload, _: AuthenticatedUser = Depends(_admin), db: Session = Depends(get_db)):
    coupon = db.get(Coupon, coupon_id)
    if not coupon: raise HTTPException(status_code=404, detail="Coupon not found")
    for key, value in payload.model_dump().items(): setattr(coupon, key, value)
    try:
        db.commit(); db.refresh(coupon)
    except IntegrityError:
        db.rollback(); raise HTTPException(status_code=409, detail="Coupon code already exists")
    return _read(coupon)

@router.post("/{coupon_id}/activate", response_model=CouponRead)
def activate_coupon(coupon_id: UUID, _: AuthenticatedUser = Depends(_admin), db: Session = Depends(get_db)):
    coupon = db.get(Coupon, coupon_id)
    if not coupon: raise HTTPException(status_code=404, detail="Coupon not found")
    coupon.is_active = True; db.commit(); db.refresh(coupon); return _read(coupon)

@router.post("/{coupon_id}/deactivate", response_model=CouponRead)
def deactivate_coupon(coupon_id: UUID, _: AuthenticatedUser = Depends(_admin), db: Session = Depends(get_db)):
    coupon = db.get(Coupon, coupon_id)
    if not coupon: raise HTTPException(status_code=404, detail="Coupon not found")
    coupon.is_active = False; db.commit(); db.refresh(coupon); return _read(coupon)
