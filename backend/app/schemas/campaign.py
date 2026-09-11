from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class CampaignCreate(ORMBaseModel):
    tenant_id: str
    name: str
    employee_id: str
    description: str | None = None


class CampaignRead(IDSchema, TimestampSchema):
    tenant_id: str
    name: str
    description: str | None = None
    status: str
    employee_id: str
