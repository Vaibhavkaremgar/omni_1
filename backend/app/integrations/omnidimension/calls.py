from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from .client import OmniDimensionClient
from .exceptions import OmniDimensionResponseError


@dataclass(frozen=True)
class ProviderDispatchResult:
    provider_call_id: str | None
    status: str
    metadata: dict[str, Any]
    provider_request_id: str | None = None


class OmniDimensionCallProvider:
    endpoint = "/calls/dispatch"

    def __init__(self, client: OmniDimensionClient):
        self.client = client
        self.logger = logging.getLogger(__name__)

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
        self.logger.info(
            "[OMNI_CALL_DISPATCH_RESPONSE] endpoint=%s agent_id=%s status=%s request_id=%s "
            "provider_call_id=%s response_keys=%s",
            self.endpoint, agent_id, response.get("status") if isinstance(response, dict) else None,
            response.get("requestId") if isinstance(response, dict) else None,
            (response.get("callId") or response.get("call_id") or response.get("call_log_id")) if isinstance(response, dict) else None,
            sorted(response.keys()) if isinstance(response, dict) else type(response).__name__,
        )
        if not isinstance(response, dict) or not response.get("requestId"):
            raise OmniDimensionResponseError("OmniDimension returned an invalid dispatch response.")
        status = str(response.get("status") or "dispatched")
        return ProviderDispatchResult(
            provider_call_id=(str(response.get("callId") or response.get("call_id") or response.get("call_log_id"))
                              if (response.get("callId") or response.get("call_id") or response.get("call_log_id")) else None),
            status=status,
            metadata={"status": status, "provider_request_id": str(response["requestId"])},
            provider_request_id=str(response["requestId"]),
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

    def get_call_log_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        """Resolve Omni's dispatch request ID to its detailed call record."""
        response = self.list_call_logs(page=1, page_size=100)
        rows = response.get("data") or response.get("call_logs") or response.get("results") or []
        if isinstance(rows, dict):
            rows = rows.get("data") or []
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate = row.get("call_request_id")
            candidate = candidate.get("id") if isinstance(candidate, dict) else candidate
            candidate = candidate or row.get("requestId") or row.get("request_id")
            if str(candidate) != str(request_id):
                continue
            provider_call_id = row.get("id") or row.get("call_log_id") or row.get("call_id")
            if provider_call_id is None:
                return row
            detail = self.get_call_log(provider_call_id)
            if isinstance(detail, dict):
                detail_rows = detail.get("call_log_data")
                if isinstance(detail_rows, list) and detail_rows and isinstance(detail_rows[0], dict):
                    return detail_rows[0]
                return detail
            return row
        return None
