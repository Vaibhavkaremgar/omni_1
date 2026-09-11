from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UserRole, UserStatus


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(String(32), nullable=False, default=UserRole.member.value)
    status: Mapped[UserStatus] = mapped_column(String(32), nullable=False, default=UserStatus.invited.value)
    last_login_at: Mapped[datetime | None] = mapped_column(nullable=True)
    must_change_password: Mapped[bool] = mapped_column(default=False, nullable=False)

    tenant = relationship("Tenant", back_populates="users")
    created_employee_versions = relationship("AIEmployeeVersion", back_populates="created_by_user")
    billing_transactions = relationship("BillingTransaction", back_populates="created_by_user")
