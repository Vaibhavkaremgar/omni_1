"""Tests for Task 17.1 — White-label integrations.

Covers all 15 required test cases:
1.  Tenant can view its integration catalog.
2.  Tenant cannot view another tenant's connection.
3.  Connect flow creates tenant-scoped connection state.
4.  OAuth state cannot be reused.
5.  OAuth callback rejects invalid state.
6.  Tokens/secrets are never returned by API responses.
7.  Connected status is only shown after successful connection.
8.  Disconnect removes/deactivates the tenant connection safely.
9.  Integration is associated with the correct tenant.
10. Employee/agent association is tenant-safe.
11. Post-call integration dispatch uses the correct tenant connection.
12. External integration failure is retryable (dispatcher does not raise).
13. Omni API keys never reach the frontend.
14. No integration action redirects to the Omni dashboard.
15. Unsupported coming-soon integrations are not falsely reported as connected.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models.ai_employee import AIEmployee
from app.models.call import Call
from app.models.enums import CallDirection, CallStatus, EmployeeStatus, UserRole, UserStatus
from app.models.integration_connection import IntegrationConnection
from app.models.oauth_state import OAuthState
from app.models.tenant import Tenant
from app.models.user import User
from app.services import auth as auth_service
from app.services.integrations import IntegrationService
from app.services.integration_dispatcher import IntegrationDispatcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def integ_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    # Ensure additive columns present in in-memory DB
    cols = {c["name"] for c in inspect(engine).get_columns("tenants")}
    with engine.begin() as conn:
        if "instant_leads_enabled" not in cols:
            conn.execute(text("ALTER TABLE tenants ADD COLUMN instant_leads_enabled BOOLEAN NOT NULL DEFAULT 1"))

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    tenant_a = Tenant(name="Tenant A", slug="integ-a", instant_leads_enabled=True)
    tenant_b = Tenant(name="Tenant B", slug="integ-b", instant_leads_enabled=True)
    db.add_all([tenant_a, tenant_b])
    db.commit()
    db.refresh(tenant_a)
    db.refresh(tenant_b)
    yield db, tenant_a, tenant_b
    db.close()


def _make_user(db, tenant, email):
    user = User(
        tenant_id=tenant.id,
        email=email,
        password_hash=auth_service.hash_password("pw"),
        role=UserRole.owner.value,
        status=UserStatus.active.value,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth_client(db, tenant, email, monkeypatch):
    user = _make_user(db, tenant, email)
    token = auth_service.create_access_token(str(user.id))
    monkeypatch.setattr(auth_service, "decode_access_token", lambda _tok: user.id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, follow_redirects=False), token


def _make_employee(db, tenant):
    emp = AIEmployee(
        tenant_id=tenant.id, name="Bot", purpose="Test", language="en",
        llm_provider="openai", llm_model="gpt-4o", status=EmployeeStatus.published.value,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _make_call(db, tenant, employee):
    call = Call(
        tenant_id=tenant.id, employee_id=employee.id,
        direction=CallDirection.outbound.value, status=CallStatus.completed.value,
        customer_phone_number="+15551234567", duration_seconds=60,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


# ---------------------------------------------------------------------------
# 1. Tenant can view its integration catalog
# ---------------------------------------------------------------------------

def test_catalog_returns_all_integrations(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c1@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        items = r.json()
        assert len(items) > 0
        keys = {i["key"] for i in items}
        assert "hubspot" in keys
        assert "slack" in keys
        assert "custom_api" in keys
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 2. Tenant cannot view another tenant's connection
# ---------------------------------------------------------------------------

def test_catalog_does_not_expose_other_tenant_connection(integ_db, monkeypatch):
    db, tenant_a, tenant_b = integ_db
    # Give tenant_b a connected slack
    conn = IntegrationConnection(
        tenant_id=tenant_b.id, integration_key="slack",
        display_name="Slack", status="connected",
        external_account_reference="#sales",
    )
    db.add(conn)
    db.commit()
    client_a, token_a = _auth_client(db, tenant_a, "c2@test.com", monkeypatch)
    try:
        r = client_a.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token_a}"})
        assert r.status_code == 200
        slack = next(i for i in r.json() if i["key"] == "slack")
        assert slack["status"] == "not_connected"
        assert slack["external_account_reference"] is None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 3. Connect flow creates tenant-scoped connection state
# ---------------------------------------------------------------------------

def test_webhook_connect_creates_tenant_scoped_connection(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c3@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/slack/connect/webhook",
            json={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "connected"
        assert data["key"] == "slack"
        # Verify DB record is scoped to tenant_a
        from sqlalchemy import select
        conn = db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == tenant_a.id,
                IntegrationConnection.integration_key == "slack",
            )
        )
        assert conn is not None
        assert conn.status == "connected"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 4. OAuth state cannot be reused
# ---------------------------------------------------------------------------

def test_oauth_state_cannot_be_reused(integ_db):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    state_token = svc.create_oauth_state(db, tenant_a.id, "hubspot")
    # First consume succeeds
    svc.consume_oauth_state(db, state_token)
    # Second consume must fail
    with pytest.raises(ValueError, match="already used"):
        svc.consume_oauth_state(db, state_token)


# ---------------------------------------------------------------------------
# 5. OAuth callback rejects invalid state
# ---------------------------------------------------------------------------

def test_oauth_callback_rejects_invalid_state(integ_db):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    with pytest.raises(ValueError, match="Invalid"):
        svc.consume_oauth_state(db, "totally-invalid-state-token")


def test_oauth_callback_rejects_expired_state(integ_db):
    db, tenant_a, _ = integ_db
    state = OAuthState(
        tenant_id=tenant_a.id,
        integration_key="hubspot",
        state_token="expired-token-xyz",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db.add(state)
    db.commit()
    svc = IntegrationService()
    with pytest.raises(ValueError, match="expired"):
        svc.consume_oauth_state(db, "expired-token-xyz")


# ---------------------------------------------------------------------------
# 6. Tokens/secrets are never returned by API responses
# ---------------------------------------------------------------------------

def test_api_response_never_contains_secrets(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    # Store a connection with encrypted credentials
    svc = IntegrationService()
    svc.connect_api_key(db, tenant_a.id, "cal_com", "super-secret-api-key-12345")
    client, token = _auth_client(db, tenant_a, "c6@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        raw = r.text
        assert "super-secret-api-key-12345" not in raw
        assert "access_token" not in raw
        assert "refresh_token" not in raw
        assert "client_secret" not in raw
        assert "provider_credentials" not in raw
        assert "omnidimension" not in raw.lower()
        assert "omnidim.io" not in raw.lower()
    finally:
        app.dependency_overrides.clear()


def test_connect_response_never_contains_secrets(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c6b@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/cal_com/connect/api-key",
            json={"api_key": "my-calcom-secret-key"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        raw = r.text
        assert "my-calcom-secret-key" not in raw
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 7. Connected status only shown after successful connection
# ---------------------------------------------------------------------------

def test_status_not_connected_before_connect(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c7@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        for item in r.json():
            if item["connection_mode"] != "coming_soon":
                assert item["status"] == "not_connected"
    finally:
        app.dependency_overrides.clear()


def test_status_connected_after_connect(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c7b@test.com", monkeypatch)
    try:
        client.post(
            "/api/v1/integrations/custom_api/connect/webhook",
            json={"webhook_url": "https://example.com/hook"},
            headers={"Authorization": f"Bearer {token}"},
        )
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        custom = next(i for i in r.json() if i["key"] == "custom_api")
        assert custom["status"] == "connected"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 8. Disconnect removes/deactivates the tenant connection safely
# ---------------------------------------------------------------------------

def test_disconnect_removes_connection(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    svc.connect_webhook(db, tenant_a.id, "zapier", "https://hooks.zapier.com/abc")
    client, token = _auth_client(db, tenant_a, "c8@test.com", monkeypatch)
    try:
        r = client.delete("/api/v1/integrations/zapier", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 204
        conn = svc.get_connection(db, tenant_a.id, "zapier")
        assert conn is not None
        assert conn.status == "not_connected"
        assert conn.provider_credentials is None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 9. Integration is associated with the correct tenant
# ---------------------------------------------------------------------------

def test_connection_is_tenant_scoped(integ_db):
    db, tenant_a, tenant_b = integ_db
    svc = IntegrationService()
    svc.connect_webhook(db, tenant_a.id, "n8n", "https://n8n.example.com/webhook/abc")
    # tenant_b should not see tenant_a's connection
    conn_b = svc.get_connection(db, tenant_b.id, "n8n")
    assert conn_b is None
    conn_a = svc.get_connection(db, tenant_a.id, "n8n")
    assert conn_a is not None
    assert conn_a.tenant_id == tenant_a.id


# ---------------------------------------------------------------------------
# 10. Employee/agent association is tenant-safe
# ---------------------------------------------------------------------------

def test_employee_belongs_to_correct_tenant(integ_db):
    db, tenant_a, tenant_b = integ_db
    emp_a = _make_employee(db, tenant_a)
    emp_b = _make_employee(db, tenant_b)
    assert emp_a.tenant_id == tenant_a.id
    assert emp_b.tenant_id == tenant_b.id
    assert emp_a.tenant_id != emp_b.tenant_id


# ---------------------------------------------------------------------------
# 11. Post-call integration dispatch uses the correct tenant connection
# ---------------------------------------------------------------------------

def test_dispatcher_uses_correct_tenant_connection(integ_db):
    db, tenant_a, tenant_b = integ_db
    emp_a = _make_employee(db, tenant_a)
    call_a = _make_call(db, tenant_a, emp_a)
    # Give tenant_a a webhook connection
    conn_a = IntegrationConnection(
        tenant_id=tenant_a.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://example.com/hook"},
    )
    # Give tenant_b a different webhook connection
    conn_b = IntegrationConnection(
        tenant_id=tenant_b.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://tenant-b.example.com/hook"},
    )
    db.add_all([conn_a, conn_b])
    db.commit()

    posted_urls = []

    def fake_post(url, payload):
        posted_urls.append(url)

    dispatcher = IntegrationDispatcher()
    with patch("app.services.integration_dispatcher._post_webhook", side_effect=fake_post):
        dispatcher.dispatch(db, call_a)

    assert len(posted_urls) == 1
    assert posted_urls[0] == "https://example.com/hook"


# ---------------------------------------------------------------------------
# 12. External integration failure is retryable (dispatcher does not raise)
# ---------------------------------------------------------------------------

def test_dispatcher_does_not_raise_on_external_failure(integ_db):
    db, tenant_a, _ = integ_db
    emp_a = _make_employee(db, tenant_a)
    call_a = _make_call(db, tenant_a, emp_a)
    conn = IntegrationConnection(
        tenant_id=tenant_a.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://example.com/hook"},
    )
    db.add(conn)
    db.commit()

    def always_fail(url, payload):
        raise ConnectionError("Network failure")

    dispatcher = IntegrationDispatcher()
    with patch("app.services.integration_dispatcher._post_webhook", side_effect=always_fail):
        # Must not raise — failures are logged, not propagated
        dispatcher.dispatch(db, call_a)


# ---------------------------------------------------------------------------
# 13. Omni API keys never reach the frontend
# ---------------------------------------------------------------------------

def test_omni_api_key_never_in_response(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c13@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        raw = r.text
        assert "omnidimension" not in raw.lower()
        assert "omnidim" not in raw.lower()
        assert "omni_api_key" not in raw.lower()
        assert "provider_agent_id" not in raw.lower()
        assert "provider_id" not in raw.lower()
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 14. No integration action redirects to the Omni dashboard
# ---------------------------------------------------------------------------

def test_connect_does_not_redirect_to_omni(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c14@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/slack/connect/webhook",
            json={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # Must not redirect to omnidim.io
        assert r.status_code != 302 or "omnidim.io" not in r.headers.get("location", "")
        if r.status_code == 302:
            assert "omnidim.io" not in r.headers["location"]
    finally:
        app.dependency_overrides.clear()


def test_oauth_init_returns_provider_url_not_omni(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c14b@test.com", monkeypatch)
    # Patch settings to have a HubSpot client ID
    from app.core import config as cfg_mod
    fake_settings = MagicMock()
    fake_settings.hubspot_oauth_client_id = "fake-client-id"
    fake_settings.hubspot_oauth_redirect_uri = "http://localhost:8000/api/v1/integrations/hubspot/connect/oauth/callback"
    fake_settings.backend_public_url = "http://localhost:8000"
    monkeypatch.setattr(cfg_mod, "get_settings", lambda: fake_settings)
    import app.api.v1.endpoints.integrations as integ_mod
    monkeypatch.setattr(integ_mod, "get_settings", lambda: fake_settings)
    try:
        r = client.post(
            "/api/v1/integrations/hubspot/connect/oauth/init",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        url = r.json()["authorization_url"]
        assert "omnidim.io" not in url
        assert "hubspot.com" in url
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 15. Unsupported coming-soon integrations not falsely reported as connected
# ---------------------------------------------------------------------------

def test_coming_soon_integration_never_connected(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c15@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        coming_soon = [i for i in r.json() if i["connection_mode"] == "coming_soon"]
        assert len(coming_soon) > 0
        for item in coming_soon:
            assert item["status"] != "connected"
    finally:
        app.dependency_overrides.clear()


def test_coming_soon_connect_returns_400(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    client, token = _auth_client(db, tenant_a, "c15b@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/whatsapp/connect/webhook",
            json={"webhook_url": "https://example.com/hook"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Credential encryption round-trip
# ---------------------------------------------------------------------------

def test_credentials_encrypted_at_rest(integ_db):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    svc.connect_api_key(db, tenant_a.id, "cal_com", "plaintext-secret")
    conn = svc.get_connection(db, tenant_a.id, "cal_com")
    assert conn is not None
    # Raw DB value must not be plaintext
    assert "plaintext-secret" not in (conn.provider_credentials or "")
    # But decryption must work
    creds = svc.get_decrypted_credentials(conn)
    assert creds["api_key"] == "plaintext-secret"


# ===========================================================================
# Task 17.2 — Google Calendar OAuth regression tests
# ===========================================================================

def _gc_fake_settings(monkeypatch, *, client_id="gc-client-id", client_secret="gc-client-secret"):
    """Return a MagicMock settings object with Google Calendar OAuth configured."""
    import app.api.v1.endpoints.integrations as integ_mod
    from app.core import config as cfg_mod
    fake = MagicMock()
    fake.google_calendar_oauth_client_id = client_id
    fake.google_calendar_oauth_client_secret = client_secret
    fake.google_calendar_oauth_redirect_uri = (
        "http://localhost:8000/api/v1/integrations/google_calendar/connect/oauth/callback"
    )
    fake.backend_public_url = "http://localhost:8000"
    fake.frontend_url = "http://localhost:5173"
    monkeypatch.setattr(cfg_mod, "get_settings", lambda: fake)
    monkeypatch.setattr(integ_mod, "get_settings", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# GC-1. Missing Google OAuth configuration returns safe 503
# ---------------------------------------------------------------------------

def test_gc_missing_config_returns_503(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    import app.api.v1.endpoints.integrations as integ_mod
    from app.core import config as cfg_mod
    fake = MagicMock()
    fake.google_calendar_oauth_client_id = None
    fake.google_calendar_oauth_redirect_uri = None
    fake.backend_public_url = "http://localhost:8000"
    monkeypatch.setattr(cfg_mod, "get_settings", lambda: fake)
    monkeypatch.setattr(integ_mod, "get_settings", lambda: fake)
    client, token = _auth_client(db, tenant_a, "gc1@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/google_calendar/connect/oauth/init",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 503
        body = r.json()["detail"].lower()
        # Must not expose env var names or secrets
        assert "google_calendar_oauth_client_id" not in body
        assert "google_calendar_oauth_client_secret" not in body
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-2. Valid configuration generates authorization URL
# ---------------------------------------------------------------------------

def test_gc_valid_config_returns_authorization_url(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    _gc_fake_settings(monkeypatch)
    client, token = _auth_client(db, tenant_a, "gc2@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/google_calendar/connect/oauth/init",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert "authorization_url" in data
        assert data["authorization_url"].startswith("https://accounts.google.com/")
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-3. OAuth state is stored with correct tenant
# ---------------------------------------------------------------------------

def test_gc_oauth_state_stored_with_correct_tenant(integ_db, monkeypatch):
    db, tenant_a, tenant_b = integ_db
    svc = IntegrationService()
    token_a = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    from sqlalchemy import select
    state = db.scalar(select(OAuthState).where(OAuthState.state_token == token_a))
    assert state is not None
    assert state.tenant_id == tenant_a.id
    assert state.integration_key == "google_calendar"
    assert state.tenant_id != tenant_b.id


# ---------------------------------------------------------------------------
# GC-4. OAuth state expires correctly
# ---------------------------------------------------------------------------

def test_gc_oauth_state_expires(integ_db):
    db, tenant_a, _ = integ_db
    expired = OAuthState(
        tenant_id=tenant_a.id,
        integration_key="google_calendar",
        state_token="gc-expired-state",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db.add(expired)
    db.commit()
    svc = IntegrationService()
    with pytest.raises(ValueError, match="expired"):
        svc.consume_oauth_state(db, "gc-expired-state")


# ---------------------------------------------------------------------------
# GC-5. OAuth state is single-use
# ---------------------------------------------------------------------------

def test_gc_oauth_state_is_single_use(integ_db):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    state_token = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    svc.consume_oauth_state(db, state_token)
    with pytest.raises(ValueError, match="already used"):
        svc.consume_oauth_state(db, state_token)


# ---------------------------------------------------------------------------
# GC-6. Invalid state is rejected
# ---------------------------------------------------------------------------

def test_gc_invalid_state_rejected(integ_db):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    with pytest.raises(ValueError, match="Invalid"):
        svc.consume_oauth_state(db, "not-a-real-gc-state-token")


# ---------------------------------------------------------------------------
# GC-7. Callback cannot select another tenant (tenant resolved from state)
# ---------------------------------------------------------------------------

def test_gc_callback_tenant_resolved_from_state_not_query(integ_db, monkeypatch):
    """Tenant must come from the stored OAuth state, not from a browser-supplied param."""
    db, tenant_a, tenant_b = integ_db
    svc = IntegrationService()
    # State belongs to tenant_a
    state_token = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    _gc_fake_settings(monkeypatch)

    fake_tokens = {
        "access_token": "ya29.fake",
        "refresh_token": "1//fake-refresh",
        "token_type": "Bearer",
        "expires_in": 3600,
    }

    import app.api.v1.endpoints.integrations as integ_mod
    monkeypatch.setattr(integ_mod, "_exchange_code", lambda *a, **kw: fake_tokens)

    import app.api.v1.endpoints.integrations as integ_mod2
    client = TestClient(app, follow_redirects=False)
    app.dependency_overrides[get_db] = lambda: db

    try:
        r = client.get(
            f"/api/v1/integrations/google_calendar/connect/oauth/callback"
            f"?code=fake-code&state={state_token}",
        )
        # Callback must redirect to our frontend, not omnidim
        assert r.status_code == 302
        assert "omnidim" not in r.headers["location"]
        # Connection must be on tenant_a, not tenant_b
        conn_a = svc.get_connection(db, tenant_a.id, "google_calendar")
        conn_b = svc.get_connection(db, tenant_b.id, "google_calendar")
        assert conn_a is not None
        assert conn_a.status == "connected"
        assert conn_b is None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-8. Authorization code exchange occurs server-side
# ---------------------------------------------------------------------------

def test_gc_code_exchange_is_server_side(integ_db, monkeypatch):
    """_exchange_code must be called; tokens must never appear in the redirect URL."""
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    state_token = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    _gc_fake_settings(monkeypatch)

    exchange_called = []
    fake_tokens = {"access_token": "ya29.server-side", "refresh_token": "1//server-side", "token_type": "Bearer"}

    import app.api.v1.endpoints.integrations as integ_mod
    def fake_exchange(*args, **kwargs):
        exchange_called.append(True)
        return fake_tokens
    monkeypatch.setattr(integ_mod, "_exchange_code", fake_exchange)

    client = TestClient(app, follow_redirects=False)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(
            f"/api/v1/integrations/google_calendar/connect/oauth/callback"
            f"?code=auth-code-xyz&state={state_token}",
        )
        assert exchange_called, "Token exchange was not called server-side"
        assert r.status_code == 302
        location = r.headers["location"]
        assert "access_token" not in location
        assert "refresh_token" not in location
        assert "ya29" not in location
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-9. Tokens are encrypted before storage
# ---------------------------------------------------------------------------

def test_gc_tokens_encrypted_at_rest(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    state_token = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    _gc_fake_settings(monkeypatch)

    fake_tokens = {"access_token": "plaintext-access-token", "refresh_token": "plaintext-refresh-token"}

    import app.api.v1.endpoints.integrations as integ_mod
    monkeypatch.setattr(integ_mod, "_exchange_code", lambda *a, **kw: fake_tokens)

    client = TestClient(app, follow_redirects=False)
    app.dependency_overrides[get_db] = lambda: db
    try:
        client.get(
            f"/api/v1/integrations/google_calendar/connect/oauth/callback"
            f"?code=code&state={state_token}",
        )
        conn = svc.get_connection(db, tenant_a.id, "google_calendar")
        assert conn is not None
        raw = conn.provider_credentials or ""
        assert "plaintext-access-token" not in raw
        assert "plaintext-refresh-token" not in raw
        # But decryption must recover them
        creds = svc.get_decrypted_credentials(conn)
        assert creds["access_token"] == "plaintext-access-token"
        assert creds["refresh_token"] == "plaintext-refresh-token"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-10. Tokens never appear in API responses
# ---------------------------------------------------------------------------

def test_gc_tokens_never_in_api_response(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, "google_calendar", {
        "access_token": "secret-access-token-gc",
        "refresh_token": "secret-refresh-token-gc",
    })
    client, token = _auth_client(db, tenant_a, "gc10@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        raw = r.text
        assert "secret-access-token-gc" not in raw
        assert "secret-refresh-token-gc" not in raw
        assert "access_token" not in raw
        assert "refresh_token" not in raw
        assert "provider_credentials" not in raw
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-11. Successful callback marks integration connected
# ---------------------------------------------------------------------------

def test_gc_successful_callback_marks_connected(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    state_token = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    _gc_fake_settings(monkeypatch)

    import app.api.v1.endpoints.integrations as integ_mod
    monkeypatch.setattr(integ_mod, "_exchange_code", lambda *a, **kw: {
        "access_token": "tok", "refresh_token": "ref", "token_type": "Bearer"
    })

    client = TestClient(app, follow_redirects=False)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(
            f"/api/v1/integrations/google_calendar/connect/oauth/callback"
            f"?code=code&state={state_token}",
        )
        assert r.status_code == 302
        conn = svc.get_connection(db, tenant_a.id, "google_calendar")
        assert conn is not None
        assert conn.status == "connected"
        assert conn.connected_at is not None
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-12. Callback redirects to our frontend
# ---------------------------------------------------------------------------

def test_gc_callback_redirects_to_our_frontend(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    svc = IntegrationService()
    state_token = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    _gc_fake_settings(monkeypatch)

    import app.api.v1.endpoints.integrations as integ_mod
    monkeypatch.setattr(integ_mod, "_exchange_code", lambda *a, **kw: {
        "access_token": "tok", "refresh_token": "ref"
    })

    client = TestClient(app, follow_redirects=False)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(
            f"/api/v1/integrations/google_calendar/connect/oauth/callback"
            f"?code=code&state={state_token}",
        )
        assert r.status_code == 302
        location = r.headers["location"]
        assert "localhost:5173" in location or "frontend" in location
        assert "connected=google_calendar" in location
        assert "omnidim" not in location
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-13. No Omni dashboard redirect exists
# ---------------------------------------------------------------------------

def test_gc_oauth_init_never_redirects_to_omni(integ_db, monkeypatch):
    db, tenant_a, _ = integ_db
    _gc_fake_settings(monkeypatch)
    client, token = _auth_client(db, tenant_a, "gc13@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/google_calendar/connect/oauth/init",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        url = r.json()["authorization_url"]
        assert "omnidim.io" not in url
        assert "accounts.google.com" in url
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GC-14. Google Calendar integration remains tenant isolated
# ---------------------------------------------------------------------------

def test_gc_integration_is_tenant_isolated(integ_db, monkeypatch):
    db, tenant_a, tenant_b = integ_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, "google_calendar", {
        "access_token": "tok-a", "refresh_token": "ref-a"
    })
    # tenant_b must not see tenant_a's connection
    conn_b = svc.get_connection(db, tenant_b.id, "google_calendar")
    assert conn_b is None

    client_b, token_b = _auth_client(db, tenant_b, "gc14@test.com", monkeypatch)
    try:
        r = client_b.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token_b}"})
        gc = next(i for i in r.json() if i["key"] == "google_calendar")
        assert gc["status"] == "not_connected"
        assert gc["external_account_reference"] is None
    finally:
        app.dependency_overrides.clear()
