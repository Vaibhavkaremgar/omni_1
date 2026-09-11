from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class AuthUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    full_name: str | None
    role: str
    status: str
    must_change_password: bool


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

class PasswordChange(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


class AuthTokenRead(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: AuthUserRead
    tenant: AuthTenantRead
