from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.ai_employee_version import AIEmployeeVersionRead


# Outbound is not offered as a self-service option. Only inbound is the
# customer-facing default. Outbound requires a manual enablement step.
CallType = Literal["inbound", "outbound", "both"]
SELF_SERVICE_CALL_TYPES = {"inbound", "both"}
CreationMode = Literal["chat", "prompt"]
SUPPORTED_EMPLOYEE_LANGUAGES = {"English", "Hindi", "Telugu", "Tamil", "Kannada", "Malayalam", "Marathi", "Bengali", "Gujarati", "Punjabi", "Odia", "Assamese"}


class AIEmployeeCreate(BaseModel):
    name: str = Field(default="New AI Employee", min_length=1, max_length=255)
    purpose: str = Field(default="To be defined through the builder", min_length=1, max_length=5000)
    call_type: CallType = "inbound"
    language: str = Field(..., min_length=1, max_length=100)
    creation_mode: CreationMode = "chat"
    direct_prompt: str | None = Field(default=None, max_length=30000)
    selected_template_id: str | None = Field(default=None, min_length=1, max_length=120)
    selected_template_version: int = Field(default=1, ge=1)
    template_values: dict | None = None

    @field_validator("language")
    @classmethod
    def language_must_be_supported(cls, v: str) -> str:
        if v not in SUPPORTED_EMPLOYEE_LANGUAGES:
            raise ValueError("Unsupported employee language")
        return v

    @field_validator("call_type")
    @classmethod
    def call_type_must_be_self_service(cls, v: str) -> str:
        if v not in SELF_SERVICE_CALL_TYPES:
            raise ValueError(
                "Outbound calling is available by request. Contact us to enable it."
            )
        return v


class AIEmployeeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    purpose: str | None = Field(default=None, min_length=1, max_length=5000)
    call_type: CallType | None = None
    language: str | None = Field(default=None, min_length=1, max_length=100)
    creation_mode: CreationMode | None = None
    configuration: dict | None = None

    @field_validator("call_type")
    @classmethod
    def call_type_must_be_self_service(cls, v: str | None) -> str | None:
        if v is not None and v not in SELF_SERVICE_CALL_TYPES:
            raise ValueError(
                "Outbound calling is available by request. Contact us to enable it."
            )
        return v


class AIEmployeeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    purpose: str
    call_type: str
    language: str
    creation_mode: str
    status: str
    configuration: dict | None
    created_at: datetime
    updated_at: datetime
    draft_version: "AIEmployeeVersionRead | None" = None
    published_version: "AIEmployeeVersionRead | None" = None
    provider_name: str | None = None
    provider_agent_id: str | None = None
    provider_status: str | None = None
