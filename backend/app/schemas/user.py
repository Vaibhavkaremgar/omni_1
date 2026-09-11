from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class UserCreate(ORMBaseModel):
    tenant_id: str
    email: str
    password: str
    full_name: str | None = None
    role: str = "member"


class UserRead(IDSchema, TimestampSchema):
    tenant_id: str
    email: str
    full_name: str | None = None
    role: str
    status: str
