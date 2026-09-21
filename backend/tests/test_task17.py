"""Tests for Task 17: settings toggle, bulk upload, integrations, calls."""
import io
import sys
import uuid
from datetime import datetime, timezone, timedelta
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
from app.api.v1.endpoints.campaign_upload import _make_import_token


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
    db, tenant_a, _ = t17_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    client, token = _auth_client(db, tenant_a, "u4@test.com", monkeypatch)
    import_token = _make_import_token(camp.id, tenant_a.id, [{"phone_number": "+15551234567", "first_name": "Alice", "last_name": "", "email": "", "customer_data": {}}])
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
    db, tenant_a, tenant_b = t17_db
    emp_a = _make_employee(db, tenant_a)
    camp_a = _make_campaign(db, tenant_a, emp_a)
    client_a, token_a = _auth_client(db, tenant_a, "u5@test.com", monkeypatch)
    # Token claims tenant_b
    import_token = _make_import_token(camp_a.id, tenant_b.id, [{"phone_number": "+15551234567"}])
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


def test_bulk_upload_confirm_token_security_and_replay(t17_db, monkeypatch):
    db, tenant_a, tenant_b = t17_db
    emp_a = _make_employee(db, tenant_a); camp_a = _make_campaign(db, tenant_a, emp_a)
    emp_b = _make_employee(db, tenant_b); camp_b = _make_campaign(db, tenant_b, emp_b)
    client_a, token_a = _auth_client(db, tenant_a, "token@test.com", monkeypatch)
    row = [{"phone_number": "+15551234567", "first_name": "A", "customer_data": {"segment": "vip"}}]
    valid = _make_import_token(camp_a.id, tenant_a.id, row)
    headers = {"Authorization": f"Bearer {token_a}"}
    try:
        ok = client_a.post(f"/api/v1/campaigns/{camp_a.id}/contacts/upload-confirm", json={"import_token": valid}, headers=headers)
        assert ok.status_code == 201 and ok.json() == {"imported": 1, "skipped_duplicates": 0}
        replay = client_a.post(f"/api/v1/campaigns/{camp_a.id}/contacts/upload-confirm", json={"import_token": valid}, headers=headers)
        assert replay.status_code == 409 and "already been applied" in replay.json()["detail"]
        assert len(db.scalars(select(CampaignContact).where(CampaignContact.campaign_id == camp_a.id)).all()) == 1
        before = len(db.scalars(select(CampaignContact)).all())
        for bad, path, expected in [("not-a-token", camp_a.id, 422), (valid[:-2] + "xx", camp_a.id, 422), (valid, camp_b.id, 422)]:
            response = client_a.post(f"/api/v1/campaigns/{path}/contacts/upload-confirm", json={"import_token": bad}, headers=headers)
            assert response.status_code == expected
        tenant_b_client, tenant_b_token = _auth_client(db, tenant_b, "other-token@test.com", monkeypatch)
        cross = tenant_b_client.post(f"/api/v1/campaigns/{camp_b.id}/contacts/upload-confirm", json={"import_token": valid}, headers={"Authorization": f"Bearer {tenant_b_token}"})
        assert cross.status_code in {403, 422}
        assert len(db.scalars(select(CampaignContact)).all()) == before
    finally:
        app.dependency_overrides.clear()


def test_bulk_upload_confirm_rejects_expired_token_without_mutation(t17_db, monkeypatch):
    db, tenant_a, _ = t17_db; emp = _make_employee(db, tenant_a); camp = _make_campaign(db, tenant_a, emp)
    client, token = _auth_client(db, tenant_a, "expired-token@test.com", monkeypatch)
    import jwt
    from app.core.config import get_settings
    expired = jwt.encode({"purpose": "campaign_import", "campaign_id": str(camp.id), "tenant_id": str(tenant_a.id), "rows": "bad", "exp": datetime.now(timezone.utc) - timedelta(minutes=1)}, get_settings().auth_secret_key, algorithm="HS256")
    try:
        response = client.post(f"/api/v1/campaigns/{camp.id}/contacts/upload-confirm", json={"import_token": expired}, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 422
        assert db.scalars(select(CampaignContact)).all() == []
    finally:
        app.dependency_overrides.clear()


def test_contact_attempt_history_is_persisted_ordered_and_redacted(t17_db, monkeypatch):
    db, tenant_a, tenant_b = t17_db; emp = _make_employee(db, tenant_a); camp = _make_campaign(db, tenant_a, emp)
    contact = CampaignContact(tenant_id=tenant_a.id, campaign_id=camp.id, phone_number="+15551234567", normalized_phone="+15551234567", callback_at=datetime.now(timezone.utc))
    db.add(contact); db.commit(); db.refresh(contact)
    first = Call(tenant_id=tenant_a.id, employee_id=emp.id, campaign_id=camp.id, campaign_contact_id=contact.id, direction=CallDirection.outbound.value, status=CallStatus.no_answer.value, outcome="no_answer", started_at=datetime(2024, 1, 1, tzinfo=timezone.utc), duration_seconds=12, provider_call_id="secret-1", recording_url="https://provider.invalid/secret")
    second = Call(tenant_id=tenant_a.id, employee_id=emp.id, campaign_id=camp.id, campaign_contact_id=contact.id, direction=CallDirection.outbound.value, status=CallStatus.completed.value, outcome="connected", started_at=datetime(2024, 1, 2, tzinfo=timezone.utc), duration_seconds=42, provider_call_id="secret-2", recording_url="https://provider.invalid/secret2")
    db.add_all([first, second]); db.commit()
    contact.last_call_id = first.id; db.commit()
    client, token = _auth_client(db, tenant_a, "history@test.com", monkeypatch)
    try:
        response = client.get(f"/api/v1/campaigns/{camp.id}/contacts/{contact.id}/attempts", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        data = response.json(); assert [item["attempt_number"] for item in data] == [2, 1]
        assert data[0]["outcome"] == "connected" and data[0]["duration_seconds"] == 42
        assert data[1]["status"] == "no_answer" and data[1]["started_at"]
        assert data[0]["callback_at"] is None and data[1]["callback_at"] is not None
        raw = str(data); assert "provider_call_id" not in raw and "recording_url" not in raw and "provider.invalid" not in raw
        empty = client.get(f"/api/v1/campaigns/{camp.id}/contacts/{uuid.uuid4()}/attempts", headers={"Authorization": f"Bearer {token}"})
        assert empty.status_code == 404
        other_campaign = _make_campaign(db, tenant_a, emp)
        denied = client.get(f"/api/v1/campaigns/{other_campaign.id}/contacts/{contact.id}/attempts", headers={"Authorization": f"Bearer {token}"})
        assert denied.status_code == 404
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
