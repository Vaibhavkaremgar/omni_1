from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PhonePurchase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The durable boundary between customer payment and provider fulfillment."""
    __tablename__ = "phone_purchases"
    __table_args__ = (UniqueConstraint("razorpay_order_id", name="uq_phone_purchases_razorpay_order"),)

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    phone_number: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(8), nullable=False)
    carrier: Mapped[str] = mapped_column(String(80), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    razorpay_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    razorpay_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    fulfillment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    omni_order_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    omni_idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
