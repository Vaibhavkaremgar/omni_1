from decimal import Decimal
from typing import Any

from pydantic import Field

from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class PhoneNumberRead(IDSchema, TimestampSchema):
    label: str | None = None
    e164_number: str
    provider_name: str | None = None
    provider_phone_number_id: str | None = None
    ownership: str = "tenant"
    status: str
    employee_id: str | None = None
    campaign_id: str | None = None
    capabilities: dict | None = None


class PlatformDemoPhoneCreate(ORMBaseModel):
    phone_number: str = Field(min_length=7, max_length=32, pattern=r"^\+[1-9][0-9]{6,30}$")
    provider_phone_number_id: str = Field(min_length=1, max_length=255)
    provider: str = Field(default="omnidimension", pattern=r"^omnidimension$")
    authorized_tenant_id: str | None = None


class MarketplaceNumberRead(ORMBaseModel):
    phone_number: str
    region: str
    carrier: str
    carrier_label: str | None = None
    customer_monthly_price_inr: Decimal
    validity_days: int | None = None
    kyc_required: bool | None = None


class MarketplaceSearchRead(ORMBaseModel):
    numbers: list[MarketplaceNumberRead]
    total: int
    page: int
    limit: int


class KycInitializeRequest(ORMBaseModel):
    phone: str = Field(min_length=7, max_length=32, pattern=r"^\+?[0-9][0-9 -]{5,30}$")


class KycStepRequest(ORMBaseModel):
    step: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$")
    region: str = Field(default="IN", pattern="^[A-Z]{2}$")
    carrier: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9-]+$")
    phone_number: str = Field(min_length=7, max_length=32, pattern=r"^\+[1-9][0-9]{6,30}$")
    values: dict[str, Any] = Field(default_factory=dict)


class PhonePurchaseOrderRequest(ORMBaseModel):
    phone_number: str = Field(min_length=7, max_length=32, pattern=r"^\+[1-9][0-9]{6,30}$")
    region: str = Field(pattern=r"^(IN|US)$")
    carrier: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9-]+$")


class PhonePurchaseOrderRead(ORMBaseModel):
    purchase_id: str
    razorpay_order_id: str
    amount: Decimal
    currency: str
    key_id: str


class PhonePurchaseVerify(ORMBaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class PhonePurchaseStatusRead(ORMBaseModel):
    purchase_id: str
    payment_status: str
    fulfillment_status: str
    phone_number: str
    message: str
