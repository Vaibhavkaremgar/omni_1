from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class OAuthState(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Short-lived, single-use OAuth state token.

    Prevents CSRF and state reuse. Expires after 10 minutes.
    """

    __tablename__ = "oauth_states"

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    integration_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state_token: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    used: Mapped[bool] = mapped_column(default=False, nullable=False)
