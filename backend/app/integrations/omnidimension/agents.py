from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import OmniDimensionClient
from .exceptions import OmniDimensionResponseError


@dataclass(frozen=True)
class ProviderAgent:
    provider_id: str
    status: str
    metadata: dict[str, Any]


class OmniDimensionAgentProvider:
    """Provider-specific agent operations backed by OmniDimensionClient."""

    def __init__(self, client: OmniDimensionClient):
        self.client = client

    def create_agent(self, payload: dict[str, Any]) -> ProviderAgent:
        return self._map_response(self.client.post("/agents/create", json=payload))

    def update_agent(self, provider_id: str, payload: dict[str, Any]) -> ProviderAgent:
        return self._map_response(self.client.put(f"/agents/{provider_id}", json=payload))

    def get_agent(self, provider_id: str) -> dict[str, Any]:
        response = self.client.get(f"/agents/{provider_id}")
        if not isinstance(response, dict):
            raise OmniDimensionResponseError("OmniDimension returned an invalid agent response.")
        return response

    @staticmethod
    def _map_response(payload: Any) -> ProviderAgent:
        if not isinstance(payload, dict) or payload.get("id") is None:
            raise OmniDimensionResponseError("OmniDimension returned an invalid agent response.")
        status = payload.get("status") or payload.get("status_of_building_flow") or "unknown"
        return ProviderAgent(
            provider_id=str(payload["id"]),
            status=str(status),
            metadata={"status": str(status)},
        )
