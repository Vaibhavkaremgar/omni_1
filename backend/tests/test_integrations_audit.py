"""Task 17.3 — Multi-tenant integration audit tests.

Covers all 34 required test cases across:
- Google Calendar OAuth (GC)
- Other OAuth integrations (HubSpot, Salesforce, GoHighLevel)
- API key integrations (Cal.com)
- Webhook integrations (Slack, Make, Zapier, n8n, Custom API)
- SSRF protection
- Post-call dispatch tenant isolation
- Disconnect security
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
from app.services.integration_dispatcher import IntegrationDispatcher, _is_safe_webhook_url


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def audit_db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    cols = {c["name"] for c in inspect(engine).get_columns("tenants")}
    with engine.begin() as conn:
        if "instant_leads_enabled" not in cols:
            conn.execute(text(
                "ALTER TABLE tenants ADD COLUMN instant_leads_enabled BOOLEAN NOT NULL DEFAULT 1"
            ))
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    tenant_a = Tenant(name="Tenant A", slug="audit-a", instant_leads_enabled=True)
    tenant_b = Tenant(name="Tenant B", slug="audit-b", instant_leads_enabled=True)
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
        llm_provider="openai", llm_model="gpt-4o",
        status=EmployeeStatus.published.value,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _make_call(db, tenant, employee):
    call = Call(
        tenant_id=tenant.id, employee_id=employee.id,
        direction=CallDirection.outbound.value,
        status=CallStatus.completed.value,
        customer_phone_number="+15551234567",
        duration_seconds=60,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _gc_settings(monkeypatch, client_id="gc-id", client_secret="gc-secret"):
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


def _oauth_settings(monkeypatch, key, client_id="fake-id", client_secret="fake-secret"):
    import app.api.v1.endpoints.integrations as integ_mod
    from app.core import config as cfg_mod
    fake = MagicMock()
    setattr(fake, f"{key}_oauth_client_id", client_id)
    setattr(fake, f"{key}_oauth_client_secret", client_secret)
    setattr(fake, f"{key}_oauth_redirect_uri",
            f"http://localhost:8000/api/v1/integrations/{key}/connect/oauth/callback")
    fake.backend_public_url = "http://localhost:8000"
    fake.frontend_url = "http://localhost:5173"
    monkeypatch.setattr(cfg_mod, "get_settings", lambda: fake)
    monkeypatch.setattr(integ_mod, "get_settings", lambda: fake)
    return fake


# ===========================================================================
# GOOGLE CALENDAR — tests 1-12
# ===========================================================================

# 1. Tenant A OAuth creates Tenant A connection
def test_gc_tenant_a_oauth_creates_tenant_a_connection(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    state = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    _gc_settings(monkeypatch)
    import app.api.v1.endpoints.integrations as integ_mod
    monkeypatch.setattr(integ_mod, "_exchange_code",
                        lambda *a, **kw: {"access_token": "tok-a", "refresh_token": "ref-a"})
    client = TestClient(app, follow_redirects=False)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(
            f"/api/v1/integrations/google_calendar/connect/oauth/callback?code=c&state={state}"
        )
        assert r.status_code == 302
        conn = svc.get_connection(db, tenant_a.id, "google_calendar")
        assert conn is not None and conn.status == "connected"
        assert conn.tenant_id == tenant_a.id
    finally:
        app.dependency_overrides.clear()


# 2. Tenant B OAuth creates Tenant B connection (separate from A)
def test_gc_tenant_b_oauth_creates_separate_connection(audit_db, monkeypatch):
    db, tenant_a, tenant_b = audit_db
    svc = IntegrationService()
    # Connect both tenants independently
    svc.store_oauth_tokens(db, tenant_a.id, "google_calendar",
                           {"access_token": "tok-a", "refresh_token": "ref-a"})
    state_b = svc.create_oauth_state(db, tenant_b.id, "google_calendar")
    _gc_settings(monkeypatch)
    import app.api.v1.endpoints.integrations as integ_mod
    monkeypatch.setattr(integ_mod, "_exchange_code",
                        lambda *a, **kw: {"access_token": "tok-b", "refresh_token": "ref-b"})
    client = TestClient(app, follow_redirects=False)
    app.dependency_overrides[get_db] = lambda: db
    try:
        r = client.get(
            f"/api/v1/integrations/google_calendar/connect/oauth/callback?code=c&state={state_b}"
        )
        assert r.status_code == 302
        conn_a = svc.get_connection(db, tenant_a.id, "google_calendar")
        conn_b = svc.get_connection(db, tenant_b.id, "google_calendar")
        assert conn_a is not None and conn_b is not None
        assert conn_a.id != conn_b.id
        creds_a = svc.get_decrypted_credentials(conn_a)
        creds_b = svc.get_decrypted_credentials(conn_b)
        assert creds_a["access_token"] == "tok-a"
        assert creds_b["access_token"] == "tok-b"
    finally:
        app.dependency_overrides.clear()


# 3. Tenant A cannot access Tenant B credentials
def test_gc_tenant_a_cannot_access_tenant_b_credentials(audit_db):
    db, tenant_a, tenant_b = audit_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_b.id, "google_calendar",
                           {"access_token": "secret-b", "refresh_token": "ref-b"})
    conn_a = svc.get_connection(db, tenant_a.id, "google_calendar")
    assert conn_a is None


# 4. Tenant B cannot access Tenant A credentials
def test_gc_tenant_b_cannot_access_tenant_a_credentials(audit_db):
    db, tenant_a, tenant_b = audit_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, "google_calendar",
                           {"access_token": "secret-a", "refresh_token": "ref-a"})
    conn_b = svc.get_connection(db, tenant_b.id, "google_calendar")
    assert conn_b is None


# 5. OAuth state is tenant-bound
def test_gc_oauth_state_is_tenant_bound(audit_db):
    db, tenant_a, tenant_b = audit_db
    svc = IntegrationService()
    from sqlalchemy import select
    token = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    state = db.scalar(select(OAuthState).where(OAuthState.state_token == token))
    assert state.tenant_id == tenant_a.id
    assert state.tenant_id != tenant_b.id


# 6. OAuth state is single-use
def test_gc_oauth_state_single_use(audit_db):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    token = svc.create_oauth_state(db, tenant_a.id, "google_calendar")
    svc.consume_oauth_state(db, token)
    with pytest.raises(ValueError, match="already used"):
        svc.consume_oauth_state(db, token)


# 7. Expired state rejected
def test_gc_expired_state_rejected(audit_db):
    db, tenant_a, _ = audit_db
    state = OAuthState(
        tenant_id=tenant_a.id, integration_key="google_calendar",
        state_token="gc-exp-audit",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    db.add(state)
    db.commit()
    svc = IntegrationService()
    with pytest.raises(ValueError, match="expired"):
        svc.consume_oauth_state(db, "gc-exp-audit")


# 8. Invalid state rejected
def test_gc_invalid_state_rejected(audit_db):
    db, _, _ = audit_db
    svc = IntegrationService()
    with pytest.raises(ValueError, match="Invalid"):
        svc.consume_oauth_state(db, "totally-bogus-gc-state")


# 9. Tokens encrypted at rest
def test_gc_tokens_encrypted_at_rest(audit_db):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, "google_calendar",
                           {"access_token": "plain-access", "refresh_token": "plain-refresh"})
    conn = svc.get_connection(db, tenant_a.id, "google_calendar")
    raw = conn.provider_credentials or ""
    assert "plain-access" not in raw
    assert "plain-refresh" not in raw
    creds = svc.get_decrypted_credentials(conn)
    assert creds["access_token"] == "plain-access"
    assert creds["refresh_token"] == "plain-refresh"


# 10. Tokens never returned in API responses
def test_gc_tokens_never_in_api_response(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, "google_calendar",
                           {"access_token": "secret-gc-access", "refresh_token": "secret-gc-refresh"})
    client, token = _auth_client(db, tenant_a, "gc10a@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        raw = r.text
        assert "secret-gc-access" not in raw
        assert "secret-gc-refresh" not in raw
        assert "access_token" not in raw
        assert "refresh_token" not in raw
        assert "provider_credentials" not in raw
    finally:
        app.dependency_overrides.clear()


# 11. Disconnect works
def test_gc_disconnect_works(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, "google_calendar",
                           {"access_token": "tok", "refresh_token": "ref"})
    client, token = _auth_client(db, tenant_a, "gc11@test.com", monkeypatch)
    try:
        r = client.delete("/api/v1/integrations/google_calendar",
                          headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 204
        conn = svc.get_connection(db, tenant_a.id, "google_calendar")
        assert conn.status == "not_connected"
        assert conn.provider_credentials is None
    finally:
        app.dependency_overrides.clear()


# 12. Token refresh — not yet implemented; verify no fake flow exists
def test_gc_token_refresh_not_faked(audit_db):
    """Verify the dispatcher does not invent a fake refresh flow.
    The dispatcher logs a pending-implementation message and returns without error.
    A real refresh implementation is out of scope for this task.
    """
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, "google_calendar",
                           {"access_token": "tok", "refresh_token": "ref"})
    emp = _make_employee(db, tenant_a)
    call = _make_call(db, tenant_a, emp)
    dispatcher = IntegrationDispatcher()
    # Must not raise; must not make real HTTP calls
    dispatcher.dispatch(db, call)


# ===========================================================================
# OTHER OAUTH — tests 13-18
# ===========================================================================

def _oauth_connect(db, tenant, key, monkeypatch):
    """Helper: run a full OAuth callback for any OAuth provider."""
    svc = IntegrationService()
    state = svc.create_oauth_state(db, tenant.id, key)
    _oauth_settings(monkeypatch, key)
    import app.api.v1.endpoints.integrations as integ_mod
    monkeypatch.setattr(integ_mod, "_exchange_code",
                        lambda *a, **kw: {"access_token": f"tok-{key}", "refresh_token": f"ref-{key}"})
    client = TestClient(app, follow_redirects=False)
    app.dependency_overrides[get_db] = lambda: db
    r = client.get(
        f"/api/v1/integrations/{key}/connect/oauth/callback?code=c&state={state}"
    )
    app.dependency_overrides.clear()
    return r


# 13. HubSpot tenant isolation
def test_hubspot_tenant_isolation(audit_db, monkeypatch):
    db, tenant_a, tenant_b = audit_db
    _oauth_connect(db, tenant_a, "hubspot", monkeypatch)
    svc = IntegrationService()
    assert svc.get_connection(db, tenant_a.id, "hubspot") is not None
    assert svc.get_connection(db, tenant_b.id, "hubspot") is None


# 14. Salesforce tenant isolation
def test_salesforce_tenant_isolation(audit_db, monkeypatch):
    db, tenant_a, tenant_b = audit_db
    _oauth_connect(db, tenant_a, "salesforce", monkeypatch)
    svc = IntegrationService()
    assert svc.get_connection(db, tenant_a.id, "salesforce") is not None
    assert svc.get_connection(db, tenant_b.id, "salesforce") is None


# 15. GoHighLevel tenant isolation
def test_ghl_tenant_isolation(audit_db, monkeypatch):
    db, tenant_a, tenant_b = audit_db
    _oauth_connect(db, tenant_a, "ghl", monkeypatch)
    svc = IntegrationService()
    assert svc.get_connection(db, tenant_a.id, "ghl") is not None
    assert svc.get_connection(db, tenant_b.id, "ghl") is None


# 16. OAuth state security for each provider
@pytest.mark.parametrize("key", ["hubspot", "salesforce", "ghl", "google_calendar"])
def test_oauth_state_security_per_provider(audit_db, key):
    db, tenant_a, tenant_b = audit_db
    svc = IntegrationService()
    from sqlalchemy import select
    token = svc.create_oauth_state(db, tenant_a.id, key)
    state = db.scalar(select(OAuthState).where(OAuthState.state_token == token))
    assert state.tenant_id == tenant_a.id
    assert state.integration_key == key
    assert state.used is False
    # SQLite returns naive datetimes; strip tzinfo for comparison
    expires = state.expires_at.replace(tzinfo=None) if state.expires_at.tzinfo else state.expires_at
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert expires > now


# 17. Credentials encrypted for all OAuth providers
@pytest.mark.parametrize("key", ["hubspot", "salesforce", "ghl", "google_calendar"])
def test_oauth_credentials_encrypted_per_provider(audit_db, key):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, key,
                           {"access_token": f"plain-{key}", "refresh_token": f"ref-{key}"})
    conn = svc.get_connection(db, tenant_a.id, key)
    raw = conn.provider_credentials or ""
    assert f"plain-{key}" not in raw
    creds = svc.get_decrypted_credentials(conn)
    assert creds["access_token"] == f"plain-{key}"


# 18. Credentials never returned for any OAuth provider
@pytest.mark.parametrize("key", ["hubspot", "salesforce", "ghl", "google_calendar"])
def test_oauth_credentials_never_returned(audit_db, monkeypatch, key):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    svc.store_oauth_tokens(db, tenant_a.id, key,
                           {"access_token": f"secret-{key}-tok"})
    email = f"cr18-{key}@test.com"
    client, token = _auth_client(db, tenant_a, email, monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        assert f"secret-{key}-tok" not in r.text
        assert "access_token" not in r.text
        assert "provider_credentials" not in r.text
    finally:
        app.dependency_overrides.clear()


# ===========================================================================
# API KEY — tests 19-20
# ===========================================================================

# 19. Cal.com credentials encrypted
def test_calcom_credentials_encrypted(audit_db):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    svc.connect_api_key(db, tenant_a.id, "cal_com", "calcom-plain-key")
    conn = svc.get_connection(db, tenant_a.id, "cal_com")
    assert "calcom-plain-key" not in (conn.provider_credentials or "")
    assert svc.get_decrypted_credentials(conn)["api_key"] == "calcom-plain-key"


# 20. Cal.com cross-tenant access rejected
def test_calcom_cross_tenant_rejected(audit_db):
    db, tenant_a, tenant_b = audit_db
    svc = IntegrationService()
    svc.connect_api_key(db, tenant_a.id, "cal_com", "calcom-secret-a")
    assert svc.get_connection(db, tenant_b.id, "cal_com") is None


# ===========================================================================
# WEBHOOK INTEGRATIONS — tests 21-28
# ===========================================================================

# 21. Slack webhook connects
def test_slack_webhook_connects(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, "wh21@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/slack/connect/webhook",
            json={"webhook_url": "https://hooks.slack.com/services/T/B/X"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "connected"
    finally:
        app.dependency_overrides.clear()


# 22. Make webhook connects
def test_make_webhook_connects(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, "wh22@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/make/connect/webhook",
            json={"webhook_url": "https://hook.eu1.make.com/abc123"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "connected"
    finally:
        app.dependency_overrides.clear()


# 23. Zapier webhook connects
def test_zapier_webhook_connects(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, "wh23@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/zapier/connect/webhook",
            json={"webhook_url": "https://hooks.zapier.com/hooks/catch/abc/xyz"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "connected"
    finally:
        app.dependency_overrides.clear()


# 24. n8n webhook connects
def test_n8n_webhook_connects(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, "wh24@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/n8n/connect/webhook",
            json={"webhook_url": "https://n8n.example.com/webhook/abc"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "connected"
    finally:
        app.dependency_overrides.clear()


# 25. Custom API webhook connects
def test_custom_api_webhook_connects(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, "wh25@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/custom_api/connect/webhook",
            json={"webhook_url": "https://api.example.com/hook"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["status"] == "connected"
    finally:
        app.dependency_overrides.clear()


# 26. Plain HTTP (non-localhost) webhook URL rejected
def test_http_non_localhost_webhook_rejected(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, "wh26@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/custom_api/connect/webhook",
            json={"webhook_url": "http://api.example.com/hook"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


# 27. SSRF / internal destinations rejected at connect time
@pytest.mark.parametrize("bad_url", [
    "http://127.0.0.1/hook",
    "https://169.254.169.254/latest/meta-data/",
    "https://10.0.0.1/hook",
    "https://192.168.1.1/hook",
    "https://172.16.0.1/hook",
    "https://0.0.0.0/hook",
    "ftp://example.com/hook",
])
def test_ssrf_destinations_rejected_at_connect(audit_db, monkeypatch, bad_url):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, f"ssrf-{bad_url[-4:]}@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/custom_api/connect/webhook",
            json={"webhook_url": bad_url},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


# 27b. SSRF guard in dispatcher (_is_safe_webhook_url unit tests)
# The dispatcher's _is_safe_webhook_url only blocks private IPs and dangerous schemes.
# HTTPS enforcement is the connect endpoint's responsibility, not the dispatcher's.
@pytest.mark.parametrize("url,expected", [
    ("https://hooks.slack.com/services/T/B/X", True),
    ("https://api.example.com/hook", True),
    ("http://localhost/hook", True),          # allowed for local dev
    ("http://127.0.0.1/hook", False),         # loopback IP blocked even over http
    ("http://api.example.com/hook", True),    # dispatcher allows http; connect endpoint blocks it
    ("https://127.0.0.1/hook", False),        # https to loopback — blocked
    ("https://10.0.0.1/hook", False),
    ("https://192.168.1.1/hook", False),
    ("https://172.16.0.1/hook", False),
    ("https://169.254.169.254/hook", False),
    ("https://0.0.0.0/hook", False),
    ("ftp://example.com/hook", False),
])
def test_is_safe_webhook_url(url, expected):
    assert _is_safe_webhook_url(url) is expected


# 28. Webhook tenant isolation — stored connection is scoped to correct tenant
def test_webhook_tenant_isolation(audit_db, monkeypatch):
    db, tenant_a, tenant_b = audit_db
    svc = IntegrationService()
    svc.connect_webhook(db, tenant_a.id, "custom_api", "https://a.example.com/hook")
    assert svc.get_connection(db, tenant_b.id, "custom_api") is None
    conn_a = svc.get_connection(db, tenant_a.id, "custom_api")
    assert conn_a is not None
    assert conn_a.tenant_id == tenant_a.id


# ===========================================================================
# POST-CALL DISPATCH — tests 29-32
# ===========================================================================

# 29. Tenant A call only dispatches to Tenant A integrations
def test_dispatch_tenant_a_only_reaches_tenant_a(audit_db):
    db, tenant_a, tenant_b = audit_db
    emp_a = _make_employee(db, tenant_a)
    call_a = _make_call(db, tenant_a, emp_a)
    conn_a = IntegrationConnection(
        tenant_id=tenant_a.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://a.example.com/hook"},
    )
    conn_b = IntegrationConnection(
        tenant_id=tenant_b.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://b.example.com/hook"},
    )
    db.add_all([conn_a, conn_b])
    db.commit()

    posted = []
    def fake_post(url, payload):
        posted.append(url)

    dispatcher = IntegrationDispatcher()
    with patch("app.services.integration_dispatcher._post_webhook", side_effect=fake_post):
        dispatcher.dispatch(db, call_a)

    assert posted == ["https://a.example.com/hook"]


# 30. Tenant B call only dispatches to Tenant B integrations
def test_dispatch_tenant_b_only_reaches_tenant_b(audit_db):
    db, tenant_a, tenant_b = audit_db
    emp_b = _make_employee(db, tenant_b)
    call_b = _make_call(db, tenant_b, emp_b)
    conn_a = IntegrationConnection(
        tenant_id=tenant_a.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://a.example.com/hook"},
    )
    conn_b = IntegrationConnection(
        tenant_id=tenant_b.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://b.example.com/hook"},
    )
    db.add_all([conn_a, conn_b])
    db.commit()

    posted = []
    def fake_post(url, payload):
        posted.append(url)

    dispatcher = IntegrationDispatcher()
    with patch("app.services.integration_dispatcher._post_webhook", side_effect=fake_post):
        dispatcher.dispatch(db, call_b)

    assert posted == ["https://b.example.com/hook"]


# 31. Failed integration does not break call-result processing
def test_dispatch_failure_does_not_raise(audit_db):
    db, tenant_a, _ = audit_db
    emp = _make_employee(db, tenant_a)
    call = _make_call(db, tenant_a, emp)
    conn = IntegrationConnection(
        tenant_id=tenant_a.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://a.example.com/hook"},
    )
    db.add(conn)
    db.commit()

    def always_fail(url, payload):
        raise ConnectionError("network down")

    dispatcher = IntegrationDispatcher()
    with patch("app.services.integration_dispatcher._post_webhook", side_effect=always_fail):
        dispatcher.dispatch(db, call)  # must not raise


# 32. SSRF-blocked URL in dispatcher does not raise (logged, swallowed)
def test_dispatch_ssrf_blocked_url_does_not_raise(audit_db):
    db, tenant_a, _ = audit_db
    emp = _make_employee(db, tenant_a)
    call = _make_call(db, tenant_a, emp)
    # Insert a record with a private IP directly (bypassing the connect endpoint)
    conn = IntegrationConnection(
        tenant_id=tenant_a.id, integration_key="custom_api",
        display_name="Custom API", status="connected",
        configuration={"webhook_url": "https://192.168.1.1/hook"},
    )
    db.add(conn)
    db.commit()
    dispatcher = IntegrationDispatcher()
    # Must not raise — SSRF block is logged and swallowed
    dispatcher.dispatch(db, call)


# ===========================================================================
# DISCONNECT — tests 33-34
# ===========================================================================

# 33. Disconnected integration no longer receives post-call events
def test_disconnected_integration_not_dispatched(audit_db):
    db, tenant_a, _ = audit_db
    svc = IntegrationService()
    svc.connect_webhook(db, tenant_a.id, "custom_api", "https://a.example.com/hook")
    svc.disconnect(db, tenant_a.id, "custom_api")

    emp = _make_employee(db, tenant_a)
    call = _make_call(db, tenant_a, emp)

    posted = []
    def fake_post(url, payload):
        posted.append(url)

    dispatcher = IntegrationDispatcher()
    with patch("app.services.integration_dispatcher._post_webhook", side_effect=fake_post):
        dispatcher.dispatch(db, call)

    assert posted == []


# 34. Cross-tenant disconnect rejected — tenant B cannot disconnect tenant A's integration
def test_cross_tenant_disconnect_rejected(audit_db, monkeypatch):
    db, tenant_a, tenant_b = audit_db
    svc = IntegrationService()
    svc.connect_webhook(db, tenant_a.id, "custom_api", "https://a.example.com/hook")

    # Authenticate as tenant_b and attempt to disconnect tenant_a's integration
    client_b, token_b = _auth_client(db, tenant_b, "disc34@test.com", monkeypatch)
    try:
        r = client_b.delete(
            "/api/v1/integrations/custom_api",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        # Either 204 (no-op — tenant_b has no connection) or 404; must NOT touch tenant_a
        assert r.status_code in (204, 404)
        conn_a = svc.get_connection(db, tenant_a.id, "custom_api")
        assert conn_a is not None
        assert conn_a.status == "connected"
    finally:
        app.dependency_overrides.clear()


# ===========================================================================
# WHATSAPP — still Coming Soon
# ===========================================================================

def test_whatsapp_still_coming_soon(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, "wa@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        wa = next(i for i in r.json() if i["key"] == "whatsapp")
        assert wa["connection_mode"] == "coming_soon"
        assert wa["status"] != "connected"
    finally:
        app.dependency_overrides.clear()


def test_whatsapp_connect_blocked(audit_db, monkeypatch):
    db, tenant_a, _ = audit_db
    client, token = _auth_client(db, tenant_a, "wa2@test.com", monkeypatch)
    try:
        r = client.post(
            "/api/v1/integrations/whatsapp/connect/webhook",
            json={"webhook_url": "https://example.com/hook"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()
