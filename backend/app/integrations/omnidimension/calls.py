from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import OmniDimensionClient
from .exceptions import OmniDimensionResponseError


@dataclass(frozen=True)
class ProviderDispatchResult:
    provider_call_id: str
    status: str
    metadata: dict[str, Any]


class OmniDimensionCallProvider:
    endpoint = "/calls/dispatch"

    def __init__(self, client: OmniDimensionClient):
        self.client = client

    def dispatch(
        self,
        *,
        agent_id: int,
        to_number: str,
        from_number_id: int,
        call_context: dict[str, str],
        metadata: dict[str, str],
    ) -> ProviderDispatchResult:
        payload = {
            "agent_id": agent_id,
            "to_number": to_number,
            "from_number_id": from_number_id,
            "call_context": call_context,
            "metadata": metadata,
        }
        response = self.client.post(self.endpoint, json=payload)
        if not isinstance(response, dict) or not response.get("requestId"):
            raise OmniDimensionResponseError("OmniDimension returned an invalid dispatch response.")
        status = str(response.get("status") or "dispatched")
        return ProviderDispatchResult(
            provider_call_id=str(response["requestId"]),
            status=status,
            metadata={"status": status},
        )

    def get_call_log(self, call_log_id: str | int) -> dict[str, Any]:
        """Read the provider's post-call record; this never mutates provider state."""
        response = self.client.get(f"/calls/logs/{call_log_id}")
        if not isinstance(response, dict):
            raise OmniDimensionResponseError("OmniDimension returned an invalid call log response.")
        return response

    def list_call_logs(self, *, page: int = 1, page_size: int = 100, agent_id: int | None = None, call_status: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {"pageno": page, "pagesize": page_size}
        if agent_id is not None:
            params["agentid"] = agent_id
        if call_status:
            params["call_status"] = call_status
        response = self.client.get("/calls/logs", params=params)
        if not isinstance(response, dict):
            raise OmniDimensionResponseError("OmniDimension returned an invalid call log list response.")
        return response
