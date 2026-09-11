from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class BillingTransactionRead(IDSchema, TimestampSchema):
    tenant_id: str
    wallet_id: str | None = None
    transaction_type: str
    status: str
    amount: float
    currency: str
    provider_reference: str | None = None

