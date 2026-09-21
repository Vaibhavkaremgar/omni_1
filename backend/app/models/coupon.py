from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Coupon(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "coupons"
    __table_args__ = (
        UniqueConstraint("code", name="uq_coupons_code"),
        CheckConstraint("discount_value >= 0", name="ck_coupons_discount_value_nonnegative"),
        CheckConstraint("promotional_minutes IS NULL OR promotional_minutes >= 0", name="ck_coupons_promotional_minutes_nonnegative"),
        CheckConstraint("usage_limit IS NULL OR usage_limit >= 0", name="ck_coupons_usage_limit_nonnegative"),
        CheckConstraint("per_tenant_usage_limit IS NULL OR per_tenant_usage_limit >= 0", name="ck_coupons_tenant_usage_limit_nonnegative"),
    )

    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    discount_type: Mapped[str] = mapped_column(String(32), nullable=False, default="promotional_minutes")
    discount_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    max_discount_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    minimum_purchase_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    promotional_minutes: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    usage_limit: Mapped[int | None] = mapped_column(nullable=True)
    per_tenant_usage_limit: Mapped[int | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
