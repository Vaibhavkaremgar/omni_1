from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.credit_transaction import CreditTransaction
from app.models.credit_wallet import CreditWallet
from app.models.usage_record import UsageRecord
from app.schemas.billing import (TopUpOrderCreate, TopUpOrderRead, TopUpVerify, TopUpVerifyRead,
    TransactionRead, UsageRead, WalletRead)
from app.models.billing_transaction import BillingTransaction
from app.models.enums import BillingTransactionStatus, BillingTransactionType
from app.integrations.razorpay import RazorpayClient, RazorpayError
from app.services.top_ups import confirm_provider_payment, paise
from app.services.auth import AuthenticatedUser
from app.services.wallets import LOW_BALANCE_THRESHOLD_MINUTES, credit_verified_top_up, ensure_wallet
from app.core.config import get_settings


MONEY_QUANTUM = Decimal("0.01")


def money(value: Decimal | None) -> Decimal | None:
    return value.quantize(MONEY_QUANTUM) if value is not None else None


router = APIRouter(prefix="/billing", tags=["billing"])
razorpay_client = RazorpayClient()


@router.get("/wallet", response_model=WalletRead)
def read_wallet(current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)) -> WalletRead:
    wallet = ensure_wallet(db, current_user.tenant.id)
    db.commit()
    available_value = max(Decimal("0"), Decimal(wallet.balance_credits)).quantize(MONEY_QUANTUM)
    available_minutes = (available_value / get_settings().call_price_inr).quantize(Decimal("0.0001"))
    balance_status = "EXHAUSTED" if available_minutes <= 0 else "LOW_BALANCE" if available_minutes <= LOW_BALANCE_THRESHOLD_MINUTES else "AVAILABLE"
    return WalletRead(
        id=wallet.id,
        balance=Decimal(wallet.balance_credits).quantize(MONEY_QUANTUM),
        available_minutes=available_minutes,
        available_value=available_value,
        available_value_inr=available_value,
        balance_status=balance_status,
        low_balance_threshold_minutes=LOW_BALANCE_THRESHOLD_MINUTES,
        currency=wallet.currency,
        status=wallet.status,
        call_price_per_minute=get_settings().call_price_inr.quantize(MONEY_QUANTUM),
        minimum_balance=get_settings().minimum_call_balance_inr.quantize(MONEY_QUANTUM),
        updated_at=wallet.updated_at,
    )


@router.get("/transactions", response_model=list[TransactionRead])
def read_transactions(
    current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TransactionRead]:
    rows = db.scalars(
        select(CreditTransaction)
        .where(CreditTransaction.tenant_id == current_user.tenant.id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(100)
    ).all()
    return [TransactionRead(
        id=row.id, wallet_id=row.wallet_id, call_id=row.call_id, transaction_type=row.transaction_type,
        status=row.status, amount=money(row.amount_credits), balance_before=money(row.balance_before),
        balance_after=money(row.balance_after), currency=row.currency, description=row.description,
        created_at=row.created_at,
    ) for row in rows]


@router.post("/top-ups/order", response_model=TopUpOrderRead, status_code=status.HTTP_201_CREATED)
def create_top_up_order(
    payload: TopUpOrderCreate, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> TopUpOrderRead:
    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payments are not configured.")
    if payload.amount < settings.razorpay_minimum_top_up_inr or payload.amount > settings.razorpay_maximum_top_up_inr:
        raise HTTPException(status_code=422, detail="Top-up amount is outside the allowed range.")
    wallet = ensure_wallet(db, current_user.tenant.id)
    top_up = BillingTransaction(
        tenant_id=current_user.tenant.id, wallet_id=wallet.id, created_by_user_id=current_user.user.id,
        transaction_type=BillingTransactionType.payment.value, status=BillingTransactionStatus.pending.value,
        amount=payload.amount, currency="INR", description="Razorpay wallet top-up", issued_at=datetime.now(timezone.utc),
    )
    db.add(top_up)
    db.flush()
    receipt = f"topup_{top_up.id.hex[:28]}"
    try:
        order = razorpay_client.create_order(amount_paise=paise(payload.amount), receipt=receipt, notes={"top_up_id": str(top_up.id)})
        if not isinstance(order.get("id"), str) or order.get("amount") != paise(payload.amount) or order.get("currency") != "INR":
            raise RazorpayError("Invalid order response")
    except RazorpayError as exc:
        top_up.status = BillingTransactionStatus.failed.value
        db.commit()
        raise HTTPException(status_code=502, detail="Unable to create payment order.") from exc
    top_up.provider_reference = order["id"]
    top_up.extra_data = {"receipt": receipt}
    db.commit()
    return TopUpOrderRead(top_up_id=top_up.id, razorpay_order_id=order["id"], amount=payload.amount, currency="INR", key_id=settings.razorpay_key_id)


@router.post("/top-ups/verify", response_model=TopUpVerifyRead)
def verify_top_up(
    payload: TopUpVerify, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> TopUpVerifyRead:
    top_up = db.scalar(select(BillingTransaction).where(
        BillingTransaction.provider_reference == payload.razorpay_order_id,
        BillingTransaction.tenant_id == current_user.tenant.id,
        BillingTransaction.transaction_type == BillingTransactionType.payment.value,
    ).with_for_update())
    if top_up is None:
        raise HTTPException(status_code=404, detail="Top-up order not found.")
    if top_up.status == BillingTransactionStatus.paid.value:
        wallet = ensure_wallet(db, current_user.tenant.id)
        return TopUpVerifyRead(top_up_id=top_up.id, status="paid", balance=money(Decimal(wallet.balance_credits)), currency="INR")
    if not razorpay_client.verify_checkout_signature(order_id=top_up.provider_reference or "", payment_id=payload.razorpay_payment_id, signature=payload.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature.")
    confirm_provider_payment(razorpay_client, top_up, payload.razorpay_payment_id)
    try:
        wallet = credit_verified_top_up(db, top_up, payload.razorpay_payment_id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        settled = db.scalar(select(BillingTransaction).where(BillingTransaction.provider_reference == payload.razorpay_order_id))
        if settled is not None and settled.status == BillingTransactionStatus.paid.value:
            settled_wallet = ensure_wallet(db, current_user.tenant.id)
            return TopUpVerifyRead(top_up_id=settled.id, status="paid", balance=money(Decimal(settled_wallet.balance_credits)), currency="INR")
        raise HTTPException(status_code=409, detail="Payment was already used.") from exc
    return TopUpVerifyRead(top_up_id=top_up.id, status="paid", balance=money(Decimal(wallet.balance_credits)), currency="INR")


@router.get("/usage", response_model=list[UsageRead])
def read_usage(
    current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[UsageRead]:
    rows = db.scalars(
        select(UsageRecord)
        .where(UsageRecord.tenant_id == current_user.tenant.id)
        .order_by(UsageRecord.occurred_at.desc())
        .limit(100)
    ).all()
    return [UsageRead(
        id=row.id, wallet_id=row.wallet_id, call_id=row.call_id, employee_id=row.employee_id,
        duration_seconds=row.duration_seconds, minutes=row.quantity, unit_price=money(row.unit_price),
        amount=money(row.customer_charge_amount), currency=row.currency, occurred_at=row.occurred_at,
        created_at=row.created_at,
    ) for row in rows]
