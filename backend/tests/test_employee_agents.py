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
from app.services.omnidimension_agents import OmniDimensionAgentService, map_employee_configuration


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
        "language": "en-US",
        "creation_mode": "chat",
    }


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
    assert mapped["model"] == {"model": "gpt-4o-mini"}
    assert "internal_secret" not in str(mapped)
    assert "context_breakdown" in mapped


def test_publish_creates_agent_and_persists_provider_state(agent_database, monkeypatch):
    db, _, _ = agent_database
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        assert request.url.path == "/api/v1/agents/create"
        payload = request.read().decode()
        assert "internal_secret" not in payload
        return httpx.Response(200, json={"id": 9001, "name": "Ava Support", "status": "Completed"})

    client, service = provider_service(handler)
    monkeypatch.setattr(employee_endpoint, "agent_service", service)
    api = authenticated_client(db, "agent-user-a", monkeypatch)
    try:
        with api:
            response = api.post("/api/v1/employees", json=employee_payload(), headers={"Authorization": "Bearer a"})
            employee_id = response.json()["id"]
            published = api.post(f"/api/v1/employees/{employee_id}/publish", headers={"Authorization": "Bearer a"})
            assert published.status_code == 200
            assert published.json()["provider_name"] == "omnidimension"
            assert published.json()["provider_agent_id"] == "9001"
            assert published.json()["provider_status"] == "Completed"
            assert "test-agent-key" not in published.text
            version = db.scalar(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == UUID(employee_id)))
            assert version.provider_agent_id == "9001"
            assert len(seen) == 1
    finally:
        client.close()
        app.dependency_overrides.clear()


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

            # No body and no Content-Type are required by the publish route.
            published = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert published.status_code == 200
            assert len(seen) == 1

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
            failed = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert failed.status_code == 502
            assert "test-agent-key" not in failed.text
            local = db.scalar(select(AIEmployee).where(AIEmployee.id == UUID(employee_id)))
            assert local.status == "draft"
            assert db.scalar(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == UUID(employee_id))).status == "draft"
            retried = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert retried.status_code == 200
            assert retried.json()["provider_agent_id"] == "9002"
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
            api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            repeated = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert repeated.status_code == 200
            assert calls[1].method == "PUT"
            assert calls[1].url.path == "/api/v1/agents/9003"
            api.patch(f"/api/v1/employees/{employee_id}", json={"purpose": "New support flow"}, headers=headers)
            new_publish = api.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert new_publish.status_code == 200
            assert calls[2].method == "PUT"
            assert calls[2].url.path == "/api/v1/agents/9003"
            versions = db.scalars(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == UUID(employee_id)).order_by(AIEmployeeVersion.version_number)).all()
            assert versions[0].provider_agent_id == "9003"
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
