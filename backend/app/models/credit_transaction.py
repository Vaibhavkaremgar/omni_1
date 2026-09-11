from decimal import Decimal
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import UniqueConstraint

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CreditTransactionStatus, CreditTransactionType


class CreditTransaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "credit_transactions"
    __table_args__ = (
        UniqueConstraint("call_id", name="uq_credit_transactions_call_id"),
        UniqueConstraint("reference_type", "reference_id", name="uq_credit_transactions_reference"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    wallet_id: Mapped[UUID] = mapped_column(ForeignKey("credit_wallets.id"), index=True, nullable=False)
    call_id: Mapped[UUID | None] = mapped_column(ForeignKey("calls.id"), nullable=True, index=True)
    campaign_id: Mapped[UUID | None] = mapped_column(ForeignKey("campaigns.id"), nullable=True, index=True)
    transaction_type: Mapped[CreditTransactionType] = mapped_column(
        String(32), nullable=False
    )
    status: Mapped[CreditTransactionStatus] = mapped_column(
        String(32), nullable=False, default=CreditTransactionStatus.posted.value
    )
    amount_credits: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    balance_before: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    balance_after: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    provider_cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    customer_charge_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    currency: Mapped[str] = mapped_column(String(16), nullable=False, default="INR")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reference_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    extra_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    tenant = relationship("Tenant")
    wallet = relationship("CreditWallet", back_populates="credit_transactions")
    call = relationship("Call", back_populates="credit_transactions")
