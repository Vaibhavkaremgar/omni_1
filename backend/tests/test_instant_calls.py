from types import SimpleNamespace
from uuid import UUID
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.v1.endpoints import calls as calls_endpoint
from app.integrations.omnidimension import OmniDimensionCallProvider, OmniDimensionClient
from app.main import app
from app.models import AIEmployee, AIEmployeeVersion, Call, CreditWallet, PhoneNumber, Tenant, User
from app.services import auth as auth_service
from app.services.instant_calls import InstantCallService


@pytest.fixture()
def call_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.db.base import Base

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant_a = Tenant(name="Call A", slug="call-a")
    tenant_b = Tenant(name="Call B", slug="call-b")
    db.add_all([tenant_a, tenant_b])
    db.flush()
    db.add_all([
        User(tenant_id=tenant_a.id, password_hash=auth_service.hash_password("password123"), email="a@call.test", status="active"),
        User(tenant_id=tenant_b.id, password_hash=auth_service.hash_password("password123"), email="b@call.test", status="active"),
        CreditWallet(tenant_id=tenant_a.id, balance_credits=100, currency="INR", status="active"),
        CreditWallet(tenant_id=tenant_b.id, balance_credits=100, currency="INR", status="active"),
    ])
    db.commit()
    try:
        yield db, tenant_a, tenant_b
    finally:
        db.close()


def create_employee(db, tenant, *, published=True, provider_agent_id="9001"):
    employee = AIEmployee(
        tenant_id=tenant.id,
        name="Ava Caller",
        purpose="Help customers",
        call_type="outbound",
        llm_provider="OpenAI",
        llm_model="gpt-4o-mini",
        language="en-US",
        creation_mode="chat",
        status="published" if published else "draft",
    )
    db.add(employee)
    db.flush()
    version = AIEmployeeVersion(
        tenant_id=tenant.id,
        employee_id=employee.id,
        version_number=1,
        status="published" if published else "draft",
        configuration={"name": employee.name, "purpose": employee.purpose},
        provider_name="omnidimension" if provider_agent_id else None,
        provider_agent_id=provider_agent_id,
        provider_status="Completed" if provider_agent_id else None,
    )
    db.add(version)
    db.flush()
    if published:
        employee.published_version = version
    db.commit()
    db.refresh(employee)
    return employee


def create_number(db, tenant, *, assigned=True, provider_id="77"):
    number = PhoneNumber(
        tenant_id=tenant.id if assigned else None,
        e164_number="+15550001111",
        provider_name="omnidimension",
        provider_phone_number_id=provider_id,
        status="active",
    )
    db.add(number)
    db.commit()
    db.refresh(number)
    return number


