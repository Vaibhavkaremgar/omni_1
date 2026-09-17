"""
Tests for Task 16: Employee → Campaign → OmniDimension agent behavior.

Covers:
1.  Create employee → build → draft exists locally.
2.  Publish employee → exactly one Omni agent is created.
3.  Publish same employee again → existing Omni agent is updated, not duplicated.
4.  Campaign can select a published employee.
5.  Campaign cannot select another tenant's employee.
6.  Campaign call resolves the selected employee's omni_agent_id.
7.  Campaign call does not use a generic/default assistant when an employee is selected.
8.  Employee rules/context are present in the deployed Omni agent configuration.
9.  Editing an employee without publishing does not alter the live Omni agent.
10. Publishing edited employee updates the existing Omni agent.
11. Missing/unready omni_agent_id prevents the campaign from becoming runnable.
12. Provider failure leaves a retryable state.
13. Retry does not create duplicate Omni agents.
14. Phone-number/agent relationships remain tenant-safe.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.v1.endpoints import employees as employee_endpoint
from app.api.v1.endpoints import campaigns as campaign_endpoint
from app.integrations.omnidimension import OmniDimensionAgentProvider, OmniDimensionClient
from app.main import app
from app.models import AIEmployee, AIEmployeeVersion, Tenant, User
from app.models.campaign import Campaign
from app.models.campaign_contact import CampaignContact
from app.models.enums import CampaignStatus, ContactStatus, EmployeeStatus, NumberStatus
from app.models.phone_number import PhoneNumber
from app.services import auth as auth_service
from app.services.omnidimension_agents import OmniDimensionAgentService, map_employee_configuration


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def campaign_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.db.base import Base
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant_a = Tenant(name="Campaign A", slug="campaign-a")
    tenant_b = Tenant(name="Campaign B", slug="campaign-b")
    db.add_all([tenant_a, tenant_b])
    db.flush()
    db.add_all([
        User(tenant_id=tenant_a.id, password_hash=auth_service.hash_password("pw"), email="a@camp.test", status="active"),
        User(tenant_id=tenant_b.id, password_hash=auth_service.hash_password("pw"), email="b@camp.test", status="active"),
    ])
    db.commit()
    try:
        yield db, tenant_a, tenant_b
    finally:
        db.close()


def _client(db, user_index, monkeypatch):
    monkeypatch.setattr(auth_service, "decode_access_token", lambda token: db.query(User).all()[user_index].id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def _employee_payload(name="Sales Qualifier", purpose="Qualify dental leads and book appointments."):
    return {
        "name": name,
        "purpose": purpose,
        "call_type": "inbound",
        "llm_provider": "OpenAI",
        "llm_model": "gpt-4o-mini",
        "language": "English",
        "creation_mode": "chat",
    }


def _mock_agent_service(monkeypatch, responses: list[dict]):
    """Install a mock agent service that returns responses in sequence."""
    calls: list[dict] = []
    idx = {"n": 0}

    class MockProvider:
        def create_agent(self, payload):
            calls.append({"method": "POST", "payload": payload})
            r = responses[idx["n"] % len(responses)]
            idx["n"] += 1
            return SimpleNamespace(provider_id=str(r["id"]), status=r.get("status", "Completed"), metadata={})

        def update_agent(self, provider_id, payload):
            calls.append({"method": "PUT", "provider_id": provider_id, "payload": payload})
            r = responses[idx["n"] % len(responses)]
            idx["n"] += 1
            return SimpleNamespace(provider_id=str(r["id"]), status=r.get("status", "Completed"), metadata={})

    svc = OmniDimensionAgentService(MockProvider())
    monkeypatch.setattr(employee_endpoint, "agent_service", svc)
    return calls


def _add_phone_number(db, tenant_id, provider_id="42") -> PhoneNumber:
    pn = PhoneNumber(
        tenant_id=tenant_id,
        e164_number="+15550001234",
        provider_name="omnidimension",
        provider_phone_number_id=provider_id,
        status=NumberStatus.active.value,
    )
    db.add(pn)
    db.commit()
    db.refresh(pn)
    return pn


# ── Test 1: Create → draft exists locally ─────────────────────────────────────

def test_create_employee_produces_local_draft(campaign_db, monkeypatch):
    db, _, _ = campaign_db
    client = _client(db, 0, monkeypatch)
    try:
        with client:
            resp = client.post("/api/v1/employees", json=_employee_payload(), headers={"Authorization": "Bearer a"})
            assert resp.status_code == 201
            assert resp.json()["status"] == "draft"
            assert resp.json()["provider_agent_id"] is None
    finally:
        app.dependency_overrides.clear()


# ── Test 2: Publish → exactly one Omni agent created ─────────────────────────

def test_publish_creates_exactly_one_omni_agent(campaign_db, monkeypatch):
    db, _, _ = campaign_db
    calls = _mock_agent_service(monkeypatch, [{"id": 1001}])
    client = _client(db, 0, monkeypatch)
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            resp = client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            assert resp.status_code == 200
            assert resp.json()["provider_agent_id"] == "1001"
            assert len(calls) == 1
            assert calls[0]["method"] == "POST"
    finally:
        app.dependency_overrides.clear()


# ── Test 3: Re-publish → update, not duplicate ────────────────────────────────

def test_republish_updates_existing_agent_not_duplicate(campaign_db, monkeypatch):
    db, _, _ = campaign_db
    calls = _mock_agent_service(monkeypatch, [{"id": 1002}])
    client = _client(db, 0, monkeypatch)
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            assert len(calls) == 2
            assert calls[0]["method"] == "POST"
            assert calls[1]["method"] == "PUT"
            assert calls[1]["provider_id"] == "1002"
    finally:
        app.dependency_overrides.clear()


# ── Test 4: Campaign can select a published employee ─────────────────────────

def test_campaign_can_select_published_employee(campaign_db, monkeypatch):
    db, _, _ = campaign_db
    _mock_agent_service(monkeypatch, [{"id": 2001}])
    client = _client(db, 0, monkeypatch)
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            resp = client.post("/api/v1/campaigns", json={"name": "Dental Q4", "employee_id": eid}, headers=h)
            assert resp.status_code == 201
            body = resp.json()
            assert body["employee_id"] == eid
            assert body["employee"]["is_ready"] is True
            assert body["employee"]["name"] == "Sales Qualifier"
    finally:
        app.dependency_overrides.clear()


# ── Test 5: Campaign cannot select another tenant's employee ──────────────────

def test_campaign_cannot_select_cross_tenant_employee(campaign_db, monkeypatch):
    db, _, _ = campaign_db
    _mock_agent_service(monkeypatch, [{"id": 3001}])
    client_a = _client(db, 0, monkeypatch)
    try:
        with client_a:
            eid = client_a.post("/api/v1/employees", json=_employee_payload(), headers={"Authorization": "Bearer a"}).json()["id"]
            client_a.post(f"/api/v1/employees/{eid}/publish", headers={"Authorization": "Bearer a"})
    finally:
        app.dependency_overrides.clear()

    client_b = _client(db, 1, monkeypatch)
    try:
        with client_b:
            resp = client_b.post("/api/v1/campaigns", json={"name": "Steal", "employee_id": eid}, headers={"Authorization": "Bearer b"})
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


# ── Test 6 & 7: Campaign call resolves employee's omni_agent_id ───────────────

def test_campaign_dispatch_uses_employee_omni_agent_id(campaign_db, monkeypatch):
    db, tenant_a, _ = campaign_db
    _mock_agent_service(monkeypatch, [{"id": 4001}])
    client = _client(db, 0, monkeypatch)
    dispatched: list[dict] = []

    class MockCallProvider:
        def dispatch(self, *, agent_id, to_number, from_number_id, call_context, metadata):
            dispatched.append({"agent_id": agent_id, "to_number": to_number, "metadata": metadata})
            return SimpleNamespace(provider_call_id="call-xyz", status="queued")

    import app.services.campaign_execution as exec_mod
    monkeypatch.setattr(exec_mod, "OmniDimensionCallProvider", lambda *_: MockCallProvider())
    monkeypatch.setattr(exec_mod, "OmniDimensionClient", lambda *_a, **_kw: None)
    # Bypass balance check — not under test here
    monkeypatch.setattr(exec_mod, "require_minimum_balance", lambda *_: None)

    pn = _add_phone_number(db, tenant_a.id, "99")
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            camp = client.post("/api/v1/campaigns", json={"name": "Dental Q4", "employee_id": eid}, headers=h).json()
            cid = camp["id"]
            client.post(f"/api/v1/campaigns/{cid}/contacts", json={"phone_number": "+919876543210"}, headers=h)
            # Start transitions to running; dispatch_next_pending does the actual call
            resp = client.post(f"/api/v1/campaigns/{cid}/start", json={"phone_number_id": str(pn.id)}, headers=h)
            assert resp.status_code == 200
        # Dispatch next pending contact directly (background thread equivalent)
        from app.services.campaign_execution import CampaignExecutionService
        from uuid import UUID as _UUID
        svc = CampaignExecutionService()
        svc.dispatch_next_pending(db, _UUID(cid), tenant_a.id)
        assert len(dispatched) == 1
        assert dispatched[0]["agent_id"] == 4001
        assert dispatched[0]["to_number"] == "+919876543210"
    finally:
        app.dependency_overrides.clear()


# ── Test 8: Employee rules present in deployed Omni agent config ──────────────

def test_employee_rules_present_in_omni_agent_context(campaign_db, monkeypatch):
    db, tenant_a, _ = campaign_db
    seen_payloads: list[dict] = []

    class CapturingProvider:
        def create_agent(self, payload):
            seen_payloads.append(payload)
            return SimpleNamespace(provider_id="5001", status="Completed", metadata={})
        def update_agent(self, pid, payload):
            seen_payloads.append(payload)
            return SimpleNamespace(provider_id=pid, status="Completed", metadata={})

    monkeypatch.setattr(employee_endpoint, "agent_service", OmniDimensionAgentService(CapturingProvider()))
    client = _client(db, 0, monkeypatch)
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(
                purpose="Qualify dental leads and book appointments."
            ), headers=h).json()["id"]
            # Patch in rich configuration
            client.patch(f"/api/v1/employees/{eid}", json={"configuration": {
                "name": "Sales Qualifier",
                "purpose": "Qualify dental leads and book appointments.",
                "goals": ["Qualify leads", "Book appointments"],
                "tasks": ["Ask about treatment", "Offer slots", "Transfer complex cases"],
                "qualification_rules": ["Must have a dental need", "Must be in service area"],
                "transfer_rules": ["Transfer if patient is in pain", "Transfer if billing issue"],
                "constraints": ["Never promise free treatment"],
                "llm_provider": "OpenAI",
                "llm_model": "gpt-4o-mini",
                "language": "English",
            }}, headers=h)
            client.post(f"/api/v1/employees/{eid}/publish", headers=h)

            assert len(seen_payloads) == 1
            payload = seen_payloads[0]
            titles = [s["title"] for s in payload["context_breakdown"]]
            bodies = " ".join(s["body"] for s in payload["context_breakdown"])

            assert "Agent Identity & Purpose" in titles
            assert "Tasks" in titles
            assert "Qualification & Workflow Rules" in titles
            assert "Human Escalation & Handoff Rules" in titles
            assert "Constraints & Guardrails" in titles
            assert "dental" in bodies.lower()
            assert "Never promise free treatment" in bodies
    finally:
        app.dependency_overrides.clear()


# ── Test 9: Edit without publish does not alter live agent ────────────────────

def test_edit_without_publish_does_not_call_omni(campaign_db, monkeypatch):
    db, _, _ = campaign_db
    calls = _mock_agent_service(monkeypatch, [{"id": 6001}])
    client = _client(db, 0, monkeypatch)
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            assert len(calls) == 1
            # Edit without publishing
            client.patch(f"/api/v1/employees/{eid}", json={"purpose": "New purpose"}, headers=h)
            assert len(calls) == 1  # No additional Omni call
    finally:
        app.dependency_overrides.clear()


# ── Test 10: Publishing edited employee updates existing agent ────────────────

def test_publish_edited_employee_updates_existing_agent(campaign_db, monkeypatch):
    db, _, _ = campaign_db
    calls = _mock_agent_service(monkeypatch, [{"id": 7001}])
    client = _client(db, 0, monkeypatch)
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            client.patch(f"/api/v1/employees/{eid}", json={"purpose": "Updated dental workflow"}, headers=h)
            resp = client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            assert resp.status_code == 200
            assert len(calls) == 2
            assert calls[1]["method"] == "PUT"
            assert calls[1]["provider_id"] == "7001"
    finally:
        app.dependency_overrides.clear()


# ── Test 11: Missing provider_agent_id prevents dispatch ─────────────────────

def test_campaign_dispatch_blocked_when_employee_not_deployed(campaign_db, monkeypatch):
    db, tenant_a, _ = campaign_db
    _mock_agent_service(monkeypatch, [{"id": 8001}])
    import app.services.campaign_execution as exec_mod
    monkeypatch.setattr(exec_mod, "require_minimum_balance", lambda *_: None)
    client = _client(db, 0, monkeypatch)
    pn = _add_phone_number(db, tenant_a.id)
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            # Do NOT publish — employee has no provider_agent_id
            camp = client.post("/api/v1/campaigns", json={"name": "Test", "employee_id": eid}, headers=h)
            # Campaign creation succeeds (draft employee allowed for creation)
            assert camp.status_code == 201
            cid = camp.json()["id"]
            client.post(f"/api/v1/campaigns/{cid}/contacts", json={"phone_number": "+919876543210"}, headers=h)
            resp = client.post(f"/api/v1/campaigns/{cid}/start", json={"phone_number_id": str(pn.id)}, headers=h)
            assert resp.status_code == 422
            assert "published" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


# ── Test 12 & 13: Provider failure → retryable, no duplicate agents ───────────

def test_provider_failure_is_retryable_and_no_duplicate_agents(campaign_db, monkeypatch):
    db, _, _ = campaign_db
    call_count = {"n": 0}

    class FlakyProvider:
        def create_agent(self, payload):
            call_count["n"] += 1
            if call_count["n"] == 1:
                from app.integrations.omnidimension.exceptions import OmniDimensionError
                raise OmniDimensionError("temporary failure")
            return SimpleNamespace(provider_id="9001", status="Completed", metadata={})
        def update_agent(self, pid, payload):
            call_count["n"] += 1
            return SimpleNamespace(provider_id=pid, status="Completed", metadata={})

    monkeypatch.setattr(employee_endpoint, "agent_service", OmniDimensionAgentService(FlakyProvider()))
    client = _client(db, 0, monkeypatch)
    try:
        with client:
            h = {"Authorization": "Bearer a"}
            eid = client.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            # First publish fails
            resp1 = client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            assert resp1.status_code == 502
            # Employee remains draft
            emp = db.scalar(select(AIEmployee).where(AIEmployee.id == UUID(eid)))
            assert emp.status == "draft"
            # Retry succeeds
            resp2 = client.post(f"/api/v1/employees/{eid}/publish", headers=h)
            assert resp2.status_code == 200
            assert resp2.json()["provider_agent_id"] == "9001"
            # Only 2 provider calls total (1 failed + 1 success) — no duplicates
            assert call_count["n"] == 2
    finally:
        app.dependency_overrides.clear()


# ── Test 14: Phone number tenant safety ───────────────────────────────────────

def test_campaign_dispatch_rejects_cross_tenant_phone_number(campaign_db, monkeypatch):
    db, tenant_a, tenant_b = campaign_db
    _mock_agent_service(monkeypatch, [{"id": 10001}])
    import app.services.campaign_execution as exec_mod
    monkeypatch.setattr(exec_mod, "require_minimum_balance", lambda *_: None)
    client_a = _client(db, 0, monkeypatch)
    pn_b = _add_phone_number(db, tenant_b.id, "77")
    try:
        with client_a:
            h = {"Authorization": "Bearer a"}
            eid = client_a.post("/api/v1/employees", json=_employee_payload(), headers=h).json()["id"]
            client_a.post(f"/api/v1/employees/{eid}/publish", headers=h)
            camp = client_a.post("/api/v1/campaigns", json={"name": "Test", "employee_id": eid}, headers=h).json()
            cid = camp["id"]
            client_a.post(f"/api/v1/campaigns/{cid}/contacts", json={"phone_number": "+919876543210"}, headers=h)
            # Try to use tenant_b's phone number
            resp = client_a.post(f"/api/v1/campaigns/{cid}/start", json={"phone_number_id": str(pn_b.id)}, headers=h)
            assert resp.status_code == 422
    finally:
        app.dependency_overrides.clear()


# ── map_employee_configuration unit tests ─────────────────────────────────────

def test_map_includes_all_context_sections():
    employee = SimpleNamespace(
        name="Dental Caller",
        purpose="Book dental appointments",
        call_type="outbound",
        llm_model="gpt-4o-mini",
        language="English",
    )
    config = {
        "name": "Dental Caller",
        "purpose": "Book dental appointments",
        "goals": ["Book appointments", "Qualify leads"],
        "tasks": ["Ask about treatment", "Offer slots"],
        "qualification_rules": ["Must need dental care"],
        "transfer_rules": ["Transfer if in pain"],
        "constraints": ["Never promise free treatment"],
        "conversation_flow": ["Introduce the company", "Ask budget and timeline"],
        "system_prompt": "Never offer unsupported discounts.",
        "llm_model": "gpt-4o-mini",
        "language": "English",
    }
    payload = map_employee_configuration(employee, config)
    titles = {s["title"] for s in payload["context_breakdown"]}
    assert "Agent Identity & Purpose" in titles
    assert "Responsibilities" in titles
    assert "Tasks" in titles
    assert "Qualification & Workflow Rules" in titles
    assert "Human Escalation & Handoff Rules" in titles
    assert "Constraints & Guardrails" in titles
    assert "Conversation Flow" in titles
    assert "Additional Behavioral Instructions" in titles
    assert "Language & Communication Rules" in titles
    # No internal fields leaked
    assert "llm_provider" not in str(payload)
    assert "provider_agent_id" not in str(payload)


def test_map_tasks_formatted_as_bullet_list():
    employee = SimpleNamespace(name="E", purpose="P", call_type="both", llm_model="m", language="English")
    config = {"name": "E", "purpose": "P", "tasks": ["Task A", "Task B"], "llm_model": "m", "language": "English"}
    payload = map_employee_configuration(employee, config)
    tasks_section = next(s for s in payload["context_breakdown"] if s["title"] == "Tasks")
    assert "- Task A" in tasks_section["body"]
    assert "- Task B" in tasks_section["body"]


def test_map_welcome_message_uses_employee_name():
    employee = SimpleNamespace(name="Ava", purpose="P", call_type="both", llm_model="m", language="English")
    config = {"name": "Ava", "purpose": "P", "llm_model": "m", "language": "English"}
    payload = map_employee_configuration(employee, config)
    assert "Ava" in payload["welcome_message"]


def test_prompt_answers_first_turn_and_defers_customer_details():
    from app.services.employee_prompt import build_employee_prompt

    prompt = build_employee_prompt({
        "name": "Vaibhav",
        "business_name": "KMG Insurance Company",
        "purpose": "Remind customers about policy renewal within the next 7 days",
        "language": "Telugu",
    })
    assert "welcome message" in prompt
    assert "Do not repeat the greeting" in prompt
    assert "respond directly to what they said first" in prompt
    assert "Do not ask for the caller's name, mobile number, or profession at the beginning" in prompt
    assert "Always collect the caller's full name" not in prompt
    assert "renewal-related next action" not in prompt
    assert "appropriate next action for the configured objective" in prompt


def test_payload_explicitly_configures_listening_and_post_call_delivery(monkeypatch):
    from app.services import omnidimension_agents as service

    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        backend_public_url="https://voice.example.com"
    ))
    employee = SimpleNamespace(
        name="Sales Assistant", purpose="Qualify demo requests", call_type="outbound",
        llm_model="gpt-4o-mini", language="English (India)",
    )
    payload = service.map_employee_configuration(employee, {
        "name": employee.name, "purpose": employee.purpose,
        "language": employee.language, "llm_model": employee.llm_model,
    })
    assert payload["is_welcome_message_interruption"] is True
    assert payload["is_interruption_allowed"] is True
    assert payload["transcriber"] == {
        "provider": "deepgram_stream", "model": "nova-3", "language": "en-IN",
        "silence_timeout_ms": 800, "interruption_min_words": 1,
    }
    webhook = payload["post_call_actions"]["webhook"]
    assert webhook["url"] == "https://voice.example.com/api/v1/webhooks/omnidimension/post-call"
    assert set(webhook["trigger_call_statuses"]) == {"completed", "failed", "no_answer", "busy", "voicemail_detected"}


def test_voice_payload_keeps_objective_completion_active_and_requires_explicit_end():
    employee = SimpleNamespace(name="Telugu Assistant", purpose="Book appointments", call_type="inbound", llm_model="gpt-4o", language="Telugu")
    payload = map_employee_configuration(employee, {
        "name": employee.name,
        "purpose": employee.purpose,
        "language": "Telugu",
        "closing_behavior": "End after the appointment is booked.",
    })
    bodies = "\n".join(section["body"] for section in payload["context_breakdown"])
    assert "Completing the business objective is not permission to end the call" in bodies
    assert "explicit end-of-call confirmation" in bodies
    assert "An interruption is a normal barge-in, not a request to hang up" in bodies
    assert "ఇంకా ఏమైనా help కావాలా?" in bodies
    assert payload["is_end_call_enabled"] is True
    assert payload["interruption_min_words"] == 1
    assert "short answer" in payload["end_call_condition"]


def test_business_identity_uses_explicit_company_name_and_keeps_requirement_as_description():
    employee = SimpleNamespace(name="Mani", purpose="Call customers whose insurance renewal date is within 7 days and remind them about renewal.", call_type="outbound", llm_model="gpt-4o", language="Telugu")
    payload = map_employee_configuration(employee, {
        "name": "Mani", "business_name": "KMG Insurance", "business_description": employee.purpose,
        "purpose": employee.purpose, "original_requirement": employee.purpose,
        "language": "Telugu", "llm_model": "gpt-4o",
    })
    assert payload["welcome_message"].startswith("నమస్కారం, నేను Mani. KMG Insurance")
    assert employee.purpose not in payload["welcome_message"]
    identity = next(item["body"] for item in payload["context_breakdown"] if item["title"] == "Agent Identity & Purpose")
    assert "Business: KMG Insurance" in identity
    assert f"Business description: {employee.purpose}" in identity


def test_missing_business_name_never_uses_requirement_as_identity():
    employee = SimpleNamespace(name="Mani", purpose="Call customers whose renewal date is within 7 days.", call_type="inbound", llm_model="gpt-4o", language="English")
    payload = map_employee_configuration(employee, {"name": employee.name, "purpose": employee.purpose, "original_requirement": employee.purpose, "language": "English"})
    assert "Business:" not in next(item["body"] for item in payload["context_breakdown"] if item["title"] == "Agent Identity & Purpose")


def test_telugu_welcome_uses_configured_business_context_without_generic_filler():
    employee = SimpleNamespace(name="Chaitanya", purpose="Book appointments", call_type="inbound", llm_model="gpt-4o", language="Telugu")
    payload = map_employee_configuration(employee, {
        "name": employee.name,
        "purpose": employee.purpose,
        "language": "Telugu",
        "selected_template_id": "pontis_hospital_v1",
        "template_values": {"business_name": "Charan Care Hospital"},
    })
    assert "Charan Care Hospital" in payload["welcome_message"]
    assert "your questions about our configured services" not in payload["welcome_message"].lower()


def test_complete_shabdha_brief_and_unknown_configuration_reach_canonical_context():
    employee = SimpleNamespace(name="Telugu Hospital Assistant", purpose="Book appointments", call_type="inbound", llm_model="gpt-4o", language="te-IN")
    config = {
        "name": employee.name,
        "purpose": employee.purpose,
        "language": "te-IN",
        "direct_prompt": "Original Shabdha brief: help patients choose a department and schedule a visit.",
        "responsibilities": ["Collect patient details", "Confirm appointment details"],
        "appointment_rules": ["Never invent availability", "Escalate emergencies"],
        "additional_information": "Do not diagnose or prescribe.",
        "future_supported_rule": {"value": "Use multiple turns"},
    }
    payload = map_employee_configuration(employee, config)
    bodies = "\n".join(section["body"] for section in payload["context_breakdown"])
    assert "Original Shabdha brief" in bodies
    assert "Do not diagnose or prescribe." in bodies
    assert "future_supported_rule" in bodies or "Use multiple turns" in bodies
    assert "Continue listening and responding after every caller turn." in bodies
    assert payload["languages"] == ["Telugu"]
    assert len(bodies) > 300


def test_payload_preserves_complete_shabdha_context_and_telugu_configuration():
    employee = SimpleNamespace(id="test", name="Charan Care Assistant", purpose="Help patients book appointments", call_type="inbound", llm_model="gpt-4o", language="Telugu")
    config = {
        "name": employee.name, "purpose": employee.purpose, "language": "Telugu", "creation_mode": "chat",
        "original_shabdha_brief": "Hospital name: Charan Care Hospital\nLocation: Hyderabad\nDepartments: Cardiology, General Medicine, Dermatology\nWorking hours: 9 AM to 8 PM\nAppointment process: collect patient name, department, preferred doctor, date and time; confirm before booking\nEmergency: direct emergencies to the hospital emergency department/contact",
        "responsibilities": ["Collect patient details", "Confirm appointment details"],
    }
    payload = map_employee_configuration(employee, config)
    titles = {section["title"] for section in payload["context_breakdown"]}
    bodies = "\n".join(section["body"] for section in payload["context_breakdown"])
    assert "Complete Employee Instructions" in titles
    assert "ORIGINAL SHABDHA BRIEF" in bodies
    for fact in ("Charan Care Hospital", "Hyderabad", "Cardiology", "9 AM to 8 PM", "preferred doctor", "emergency department"):
        assert fact in bodies
    assert "patient details" in bodies
    assert "Converse naturally in Telugu" in bodies
    assert "To be defined through the builder" not in bodies
    assert payload["welcome_message"] not in bodies
    assert payload["model"]["model"] == "gpt-4o"


def test_payload_preserves_exact_paste_prompt_without_system_prompt_duplication():
    employee = SimpleNamespace(id="test", name="Paste Assistant", purpose="Answer customer questions", call_type="inbound", llm_model="gpt-4o", language="English")
    prompt = "Handle returns within 30 days. Ask for the order number and explain the refund timeline."
    payload = map_employee_configuration(employee, {"name": employee.name, "purpose": employee.purpose, "language": "English", "creation_mode": "prompt", "direct_prompt": prompt, "system_prompt": prompt, "llm_model": "gpt-4o"})
    bodies = "\n".join(section["body"] for section in payload["context_breakdown"])
    assert "ORIGINAL CUSTOMER PROMPT" in bodies
    assert prompt in bodies
    assert bodies.count(prompt) == 1
