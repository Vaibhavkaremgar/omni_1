from uuid import UUID

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import WalletStatus


class CreditWallet(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "credit_wallets"
    __table_args__ = ()

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), unique=True, index=True, nullable=False)
    balance_credits: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    promotional_minutes: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    reserved_credits: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="INR")
    status: Mapped[WalletStatus] = mapped_column(String(32), nullable=False, default=WalletStatus.active.value)

    tenant = relationship("Tenant", back_populates="credit_wallet")
    credit_transactions = relationship("CreditTransaction", back_populates="wallet")
    usage_records = relationship("UsageRecord", back_populates="wallet")
