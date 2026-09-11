from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class InstantLeadSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "instant_lead_sources"
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("ai_employees.id"), nullable=False)
    phone_number_id: Mapped[UUID] = mapped_column(ForeignKey("phone_numbers.id"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), default="Google Sheet", nullable=False)
    name: Mapped[str] = mapped_column(String(255), default="Instant Leads source", nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="google_sheet", nullable=False)
    integration_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    spreadsheet_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frequency_minutes: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    auto_call: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    working_hours: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    daily_call_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_imported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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
    external_record_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False)

    source = relationship("InstantLeadSource", back_populates="rows")
