from uuid import UUID

from pydantic import Field

from app.schemas.base import IDSchema, ORMBaseModel, TimestampSchema


class TenantCreate(ORMBaseModel):
    name: str
    slug: str
    timezone: str | None = None


class TenantRead(IDSchema, TimestampSchema):
    name: str
    slug: str
    status: str
    timezone: str | None = None

