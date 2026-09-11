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
