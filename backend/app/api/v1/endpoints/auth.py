from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import AuthCredentials, AuthMeRead, AuthTokenRead, PasswordChange
from app.services.auth import AuthenticatedUser, create_access_token, hash_password, verify_password


router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(user: User, tenant: Tenant) -> AuthTokenRead:
    return AuthTokenRead(access_token=create_access_token(user.id), user=user, tenant=tenant)


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

@router.post("/change-password", response_model=AuthTokenRead)
def change_password(payload: PasswordChange, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)) -> AuthTokenRead:
    current_user.user.password_hash = hash_password(payload.new_password)
    current_user.user.must_change_password = False
    db.commit()
    return _token_response(current_user.user, current_user.tenant)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(_: AuthenticatedUser = Depends(get_current_user)) -> Response:
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AuthMeRead)
def read_current_user(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthMeRead:
    return AuthMeRead.model_validate({"user": current_user.user, "tenant": current_user.tenant})
