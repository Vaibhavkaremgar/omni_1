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
from app.integrations.omnidimension import OmniDimensionAgentProvider, OmniDimensionClient
from app.main import app
from app.models import AIEmployee, AIEmployeeVersion, Tenant, User
from app.services import auth as auth_service
from app.services.omnidimension_agents import OmniDimensionAgentService, map_employee_configuration, _provider_verification, _returned_configuration, _sent_configuration


@pytest.fixture()
def agent_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.db.base import Base

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant_a = Tenant(name="Agent A", slug="agent-a")
    tenant_b = Tenant(name="Agent B", slug="agent-b")
    db.add_all([tenant_a, tenant_b])
    db.flush()
    db.add_all([
        User(tenant_id=tenant_a.id, password_hash=auth_service.hash_password("password123"), email="a@agent.test", status="active"),
        User(tenant_id=tenant_b.id, password_hash=auth_service.hash_password("password123"), email="b@agent.test", status="active"),
    ])
    db.commit()
    try:
        yield db, tenant_a, tenant_b
    finally:
        db.close()


def employee_payload():
    return {
        "name": "Ava Support",
        "purpose": "Help customers resolve account issues.",
        "call_type": "inbound",
        "llm_provider": "OpenAI",
        "llm_model": "gpt-4o-mini",
        "language": "English",
        "creation_mode": "chat",
        "selected_template_id": "pontis_sales_v1",
        "selected_template_version": 1,
        "template_values": {"business_name": "Test Business", "product_or_service": "Test service", "target_customer": "Test customers", "service_area": "Hyderabad", "lead_qualification_questions": "Need", "sales_team_contact": "100", "working_hours": "9-5"},
    }


def six_section_script():
    return {
        "Identity & Purpose": "You are Ava from Test Business helping callers with support.",
        "Greeting & Intro": "Hi, this is Ava from Test Business. How can I help you today?",
        "Qualification": "Ask what account issue the caller needs help with.",
        "Handling Objections": "If they are unsure, explain the support options briefly.",
        "Call to Action": "Offer the next useful support step.",
        "Closing": "Ask whether they need anything else, then close politely.",
    }


def prepare_publishable_draft(api, employee_id: str, headers: dict[str, str], **configuration):
    payload = {"configuration": {"call_script": six_section_script(), "language": "English", **configuration}}
    response = api.patch(f"/api/v1/employees/{employee_id}", json=payload, headers=headers)
    assert response.status_code == 200
    return response


