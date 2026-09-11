from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class CampaignContactCreate(ORMBaseModel):
    tenant_id: str
    campaign_id: str
    phone_number: str
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None


class CampaignContactRead(IDSchema, TimestampSchema):
    tenant_id: str
    campaign_id: str
    lead_id: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone_number: str
    email: str | None = None
    status: str
    attempt_count: int
    last_called_at: str | None = None
    last_call_id: str | None = None

