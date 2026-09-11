from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .client import OmniDimensionClient
from .exceptions import OmniDimensionResponseError


@dataclass(frozen=True)
class ProviderPhoneNumber:
    provider_id: str
    e164_number: str
    status: str
    label: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ProviderAvailablePhoneNumber:
    e164_number: str
    region: str
    carrier: str
    monthly_rental_usd: float | None
    validity_days: int | None
    kyc_required: bool | None


class OmniDimensionPhoneNumberProvider:
    """Provider-specific phone inventory mapping."""

    endpoint = "/phone_number/list"

    def __init__(self, client: OmniDimensionClient):
        self.client = client

    def list_phone_numbers(self, *, page: int = 1, page_size: int = 150, user_id: str | None = None) -> list[ProviderPhoneNumber]:
        params: dict[str, Any] = {"pageno": page, "pagesize": min(page_size, 150)}
        if user_id:
            params["user_id"] = user_id
        payload = self.client.get(
            self.endpoint,
            params=params,
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("phone_numbers"), list):
            raise OmniDimensionResponseError("OmniDimension returned an invalid phone-number list.")

        numbers: list[ProviderPhoneNumber] = []
        for item in payload["phone_numbers"]:
            if not isinstance(item, dict):
                raise OmniDimensionResponseError("OmniDimension returned an invalid phone-number item.")
            provider_id = item.get("id")
            phone_number = item.get("phone_number")
            if provider_id is None or not isinstance(phone_number, str) or not phone_number.strip():
                raise OmniDimensionResponseError("OmniDimension returned an incomplete phone-number item.")
            metadata = {
                key: item[key]
                for key in (
                    "location",
                    "number_provider",
                    "number_source",
                    "can_message",
                    "purchase_date",
                    "active_bot_id",
                    "health_score",
                    "expiry_date",
                    "is_phone_wa",
                    "is_cloud_wa",
                )
                if key in item
            }
            numbers.append(
                ProviderPhoneNumber(
                    provider_id=str(provider_id),
                    e164_number=phone_number.strip(),
                    status=_normalize_status(item.get("status")),
                    label=item.get("name") if isinstance(item.get("name"), str) else None,
                    metadata=metadata,
                )
            )
        return numbers

    def search_available_numbers(
        self, *, region: str, carrier: str, pattern: str | None = None, page: int = 1, limit: int = 20
    ) -> tuple[list[ProviderAvailablePhoneNumber], int, int, int, str | None]:
        """Search the provider marketplace; this never creates or reserves a number."""
        params: dict[str, Any] = {"region": region, "carrier": carrier, "page": page, "limit": limit}
        if pattern:
            params["pattern"] = pattern
        payload = self.client.get("/phone_number/search", params=params)
        if not isinstance(payload, dict) or not isinstance(payload.get("numbers"), list):
            raise OmniDimensionResponseError("OmniDimension returned an invalid number-search response.")
        numbers: list[ProviderAvailablePhoneNumber] = []
        for item in payload["numbers"]:
            if not isinstance(item, dict) or not isinstance(item.get("phone_number"), str) or not item["phone_number"].strip():
                raise OmniDimensionResponseError("OmniDimension returned an invalid available number.")
            price = item.get("monthly_rental_usd")
            validity = item.get("validity_days")
            if price is not None and (isinstance(price, bool) or not isinstance(price, (int, float))):
                raise OmniDimensionResponseError("OmniDimension returned an invalid rental price.")
            if validity is not None and (isinstance(validity, bool) or not isinstance(validity, int)):
                raise OmniDimensionResponseError("OmniDimension returned an invalid validity period.")
            numbers.append(ProviderAvailablePhoneNumber(
                e164_number=item["phone_number"].strip(),
                region=item.get("region") if isinstance(item.get("region"), str) else region,
                carrier=item.get("carrier") if isinstance(item.get("carrier"), str) and item["carrier"].strip() else carrier,
                monthly_rental_usd=float(price) if price is not None else None,
                validity_days=validity,
                kyc_required=item.get("kyc_required") if isinstance(item.get("kyc_required"), bool) else None,
            ))
        total = payload.get("total", len(numbers))
        result_page = payload.get("page", page)
        result_limit = payload.get("limit", limit)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (total, result_page, result_limit)):
            raise OmniDimensionResponseError("OmniDimension returned invalid pagination.")
        label = payload.get("carrier_label") if isinstance(payload.get("carrier_label"), str) else None
        return numbers, total, result_page, result_limit, label

    def purchase_number(self, *, region: str, carrier: str, phone_number: str, user_id: str, idempotency_key: str) -> dict[str, Any]:
        payload = self.client.post("/phone_number/purchase", json={
            "region": region, "carrier": carrier, "phone_number": phone_number, "user_id": user_id,
        }, headers={"Idempotency-Key": idempotency_key})
        if not isinstance(payload, dict) or payload.get("success") is not True:
            raise OmniDimensionResponseError("OmniDimension did not confirm the phone-number purchase.")
        return payload

    def release_number(self, *, phone_number: str, user_id: str, idempotency_key: str) -> dict[str, Any]:
        payload = self.client.post("/phone_number/release", json={"phone_number": phone_number, "user_id": user_id}, headers={"Idempotency-Key": idempotency_key})
        if not isinstance(payload, dict):
            raise OmniDimensionResponseError("OmniDimension returned an invalid release response.")
        return payload


def _normalize_status(value: Any) -> str:
    normalized = str(value or "active").strip().lower()
    return normalized if normalized in {"provisioning", "active", "inactive", "released"} else "active"