def authenticated_client(db, user_id, monkeypatch):
    monkeypatch.setattr(auth_service, "decode_access_token", lambda token: db.query(User).all()[0 if user_id.endswith("-a") else 1].id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def provider_service(handler):
    client = OmniDimensionClient(
        settings=SimpleNamespace(
            omnidimension_api_key="test-agent-key",
            omnidimension_base_url="https://provider.test/api/v1",
            omnidimension_timeout_seconds=5,
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return client, OmniDimensionAgentService(OmniDimensionAgentProvider(client))


def test_mapper_sends_only_supported_agent_fields(agent_database):
    db, _, _ = agent_database
    employee = AIEmployee(
        tenant_id=agent_database[1].id,
        name="Ava",
        purpose="Support customers",
        call_type="inbound",
        llm_provider="OpenAI",
        llm_model="gpt-4o-mini",
        language="en-US",
        creation_mode="chat",
    )
    version = AIEmployeeVersion(configuration={
        "name": "Ava",
        "purpose": "Support customers",
        "goals": ["Resolve account issues"],
        "tone": "Warm",
        "internal_secret": "must stay local",
    })
    mapped = map_employee_configuration(employee, version.configuration)
    assert mapped["name"] == "Ava"
    assert mapped["call_type"] == "Incoming"
    assert mapped["model"] == {"model": "gemini-2.5-flash-lite"}
    assert "internal_secret" not in str(mapped)
    assert "context_breakdown" in mapped


def test_publish_creates_agent_and_persists_provider_state(agent_database, monkeypatch):
    db, _, _ = agent_database
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        assert request.url.path == "/api/v1/agents/create"
        raw = request.read().decode()
        import json
        body = json.loads(raw)
        assert body["post_call_actions"]["webhook"]["url"].endswith("/api/v1/webhooks/omnidimension/post-call")
        assert body["post_call_actions"]["webhook"]["url"] not in str(body.get("context_breakdown"))
        assert "completed" in body["post_call_actions"]["webhook"]["trigger_call_statuses"]
        payload = raw
        assert "internal_secret" not in payload
        return httpx.Response(200, json={"id": 9001, "name": "Ava Support", "status": "Completed"})

    client, service = provider_service(handler)
    monkeypatch.setattr(employee_endpoint, "agent_service", service)
    api = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with api:
            headers = {"Authorization": "Bearer a"}
            response = api.post("/api/v1/employees", json=employee_payload(), headers=headers)
            employee_id = response.json()["id"]
            prepare_publishable_draft(api, employee_id, headers)
            published = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert published.status_code == 200
            assert published.json()["provider_name"] == "omnidimension"
            assert "provider_agent_id" not in published.json()
            assert published.json()["provider_status"] == "Completed"
            assert "test-agent-key" not in published.text
            version = db.scalar(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == UUID(employee_id)))
            assert version.provider_agent_id == "9001"
            assert [request.method for request in seen] == ["POST", "GET"]
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_publish_persists_verified_provider_configuration(agent_database, monkeypatch):
    db, _, _ = agent_database
    sent_body = {}

    def handler(request: httpx.Request):
        nonlocal sent_body
        if request.method == "POST":
            import json
            sent_body = json.loads(request.read().decode())
            return httpx.Response(200, json={"id": 9010, "status": "Completed"})
        readback = {
            **sent_body,
            "id": 9010,
            "status": "Completed",
        }
        return httpx.Response(200, json=readback)

    client, service = provider_service(handler)
    monkeypatch.setattr(employee_endpoint, "agent_service", service)
    api = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with api:
            headers = {"Authorization": "Bearer a"}
            payload = {**employee_payload(), "call_type": "outbound"}
            employee_id = api.post("/api/v1/employees", json=payload, headers=headers).json()["id"]
            api.patch(
                f"/api/v1/employees/{employee_id}",
                json={"configuration": {"call_type": "outbound", "call_script": six_section_script()}},
                headers=headers,
            )
            published = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert published.status_code == 200
            verification = published.json()["provider_verification"]
            assert verification["status"] == "verified"
            assert verification["intended_configuration"]["call_type"] == "Outgoing"
            assert verification["sent_configuration"]["call_type"] == "Outgoing"
            assert verification["returned_configuration"]["call_type"] == "Outgoing"
            assert verification["intended_configuration"]["welcome_message"] == six_section_script()["Greeting & Intro"]
            assert "api_key" not in published.text.casefold()
            version = db.scalar(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == UUID(employee_id)))
            assert version.provider_metadata["provider_verification"]["status"] == "verified"
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_publish_persists_mismatch_without_failing(agent_database, monkeypatch):
    db, _, _ = agent_database
    sent_body = {}

    def handler(request: httpx.Request):
        nonlocal sent_body
        if request.method == "POST":
            import json
            sent_body = json.loads(request.read().decode())
            return httpx.Response(200, json={"id": 9011, "status": "Completed"})
        return httpx.Response(200, json={**sent_body, "call_type": "Incoming", "id": 9011, "status": "Completed"})

    client, service = provider_service(handler)
    monkeypatch.setattr(employee_endpoint, "agent_service", service)
    api = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with api:
            headers = {"Authorization": "Bearer a"}
            payload = {**employee_payload(), "call_type": "outbound"}
            employee_id = api.post("/api/v1/employees", json=payload, headers=headers).json()["id"]
            prepare_publishable_draft(api, employee_id, headers, call_type="outbound")
            published = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert published.status_code == 200
            verification = published.json()["provider_verification"]
            assert verification["status"] == "mismatch"
            assert "call_type" in {item["field"] for item in verification["mismatches"]}
            version = db.scalar(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == UUID(employee_id)))
            assert version.provider_metadata["verification_status"] == "mismatch"
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_publish_marks_provider_readback_failed_without_failing(agent_database, monkeypatch):
    db, _, _ = agent_database

    def handler(request: httpx.Request):
        if request.method == "GET":
            return httpx.Response(503, json={"error": "readback failed"})
        return httpx.Response(200, json={"id": 9012, "status": "Completed"})

    client, service = provider_service(handler)
    monkeypatch.setattr(employee_endpoint, "agent_service", service)
    api = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with api:
            headers = {"Authorization": "Bearer a"}
            employee_id = api.post("/api/v1/employees", json=employee_payload(), headers=headers).json()["id"]
            prepare_publishable_draft(api, employee_id, headers)
            published = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert published.status_code == 200
            assert published.json()["provider_verification"]["status"] == "provider_readback_failed"
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_verification_detects_reordered_six_section_prompt(agent_database):
    _, tenant, _ = agent_database
    employee = AIEmployee(
        tenant_id=tenant.id,
        name="Ava",
        purpose="Support customers",
        call_type="inbound",
        llm_provider="OpenAI",
        llm_model="gpt-4o-mini",
        language="English",
        creation_mode="chat",
    )
    configuration = {"call_type": "inbound", "language": "English", "call_script": six_section_script()}
    payload = map_employee_configuration(employee, configuration)
    readback_sections = []
    for section in payload["context_breakdown"]:
        copied = dict(section)
        if copied.get("title") == "Published Call Script Source of Truth":
            copied["body"] = "6. Closing\nAsk whether they need anything else, then close politely.\n\n1. Identity & Purpose\nYou are Ava from Test Business helping callers with support."
        readback_sections.append(copied)
    readback = {**payload, "context_breakdown": readback_sections}
    verification = _provider_verification(
        _sent_configuration(payload),
        _sent_configuration(payload),
        _returned_configuration(readback),
        "9013",
    )
    assert verification["status"] == "mismatch"
    assert "six_section_prompt" in {item["field"] for item in verification["mismatches"]}


