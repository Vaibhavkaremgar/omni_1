from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AuthUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str | None
    role: str
    status: str


class AuthTenantRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    status: str
    timezone: str | None


class AuthMeRead(BaseModel):
    user: AuthUserRead
    tenant: AuthTenantRead


class AuthCredentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class AuthRegister(AuthCredentials):
    tenant_name: str | None = Field(default=None, max_length=255)


class AuthTokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserRead
    tenant: AuthTenantRead
