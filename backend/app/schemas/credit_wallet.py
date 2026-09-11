from decimal import Decimal

from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class CreditWalletRead(IDSchema, TimestampSchema):
    balance_credits: Decimal
    reserved_credits: Decimal
    currency: str
    status: str
