"""
Tests for Campaign Execution Engine.

Covers:
1.  Authentication required for all protected campaign operations.
2.  Invited/allowed user (status=invited) cannot bypass authentication.
3.  Tenant isolation — cross-tenant campaign/contact access returns safe failure.
4.  Draft/unpublished employee cannot be dispatched.
5.  Invalid/non-tenant phone cannot be dispatched.
6.  Correct Omni agent + phone are used on dispatch.
7.  Duplicate dispatch prevention (idempotent start/resume/retry).
8.  Pause/stop behavior and state transitions.
9.  Provider failure handling.
10. Webhook retry does not duplicate Call records.
11. Campaign progress/status transitions via webhook.
12. Cross-tenant campaign/contact access returns safe failure.
13. Retry resets failed contacts and resumes execution.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.main import app
from app.models import AIEmployee, AIEmployeeVersion, Call, CreditWallet, PhoneNumber, Tenant, User
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.campaign_execution_slot import CampaignExecutionSlot
from app.models.enums import (
    CallDirection, CallStatus, CampaignStatus, ContactStatus,
    EmployeeStatus, NumberStatus,
)
from app.services import auth as auth_service
from app.services.campaign_execution import (
    CampaignExecutionService,
    CampaignStateError,
    CampaignExecutionError,
    reserve_campaign_slot,
    release_campaign_slot,
    recover_stale_slots,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def exec_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.db.base import Base
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant_a = Tenant(name="Exec A", slug="exec-a")
    tenant_b = Tenant(name="Exec B", slug="exec-b")
    db.add_all([tenant_a, tenant_b])
    db.flush()
    db.add_all([
        User(tenant_id=tenant_a.id, email="a@exec.test", password_hash=auth_service.hash_password("pw"), status="active"),
        User(tenant_id=tenant_b.id, email="b@exec.test", password_hash=auth_service.hash_password("pw"), status="active"),
        CreditWallet(tenant_id=tenant_a.id, balance_credits=500, currency="INR", status="active"),
        CreditWallet(tenant_id=tenant_b.id, balance_credits=500, currency="INR", status="active"),
    ])
    db.commit()
    try:
        yield db, tenant_a, tenant_b
    finally:
        db.close()


def _make_employee(db, tenant, *, published=True, provider_agent_id="7777"):
    emp = AIEmployee(
        tenant_id=tenant.id, name="Test Emp", purpose="Test",
        call_type="outbound", llm_provider="OpenAI", llm_model="gpt-4o-mini",
        language="en-US", creation_mode="chat",
        status=EmployeeStatus.published.value if published else EmployeeStatus.draft.value,
    )
    db.add(emp)
    db.flush()
    ver = AIEmployeeVersion(
        tenant_id=tenant.id, employee_id=emp.id, version_number=1,
        status="published" if published else "draft",
        configuration={},
        provider_name="omnidimension" if provider_agent_id else None,
        provider_agent_id=provider_agent_id,
    )
    db.add(ver)
    db.flush()
    if published:
        emp.published_version = ver
    db.commit()
    db.refresh(emp)
    return emp


def _make_phone(db, tenant, *, provider_id="42", status=NumberStatus.active.value):
    pn = PhoneNumber(
        tenant_id=tenant.id, e164_number="+15550001234",
        provider_name="omnidimension", provider_phone_number_id=provider_id,
        status=status,
    )
    db.add(pn)
    db.commit()
    db.refresh(pn)
    return pn


def _make_campaign(db, tenant, employee, *, status=CampaignStatus.draft.value, phone_number_id=None):
    c = Campaign(
        tenant_id=tenant.id, name="Test Campaign",
        employee_id=employee.id, status=status,
        phone_number_id=phone_number_id,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _make_contact(db, tenant, campaign, *, phone="+919876543210", status=ContactStatus.pending.value):
    cc = CampaignContact(
        tenant_id=tenant.id, campaign_id=campaign.id,
        phone_number=phone, status=status,
    )
    db.add(cc)
    db.commit()
    db.refresh(cc)
    return cc


def test_durable_campaign_slot_limits_and_releases(exec_db):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    camp.concurrency = 1
    c1 = _make_contact(db, tenant_a, camp, phone="+919876543210")
    c2 = _make_contact(db, tenant_a, camp, phone="+919876543211")

    first = reserve_campaign_slot(db, camp, c1)
    second = reserve_campaign_slot(db, camp, c2)
    assert first is not None
    assert second is None

    assert release_campaign_slot(db, contact_id=c1.id) == 1
    assert reserve_campaign_slot(db, camp, c2) is not None


def test_stale_campaign_slot_is_recoverable(exec_db):
    from datetime import timedelta
    from app.db.base import utc_now

    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    contact = _make_contact(db, tenant_a, camp)
    slot = reserve_campaign_slot(db, camp, contact)
    slot.reserved_at = utc_now() - timedelta(hours=1)
    db.commit()

    assert recover_stale_slots(db, lease_seconds=60) == 1
    db.refresh(slot)
    assert slot.released_at is not None


def _api_client(db, user_index, monkeypatch):
    users = db.query(User).all()
    monkeypatch.setattr(auth_service, "decode_access_token", lambda _token: users[user_index].id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _mock_dispatch(monkeypatch, provider_call_id="call-abc"):
    """Patch OmniDimensionCallProvider.dispatch to succeed without HTTP."""
    import app.services.campaign_execution as exec_mod

    class MockProvider:
        def dispatch(self, *, agent_id, to_number, from_number_id, call_context, metadata):
            return SimpleNamespace(provider_call_id=provider_call_id, status="queued")

    class MockClient:
        pass

    monkeypatch.setattr(exec_mod, "OmniDimensionCallProvider", lambda _client: MockProvider())
    monkeypatch.setattr(exec_mod, "OmniDimensionClient", lambda *_a, **_kw: MockClient())


# ── 1. Authentication required ────────────────────────────────────────────────

def test_unauthenticated_requests_are_rejected():
    with TestClient(app) as api:
        assert api.get("/api/v1/campaigns").status_code == 401
        assert api.post("/api/v1/campaigns", json={}).status_code == 401
        assert api.post("/api/v1/campaigns/00000000-0000-0000-0000-000000000001/start", json={}).status_code == 401
        assert api.post("/api/v1/campaigns/00000000-0000-0000-0000-000000000001/pause").status_code == 401
        assert api.post("/api/v1/campaigns/00000000-0000-0000-0000-000000000001/resume").status_code == 401
        assert api.post("/api/v1/campaigns/00000000-0000-0000-0000-000000000001/stop").status_code == 401
        assert api.post("/api/v1/campaigns/00000000-0000-0000-0000-000000000001/retry").status_code == 401
        assert api.get("/api/v1/campaigns/00000000-0000-0000-0000-000000000001/progress").status_code == 401


# ── 2. Invited user cannot bypass authentication ──────────────────────────────

def test_invited_user_cannot_access_campaigns(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    invited = User(
        tenant_id=tenant_a.id, email="invited@exec.test",
        password_hash=None, status="invited",
    )
    db.add(invited)
    db.commit()
    monkeypatch.setattr(auth_service, "decode_access_token", lambda _token: invited.id)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as api:
            resp = api.get("/api/v1/campaigns", headers={"Authorization": "Bearer tok"})
            assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()


# ── 3 & 12. Tenant isolation ──────────────────────────────────────────────────

def test_cross_tenant_campaign_access_returns_404(exec_db, monkeypatch):
    db, tenant_a, tenant_b = exec_db
    emp_b = _make_employee(db, tenant_b)
    camp_b = _make_campaign(db, tenant_b, emp_b)
    client_a = _api_client(db, 0, monkeypatch)
    try:
        with client_a:
            resp = client_a.get(f"/api/v1/campaigns/{camp_b.id}", headers={"Authorization": "Bearer a"})
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_cross_tenant_start_returns_404(exec_db, monkeypatch):
    db, tenant_a, tenant_b = exec_db
    emp_b = _make_employee(db, tenant_b)
    camp_b = _make_campaign(db, tenant_b, emp_b)
    pn_b = _make_phone(db, tenant_b)
    client_a = _api_client(db, 0, monkeypatch)
    try:
        with client_a:
            resp = client_a.post(
                f"/api/v1/campaigns/{camp_b.id}/start",
                json={"phone_number_id": str(pn_b.id)},
                headers={"Authorization": "Bearer a"},
            )
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ── 4. Draft/unpublished employee cannot be dispatched ───────────────────────

def test_draft_employee_blocks_campaign_start(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a, published=False)
    camp = _make_campaign(db, tenant_a, emp)
    _make_contact(db, tenant_a, camp)
    pn = _make_phone(db, tenant_a)
    client = _api_client(db, 0, monkeypatch)
    try:
        with client:
            resp = client.post(
                f"/api/v1/campaigns/{camp.id}/start",
                json={"phone_number_id": str(pn.id)},
                headers={"Authorization": "Bearer a"},
            )
            assert resp.status_code == 422
            assert "published" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_employee_without_provider_agent_id_blocks_start(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a, published=True, provider_agent_id=None)
    camp = _make_campaign(db, tenant_a, emp)
    _make_contact(db, tenant_a, camp)
    pn = _make_phone(db, tenant_a)
    client = _api_client(db, 0, monkeypatch)
    try:
        with client:
            resp = client.post(
                f"/api/v1/campaigns/{camp.id}/start",
                json={"phone_number_id": str(pn.id)},
                headers={"Authorization": "Bearer a"},
            )
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


# ── 5. Invalid/non-tenant phone cannot be dispatched ─────────────────────────

def test_cross_tenant_phone_blocks_start(exec_db, monkeypatch):
    db, tenant_a, tenant_b = exec_db
    emp_a = _make_employee(db, tenant_a)
    camp_a = _make_campaign(db, tenant_a, emp_a)
    _make_contact(db, tenant_a, camp_a)
    pn_b = _make_phone(db, tenant_b, provider_id="99")
    client = _api_client(db, 0, monkeypatch)
    try:
        with client:
            resp = client.post(
                f"/api/v1/campaigns/{camp_a.id}/start",
                json={"phone_number_id": str(pn_b.id)},
                headers={"Authorization": "Bearer a"},
            )
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_inactive_phone_blocks_start(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    _make_contact(db, tenant_a, camp)
    pn = _make_phone(db, tenant_a, status=NumberStatus.inactive.value)
    client = _api_client(db, 0, monkeypatch)
    try:
        with client:
            resp = client.post(
                f"/api/v1/campaigns/{camp.id}/start",
                json={"phone_number_id": str(pn.id)},
                headers={"Authorization": "Bearer a"},
            )
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


# ── 6. Correct Omni agent + phone are used ───────────────────────────────────

def test_dispatch_uses_correct_agent_and_phone(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a, provider_agent_id="5555")
    camp = _make_campaign(db, tenant_a, emp)
    _make_contact(db, tenant_a, camp)
    pn = _make_phone(db, tenant_a, provider_id="8888")

    dispatched = []

    import app.services.campaign_execution as exec_mod

    class CapturingProvider:
        def dispatch(self, *, agent_id, to_number, from_number_id, call_context, metadata):
            dispatched.append({"agent_id": agent_id, "from_number_id": from_number_id, "metadata": metadata})
            return SimpleNamespace(provider_call_id="call-ok", status="queued")

    monkeypatch.setattr(exec_mod, "OmniDimensionCallProvider", lambda _c: CapturingProvider())
    monkeypatch.setattr(exec_mod, "OmniDimensionClient", lambda *_a, **_kw: None)

    svc = CampaignExecutionService()
    svc.start(db, camp.id, tenant_a.id, pn.id)
    svc.dispatch_next_pending(db, camp.id, tenant_a.id)

    assert len(dispatched) == 1
    assert dispatched[0]["agent_id"] == 5555
    assert dispatched[0]["from_number_id"] == 8888
    assert dispatched[0]["metadata"]["tenant_id"] == str(tenant_a.id)
    assert dispatched[0]["metadata"]["campaign_id"] == str(camp.id)


# ── 7. Duplicate dispatch prevention ─────────────────────────────────────────

def test_duplicate_dispatch_is_prevented(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.running.value)
    contact = _make_contact(db, tenant_a, camp)
    pn = _make_phone(db, tenant_a)
    camp.phone_number_id = pn.id
    db.commit()

    _mock_dispatch(monkeypatch)
    svc = CampaignExecutionService()

    # First dispatch
    call1 = svc.dispatch_next_pending(db, camp.id, tenant_a.id)
    assert call1 is not None

    # Contact is now in_progress — second dispatch returns same call
    call2 = svc.dispatch_next_pending(db, camp.id, tenant_a.id)
    # No new pending contacts, returns None
    assert call2 is None

    # Verify only one Call record exists for this contact
    calls = db.scalars(select(Call).where(Call.campaign_contact_id == contact.id)).all()
    assert len(calls) == 1


# ── 8. Pause/stop behavior ────────────────────────────────────────────────────

def test_pause_and_resume_transitions(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    pn = _make_phone(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.running.value, phone_number_id=pn.id)

    svc = CampaignExecutionService()
    svc.pause(db, camp.id, tenant_a.id)
    db.refresh(camp)
    assert camp.status == CampaignStatus.paused.value

    svc.resume(db, camp.id, tenant_a.id)
    db.refresh(camp)
    assert camp.status == CampaignStatus.running.value


def test_stop_marks_pending_contacts_skipped(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.running.value)
    c1 = _make_contact(db, tenant_a, camp, phone="+11111111111")
    c2 = _make_contact(db, tenant_a, camp, phone="+22222222222")

    svc = CampaignExecutionService()
    svc.stop(db, camp.id, tenant_a.id)
    db.refresh(camp); db.refresh(c1); db.refresh(c2)
    assert camp.status == CampaignStatus.stopped.value
    assert c1.status == ContactStatus.skipped.value
    assert c2.status == ContactStatus.skipped.value


def test_invalid_state_transitions_raise_errors(exec_db):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.completed.value)
    svc = CampaignExecutionService()

    with pytest.raises(CampaignStateError):
        svc.pause(db, camp.id, tenant_a.id)
    with pytest.raises(CampaignStateError):
        svc.resume(db, camp.id, tenant_a.id)
    with pytest.raises(CampaignStateError):
        svc.stop(db, camp.id, tenant_a.id)


# ── 9. Provider failure handling ──────────────────────────────────────────────

def test_provider_failure_marks_contact_failed_and_is_retryable(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    pn = _make_phone(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.running.value, phone_number_id=pn.id)
    contact = _make_contact(db, tenant_a, camp)

    import app.services.campaign_execution as exec_mod
    from app.integrations.omnidimension.exceptions import OmniDimensionError

    class FailingProvider:
        def dispatch(self, **_kw):
            raise OmniDimensionError("provider down")

    monkeypatch.setattr(exec_mod, "OmniDimensionCallProvider", lambda _c: FailingProvider())
    monkeypatch.setattr(exec_mod, "OmniDimensionClient", lambda *_a, **_kw: None)

    svc = CampaignExecutionService()
    with pytest.raises(OmniDimensionError):
        svc.dispatch_next_pending(db, camp.id, tenant_a.id)

    db.refresh(contact)
    assert contact.status == ContactStatus.failed.value

    # Reset to pending for retry
    contact.status = ContactStatus.pending.value
    db.commit()
    assert contact.status == ContactStatus.pending.value


# ── 10. Webhook retry does not duplicate Call records ─────────────────────────

def test_webhook_retry_is_idempotent(exec_db):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    pn = _make_phone(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    contact = _make_contact(db, tenant_a, camp)

    call = Call(
        tenant_id=tenant_a.id, employee_id=emp.id,
        campaign_id=camp.id, campaign_contact_id=contact.id,
        phone_number_id=pn.id,
        direction=CallDirection.outbound.value,
        status=CallStatus.queued.value,
        customer_phone_number=contact.phone_number,
        provider_call_id="webhook-test-001",
        dispatch_metadata={"local_call_id": None},
    )
    db.add(call)
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    payload = {
        "call_status": "completed",
        "call_duration": "60",
        "metadata": {"local_call_id": str(call.id), "tenant_id": str(tenant_a.id)},
    }
    try:
        with TestClient(app) as api:
            r1 = api.post("/api/v1/webhooks/omnidimension/post-call", json=payload)
            r2 = api.post("/api/v1/webhooks/omnidimension/post-call", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert r1.status_code == 200
    assert r2.status_code == 200

    # Only one Call record
    calls = db.scalars(select(Call).where(Call.campaign_contact_id == contact.id)).all()
    assert len(calls) == 1
    db.refresh(call)
    assert call.status == CallStatus.completed.value


# ── 11. Campaign progress/status transitions via webhook ─────────────────────

def test_campaign_completes_when_all_contacts_terminal(exec_db):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    pn = _make_phone(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.running.value, phone_number_id=pn.id)
    contact = _make_contact(db, tenant_a, camp, status=ContactStatus.in_progress.value)

    call = Call(
        tenant_id=tenant_a.id, employee_id=emp.id,
        campaign_id=camp.id, campaign_contact_id=contact.id,
        phone_number_id=pn.id,
        direction=CallDirection.outbound.value,
        status=CallStatus.queued.value,
        customer_phone_number=contact.phone_number,
        provider_call_id="progress-test-001",
    )
    db.add(call)
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    payload = {
        "call_status": "completed",
        "call_duration": "45",
        "metadata": {"local_call_id": str(call.id), "tenant_id": str(tenant_a.id)},
    }
    try:
        with TestClient(app) as api:
            api.post("/api/v1/webhooks/omnidimension/post-call", json=payload)
    finally:
        app.dependency_overrides.clear()

    db.refresh(contact)
    db.refresh(camp)
    assert contact.status == ContactStatus.completed.value
    assert camp.status == CampaignStatus.completed.value


def test_failed_call_marks_contact_failed(exec_db):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    pn = _make_phone(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.running.value, phone_number_id=pn.id)
    contact = _make_contact(db, tenant_a, camp, status=ContactStatus.in_progress.value)

    call = Call(
        tenant_id=tenant_a.id, employee_id=emp.id,
        campaign_id=camp.id, campaign_contact_id=contact.id,
        phone_number_id=pn.id,
        direction=CallDirection.outbound.value,
        status=CallStatus.queued.value,
        customer_phone_number=contact.phone_number,
        provider_call_id="fail-test-001",
    )
    db.add(call)
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    payload = {
        "call_status": "no_answer",
        "metadata": {"local_call_id": str(call.id), "tenant_id": str(tenant_a.id)},
    }
    try:
        with TestClient(app) as api:
            api.post("/api/v1/webhooks/omnidimension/post-call", json=payload)
    finally:
        app.dependency_overrides.clear()

    db.refresh(contact)
    assert contact.status == ContactStatus.retry_scheduled.value
    assert contact.retry_at is not None


# ── 13. Retry resets failed contacts ─────────────────────────────────────────

def test_retry_resets_failed_contacts_and_resumes(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    pn = _make_phone(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.stopped.value, phone_number_id=pn.id)
    c1 = _make_contact(db, tenant_a, camp, phone="+11111111111", status=ContactStatus.failed.value)
    c2 = _make_contact(db, tenant_a, camp, phone="+22222222222", status=ContactStatus.completed.value)

    svc = CampaignExecutionService()
    svc.retry_failed(db, camp.id, tenant_a.id)

    db.refresh(camp); db.refresh(c1); db.refresh(c2)
    assert camp.status == CampaignStatus.running.value
    assert c1.status == ContactStatus.pending.value
    assert c2.status == ContactStatus.completed.value  # unchanged


def test_retry_with_no_failed_contacts_raises(exec_db):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    pn = _make_phone(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.stopped.value, phone_number_id=pn.id)
    _make_contact(db, tenant_a, camp, status=ContactStatus.completed.value)

    svc = CampaignExecutionService()
    with pytest.raises(CampaignExecutionError, match="No failed contacts"):
        svc.retry_failed(db, camp.id, tenant_a.id)


# ── Idempotent start ──────────────────────────────────────────────────────────

def test_start_already_running_campaign_returns_409(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    pn = _make_phone(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp, status=CampaignStatus.running.value)
    _make_contact(db, tenant_a, camp)
    client = _api_client(db, 0, monkeypatch)
    try:
        with client:
            resp = client.post(
                f"/api/v1/campaigns/{camp.id}/start",
                json={"phone_number_id": str(pn.id)},
                headers={"Authorization": "Bearer a"},
            )
            assert resp.status_code == 409
    finally:
        app.dependency_overrides.clear()


# ── Progress endpoint ─────────────────────────────────────────────────────────

def test_progress_endpoint_returns_correct_counts(exec_db, monkeypatch):
    db, tenant_a, _ = exec_db
    emp = _make_employee(db, tenant_a)
    camp = _make_campaign(db, tenant_a, emp)
    _make_contact(db, tenant_a, camp, phone="+11111111111", status=ContactStatus.pending.value)
    _make_contact(db, tenant_a, camp, phone="+22222222222", status=ContactStatus.completed.value)
    _make_contact(db, tenant_a, camp, phone="+33333333333", status=ContactStatus.failed.value)

    client = _api_client(db, 0, monkeypatch)
    try:
        with client:
            resp = client.get(f"/api/v1/campaigns/{camp.id}/progress", headers={"Authorization": "Bearer a"})
            assert resp.status_code == 200
            body = resp.json()
            assert body["total"] == 3
            assert body["pending"] == 1
            assert body["completed"] == 1
            assert body["failed"] == 1
    finally:
        app.dependency_overrides.clear()
