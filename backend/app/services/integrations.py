from __future__ import annotations

"""Integration catalog and connection management service.

Capability assessment per integration:
- webhook_url: Customer provides their webhook URL → we POST post-call data. Fully implementable.
- api_key: Customer provides an API key → we call their service. Fully implementable.
- oauth: We implement the OAuth flow directly against the provider. Implementable for
  HubSpot, Salesforce, Google Calendar, Slack, GHL.
- coming_soon: Requires provider-dashboard-only configuration that has no public API
  we can call on the customer's behalf (e.g. WhatsApp Cloud requires Meta Business
  verification steps that cannot be automated via API alone).

OmniDimension is NOT used for any integration management. Omni remains the voice
infrastructure layer only. All integration state lives in our database.
"""

from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import utc_now
from app.models.integration_connection import IntegrationConnection
from app.models.oauth_state import OAuthState
from app.services.credential_store import decrypt_credentials, encrypt_credentials

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

CONNECTION_MODE_WEBHOOK = "webhook_url"
CONNECTION_MODE_API_KEY = "api_key"
CONNECTION_MODE_OAUTH = "oauth"
CONNECTION_MODE_COMING_SOON = "coming_soon"

CATALOG: list[dict[str, Any]] = [
    {
        "key": "hubspot",
        "name": "HubSpot",
        "category": "CRM",
        "description": "Sync contacts, deals and call outcomes with HubSpot. Coming soon.",
        "connection_mode": CONNECTION_MODE_COMING_SOON,
    },
    {
        "key": "salesforce",
        "name": "Salesforce",
        "category": "CRM",
        "description": "Sync leads and call outcomes with Salesforce. Coming soon.",
        "connection_mode": CONNECTION_MODE_COMING_SOON,
    },
    {
        "key": "ghl",
        "name": "GoHighLevel",
        "category": "CRM",
        "description": "Sync contacts and calls with GoHighLevel CRM. Coming soon.",
        "connection_mode": CONNECTION_MODE_COMING_SOON,
    },
    {
        "key": "google_calendar",
        "name": "Google Calendar",
        "category": "Scheduling",
        "description": "Schedule and manage appointments with Google Calendar.",
        "connection_mode": CONNECTION_MODE_OAUTH,
    },
    {
        "key": "cal_com",
        "name": "Cal.com",
        "category": "Scheduling",
        "description": "Book appointments directly from AI calls via Cal.com.",
        "connection_mode": CONNECTION_MODE_API_KEY,
    },
    {
        "key": "slack",
        "name": "Slack",
        "category": "Notifications",
        "description": "Receive call summaries and alerts in Slack.",
        "connection_mode": CONNECTION_MODE_WEBHOOK,
    },
    {
        "key": "make",
        "name": "Make",
        "category": "Automation",
        "description": "Trigger Make (Integromat) scenarios from completed calls.",
        "connection_mode": CONNECTION_MODE_WEBHOOK,
    },
    {
        "key": "zapier",
        "name": "Zapier",
        "category": "Automation",
        "description": "Trigger Zapier zaps from completed calls.",
        "connection_mode": CONNECTION_MODE_WEBHOOK,
    },
    {
        "key": "n8n",
        "name": "n8n",
        "category": "Automation",
        "description": "Trigger n8n workflows from completed calls.",
        "connection_mode": CONNECTION_MODE_WEBHOOK,
    },
    {
        "key": "custom_api",
        "name": "Custom API",
        "category": "Developer",
        "description": "POST call results to any custom HTTP endpoint.",
        "connection_mode": CONNECTION_MODE_WEBHOOK,
    },
    {
        "key": "whatsapp",
        "name": "WhatsApp",
        "category": "Messaging",
        "description": "Connect your WhatsApp Business number and let your AI employees handle customer conversations. Coming soon — full WhatsApp Cloud Business integration is in progress.",
        "connection_mode": CONNECTION_MODE_COMING_SOON,
    },
]

CATALOG_BY_KEY: dict[str, dict[str, Any]] = {item["key"]: item for item in CATALOG}

# ---------------------------------------------------------------------------
# OAuth provider configurations
# ---------------------------------------------------------------------------

# These are populated from environment variables at runtime.
# Client secrets are NEVER returned through any API response.
OAUTH_PROVIDERS: dict[str, dict[str, str]] = {
    "hubspot": {
        "auth_url": "https://app.hubspot.com/oauth/authorize",
        "token_url": "https://api.hubapi.com/oauth/v1/token",
        "scopes": "crm.objects.contacts.read crm.objects.contacts.write crm.objects.deals.read crm.objects.deals.write",
    },
    "salesforce": {
        "auth_url": "https://login.salesforce.com/services/oauth2/authorize",
        "token_url": "https://login.salesforce.com/services/oauth2/token",
        "scopes": "api refresh_token",
    },
    "google_calendar": {
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scopes": "https://www.googleapis.com/auth/calendar.events https://www.googleapis.com/auth/calendar.readonly",
    },
    "ghl": {
        "auth_url": "https://marketplace.gohighlevel.com/oauth/chooselocation",
        "token_url": "https://services.leadconnectorhq.com/oauth/token",
        "scopes": "contacts.readonly contacts.write",
    },
}

