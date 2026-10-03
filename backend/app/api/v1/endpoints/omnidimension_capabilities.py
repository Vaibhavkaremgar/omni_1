from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.integrations.omnidimension import OmniDimensionAgentProvider, OmniDimensionCallProvider, OmniDimensionClient
from app.services.auth import AuthenticatedUser, require_admin
from app.services.omnidimension_capability_probe import OmniDimensionCapabilityProbe

router = APIRouter(prefix="/admin/omnidimension", tags=["admin"])


def _admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    return require_admin(user)


@router.get("/capabilities")
def capabilities(
    request_id: str | None = Query(default=None, max_length=128),
    agent_id: str | None = Query(default=None, max_length=128),
    _: AuthenticatedUser = Depends(_admin),
) -> dict[str, Any]:
    client = OmniDimensionClient(get_settings())
    try:
        return OmniDimensionCapabilityProbe(OmniDimensionCallProvider(client), OmniDimensionAgentProvider(client)).run(request_id=request_id.strip() if request_id else None, agent_id=agent_id.strip() if agent_id else None)
    finally:
        client.close()
