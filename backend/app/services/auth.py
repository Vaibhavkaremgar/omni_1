from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
import jwt
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.tenant import Tenant
from app.models.user import User

JWT_ALGORITHM = "HS256"


@dataclass(frozen=True)
class AuthenticatedUser:
    user: User
    tenant: Tenant


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: UUID | str) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.auth_access_token_expire_minutes)
    return jwt.encode({"sub": str(user_id), "exp": expires_at}, settings.auth_secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> UUID:
    try:
        payload = jwt.decode(token, get_settings().auth_secret_key, algorithms=[JWT_ALGORITHM])
        return UUID(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def resolve_authenticated_user(db: Session, token: str) -> AuthenticatedUser:
    user = db.get(User, decode_access_token(token))
    if user is None or user.status != "active" or user.tenant is None or user.tenant.status != "active":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user is not provisioned for an active tenant",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return AuthenticatedUser(user=user, tenant=user.tenant)
