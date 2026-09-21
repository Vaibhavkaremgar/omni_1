from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator


class WalletRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    balance: Decimal
    available_minutes: Decimal
    available_value: Decimal
    available_value_inr: Decimal
    balance_status: str
    low_balance_threshold_minutes: Decimal
    currency: str
    status: str
    call_price_per_minute: Decimal
    minimum_balance: Decimal
    updated_at: datetime


class TransactionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    wallet_id: UUID
    call_id: UUID | None
    transaction_type: str
    status: str
    amount: Decimal
    balance_before: Decimal | None
    balance_after: Decimal | None
    currency: str
    description: str | None
    created_at: datetime


class UsageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    wallet_id: UUID
    call_id: UUID | None
    employee_id: UUID | None
    duration_seconds: int | None
    minutes: Decimal
    unit_price: Decimal | None
    amount: Decimal | None
    currency: str
    occurred_at: datetime
    created_at: datetime


class TopUpOrderCreate(BaseModel):
    amount: Decimal

    @field_validator("amount")
    @classmethod
    def valid_money(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -2:
            raise ValueError("Amount must be a positive INR value with at most two decimal places.")
        return value.quantize(Decimal("0.01"))


class TopUpOrderRead(BaseModel):
    top_up_id: UUID
    razorpay_order_id: str
    amount: Decimal
    currency: str
    key_id: str


class TopUpVerify(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class TopUpVerifyRead(BaseModel):
    top_up_id: UUID
    status: str
    balance: Decimal
    currency: str
