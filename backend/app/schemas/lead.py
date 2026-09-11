from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class LeadCreate(ORMBaseModel):
    tenant_id: str
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    email: str | None = None
    company: str | None = None


class LeadRead(IDSchema, TimestampSchema):
    tenant_id: str
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str | None = None
    email: str | None = None
    company: str | None = None
    source: str | None = None
    status: str

