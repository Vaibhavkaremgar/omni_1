from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.credit_transaction import CreditTransaction
from app.models.credit_wallet import CreditWallet
from app.models.billing_transaction import BillingTransaction
from app.models.enums import CreditTransactionStatus, CreditTransactionType, WalletStatus


class WalletError(Exception):
    pass


class InsufficientBalanceError(WalletError):
    pass


def ensure_wallet(db: Session, tenant_id: UUID) -> CreditWallet:
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_id))
    if wallet is not None:
        return wallet
    wallet = CreditWallet(tenant_id=tenant_id, balance_credits=Decimal("0"), reserved_credits=Decimal("0"), currency="INR")
    db.add(wallet)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_id))
        if wallet is None:
            raise
    return wallet


def require_minimum_balance(db: Session, tenant_id: UUID, minimum: Decimal) -> CreditWallet:
    wallet = ensure_wallet(db, tenant_id)
    if wallet.status != WalletStatus.active.value or Decimal(wallet.balance_credits) < minimum:
        raise InsufficientBalanceError("Wallet balance is too low to place calls.")
    return wallet


def internal_top_up(db: Session, tenant_id: UUID, amount: Decimal, description: str = "Internal top-up") -> CreditWallet:
    if amount <= 0:
        raise WalletError("Top-up amount must be positive.")
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_id).with_for_update())
    if wallet is None:
        wallet = ensure_wallet(db, tenant_id)
    before = Decimal(wallet.balance_credits)
    after = before + amount
    wallet.balance_credits = after
    db.add(CreditTransaction(
        tenant_id=tenant_id,
        wallet_id=wallet.id,
        transaction_type=CreditTransactionType.top_up.value,
        status=CreditTransactionStatus.posted.value,
        amount_credits=amount,
        balance_before=before,
        balance_after=after,
        customer_charge_amount=amount,
        currency="INR",
        description=description,
        reference_type="internal_top_up",
    ))
    return wallet


def credit_verified_top_up(db: Session, top_up: BillingTransaction, payment_id: str) -> CreditWallet:
    """Atomically post one provider-verified top-up into the immutable ledger."""
    if top_up.status == "paid":
        wallet = db.scalar(select(CreditWallet).where(CreditWallet.id == top_up.wallet_id))
        if wallet is None:
            raise WalletError("Top-up has no wallet.")
        return wallet
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.id == top_up.wallet_id).with_for_update())
    if wallet is None or wallet.tenant_id != top_up.tenant_id:
        raise WalletError("Top-up has no valid wallet.")
    existing = db.scalar(select(CreditTransaction).where(
        CreditTransaction.reference_type == "razorpay_top_up",
        CreditTransaction.reference_id == str(top_up.id),
    ))
    if existing is not None:
        top_up.status = "paid"
        top_up.provider_payment_id = existing.extra_data.get("razorpay_payment_id") if existing.extra_data else payment_id
        return wallet
    before = Decimal(wallet.balance_credits)
    amount = Decimal(top_up.amount)
    wallet.balance_credits = before + amount
    top_up.status = "paid"
    top_up.provider_payment_id = payment_id
    from datetime import datetime, timezone
    top_up.processed_at = datetime.now(timezone.utc)
    db.add(CreditTransaction(
        tenant_id=top_up.tenant_id, wallet_id=wallet.id,
        transaction_type=CreditTransactionType.top_up.value,
        status=CreditTransactionStatus.posted.value,
        amount_credits=amount, balance_before=before, balance_after=before + amount,
        customer_charge_amount=amount, currency="INR", description="Razorpay wallet top-up",
        reference_type="razorpay_top_up", reference_id=str(top_up.id),
        extra_data={"razorpay_order_id": top_up.provider_reference, "razorpay_payment_id": payment_id},
    ))
    return wallet
