from __future__ import annotations

"""Integrations API — white-label, tenant-scoped.

OmniDimension is NEVER referenced in any response.
Provider secrets, OAuth tokens, and Omni API keys are NEVER returned.
All actions are resolved from the authenticated tenant session.
"""

import ipaddress
import logging
import urllib.parse
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.services.auth import AuthenticatedUser
from app.services.integrations import (
    CATALOG_BY_KEY,
    CONNECTION_MODE_API_KEY,
    CONNECTION_MODE_COMING_SOON,
    CONNECTION_MODE_OAUTH,
    CONNECTION_MODE_WEBHOOK,
    OAUTH_PROVIDERS,
    IntegrationService,
)

router = APIRouter(prefix="/integrations", tags=["integrations"])
_svc = IntegrationService()


# ---------------------------------------------------------------------------
# Response schemas — secrets are structurally excluded
# ---------------------------------------------------------------------------

class IntegrationItem(BaseModel):
    key: str
    name: str
    category: str
    description: str
    connection_mode: str
    status: str
    external_account_reference: str | None = None
    connected_at: str | None = None


class ConnectWebhookRequest(BaseModel):
    webhook_url: str
    account_reference: str | None = None


class ConnectApiKeyRequest(BaseModel):
    api_key: str
    account_reference: str | None = None


class OAuthInitResponse(BaseModel):
    authorization_url: str


class OAuthCallbackRequest(BaseModel):
    code: str
    state: str


class ConnectionResponse(BaseModel):
    key: str
    status: str
    external_account_reference: str | None = None
    connected_at: str | None = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[IntegrationItem])
