from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import UsageType, UsageUnit


class UsageRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("call_id", name="uq_usage_records_call_id"),)

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    wallet_id: Mapped[UUID] = mapped_column(ForeignKey("credit_wallets.id"), index=True, nullable=False)
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id"), nullable=True, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    employee_id: Mapped[UUID | None] = mapped_column(ForeignKey("ai_employees.id"), nullable=True, index=True)
    usage_type: Mapped[UsageType] = mapped_column(String(64), nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    unit: Mapped[UsageUnit] = mapped_column(String(32), nullable=False)
    cost_credits: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    provider_cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    customer_charge_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="INR")
    occurred_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    tenant = relationship("Tenant")
    wallet = relationship("CreditWallet", back_populates="usage_records")
    call = relationship("Call", back_populates="usage_records")
