from datetime import datetime
from uuid import UUID
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

class CouponTenantShare(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "coupon_tenant_shares"
    __table_args__ = (UniqueConstraint("coupon_id", "tenant_id", name="uq_coupon_tenant_share"),)
    coupon_id: Mapped[UUID] = mapped_column(ForeignKey("coupons.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    shared_at: Mapped[datetime] = mapped_column(nullable=False)
    viewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    redeemed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SHARED")
