from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CampaignStatus


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[CampaignStatus] = mapped_column(
        String(32), nullable=False, default=CampaignStatus.draft.value
    )
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("ai_employees.id"), nullable=False)
    # The phone number selected when the campaign was started. Stored so every
    # subsequent dispatch (resume, retry) uses the same tenant-owned number.
    phone_number_id: Mapped[UUID | None] = mapped_column(ForeignKey("phone_numbers.id"), nullable=True)
    schedule_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(nullable=True)
    scheduled_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    calling_window_start: Mapped[str | None] = mapped_column(String(8), nullable=True)
    calling_window_end: Mapped[str | None] = mapped_column(String(8), nullable=True)
    max_attempts: Mapped[int] = mapped_column(default=3, nullable=False)
    concurrency: Mapped[int] = mapped_column(default=1, nullable=False)
    retry_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    retry_intervals: Mapped[list | None] = mapped_column(JSON, nullable=True)

    tenant = relationship("Tenant", back_populates="campaigns")
    employee = relationship("AIEmployee", back_populates="campaigns")
    phone_number = relationship("PhoneNumber", foreign_keys=[phone_number_id])
    phone_numbers = relationship("PhoneNumber", back_populates="campaign", foreign_keys="PhoneNumber.campaign_id")
    contacts = relationship("CampaignContact", back_populates="campaign", cascade="all, delete-orphan")
    calls = relationship("Call", back_populates="campaign")
