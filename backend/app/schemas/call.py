from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class InstantCallRequest(ORMBaseModel):
    employee_id: UUID
    phone_number_id: UUID
    destination_phone_number: str = Field(min_length=8, max_length=32)
    customer_name: str | None = Field(default=None, max_length=255)
    lead_id: UUID | None = None
    context: str | None = Field(default=None, max_length=2000)

    @field_validator("destination_phone_number")
    @classmethod
    def validate_e164(cls, value: str) -> str:
        normalized = value.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        if not normalized.startswith("+") or not normalized[1:].isdigit() or not 7 <= len(normalized[1:]) <= 15:
            raise ValueError("Destination must be an E.164 phone number with a country code")
        return normalized


class InstantCallRead(IDSchema, TimestampSchema):
    employee_id: UUID
    lead_id: UUID | None = None
    phone_number_id: UUID | None = None
    direction: str
    status: str
    customer_name: str | None = None
    customer_phone_number: str | None = None
    provider_call_id: str | None = None


class CallCreate(ORMBaseModel):
    tenant_id: str
    employee_id: str
    direction: str
    customer_phone_number: str | None = None


class CallRead(IDSchema, TimestampSchema):
    employee_id: UUID
    campaign_id: UUID | None = None
    lead_id: UUID | None = None
    phone_number_id: UUID | None = None
    direction: str
    status: str
    customer_name: str | None = None
    customer_phone_number: str | None = None
    duration_seconds: int | None = None
    recording_url: str | None = None
    transcript: str | None = None
    summary: str | None = None
    sentiment: str | None = None
    extracted_attributes: dict | None = None
    outcome: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
