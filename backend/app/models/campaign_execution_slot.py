from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CampaignExecutionSlot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Durable per-campaign concurrency reservation."""
    __tablename__ = "campaign_execution_slots"
    __table_args__ = (UniqueConstraint("campaign_id", "slot_number", name="uq_campaign_execution_slot"),)

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    campaign_id: Mapped[UUID] = mapped_column(ForeignKey("campaigns.id"), index=True, nullable=False)
    campaign_contact_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaign_contacts.id"), nullable=True, index=True)
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id"), nullable=True, index=True)
    slot_number: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    reserved_at: Mapped[datetime] = mapped_column(nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(nullable=True)

    campaign = relationship("Campaign")
    campaign_contact = relationship("CampaignContact")
    call = relationship("Call")
