from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CallDirection, CallStatus


class Call(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "calls"
    __table_args__ = ()

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("ai_employees.id"), index=True, nullable=False)
    employee_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_employee_versions.id"), nullable=True, index=True
    )
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    campaign_contact_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("campaign_contacts.id"), nullable=True, index=True
    )
    lead_id: Mapped[UUID | None] = mapped_column(ForeignKey("leads.id"), nullable=True, index=True)
    phone_number_id: Mapped[UUID | None] = mapped_column(ForeignKey("phone_numbers.id"), nullable=True)
    direction: Mapped[CallDirection] = mapped_column(String(32), nullable=False)
    status: Mapped[CallStatus] = mapped_column(String(32), nullable=False, default=CallStatus.queued.value)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_call_id: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recording_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    sentiment: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extracted_attributes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    dispatch_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    transcript_data: Mapped[list | None] = mapped_column(JSON, nullable=True)
    analysis_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    analysis_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    customer_intent: Mapped[str | None] = mapped_column(String(255), nullable=True)
    key_points: Mapped[list | None] = mapped_column(JSON, nullable=True)
    action_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    follow_up_required: Mapped[bool | None] = mapped_column(nullable=True)
    follow_up_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant = relationship("Tenant", back_populates="calls")
    employee = relationship("AIEmployee", back_populates="calls")
    campaign = relationship("Campaign", back_populates="calls")
    campaign_contact = relationship("CampaignContact", foreign_keys=[campaign_contact_id])
    lead = relationship("Lead", back_populates="calls")
    phone_number = relationship("PhoneNumber")
    usage_records = relationship("UsageRecord", back_populates="call")
    credit_transactions = relationship("CreditTransaction", back_populates="call")
