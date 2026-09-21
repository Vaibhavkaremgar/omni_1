from datetime import datetime
from decimal import Decimal
from uuid import UUID
from pydantic import Field, field_validator, model_validator
from app.schemas.base import ORMBaseModel

class CouponPayload(ORMBaseModel):
    code: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    discount_type: str = "promotional_minutes"
    discount_value: Decimal = Field(default=0, ge=0)
    max_discount_amount: Decimal | None = Field(default=None, ge=0)
    minimum_purchase_amount: Decimal | None = Field(default=None, ge=0)
    promotional_minutes: Decimal | None = Field(default=None, ge=0)
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    usage_limit: int | None = Field(default=None, ge=0)
    per_tenant_usage_limit: int | None = Field(default=None, ge=0)
    is_active: bool = True

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def validate_dates(self):
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be after starts_at")
        if self.discount_type == "promotional_minutes" and (self.promotional_minutes is None or self.promotional_minutes <= 0):
            raise ValueError("promotional_minutes must be greater than zero for this coupon type")
        return self

class CouponRead(CouponPayload):
    id: UUID
    created_at: datetime
    updated_at: datetime
    status: str
    used_count: int = 0
