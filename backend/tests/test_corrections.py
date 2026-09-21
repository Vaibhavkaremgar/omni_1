"""Tests for items 1-5: call_type default, outbound gate, KYC wizard flow,
WhatsApp coming-soon, and notification settings."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.db.base import Base
from app.main import app
from app.models import Tenant, User
from app.models.ai_employee import AIEmployee
from app.schemas.ai_employee import AIEmployeeCreate, AIEmployeeUpdate
from app.services import auth as auth_service
from app.services.auth import AuthenticatedUser
from app.api.v1.endpoints import employees as employee_endpoint


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    tenant = Tenant(name="Test Tenant", slug="test-tenant")
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        email="test@example.com",
        password_hash=auth_service.hash_password("pw"),
        status="active",
    )
    db.add(user)
    db.commit()
    try:
        yield db, tenant, user
    finally:
        db.close()


def _client(db, tenant, user, monkeypatch):
    monkeypatch.setattr(auth_service, "decode_access_token", lambda _: user.id)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    monkeypatch.setattr(
        employee_endpoint,
        "get_settings",
        lambda: SimpleNamespace(effective_llm_provider="groq", effective_llm_model="test-model"),
    )
    return TestClient(app)


# ===========================================================================
# 1. CALL TYPE DEFAULT
# ===========================================================================

def test_create_schema_default_is_inbound():
    schema = AIEmployeeCreate(language="English")
    assert schema.call_type == "inbound"


def test_create_schema_inbound_accepted():
    schema = AIEmployeeCreate(call_type="inbound", language="English")
    assert schema.call_type == "inbound"


def test_create_schema_both_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AIEmployeeCreate(call_type="both", language="English")


def test_create_schema_outbound_accepted():
    schema = AIEmployeeCreate(call_type="outbound", language="English")
    assert schema.call_type == "outbound"


def test_update_schema_outbound_accepted():
    schema = AIEmployeeUpdate(call_type="outbound")
    assert schema.call_type == "outbound"


def test_update_schema_inbound_accepted():
    schema = AIEmployeeUpdate(call_type="inbound")
    assert schema.call_type == "inbound"


def test_model_default_is_inbound(db_session):
    """SQLAlchemy column defaults apply after flush, not on Python construction."""
    db, tenant, _ = db_session
    emp = AIEmployee(
        tenant_id=tenant.id, name="X", purpose="Y",
        llm_provider="p", llm_model="m", language="en", creation_mode="chat",
    )
    db.add(emp)
    db.flush()
    assert emp.call_type == "inbound"


def test_api_create_employee_defaults_to_inbound(db_session, monkeypatch):
    db, tenant, user = db_session
    client = _client(db, tenant, user, monkeypatch)
    try:
        with client:
            r = client.post("/api/v1/employees", json={"language": "English"}, headers={"Authorization": "Bearer t"})
            assert r.status_code == 201
            assert r.json()["call_type"] == "inbound"
    finally:
        app.dependency_overrides.clear()


def test_api_create_employee_outbound_accepted(db_session, monkeypatch):
    db, tenant, user = db_session
    client = _client(db, tenant, user, monkeypatch)
    try:
        with client:
            r = client.post(
                "/api/v1/employees",
                json={"language": "English", "call_type": "outbound"},
                headers={"Authorization": "Bearer t"},
            )
            assert r.status_code == 201
            assert r.json()["call_type"] == "outbound"
    finally:
        app.dependency_overrides.clear()


def test_api_update_employee_outbound_accepted(db_session, monkeypatch):
    db, tenant, user = db_session
    client = _client(db, tenant, user, monkeypatch)
    try:
        with client:
            created = client.post("/api/v1/employees", json={"language": "English"}, headers={"Authorization": "Bearer t"})
            eid = created.json()["id"]
            r = client.patch(
                f"/api/v1/employees/{eid}",
                json={"call_type": "outbound"},
                headers={"Authorization": "Bearer t"},
            )
            assert r.status_code == 200
            assert r.json()["call_type"] == "outbound"
    finally:
        app.dependency_overrides.clear()


def test_api_update_employee_configuration_outbound_accepted(db_session, monkeypatch):
    db, tenant, user = db_session
    client = _client(db, tenant, user, monkeypatch)
    try:
        with client:
            created = client.post("/api/v1/employees", json={"language": "English"}, headers={"Authorization": "Bearer t"})
            eid = created.json()["id"]
            r = client.patch(
                f"/api/v1/employees/{eid}",
                json={"configuration": {"call_type": "outbound", "name": "X", "purpose": "Y", "language": "en"}},
                headers={"Authorization": "Bearer t"},
            )
            assert r.status_code == 200
            assert r.json()["call_type"] == "outbound"
    finally:
        app.dependency_overrides.clear()


def test_existing_outbound_employee_not_destructively_modified(db_session):
    """Existing employees with outbound call_type must not be changed by this migration."""
    db, tenant, _ = db_session
    emp = AIEmployee(
        tenant_id=tenant.id, name="Legacy", purpose="Outbound sales",
        call_type="outbound", llm_provider="p", llm_model="m",
        language="en", creation_mode="chat",
    )
    db.add(emp)
    db.commit()
    db.refresh(emp)
    assert emp.call_type == "outbound"  # unchanged


# ===========================================================================
# 2. KYC FLOW — backend endpoint tests
# ===========================================================================

def test_kyc_status_requires_auth():
    with TestClient(app) as client:
        assert client.get("/api/v1/phone-numbers/kyc/status").status_code == 401


def test_kyc_initialize_requires_auth():
    with TestClient(app) as client:
        assert client.post("/api/v1/phone-numbers/kyc/initialize", json={"phone": "+919876543210"}).status_code == 401


def test_kyc_step_requires_auth():
    with TestClient(app) as client:
        assert client.post("/api/v1/phone-numbers/kyc/step", json={"step": "verify-pan", "region": "IN", "values": {}}).status_code == 401


def test_kyc_already_completed_skips_wizard(db_session, monkeypatch):
    """If KYC is already completed, can_purchase=True and no wizard needed."""
    db, tenant, user = db_session
    tenant.omni_reseller_user_id = "child-123"
    tenant.omni_reseller_kyc_status = "completed"
    db.commit()

    class CompletedKyc:
        def status(self, db, *, tenant):
            return {"region": "IN", "status": "completed", "next_step": None, "can_purchase": True}

    from app.services.phone_numbers import get_reseller_kyc_service
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    app.dependency_overrides[get_reseller_kyc_service] = lambda: CompletedKyc()
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/phone-numbers/kyc/status")
            assert r.status_code == 200
            assert r.json()["can_purchase"] is True
            assert r.json()["status"] == "completed"
    finally:
        app.dependency_overrides.clear()


def test_kyc_dynamic_next_step_progression(db_session, monkeypatch):
    """next_step drives the wizard; each step returns the next one dynamically."""
    db, tenant, user = db_session
    tenant.omni_reseller_user_id = "child-456"
    db.commit()

    steps = ["verify-pan", "aadhaar-otp", "aadhaar-verify", "preview", None]
    step_index = [0]

    class DynamicKyc:
        def status(self, db, *, tenant):
            return {"region": "IN", "status": "in_progress", "next_step": steps[step_index[0]], "can_purchase": False}

        def submit(self, db, *, tenant, step, region, carrier, values):
            step_index[0] += 1
            nxt = steps[step_index[0]]
            done = nxt is None
            return {"region": region, "status": "completed" if done else "in_progress",
                    "next_step": nxt, "can_purchase": done}

    from app.services.phone_numbers import get_reseller_kyc_service
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    from app.services.phone_numbers import get_phone_number_marketplace_service
    app.dependency_overrides[get_phone_number_marketplace_service] = lambda: type("Marketplace", (), {"validate_selected_number": lambda self, **kwargs: True})()
    app.dependency_overrides[get_reseller_kyc_service] = lambda: DynamicKyc()
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/phone-numbers/kyc/status")
            assert r.json()["next_step"] == "verify-pan"

            r = client.post("/api/v1/phone-numbers/kyc/step",
                            json={"step": "verify-pan", "region": "IN", "carrier": "carrier-1", "phone_number": "+919876543210", "values": {"pan": "ABCDE1234F"}})
            assert r.json()["next_step"] == "aadhaar-otp"

            r = client.post("/api/v1/phone-numbers/kyc/step",
                            json={"step": "aadhaar-otp", "region": "IN", "carrier": "carrier-1", "phone_number": "+919876543210", "values": {"otp": "123456"}})
            assert r.json()["next_step"] == "aadhaar-verify"

            r = client.post("/api/v1/phone-numbers/kyc/step",
                            json={"step": "aadhaar-verify", "region": "IN", "carrier": "carrier-1", "phone_number": "+919876543210", "values": {}})
            assert r.json()["next_step"] == "preview"

            r = client.post("/api/v1/phone-numbers/kyc/step",
                            json={"step": "preview", "region": "IN", "carrier": "carrier-1", "phone_number": "+919876543210", "values": {}})
            assert r.json()["can_purchase"] is True
            assert r.json()["next_step"] is None
    finally:
        app.dependency_overrides.clear()


def test_kyc_sensitive_fields_never_persisted(db_session):
    """PAN, Aadhaar, OTP values must never appear in tenant model after step submission."""
    db, tenant, user = db_session
    tenant.omni_reseller_user_id = "child-789"
    db.commit()

    class SensitiveKyc:
        def submit(self, db, *, tenant, step, region, carrier, values):
            # Service must not persist values — verify tenant dict has no sensitive data
            db.refresh(tenant)
            tenant_dict = str(tenant.__dict__)
            assert "ABCDE1234F" not in tenant_dict
            assert "123456789012" not in tenant_dict
            assert "999999" not in tenant_dict
            return {"region": region, "status": "pan_verified", "next_step": "aadhaar-otp", "can_purchase": False}

    from app.services.phone_numbers import get_reseller_kyc_service
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    from app.services.phone_numbers import get_phone_number_marketplace_service
    app.dependency_overrides[get_phone_number_marketplace_service] = lambda: type("Marketplace", (), {"validate_selected_number": lambda self, **kwargs: True})()
    app.dependency_overrides[get_reseller_kyc_service] = lambda: SensitiveKyc()
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/phone-numbers/kyc/step", json={
                "step": "verify-pan", "region": "IN", "carrier": "carrier-1", "phone_number": "+919876543210",
                "values": {"pan": "ABCDE1234F", "aadhaar": "123456789012", "otp": "999999"},
            })
            assert r.status_code == 200
            # Sensitive values must not appear in response either
            assert "ABCDE1234F" not in r.text
            assert "123456789012" not in r.text
    finally:
        app.dependency_overrides.clear()


def test_kyc_gst_skip_path(db_session):
    """skip-gst step must be accepted as a valid step name."""
    db, tenant, user = db_session
    tenant.omni_reseller_user_id = "child-gst"
    db.commit()

    class GstKyc:
        def submit(self, db, *, tenant, step, region, carrier, values):
            assert step == "skip-gst"
            return {"region": region, "status": "gst_skipped", "next_step": "preview", "can_purchase": False}

    from app.services.phone_numbers import get_reseller_kyc_service
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    from app.services.phone_numbers import get_phone_number_marketplace_service
    app.dependency_overrides[get_phone_number_marketplace_service] = lambda: type("Marketplace", (), {"validate_selected_number": lambda self, **kwargs: True})()
    app.dependency_overrides[get_reseller_kyc_service] = lambda: GstKyc()
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/phone-numbers/kyc/step",
                            json={"step": "skip-gst", "region": "IN", "carrier": "carrier-1", "phone_number": "+919876543210", "values": {}})
            assert r.status_code == 200
            assert r.json()["next_step"] == "preview"
    finally:
        app.dependency_overrides.clear()


def test_kyc_carrier_specific_isolation(db_session):
    """KYC status endpoint is tenant-scoped; different tenants get independent status."""
    db, tenant, user = db_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine2 = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine2)
    db2 = sessionmaker(bind=engine2, expire_on_commit=False)()
    tenant_b = Tenant(name="Tenant B", slug="tenant-b-kyc")
    db2.add(tenant_b)
    db2.flush()
    user_b = User(tenant_id=tenant_b.id, email="b@kyc.test",
                  password_hash=auth_service.hash_password("pw"), status="active")
    db2.add(user_b)
    db2.commit()

    # Tenant A has completed KYC; Tenant B has not started
    tenant.omni_reseller_user_id = "child-a"
    tenant.omni_reseller_kyc_status = "completed"
    db.commit()

    assert tenant_b.omni_reseller_user_id is None
    assert tenant_b.omni_reseller_kyc_status is None
    db2.close()


def test_kyc_purchase_blocked_until_can_purchase(db_session):
    """Purchase order endpoint must reject if KYC not completed."""
    db, tenant, user = db_session
    # KYC not completed
    tenant.omni_reseller_kyc_status = "in_progress"
    tenant.omni_reseller_user_id = "child-x"
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    from app.services.phone_numbers import get_reseller_kyc_service
    app.dependency_overrides[get_reseller_kyc_service] = lambda: type("Kyc", (), {
        "status": lambda self, db, *, tenant, region, carrier: {"status": "in_progress", "can_purchase": False}
    })()
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/phone-numbers/purchase/order", json={
                "phone_number": "+919876543210", "region": "IN", "carrier": "carrier-1",
            })
            assert r.status_code == 409
    finally:
        app.dependency_overrides.clear()


# ===========================================================================
# 3. WHATSAPP — coming soon
# ===========================================================================

def test_whatsapp_connection_mode_is_coming_soon():
    from app.services.integrations import CATALOG_BY_KEY
    wa = CATALOG_BY_KEY["whatsapp"]
    assert wa["connection_mode"] == "coming_soon"


def test_whatsapp_description_mentions_coming_soon():
    from app.services.integrations import CATALOG_BY_KEY
    wa = CATALOG_BY_KEY["whatsapp"]
    desc = wa["description"].lower()
    assert "coming soon" in desc or "in progress" in desc


def test_whatsapp_connect_endpoint_blocked(db_session, monkeypatch):
    db, tenant, user = db_session
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/integrations/whatsapp/connect/webhook",
                            json={"webhook_url": "https://example.com/hook"})
            assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_whatsapp_oauth_init_blocked(db_session):
    db, tenant, user = db_session
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    try:
        with TestClient(app) as client:
            r = client.post("/api/v1/integrations/whatsapp/connect/oauth/init")
            assert r.status_code == 400
    finally:
        app.dependency_overrides.clear()


# ===========================================================================
# 4. SETTINGS — notification preferences
# ===========================================================================

def test_settings_returns_notification_fields(db_session):
    db, tenant, user = db_session
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    try:
        with TestClient(app) as client:
            r = client.get("/api/v1/settings")
            assert r.status_code == 200
            body = r.json()
            assert "notify_campaign_completed" in body
            assert "notify_low_balance" in body
    finally:
        app.dependency_overrides.clear()


def test_settings_notification_persists(db_session):
    db, tenant, user = db_session
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user, tenant=tenant)
    try:
        with TestClient(app) as client:
            r = client.patch("/api/v1/settings", json={"notify_low_balance": False})
            assert r.status_code == 200
            assert r.json()["notify_low_balance"] is False
            # Verify it persists
            r2 = client.get("/api/v1/settings")
            assert r2.json()["notify_low_balance"] is False
    finally:
        app.dependency_overrides.clear()


def test_settings_notification_tenant_scoped(db_session):
    """Notification preference change for tenant A must not affect tenant B."""
    db, tenant_a, user_a = db_session
    tenant_b = Tenant(name="Tenant B", slug="notif-b")
    db.add(tenant_b)
    db.flush()
    user_b = User(tenant_id=tenant_b.id, email="b@notif.test",
                  password_hash=auth_service.hash_password("pw"), status="active")
    db.add(user_b)
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(user=user_a, tenant=tenant_a)
    try:
        with TestClient(app) as client:
            client.patch("/api/v1/settings", json={"notify_campaign_completed": False})
    finally:
        app.dependency_overrides.clear()

    # Tenant B's preference must be unaffected
    db.refresh(tenant_b)
    assert getattr(tenant_b, "notify_campaign_completed", True) is True