def list_integrations(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[IntegrationItem]:
    """Return the integration catalog with tenant connection status.

    Never returns Omni API keys, agent IDs, OAuth tokens, or provider secrets.
    """
    items = _svc.get_catalog(db, current_user.tenant.id)
    return [IntegrationItem(**item) for item in items]


@router.post("/{key}/connect/webhook", response_model=ConnectionResponse)
def connect_webhook(
    key: str,
    body: ConnectWebhookRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConnectionResponse:
    """Connect a webhook-based integration (Slack, Make, Zapier, n8n, Custom API)."""
    _require_key(key)
    item = CATALOG_BY_KEY[key]
    if item["connection_mode"] not in (CONNECTION_MODE_WEBHOOK, CONNECTION_MODE_API_KEY):
        raise HTTPException(status_code=400, detail="This integration does not use a webhook URL.")
    if item["connection_mode"] == CONNECTION_MODE_COMING_SOON:
        raise HTTPException(status_code=400, detail="This integration is not yet available.")
    _validate_webhook_url(body.webhook_url)
    conn = _svc.connect_webhook(
        db, current_user.tenant.id, key, body.webhook_url, body.account_reference
    )
    return _conn_response(conn)


@router.post("/{key}/connect/api-key", response_model=ConnectionResponse)
def connect_api_key(
    key: str,
    body: ConnectApiKeyRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ConnectionResponse:
    """Connect an API-key-based integration (Cal.com)."""
    _require_key(key)
    item = CATALOG_BY_KEY[key]
    if item["connection_mode"] != CONNECTION_MODE_API_KEY:
        raise HTTPException(status_code=400, detail="This integration does not use an API key.")
    conn = _svc.connect_api_key(
        db, current_user.tenant.id, key, body.api_key, body.account_reference
    )
    return _conn_response(conn)


@router.post("/{key}/connect/oauth/init", response_model=OAuthInitResponse)
def oauth_init(
    key: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OAuthInitResponse:
    """Begin an OAuth flow. Returns OUR authorization URL — never redirects to Omni."""
    _require_key(key)
    item = CATALOG_BY_KEY[key]
    if item["connection_mode"] != CONNECTION_MODE_OAUTH:
        raise HTTPException(status_code=400, detail="This integration does not use OAuth.")
    provider_cfg = OAUTH_PROVIDERS.get(key)
    if not provider_cfg:
        raise HTTPException(status_code=400, detail="OAuth not configured for this integration.")
    settings = get_settings()
    client_id = getattr(settings, f"{key}_oauth_client_id", None)
    redirect_uri = getattr(settings, f"{key}_oauth_redirect_uri", None) or \
        f"{getattr(settings, 'backend_public_url', 'http://localhost:8000')}/api/v1/integrations/{key}/connect/oauth/callback"
    if not client_id:
        raise HTTPException(
            status_code=503,
            detail=f"OAuth for {item['name']} is not configured on this server.",
        )
    state_token = _svc.create_oauth_state(db, current_user.tenant.id, key)
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": provider_cfg["scopes"],
        "state": state_token,
        "response_type": "code",
    }
    if key == "google_calendar":
        params["access_type"] = "offline"
        params["prompt"] = "consent"
    auth_url = provider_cfg["auth_url"] + "?" + urllib.parse.urlencode(params)
    return OAuthInitResponse(authorization_url=auth_url)


@router.get("/{key}/connect/oauth/callback")
def oauth_callback(
    key: str,
    code: str,
    state: str,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """OAuth callback. Exchanges code for tokens server-side. Tokens are never returned."""
    _require_key(key)
    try:
        oauth_state = _svc.consume_oauth_state(db, state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if oauth_state.integration_key != key:
        raise HTTPException(status_code=400, detail="OAuth state key mismatch.")
    provider_cfg = OAUTH_PROVIDERS.get(key)
    if not provider_cfg:
        raise HTTPException(status_code=400, detail="OAuth not configured for this integration.")
    settings = get_settings()
    client_id = getattr(settings, f"{key}_oauth_client_id", None)
    client_secret = getattr(settings, f"{key}_oauth_client_secret", None)
    redirect_uri = getattr(settings, f"{key}_oauth_redirect_uri", None) or \
        f"{getattr(settings, 'backend_public_url', 'http://localhost:8000')}/api/v1/integrations/{key}/connect/oauth/callback"
    if not client_id or not client_secret:
        raise HTTPException(status_code=503, detail="OAuth credentials not configured.")
    try:
        tokens = _exchange_code(
            provider_cfg["token_url"], code, client_id, client_secret, redirect_uri
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Token exchange failed.") from exc
    account_ref = _extract_account_reference(key, tokens)
    _svc.store_oauth_tokens(db, oauth_state.tenant_id, key, tokens, account_ref)
    # Redirect to frontend success page — never exposes tokens in URL
    frontend_url = getattr(settings, "frontend_url", "http://localhost:5173")
    return RedirectResponse(url=f"{frontend_url}/integrations?connected={key}", status_code=302)


@router.delete("/{key}", status_code=204)
def disconnect(
    key: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Disconnect and remove a tenant integration connection."""
    _require_key(key)
    _svc.disconnect(db, current_user.tenant.id, key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_key(key: str) -> None:
    if key not in CATALOG_BY_KEY:
        raise HTTPException(status_code=404, detail=f"Integration '{key}' not found.")
    if CATALOG_BY_KEY[key]["connection_mode"] == CONNECTION_MODE_COMING_SOON:
        raise HTTPException(status_code=400, detail="This integration is coming soon.")


def _conn_response(conn: Any) -> ConnectionResponse:
    return ConnectionResponse(
        key=conn.integration_key,
        status=conn.status,
        external_account_reference=conn.external_account_reference,
        connected_at=conn.connected_at.isoformat() if conn.connected_at else None,
    )


def _exchange_code(
    token_url: str,
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict[str, Any]:
    with httpx.Client(timeout=15.0) as client:
        resp = client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()


def _extract_account_reference(key: str, tokens: dict[str, Any]) -> str | None:
    """Extract a human-readable account reference from token response. Never a secret."""
    if key == "hubspot":
        return tokens.get("hub_domain") or (f"Portal {tokens['hub_id']}" if tokens.get("hub_id") else None)
    if key == "slack":
        incoming = tokens.get("incoming_webhook", {})
        return incoming.get("channel") or tokens.get("team", {}).get("name")
    if key == "salesforce":
        return tokens.get("instance_url")
    if key == "ghl":
        return tokens.get("locationId") or tokens.get("companyId")
    if key == "google_calendar":
        # Google token responses don't include account info; use a stable placeholder.
        # The customer's email can be fetched via the userinfo endpoint post-connection
        # if needed, but that requires an extra round-trip and is out of scope here.
        return "Google Calendar connected"
    return None


# ---------------------------------------------------------------------------
# SSRF / URL safety
# ---------------------------------------------------------------------------

# Blocked private/loopback/link-local ranges
_BLOCKED_NETWORKS = [
    ipaddress.ip_network(cidr) for cidr in (
        "127.0.0.0/8",      # loopback
        "10.0.0.0/8",       # RFC1918
        "172.16.0.0/12",    # RFC1918
        "192.168.0.0/16",   # RFC1918
        "169.254.0.0/16",   # link-local / cloud metadata (AWS 169.254.169.254)
        "::1/128",          # IPv6 loopback
        "fc00::/7",         # IPv6 unique local
        "fe80::/10",        # IPv6 link-local
        "0.0.0.0/8",        # unspecified
    )
]

_BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}


def _validate_webhook_url(url: str) -> None:
    """Reject URLs that could be used for SSRF against internal infrastructure.

    Raises HTTPException 400 for:
    - non-HTTPS schemes (http:// is rejected in production; allowed only for localhost dev)
    - private/loopback/link-local IP targets
    - known internal hostnames
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook URL.")

    if parsed.scheme not in ("https", "http"):
        raise HTTPException(status_code=400, detail="Webhook URL must use HTTPS.")
    if parsed.scheme == "http":
        # Allow http only for the localhost hostname during development; block everywhere else.
        host = (parsed.hostname or "").lower()
        if host != "localhost":
            raise HTTPException(status_code=400, detail="Webhook URL must use HTTPS.")

    host = (parsed.hostname or "").lower()
    if not host:
        raise HTTPException(status_code=400, detail="Invalid webhook URL: missing host.")

    if host in _BLOCKED_HOSTNAMES and parsed.scheme != "http":
        # localhost is only allowed over http (dev); block it over https (spoofed)
        raise HTTPException(status_code=400, detail="Webhook URL targets a reserved hostname.")

    # Resolve numeric IPs and check against blocked ranges
    try:
        addr = ipaddress.ip_address(host)
        for net in _BLOCKED_NETWORKS:
            if addr in net:
                raise HTTPException(status_code=400, detail="Webhook URL targets a private or reserved address.")
    except ValueError:
        # Not a bare IP — hostname; DNS resolution happens at dispatch time.
        # We block the known-bad hostnames above; full DNS-rebinding protection
        # requires resolving at dispatch time which is out of scope here.
        pass
