from __future__ import annotations

"""Post-call integration dispatcher.

Called by CallResultService after a call is committed.
Dispatches call data to all connected integrations for the tenant.
External API failures are logged but never block the webhook response.
"""

import logging
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.call import Call
from app.models.integration_connection import IntegrationConnection
from app.services.credential_store import decrypt_credentials

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


def _call_payload(call: Call) -> dict[str, Any]:
    """Build a safe, provider-agnostic call summary. Never includes Omni internals."""
    return {
        "call_id": str(call.id),
        "status": call.status,
        "direction": call.direction,
        "duration_seconds": call.duration_seconds,
        "customer_phone_number": call.customer_phone_number,
        "summary": call.summary,
        "transcript": call.transcript,
        "sentiment": call.sentiment,
        "extracted_attributes": call.extracted_attributes,
        "ended_at": call.ended_at.isoformat() if call.ended_at else None,
    }


def _is_safe_webhook_url(url: str) -> bool:
    """Return False for URLs that target private/loopback/metadata addresses.

    This mirrors the validation in the connect endpoint and acts as a
    defence-in-depth check at dispatch time in case a record was inserted
    outside the normal API path.
    """
    import ipaddress
    import urllib.parse
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("https", "http"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    # Block known-bad hostnames (metadata endpoints, etc.)
    if host in ("metadata.google.internal",):
        return False
    # Block numeric IPs in private/loopback/link-local ranges
    try:
        addr = ipaddress.ip_address(host)
        blocked = [
            ipaddress.ip_network(c) for c in (
                "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
                "192.168.0.0/16", "169.254.0.0/16",
                "::1/128", "fc00::/7", "fe80::/10", "0.0.0.0/8",
            )
        ]
        if any(addr in net for net in blocked):
            return False
    except ValueError:
        pass  # hostname, not a bare IP
    return True


def _post_webhook(url: str, payload: dict[str, Any]) -> None:
    if not _is_safe_webhook_url(url):
        raise ValueError(f"Webhook URL blocked by SSRF policy: {url}")
    with httpx.Client(timeout=_TIMEOUT) as client:
        resp = client.post(url, json=payload, headers={"Content-Type": "application/json"})
        resp.raise_for_status()


class IntegrationDispatcher:
    """Dispatch post-call events to all connected tenant integrations."""

    def dispatch(self, db: Session, call: Call) -> None:
        connections = db.scalars(
            select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == call.tenant_id,
                IntegrationConnection.status == "connected",
            )
        ).all()
        payload = _call_payload(call)
        for conn in connections:
            try:
                self._dispatch_one(conn, payload)
            except Exception as exc:
                logger.warning(
                    "Integration dispatch failed tenant=%s key=%s: %s",
                    call.tenant_id,
                    conn.integration_key,
                    exc,
                )

    def _dispatch_one(self, conn: IntegrationConnection, payload: dict[str, Any]) -> None:
        key = conn.integration_key
        cfg = conn.configuration or {}

        # Webhook-based integrations (Slack incoming webhook, Make, Zapier, n8n, custom_api)
        if key in ("slack", "make", "zapier", "n8n", "custom_api"):
            url = cfg.get("webhook_url")
            if url:
                _post_webhook(url, payload)
            return

        # OAuth/API-key integrations — placeholder for provider-specific logic.
        # Credentials are decrypted here, backend-only, never serialised.
        if key in ("hubspot", "salesforce", "ghl", "google_calendar", "cal_com"):
            if not conn.provider_credentials:
                return
            _creds = decrypt_credentials(conn.provider_credentials)
            # TODO: implement provider-specific CRM/calendar push using _creds
            # This is where HubSpot contact/deal creation, Salesforce lead upsert, etc. go.
            logger.info("Post-call dispatch for %s: provider integration pending implementation.", key)
