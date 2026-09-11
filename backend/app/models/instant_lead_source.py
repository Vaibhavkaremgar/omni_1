from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class InstantLeadSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "instant_lead_sources"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("ai_employees.id"), nullable=False)
    phone_number_id: Mapped[UUID] = mapped_column(ForeignKey("phone_numbers.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_imported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    rows = relationship("InstantLeadRow", back_populates="source", cascade="all, delete-orphan")


class InstantLeadRow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "instant_lead_rows"
    __table_args__ = (UniqueConstraint("source_id", "fingerprint", name="uq_instant_lead_source_fingerprint"),)
    source_id: Mapped[UUID] = mapped_column(ForeignKey("instant_lead_sources.id"), index=True, nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    lead_id: Mapped[UUID | None] = mapped_column(ForeignKey("leads.id"), nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)

    source = relationship("InstantLeadSource", back_populates="rows")
