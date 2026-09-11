from typing import TypeVar
from uuid import UUID

from sqlalchemy import Select, select


TenantModel = TypeVar("TenantModel")


def tenant_select(model: type[TenantModel], tenant_id: UUID) -> Select[tuple[TenantModel]]:
    """Build a tenant-scoped select for every tenant-owned model."""

    return select(model).where(model.tenant_id == tenant_id)  # type: ignore[attr-defined]
