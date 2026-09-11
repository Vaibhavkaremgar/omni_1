from decimal import Decimal

from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class CreditTransactionRead(IDSchema, TimestampSchema):
    wallet_id: str
    transaction_type: str
    status: str
    amount_credits: Decimal
    balance_before: Decimal | None = None
    balance_after: Decimal | None = None
    provider_cost_amount: Decimal | None = None
    customer_charge_amount: Decimal | None = None
    currency: str
    call_id: str | None = None
    description: str | None = None
    reference_type: str | None = None
    reference_id: str | None = None