def test_publish_request_contract_is_bodyless_and_invalid_path_is_rejected(agent_database, monkeypatch):
    """The browser publishes the already-saved draft with POST and no JSON body."""
    db, _, _ = agent_database
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"id": 9004, "status": "Completed"})

    client, service = provider_service(handler)
    monkeypatch.setattr(employee_endpoint, "agent_service", service)
    api = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with api:
            headers = {"Authorization": "Bearer a"}
            employee_id = api.post("/api/v1/employees", json=employee_payload(), headers=headers).json()["id"]
            prepare_publishable_draft(api, employee_id, headers)

            # No body and no Content-Type are required by the publish route.
            published = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert published.status_code == 200
            assert [request.method for request in seen] == ["POST", "GET"]

            invalid_path = api.post("/api/v1/employees/not-a-uuid/publish", headers=headers)
            assert invalid_path.status_code == 422
            assert invalid_path.json()["detail"][0]["loc"] == ["path", "employee_id"]
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_provider_failure_preserves_draft_and_retry_succeeds(agent_database, monkeypatch):
    db, _, _ = agent_database
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(503, json={"error": "temporary"})
        return httpx.Response(200, json={"id": 9002, "status": "Completed"})

    client, service = provider_service(handler)
    monkeypatch.setattr(employee_endpoint, "agent_service", service)
    api = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with api:
            headers = {"Authorization": "Bearer a"}
            employee_id = api.post("/api/v1/employees", json=employee_payload(), headers=headers).json()["id"]
            prepare_publishable_draft(api, employee_id, headers)
            failed = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert failed.status_code == 502
            assert "test-agent-key" not in failed.text
            local = db.scalar(select(AIEmployee).where(AIEmployee.id == UUID(employee_id)))
            assert local.status == "draft"
            assert db.scalar(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == UUID(employee_id))).status == "draft"
            retried = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert retried.status_code == 200
            assert "provider_agent_id" not in retried.json()
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_same_employee_updates_existing_agent_after_a_draft_edit(agent_database, monkeypatch):
    db, _, _ = agent_database
    calls = []

    def handler(request: httpx.Request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": 9003, "status": "Completed"})
        return httpx.Response(200, json={"id": 9003, "status": "Completed"})

    client, service = provider_service(handler)
    monkeypatch.setattr(employee_endpoint, "agent_service", service)
    api = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with api:
            headers = {"Authorization": "Bearer a"}
            employee_id = api.post("/api/v1/employees", json=employee_payload(), headers=headers).json()["id"]
            prepare_publishable_draft(api, employee_id, headers)
            api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            repeated = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert repeated.status_code == 200
            assert calls[2].method == "PUT"
            assert calls[2].url.path == "/api/v1/agents/9003"
            api.patch(f"/api/v1/employees/{employee_id}", json={"purpose": "New support flow"}, headers=headers)
            new_publish = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert new_publish.status_code == 200
            assert calls[2].method == "PUT"
            assert calls[2].url.path == "/api/v1/agents/9003"
            versions = db.scalars(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == UUID(employee_id)).order_by(AIEmployeeVersion.version_number)).all()
            # The provider identity is unique: only the current published
            # version owns the provider columns; archived history keeps it in
            # provider_metadata instead.
            assert versions[0].provider_agent_id is None
            assert versions[0].provider_metadata["historical_provider_agent_id"] == "9003"
            assert versions[-1].provider_agent_id == "9003"
            assert versions[1].provider_agent_id == "9003"
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_cross_tenant_publish_and_unauthenticated_access_are_rejected(agent_database, monkeypatch):
    db, _, _ = agent_database
    monkeypatch.setattr(employee_endpoint, "agent_service", SimpleNamespace(synchronize=lambda *_: None))
    owner = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with owner:
            employee_id = owner.post("/api/v1/employees", json=employee_payload(), headers={"Authorization": "Bearer a"}).json()["id"]
    finally:
        app.dependency_overrides.clear()
    other = authenticated_client(db, "agent-user-b", monkeypatch)
    try:
        with other:
            assert other.post(f"/api/v1/employees/{employee_id}/publish", headers={"Authorization": "Bearer b"}).status_code == 404
    finally:
        app.dependency_overrides.clear()
    with TestClient(app) as unauthenticated:
        assert unauthenticated.post(f"/api/v1/employees/{employee_id}/publish").status_code == 401
