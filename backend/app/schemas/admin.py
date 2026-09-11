from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, EmailStr, Field

class ClientCreate(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    full_name: str | None = Field(default=None, max_length=255)

class ClientRead(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_name: str
    email: str
    full_name: str | None
    status: str
    tenant_status: str
    created_at: datetime

class ClientCreated(ClientRead):
    temporary_password: str

class ClientStatusUpdate(BaseModel):
    status: str
