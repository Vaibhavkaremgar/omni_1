from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, String, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ContactStatus


class CampaignContact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaign_contacts"
    __table_args__ = (
        UniqueConstraint("campaign_id", "normalized_phone", name="uq_campaign_contact_phone"),
        Index("ix_campaign_contacts_status", "status"),
        Index("ix_campaign_contacts_normalized_phone", "normalized_phone"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    lead_id: Mapped[UUID | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[ContactStatus] = mapped_column(
        String(32), nullable=False, default=ContactStatus.pending.value
    )
    attempt_count: Mapped[int] = mapped_column(default=0, nullable=False)
    last_called_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id"), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    provider_call_id: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    tenant = relationship("Tenant")
    campaign = relationship("Campaign", back_populates="contacts")
    lead = relationship("Lead", back_populates="campaign_contacts")
    last_call = relationship("Call", foreign_keys=[last_call_id], uselist=False)
