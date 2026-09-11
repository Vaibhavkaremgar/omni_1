"""Tests for Task 17: settings toggle, bulk upload, integrations, calls."""
import io
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.db.base import Base
from app.main import app
from app.models.ai_employee import AIEmployee
from app.models.call import Call
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.enums import (
    CampaignStatus, CallDirection, CallStatus, EmployeeStatus, UserRole, UserStatus,
)
from app.models.phone_number import PhoneNumber
from app.models.tenant import Tenant
from app.models.user import User
from app.services import auth as auth_service


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def t17_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    # Add instant_leads_enabled if not present (in-memory SQLite)
    cols = {c["name"] for c in inspect(engine).get_columns("tenants")}
    if "instant_leads_enabled" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE tenants ADD COLUMN instant_leads_enabled BOOLEAN NOT NULL DEFAULT 1"))

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    tenant_a = Tenant(name="Tenant A", slug="t17-a", instant_leads_enabled=True)
    tenant_b = Tenant(name="Tenant B", slug="t17-b", instant_leads_enabled=True)
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
    """Return (TestClient, token) with auth patched to resolve to tenant."""
    user = _make_user(db, tenant, email)
    token = auth_service.create_access_token({"sub": str(user.id)})
    # Patch decode_access_token to return this user's id
    monkeypatch.setattr(auth_service, "decode_access_token", lambda _tok: user.id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app), token


