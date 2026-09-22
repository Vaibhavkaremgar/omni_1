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
        return self._map_response(self.client.post("/agents/create", json=payload), self.client.last_response)

    def update_agent(self, provider_id: str, payload: dict[str, Any]) -> ProviderAgent:
        return self._map_response(self.client.put(f"/agents/{provider_id}", json=payload), self.client.last_response)

    def get_agent(self, provider_id: str) -> dict[str, Any]:
        response = self.client.get(f"/agents/{provider_id}")
        if not isinstance(response, dict):
            raise OmniDimensionResponseError("OmniDimension returned an invalid agent response.")
        return response

    def upload_knowledge_file(self, content_base64: str, filename: str) -> str:
        response = self.client.post("/knowledge-base/files", json={"file_data": content_base64, "filename": filename})
        file = response.get("file") if isinstance(response, dict) else None
        file_id = file.get("id") if isinstance(file, dict) else response.get("id") if isinstance(response, dict) else None
        if file_id is None:
            raise OmniDimensionResponseError("OmniDimension returned an invalid knowledge file response.")
        return str(file_id)

    def attach_knowledge_file(self, file_id: str, agent_id: str) -> None:
        self.client.post("/knowledge-base/attach", json={"file_ids": [int(file_id)], "agent_id": int(agent_id)})

    def detach_knowledge_file(self, file_id: str, agent_id: str) -> None:
        self.client.post("/knowledge-base/detach", json={"file_ids": [int(file_id)], "agent_id": int(agent_id)})

    def delete_knowledge_file(self, file_id: str) -> None:
        self.client.delete(f"/knowledge-base/files/{file_id}")

    @staticmethod
    def _map_response(payload: Any, response_metadata: dict[str, Any] | None = None) -> ProviderAgent:
        if not isinstance(payload, dict) or payload.get("id") is None:
            raise OmniDimensionResponseError("OmniDimension returned an invalid agent response.")
        status = payload.get("status") or payload.get("status_of_building_flow") or "unknown"
        return ProviderAgent(
            provider_id=str(payload["id"]),
            status=str(status),
            metadata={
                "status": str(status),
                **({"http_status": response_metadata["status"]} if isinstance(response_metadata, dict) and response_metadata.get("status") is not None else {}),
            },
        )
