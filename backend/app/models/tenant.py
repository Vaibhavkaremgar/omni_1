from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import TenantStatus


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[TenantStatus] = mapped_column(
        String(32), nullable=False, default=TenantStatus.active.value
    )
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Reseller state only. KYC documents, identifiers and one-time codes are never stored.
    omni_reseller_user_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True, index=True)
    omni_reseller_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    omni_reseller_kyc_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    omni_reseller_region: Mapped[str | None] = mapped_column(String(8), nullable=True)
    omni_reseller_verified_at: Mapped[datetime | None] = mapped_column(nullable=True)
    # Required by OmniDimension when a reseller creates its child user. This is
    # ordinary account contact data, not a KYC document or identifier.
    omni_reseller_contact_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    instant_leads_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    # Notification preferences — only options with real backend behavior
    notify_campaign_completed: Mapped[bool] = mapped_column(default=True, nullable=False)
    notify_low_balance: Mapped[bool] = mapped_column(default=True, nullable=False)

    users = relationship("User", back_populates="tenant", cascade="all, delete-orphan")
    ai_employees = relationship("AIEmployee", back_populates="tenant", cascade="all, delete-orphan")
    phone_numbers = relationship("PhoneNumber", back_populates="tenant", cascade="all, delete-orphan")
    campaigns = relationship("Campaign", back_populates="tenant", cascade="all, delete-orphan")
    leads = relationship("Lead", back_populates="tenant", cascade="all, delete-orphan")
    calls = relationship("Call", back_populates="tenant", cascade="all, delete-orphan")
    credit_wallet = relationship("CreditWallet", back_populates="tenant", uselist=False, cascade="all, delete-orphan")
    integration_connections = relationship("IntegrationConnection", back_populates="tenant", cascade="all, delete-orphan")