def service_for(handler):
    client = OmniDimensionClient(
        settings=SimpleNamespace(
            omnidimension_api_key="test-call-key",
            omnidimension_base_url="https://provider.test/api/v1",
            omnidimension_timeout_seconds=5,
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return client, InstantCallService(OmniDimensionCallProvider(client))


def authenticated_client(db, user_id, monkeypatch):
    monkeypatch.setattr(auth_service, "decode_access_token", lambda token: db.query(User).all()[0 if user_id.endswith("-a") else 1].id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def request(employee_id, phone_number_id):
    return {
        "employee_id": str(employee_id),
        "phone_number_id": str(phone_number_id),
        "destination_phone_number": "+15551234567",
        "customer_name": "Jane Doe",
        "context": "Interested in the enterprise plan",
    }


def test_dispatch_uses_documented_endpoint_and_persists_queued_call(call_database, monkeypatch):
    db, tenant_a, _ = call_database
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    seen = []

    def handler(http_request: httpx.Request):
        seen.append(http_request)
        assert http_request.url.path == "/api/v1/calls/dispatch"
        payload = json.loads(http_request.content)
        assert payload["agent_id"] == 9001
        assert payload["from_number_id"] == 77
        assert payload["to_number"] == "+15551234567"
        assert payload["call_context"]["customer_name"] == "Jane Doe"
        assert payload["metadata"]["tenant_id"] == str(tenant_a.id)
        assert payload["metadata"]["local_call_id"]
        assert "test-call-key" not in str(payload)
        return httpx.Response(200, json={"success": True, "status": "dispatched", "requestId": 3166940})

    client, service = service_for(handler)
    monkeypatch.setattr(calls_endpoint, "instant_call_service", service)
    api = authenticated_client(db, "call-user-a", monkeypatch)
    try:
        with api:
            response = api.post("/api/v1/calls/instant", json=request(employee.id, phone.id), headers={"Authorization": "Bearer a"})
            assert response.status_code == 201
            body = response.json()
            assert body["provider_call_id"] == "3166940"
            assert body["status"] == "queued"
            assert "test-call-key" not in response.text
            call = db.scalar(select(Call).where(Call.id == UUID(body["id"])))
            assert call.status == "queued"
            assert call.provider_call_id == "3166940"
            assert call.employee_version_id == employee.published_version.id
            assert len(seen) == 1
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_provider_failure_marks_call_failed_and_returns_safe_error(call_database, monkeypatch):
    db, tenant_a, _ = call_database
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    client, service = service_for(lambda _: httpx.Response(503, json={"secret": "provider"}))
    monkeypatch.setattr(calls_endpoint, "instant_call_service", service)
    api = authenticated_client(db, "call-user-a", monkeypatch)
    try:
        with api:
            response = api.post("/api/v1/calls/instant", json=request(employee.id, phone.id), headers={"Authorization": "Bearer a"})
            assert response.status_code == 502
            assert "test-call-key" not in response.text
            call = db.scalar(select(Call).order_by(Call.created_at.desc()))
            assert call.status == "failed"
            assert call.provider_call_id is None
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_network_failure_and_duplicate_dispatch_are_controlled(call_database, monkeypatch):
    db, tenant_a, _ = call_database
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    def offline(_: httpx.Request):
        raise httpx.ConnectError("offline")

    client, service = service_for(offline)
    monkeypatch.setattr(calls_endpoint, "instant_call_service", service)
    api = authenticated_client(db, "call-user-a", monkeypatch)
    try:
        with api:
            headers = {"Authorization": "Bearer a"}
            first = api.post("/api/v1/calls/instant", json=request(employee.id, phone.id), headers=headers)
            assert first.status_code == 502
        client.close()

        def successful(_: httpx.Request):
            return httpx.Response(200, json={"success": True, "status": "dispatched", "requestId": 88})

        client, service = service_for(successful)
        monkeypatch.setattr(calls_endpoint, "instant_call_service", service)
        with api:
            assert api.post("/api/v1/calls/instant", json=request(employee.id, phone.id), headers=headers).status_code == 201
            assert api.post("/api/v1/calls/instant", json=request(employee.id, phone.id), headers=headers).status_code == 409
    finally:
        client.close()
        app.dependency_overrides.clear()


@pytest.mark.parametrize("case", ["draft_employee", "missing_agent", "other_employee", "other_phone", "platform_phone"])
def test_dispatch_rejects_invalid_tenant_or_employee_resources(call_database, monkeypatch, case):
    db, tenant_a, tenant_b = call_database
    employee_a = create_employee(db, tenant_a, published=case not in {"draft_employee", "missing_agent"}, provider_agent_id=None if case == "missing_agent" else "9001")
    employee_b = create_employee(db, tenant_b)
    phone_a = create_number(db, tenant_a, assigned=case not in {"platform_phone"})
    phone_b = create_number(db, tenant_b, provider_id="78")
    client, service = service_for(lambda _: httpx.Response(200, json={"requestId": 1, "status": "dispatched"}))
    monkeypatch.setattr(calls_endpoint, "instant_call_service", service)
    api = authenticated_client(db, "call-user-a", monkeypatch)
    try:
        selected_employee = employee_b if case == "other_employee" else employee_a
        selected_phone = phone_b if case == "other_phone" else phone_a
        with api:
            response = api.post("/api/v1/calls/instant", json=request(selected_employee.id, selected_phone.id), headers={"Authorization": "Bearer a"})
            assert response.status_code in {404, 422}
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_unauthenticated_instant_call_is_rejected():
    with TestClient(app) as api:
        assert api.post("/api/v1/calls/instant", json={}).status_code == 401
