from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer

from app.services.employee_configuration import public_employee_configuration


class AIEmployeeVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    employee_id: UUID
    version_number: int
    status: str
    configuration: dict | None
    change_summary: str | None
    published_at: datetime | None
    reviewed_at: datetime | None
    created_by_user_id: UUID | None
    created_at: datetime
    updated_at: datetime
    provider_name: str | None = None
    provider_agent_id: str | None = None
    provider_status: str | None = None

    @field_serializer("configuration")
    def serialize_configuration(self, value: dict | None) -> dict | None:
        return public_employee_configuration(value)
