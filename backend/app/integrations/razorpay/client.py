"""Minimal server-side Razorpay Orders/Payments integration."""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from app.core.config import Settings, get_settings


class RazorpayError(Exception):
    pass


class RazorpayClient:
    base_url = "https://api.razorpay.com/v1"

    def __init__(self, settings: Settings | None = None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.client = client or httpx.Client(timeout=15.0)
        self._owns_client = client is None

    def _credentials(self) -> tuple[str, str]:
        if not self.settings.razorpay_key_id or not self.settings.razorpay_key_secret:
            raise RazorpayError("Razorpay is not configured.")
        return self.settings.razorpay_key_id, self.settings.razorpay_key_secret

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self.client.request(method, f"{self.base_url}{path}", auth=self._credentials(), **kwargs)
        except httpx.HTTPError as exc:
            raise RazorpayError("Unable to reach the payment provider.") from exc
        if response.status_code >= 400:
            raise RazorpayError("Payment provider rejected the request.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RazorpayError("Payment provider returned an invalid response.")
        return payload

    def create_order(self, *, amount_paise: int, receipt: str, notes: dict[str, str]) -> dict[str, Any]:
        return self._request("POST", "/orders", json={
            "amount": amount_paise, "currency": "INR", "receipt": receipt, "notes": notes,
        })

    def fetch_payment(self, payment_id: str) -> dict[str, Any]:
        return self._request("GET", f"/payments/{payment_id}")

    def fetch_order(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", f"/orders/{order_id}")

    def verify_checkout_signature(self, *, order_id: str, payment_id: str, signature: str) -> bool:
        _, secret = self._credentials()
        expected = hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def verify_webhook_signature(self, *, raw_body: bytes, signature: str) -> bool:
        secret = self.settings.razorpay_webhook_secret
        if not secret:
            return False
        expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
