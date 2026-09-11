from __future__ import annotations

from typing import Any

from .client import OmniDimensionClient
from .exceptions import OmniDimensionResponseError


class OmniDimensionResellerProvider:
    """Backend-only adapter for OmniDimension reseller child accounts and KYC."""

    def __init__(self, client: OmniDimensionClient):
        self.client = client

    def create_child_user(self, *, name: str, email: str, phone: str, password: str) -> dict[str, Any]:
        payload = self.client.post("/reseller/users/add", json={
            "name": name, "email": email, "phone": phone, "password": password,
        })
        if not isinstance(payload, dict) or self._user_id(payload) is None:
            raise OmniDimensionResponseError("OmniDimension returned an invalid child-user response.")
        return payload

    def get_kyc_status(self, *, user_id: str) -> dict[str, Any]:
        payload = self.client.get("/reseller/kyc/status", params={"user_id": user_id})
        if not isinstance(payload, dict):
            raise OmniDimensionResponseError("OmniDimension returned an invalid KYC status response.")
        return payload

    def get_kyc_requirements(self, *, region: str, carrier: str) -> dict[str, Any]:
        payload = self.client.get("/reseller/kyc/requirements", params={"region": region, "carrier": carrier})
        if not isinstance(payload, dict) or not isinstance(payload.get("steps"), list):
            raise OmniDimensionResponseError("OmniDimension returned invalid KYC requirements.")
        return payload

    def submit_kyc_step(self, *, user_id: str, region: str, carrier: str, step: str, values: dict[str, Any]) -> dict[str, Any]:
        payload = self.client.post(f"/reseller/kyc/steps/{step}", json={
            "user_id": user_id, "region": region, "carrier": carrier, **values,
        })
        if not isinstance(payload, dict):
            raise OmniDimensionResponseError("OmniDimension returned an invalid KYC step response.")
        return payload

    @staticmethod
    def _user_id(payload: dict[str, Any]) -> str | None:
        for key in ("user_id", "id"):
            value = payload.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value)
        user = payload.get("user")
        if isinstance(user, dict):
            return OmniDimensionResellerProvider._user_id(user)
        return None
