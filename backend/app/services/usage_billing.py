from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.call import Call
from app.models.credit_transaction import CreditTransaction
from app.models.credit_wallet import CreditWallet
from app.models.enums import CreditTransactionStatus, CreditTransactionType, UsageType, UsageUnit
from app.models.usage_record import UsageRecord
from app.services.pricing import calculate_call_charge, call_price_inr
from app.services.wallets import InsufficientBalanceError, ensure_wallet


class BillingAttemptError(Exception):
    pass


def charge_completed_call(db: Session, call: Call) -> UsageRecord | None:
    existing = db.scalar(select(UsageRecord).where(UsageRecord.call_id == call.id))
    if existing is not None:
        return existing
    if call.duration_seconds is None or call.duration_seconds < 0:
        return None
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == call.tenant_id).with_for_update())
    if wallet is None:
        wallet = ensure_wallet(db, call.tenant_id)
    if Decimal(wallet.balance_credits) < Decimal("0"):
        raise InsufficientBalanceError("Wallet balance cannot be negative.")
    minutes, amount = calculate_call_charge(call.duration_seconds, call_price_inr())
    before = Decimal(wallet.balance_credits)
    if before < amount:
        raise InsufficientBalanceError("Wallet balance is too low for this call charge.")
    after = before - amount
    wallet.balance_credits = after
    usage = UsageRecord(
        tenant_id=call.tenant_id,
        wallet_id=wallet.id,
        call_id=call.id,
        employee_id=call.employee_id,
        usage_type=UsageType.call_minutes.value,
        duration_seconds=call.duration_seconds,
        quantity=minutes,
        unit=UsageUnit.minutes.value,
        cost_credits=amount,
        unit_price=call_price_inr(),
        customer_charge_amount=amount,
        currency="INR",
        occurred_at=call.ended_at or call.created_at,
    )
    db.add(usage)
    db.add(CreditTransaction(
        tenant_id=call.tenant_id,
        wallet_id=wallet.id,
        call_id=call.id,
        transaction_type=CreditTransactionType.call_usage.value,
        status=CreditTransactionStatus.posted.value,
        amount_credits=-amount,
        balance_before=before,
        balance_after=after,
        customer_charge_amount=amount,
        currency="INR",
        description=f"Call usage: {call.duration_seconds} seconds",
        reference_type="call",
        reference_id=str(call.id),
    ))
    return usage
