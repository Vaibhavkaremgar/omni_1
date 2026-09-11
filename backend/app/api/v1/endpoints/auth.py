import re
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import AuthCredentials, AuthMeRead, AuthRegister, AuthTokenRead
from app.services.auth import AuthenticatedUser, create_access_token, hash_password, verify_password


router = APIRouter(prefix="/auth", tags=["auth"])


def _tenant_slug(email: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@", 1)[0].lower()).strip("-") or "tenant"
    return f"{base}-{uuid4().hex[:8]}"


def _token_response(user: User, tenant: Tenant) -> AuthTokenRead:
    return AuthTokenRead(access_token=create_access_token(user.id), user=user, tenant=tenant)


@router.post("/register", response_model=AuthTokenRead, status_code=status.HTTP_201_CREATED)
def register(payload: AuthRegister, db: Session = Depends(get_db)) -> AuthTokenRead:
    email = payload.email.lower()
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account already exists for this email")
    tenant = Tenant(name=payload.tenant_name or email.split("@", 1)[0], slug=_tenant_slug(email), status="active")
    user = User(tenant=tenant, email=email, password_hash=hash_password(payload.password), status="active")
    try:
        db.add(user)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(user)
    return _token_response(user, tenant)


@router.post("/login", response_model=AuthTokenRead)
def login(payload: AuthCredentials, db: Session = Depends(get_db)) -> AuthTokenRead:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if user.status != "active" or user.tenant is None or user.tenant.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is inactive")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    return _token_response(user, user.tenant)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(_: AuthenticatedUser = Depends(get_current_user)) -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AuthMeRead)
def read_current_user(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthMeRead:
    return AuthMeRead.model_validate({"user": current_user.user, "tenant": current_user.tenant})