def _make_employee(db, tenant):
    emp = AIEmployee(
        tenant_id=tenant.id, name="Bot", purpose="Test", language="en",
        llm_provider="openai", llm_model="gpt-4o", status=EmployeeStatus.published.value,
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return emp


def _make_campaign(db, tenant, employee):
    c = Campaign(
        tenant_id=tenant.id, name="Camp", employee_id=employee.id,
        status=CampaignStatus.draft.value,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── Instant Leads toggle ──────────────────────────────────────────────────────

def test_settings_get_returns_instant_leads_enabled(t17_db, monkeypatch):
    db, tenant_a, _ = t17_db
    client, token = _auth_client(db, tenant_a, "s1@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/settings", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["instant_leads_enabled"] is True
    finally:
        app.dependency_overrides.clear()


def test_settings_patch_persists_toggle(t17_db, monkeypatch):
    db, tenant_a, _ = t17_db
    client, token = _auth_client(db, tenant_a, "s2@test.com", monkeypatch)
    try:
        r = client.patch(
            "/api/v1/settings",
            json={"instant_leads_enabled": False},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        assert r.json()["instant_leads_enabled"] is False
        db.refresh(tenant_a)
        assert tenant_a.instant_leads_enabled is False
    finally:
        app.dependency_overrides.clear()


def test_settings_cross_tenant_isolation(t17_db, monkeypatch):
    """Tenant B toggling OFF must not affect Tenant A."""
    db, tenant_a, tenant_b = t17_db
    client_b, token_b = _auth_client(db, tenant_b, "s3@test.com", monkeypatch)
    try:
        r = client_b.patch(
            "/api/v1/settings",
            json={"instant_leads_enabled": False},
            headers={"Authorization": f"Bearer {token_b}"},
        )
        assert r.status_code == 200
        db.refresh(tenant_a)
        assert tenant_a.instant_leads_enabled is True  # unchanged
    finally:
        app.dependency_overrides.clear()


# ── Bulk upload ───────────────────────────────────────────────────────────────

CSV_VALID = b"first_name,last_name,phone,email\nAlice,Smith,+15551234567,alice@test.com\nBob,Jones,+15559876543,bob@test.com\n"
CSV_INVALID_PHONE = b"first_name,phone\nBad,notaphone\n"
CSV_DUPLICATE = b"first_name,phone\nAlice,+15551234567\nAlice,+15551234567\n"


def test_bulk_upload_preview_valid_csv(t17_db, monkeypatch):
    db, tenant_a, _ = t17_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    client, token = _auth_client(db, tenant_a, "u1@test.com", monkeypatch)
    try:
        r = client.post(
            f"/api/v1/campaigns/{camp.id}/contacts/upload-preview",
            files={"file": ("contacts.csv", io.BytesIO(CSV_VALID), "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid_count"] == 2
        assert data["invalid_count"] == 0
        assert data["duplicate_count"] == 0
        assert "import_token" in data
    finally:
        app.dependency_overrides.clear()


def test_bulk_upload_preview_flags_invalid_phone(t17_db, monkeypatch):
    db, tenant_a, _ = t17_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    client, token = _auth_client(db, tenant_a, "u2@test.com", monkeypatch)
    try:
        r = client.post(
            f"/api/v1/campaigns/{camp.id}/contacts/upload-preview",
            files={"file": ("contacts.csv", io.BytesIO(CSV_INVALID_PHONE), "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid_count"] == 0
        assert data["invalid_count"] == 1
    finally:
        app.dependency_overrides.clear()


def test_bulk_upload_preview_deduplicates(t17_db, monkeypatch):
    db, tenant_a, _ = t17_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    client, token = _auth_client(db, tenant_a, "u3@test.com", monkeypatch)
    try:
        r = client.post(
            f"/api/v1/campaigns/{camp.id}/contacts/upload-preview",
            files={"file": ("contacts.csv", io.BytesIO(CSV_DUPLICATE), "text/csv")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["valid_count"] == 1
        assert data["duplicate_count"] == 1
    finally:
        app.dependency_overrides.clear()


def test_bulk_upload_confirm_creates_contacts(t17_db, monkeypatch):
    import json, base64
    db, tenant_a, _ = t17_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    client, token = _auth_client(db, tenant_a, "u4@test.com", monkeypatch)
    token_data = {
        "campaign_id": str(camp.id),
        "tenant_id": str(tenant_a.id),
        "rows": [{"phone_number": "+15551234567", "first_name": "Alice", "last_name": "", "email": ""}],
    }
    import_token = base64.b64encode(json.dumps(token_data).encode()).decode()
    try:
        r = client.post(
            f"/api/v1/campaigns/{camp.id}/contacts/upload-confirm",
            json={"import_token": import_token},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201
        assert r.json()["imported"] == 1
        contacts = db.scalars(
            select(CampaignContact).where(CampaignContact.campaign_id == camp.id)
        ).all()
        assert len(contacts) == 1
        assert contacts[0].phone_number == "+15551234567"
        assert contacts[0].tenant_id == tenant_a.id
    finally:
        app.dependency_overrides.clear()


def test_bulk_upload_confirm_rejects_cross_tenant_token(t17_db, monkeypatch):
    import json, base64
    db, tenant_a, tenant_b = t17_db
    emp_a = _make_employee(db, tenant_a)
    camp_a = _make_campaign(db, tenant_a, emp_a)
    client_a, token_a = _auth_client(db, tenant_a, "u5@test.com", monkeypatch)
    # Token claims tenant_b
    token_data = {
        "campaign_id": str(camp_a.id),
        "tenant_id": str(tenant_b.id),
        "rows": [{"phone_number": "+15551234567", "first_name": "", "last_name": "", "email": ""}],
    }
    import_token = base64.b64encode(json.dumps(token_data).encode()).decode()
    try:
        r = client_a.post(
            f"/api/v1/campaigns/{camp_a.id}/contacts/upload-confirm",
            json={"import_token": import_token},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        assert r.status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_bulk_upload_rejects_unsupported_format(t17_db, monkeypatch):
    db, tenant_a, _ = t17_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    client, token = _auth_client(db, tenant_a, "u6@test.com", monkeypatch)
    try:
        r = client.post(
            f"/api/v1/campaigns/{camp.id}/contacts/upload-preview",
            files={"file": ("data.pdf", io.BytesIO(b"%PDF"), "application/pdf")},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


# ── Integrations ──────────────────────────────────────────────────────────────

def test_integrations_returns_catalog_without_provider_keys(t17_db, monkeypatch):
    db, tenant_a, _ = t17_db
    client, token = _auth_client(db, tenant_a, "i1@test.com", monkeypatch)
    try:
        r = client.get("/api/v1/integrations", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        items = r.json()
        assert len(items) > 0
        for item in items:
            assert "key" in item
            assert "name" in item
            assert "status" in item
            # Omni internals must never appear
            raw = str(item)
            assert "api_key" not in raw or item.get("connection_mode") == "api_key"  # connection_mode value is fine
            assert "omnidimension" not in raw.lower()
            assert "provider_credentials" not in raw
    finally:
        app.dependency_overrides.clear()


# ── Calls tenant isolation ────────────────────────────────────────────────────

def test_calls_list_is_tenant_scoped(t17_db, monkeypatch):
    db, tenant_a, tenant_b = t17_db
    emp_a = _make_employee(db, tenant_a)
    emp_b = _make_employee(db, tenant_b)
    call_a = Call(
        tenant_id=tenant_a.id, employee_id=emp_a.id,
        direction=CallDirection.outbound.value, status=CallStatus.completed.value,
        customer_phone_number="+15551111111",
    )
    call_b = Call(
        tenant_id=tenant_b.id, employee_id=emp_b.id,
        direction=CallDirection.outbound.value, status=CallStatus.completed.value,
        customer_phone_number="+15552222222",
    )
    db.add_all([call_a, call_b])
    db.commit()
    client_a, token_a = _auth_client(db, tenant_a, "c1@test.com", monkeypatch)
    try:
        r = client_a.get("/api/v1/calls", headers={"Authorization": f"Bearer {token_a}"})
        assert r.status_code == 200
        ids = [item["id"] for item in r.json()]
        assert str(call_a.id) in ids
        assert str(call_b.id) not in ids
    finally:
        app.dependency_overrides.clear()


def test_call_detail_cross_tenant_returns_404(t17_db, monkeypatch):
    db, tenant_a, tenant_b = t17_db
    emp_b = _make_employee(db, tenant_b)
    call_b = Call(
        tenant_id=tenant_b.id, employee_id=emp_b.id,
        direction=CallDirection.outbound.value, status=CallStatus.completed.value,
    )
    db.add(call_b)
    db.commit()
    client_a, token_a = _auth_client(db, tenant_a, "c2@test.com", monkeypatch)
    try:
        r = client_a.get(f"/api/v1/calls/{call_b.id}", headers={"Authorization": f"Bearer {token_a}"})
        assert r.status_code == 404
    finally:
        app.dependency_overrides.clear()
