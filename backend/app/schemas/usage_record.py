from datetime import datetime
from decimal import Decimal

from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class UsageRecordRead(IDSchema, TimestampSchema):
    wallet_id: str
    call_id: str | None = None
    campaign_id: str | None = None
    employee_id: str | None = None
    usage_type: str
    duration_seconds: int | None = None
    quantity: Decimal
    unit: str
    cost_credits: Decimal | None = None
    unit_price: Decimal | None = None
    customer_charge_amount: Decimal | None = None
    currency: str
    occurred_at: datetime
