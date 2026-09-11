from secrets import token_urlsafe
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import get_current_user, get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.admin import ClientCreate, ClientRead, ClientCreated, ClientStatusUpdate
from app.services.auth import AuthenticatedUser, hash_password, require_admin

router = APIRouter(prefix="/admin/clients", tags=["admin"])

def _admin(user: AuthenticatedUser = Depends(get_current_user)):
    return require_admin(user)

def _read(user: User) -> ClientRead:
    return ClientRead(id=user.id, tenant_id=user.tenant_id, tenant_name=user.tenant.name, email=user.email, full_name=user.full_name, status=user.status, tenant_status=user.tenant.status, created_at=user.created_at)

@router.get("", response_model=list[ClientRead])
def list_clients(_: AuthenticatedUser = Depends(_admin), db: Session = Depends(get_db)):
    return [_read(user) for user in db.scalars(select(User).where(User.role != "admin").order_by(User.created_at.desc())).all()]

@router.post("", response_model=ClientCreated, status_code=status.HTTP_201_CREATED)
def create_client(payload: ClientCreate, _: AuthenticatedUser = Depends(_admin), db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account already exists for this email")
    temporary_password = token_urlsafe(16)
    tenant = Tenant(name=payload.tenant_name, slug=f"{token_urlsafe(8).lower()}", status="active")
    user = User(tenant=tenant, email=email, full_name=payload.full_name, password_hash=hash_password(temporary_password), role="member", status="active", must_change_password=True)
    db.add(user); db.commit(); db.refresh(user)
    return ClientCreated(**_read(user).model_dump(), temporary_password=temporary_password)

@router.patch("/{client_id}", response_model=ClientRead)
def deactivate_client(client_id: UUID, payload: ClientStatusUpdate, _: AuthenticatedUser = Depends(_admin), db: Session = Depends(get_db)):
    user = db.get(User, client_id)
    if not user or user.role == "admin": raise HTTPException(status_code=404, detail="Client not found")
    if payload.status not in {"active", "suspended"}:
        raise HTTPException(status_code=400, detail="Unsupported client status")
    user.status = "active" if payload.status == "active" else "disabled"
    user.tenant.status = payload.status
    db.commit(); db.refresh(user)
    return _read(user)