_STATE_TTL_MINUTES = 10


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class IntegrationService:

    # ── Catalog ──────────────────────────────────────────────────────────────

    def get_catalog(self, db: Session, tenant_id: UUID) -> list[dict[str, Any]]:
        connections = {
            row.integration_key: row
            for row in db.scalars(
                select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id)
            ).all()
        }
        result = []
        for item in CATALOG:
            conn = connections.get(item["key"])
            result.append({
                "key": item["key"],
                "name": item["name"],
                "category": item["category"],
                "description": item["description"],
                "connection_mode": item["connection_mode"],
                "status": conn.status if conn else "not_connected",
                "external_account_reference": conn.external_account_reference if conn else None,
                "connected_at": conn.connected_at.isoformat() if conn and conn.connected_at else None,
            })
        return result

    def get_connection(self, db: Session, tenant_id: UUID, key: str) -> IntegrationConnection | None:
        return db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == tenant_id,
                IntegrationConnection.integration_key == key,
            )
        )

    # ── Webhook / API-key connections ─────────────────────────────────────────

    def connect_webhook(
        self,
        db: Session,
        tenant_id: UUID,
        key: str,
        webhook_url: str,
        account_reference: str | None = None,
    ) -> IntegrationConnection:
        item = CATALOG_BY_KEY.get(key)
        if item is None or item["connection_mode"] not in (CONNECTION_MODE_WEBHOOK, CONNECTION_MODE_API_KEY):
            raise ValueError(f"Integration '{key}' does not support webhook/api-key connection.")
        conn = self._get_or_create(db, tenant_id, key, item["name"])
        conn.configuration = {"webhook_url": webhook_url}
        conn.external_account_reference = account_reference or webhook_url[:80]
        conn.status = "connected"
        conn.connected_at = utc_now()
        conn.error_message = None
        db.commit()
        db.refresh(conn)
        return conn

    def connect_api_key(
        self,
        db: Session,
        tenant_id: UUID,
        key: str,
        api_key: str,
        account_reference: str | None = None,
    ) -> IntegrationConnection:
        item = CATALOG_BY_KEY.get(key)
        if item is None or item["connection_mode"] != CONNECTION_MODE_API_KEY:
            raise ValueError(f"Integration '{key}' does not support api-key connection.")
        conn = self._get_or_create(db, tenant_id, key, item["name"])
        conn.provider_credentials = encrypt_credentials({"api_key": api_key})
        conn.external_account_reference = account_reference or "API key configured"
        conn.status = "connected"
        conn.connected_at = utc_now()
        conn.error_message = None
        db.commit()
        db.refresh(conn)
        return conn

    # ── OAuth flow ────────────────────────────────────────────────────────────

    def create_oauth_state(self, db: Session, tenant_id: UUID, key: str) -> str:
        """Create a single-use state token for OAuth CSRF protection."""
        if key not in OAUTH_PROVIDERS:
            raise ValueError(f"Integration '{key}' does not support OAuth.")
        # Expire old unused states for this tenant+key
        old_states = db.scalars(
            select(OAuthState).where(
                OAuthState.tenant_id == tenant_id,
                OAuthState.integration_key == key,
                OAuthState.used == False,  # noqa: E712
            )
        ).all()
        for s in old_states:
            db.delete(s)
        state = OAuthState(
            tenant_id=tenant_id,
            integration_key=key,
            state_token=token_urlsafe(32),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES),
        )
        db.add(state)
        db.commit()
        db.refresh(state)
        return state.state_token

    def consume_oauth_state(self, db: Session, state_token: str) -> OAuthState:
        """Validate and consume a state token. Raises ValueError on invalid/expired/reused."""
        state = db.scalar(select(OAuthState).where(OAuthState.state_token == state_token))
        if state is None:
            raise ValueError("Invalid OAuth state.")
        if state.used:
            raise ValueError("OAuth state already used.")
        if datetime.now(timezone.utc) > state.expires_at.replace(tzinfo=timezone.utc):
            raise ValueError("OAuth state expired.")
        state.used = True
        db.commit()
        return state

    def store_oauth_tokens(
        self,
        db: Session,
        tenant_id: UUID,
        key: str,
        tokens: dict[str, Any],
        account_reference: str | None = None,
    ) -> IntegrationConnection:
        item = CATALOG_BY_KEY.get(key)
        if item is None:
            raise ValueError(f"Unknown integration '{key}'.")
        conn = self._get_or_create(db, tenant_id, key, item["name"])
        conn.provider_credentials = encrypt_credentials(tokens)
        conn.external_account_reference = account_reference
        conn.status = "connected"
        conn.connected_at = utc_now()
        conn.error_message = None
        db.commit()
        db.refresh(conn)
        return conn

    def get_decrypted_credentials(self, conn: IntegrationConnection) -> dict[str, Any]:
        """Backend-only. Never call from an API response serialiser."""
        if not conn.provider_credentials:
            return {}
        return decrypt_credentials(conn.provider_credentials)

    # ── Disconnect ────────────────────────────────────────────────────────────

    def disconnect(self, db: Session, tenant_id: UUID, key: str) -> None:
        conn = self.get_connection(db, tenant_id, key)
        if conn is None:
            return
        conn.status = "not_connected"
        conn.provider_credentials = None
        conn.external_account_reference = None
        conn.configuration = None
        conn.connected_at = None
        conn.error_message = None
        db.commit()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _get_or_create(db: Session, tenant_id: UUID, key: str, display_name: str) -> IntegrationConnection:
        conn = db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == tenant_id,
                IntegrationConnection.integration_key == key,
            )
        )
        if conn is None:
            conn = IntegrationConnection(
                tenant_id=tenant_id,
                integration_key=key,
                display_name=display_name,
            )
            db.add(conn)
            db.flush()
        return conn
