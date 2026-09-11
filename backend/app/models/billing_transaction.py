from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import BillingTransactionStatus, BillingTransactionType


class BillingTransaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "billing_transactions"
    __table_args__ = (
        UniqueConstraint("provider_reference", name="uq_billing_transactions_provider_reference"),
        UniqueConstraint("provider_payment_id", name="uq_billing_transactions_provider_payment_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    wallet_id: Mapped[UUID | None] = mapped_column(ForeignKey("credit_wallets.id"), nullable=True, index=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    transaction_type: Mapped[BillingTransactionType] = mapped_column(String(32), nullable=False)
    status: Mapped[BillingTransactionStatus] = mapped_column(
        String(32), nullable=False, default=BillingTransactionStatus.pending.value
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="INR")
    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    issued_at: Mapped[datetime] = mapped_column(nullable=False, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant = relationship("Tenant")
    wallet = relationship("CreditWallet")
    created_by_user = relationship("User", back_populates="billing_transactions")
