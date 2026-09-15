from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import NumberStatus, PhoneOwnership


class PhoneNumber(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "phone_numbers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "e164_number", name="uq_phone_numbers_tenant_e164"),
        UniqueConstraint("provider_name", "provider_phone_number_id", name="uq_phone_numbers_provider_id"),
    )

    # Null means the record is synced platform inventory, not tenant-owned.
    tenant_id: Mapped[UUID | None] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    e164_number: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_phone_number_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ownership: Mapped[str] = mapped_column(String(32), nullable=False, default=PhoneOwnership.tenant.value)
    status: Mapped[NumberStatus] = mapped_column(
        String(32), nullable=False, default=NumberStatus.provisioning.value
    )
    employee_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_employees.id"), nullable=True)
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    release_idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    release_failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant = relationship("Tenant", back_populates="phone_numbers")
    employee = relationship("AIEmployee")
    campaign = relationship("Campaign", back_populates="phone_numbers", foreign_keys=[campaign_id])
    demo_access = relationship("PlatformDemoPhoneAccess", back_populates="phone", cascade="all, delete-orphan")
