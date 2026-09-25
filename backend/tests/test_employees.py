from uuid import UUID
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_db
from app.api.v1.endpoints import employees as employee_endpoint
from app.main import app
from app.models import AIEmployee, AIEmployeeVersion, Tenant, User
from app.models.enums import VersionStatus
from app.services import auth as auth_service
from app.services import employee_configuration as employee_configuration_service


@pytest.fixture()
def employee_database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.db.base import Base

    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant_a = Tenant(name="Tenant A", slug="employee-tenant-a")
    tenant_b = Tenant(name="Tenant B", slug="employee-tenant-b")
    db.add_all([tenant_a, tenant_b])
    db.flush()
    db.add_all([
        User(tenant_id=tenant_a.id, password_hash=auth_service.hash_password("password123"), email="a@employees.test", status="active"),
        User(tenant_id=tenant_b.id, password_hash=auth_service.hash_password("password123"), email="b@employees.test", status="active"),
    ])
    db.commit()
    try:
        yield db, tenant_a, tenant_b
    finally:
        db.close()


def payload():
    return {
        "name": "Ava Lead Qualifier",
        "purpose": "Qualify new leads and route qualified prospects.",
        "call_type": "inbound",
        "llm_provider": "OpenAI",
        "llm_model": "gpt-4o-mini",
        "language": "English",
        "creation_mode": "chat",
        "selected_template_id": "pontis_sales_v1",
        "selected_template_version": 1,
        "template_values": {"business_name": "Test Business", "product_or_service": "Test service", "target_customer": "Test customers", "service_area": "Hyderabad", "lead_qualification_questions": "Need", "sales_team_contact": "100", "working_hours": "9-5"},
    }


def client_for(db, user_id, monkeypatch):
    monkeypatch.setattr(auth_service, "decode_access_token", lambda token: db.query(User).all()[0 if user_id.endswith("-a") else 1].id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_unauthenticated_employee_request_is_rejected():
    with TestClient(app) as client:
        response = client.get("/api/v1/employees")
    assert response.status_code == 401


def test_employee_creation_allows_optional_template_and_validates_language(employee_database, monkeypatch):
    db, _, _ = employee_database
    monkeypatch.setattr(employee_endpoint, "get_settings", lambda: SimpleNamespace(effective_llm_provider="groq", effective_llm_model="openai/gpt-oss-20b"))
    client = client_for(db, "employee-user-a", monkeypatch)
    base = payload()
    with client:
        invalid_template = {**base, "selected_template_id": "not-a-pontis-template"}
        assert client.post("/api/v1/employees", json=invalid_template, headers={"Authorization": "Bearer a"}).status_code == 422
        invalid_version = {**base, "selected_template_version": 99}
        assert client.post("/api/v1/employees", json=invalid_version, headers={"Authorization": "Bearer a"}).status_code == 422
        invalid_language = {**base, "language": "not-supported"}
        assert client.post("/api/v1/employees", json=invalid_language, headers={"Authorization": "Bearer a"}).status_code == 422
        without_template = {key: value for key, value in base.items() if key != "selected_template_id"}
        assert client.post("/api/v1/employees", json=without_template, headers={"Authorization": "Bearer a"}).status_code == 201


def test_all_creation_modes_persist_template_and_customer_prompt(employee_database, monkeypatch):
    db, _, _ = employee_database
    monkeypatch.setattr(employee_endpoint, "get_settings", lambda: SimpleNamespace(effective_llm_provider="groq", effective_llm_model="openai/gpt-oss-20b"))
    client = client_for(db, "employee-user-a", monkeypatch)
    with client:
        for mode in ("chat", "prompt"):
            request = {**payload(), "name": f"{mode} employee", "creation_mode": mode, "language": "Telugu"}
            if mode == "prompt":
                request["direct_prompt"] = "Help callers understand our configured service and ask one question at a time."
            response = client.post("/api/v1/employees", json=request, headers={"Authorization": "Bearer a"})
            assert response.status_code == 201
            configuration = response.json()["configuration"]
            assert configuration["selected_template_id"] == "pontis_sales_v1"
            assert configuration["selected_template_version"] == 1
            assert configuration["language"] == "Telugu"
            if mode == "prompt":
                assert request["direct_prompt"] in configuration["direct_prompt"]


def test_tenant_can_create_and_read_own_draft(employee_database, monkeypatch):
    db, tenant_a, _ = employee_database
    monkeypatch.setattr(
        employee_endpoint,
        "get_settings",
        lambda: SimpleNamespace(effective_llm_provider="backend-provider", effective_llm_model="backend-model"),
    )
    client = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client:
            created = client.post("/api/v1/employees", json=payload(), headers={"Authorization": "Bearer a"})
            assert created.status_code == 201
            body = created.json()
            assert body["status"] == "draft"
            assert "llm_provider" not in body
            assert "llm_model" not in body
            assert body["language"] == "English"
            assert body["creation_mode"] == "chat"
            employee_id = body["id"]

            listed = client.get("/api/v1/employees", headers={"Authorization": "Bearer a"})
            assert listed.status_code == 200
            assert listed.json()[0]["id"] == employee_id
            assert listed.json()[0]["status"] == "draft"

            detail = client.get(f"/api/v1/employees/{employee_id}", headers={"Authorization": "Bearer a"})
            assert detail.status_code == 200
            assert detail.json()["configuration"]["purpose"] == payload()["purpose"]
            assert "llm_provider" not in detail.json()["configuration"]
            assert "llm_model" not in detail.json()["configuration"]
            assert detail.json()["id"] == employee_id
            employee = db.get(AIEmployee, UUID(employee_id))
            assert employee.tenant_id == tenant_a.id
            assert employee.llm_provider == "backend-provider"
            assert employee.llm_model == "backend-model"
            version = db.scalar(select(AIEmployeeVersion).where(AIEmployeeVersion.employee_id == employee.id))
            assert version.configuration["llm_provider"] == "backend-provider"
            assert version.configuration["llm_model"] == "backend-model"
    finally:
        app.dependency_overrides.clear()


def test_publish_repairs_missing_internal_configuration_from_backend_settings(employee_database, monkeypatch):
    db, tenant_a, _ = employee_database
    employee = AIEmployee(
        tenant_id=tenant_a.id,
        name="Legacy employee",
        purpose="Qualify leads",
        call_type="outbound",
        llm_provider="internal",
        llm_model="default",
        language="English",
        creation_mode="chat",
    )
    db.add(employee)
    db.flush()
    version = AIEmployeeVersion(
        tenant_id=tenant_a.id,
        employee_id=employee.id,
        version_number=1,
        status="draft",
        configuration={"name": employee.name, "purpose": employee.purpose, "language": employee.language},
    )
    db.add(version)
    db.commit()
    monkeypatch.setattr(
        employee_configuration_service,
        "get_settings",
        lambda: SimpleNamespace(effective_llm_provider="groq", effective_llm_model="backend-model"),
    )
    monkeypatch.setattr(
        employee_endpoint,
        "agent_service",
        SimpleNamespace(synchronize=lambda *_: SimpleNamespace(provider_id="agent-1", status="Completed", metadata={})),
    )
    client = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client:
            response = client.post(f"/api/v1/employees/{employee.id}/publish", headers={"Authorization": "Bearer a"})
            assert response.status_code == 200
            assert employee.llm_provider == "groq"
            assert employee.llm_model == "backend-model"
            assert version.configuration["llm_provider"] == "groq"
            assert version.configuration["llm_model"] == "backend-model"
    finally:
        app.dependency_overrides.clear()


def test_publish_preserves_valid_persisted_internal_configuration(employee_database, monkeypatch):
    db, tenant_a, _ = employee_database
    employee = AIEmployee(
        tenant_id=tenant_a.id,
        name="Configured employee",
        purpose="Support customers",
        call_type="inbound",
        llm_provider="persisted-provider",
        llm_model="persisted-model",
        language="English",
        creation_mode="chat",
    )
    db.add(employee)
    db.flush()
    version = AIEmployeeVersion(
        tenant_id=tenant_a.id,
        employee_id=employee.id,
        version_number=1,
        status="draft",
        configuration={
            "name": employee.name,
            "purpose": employee.purpose,
            "language": employee.language,
            "llm_provider": "persisted-provider",
            "llm_model": "persisted-model",
        },
    )
    db.add(version)
    db.commit()
    monkeypatch.setattr(
        employee_configuration_service,
        "get_settings",
        lambda: SimpleNamespace(effective_llm_provider="different-provider", effective_llm_model="different-model"),
    )
    monkeypatch.setattr(
        employee_endpoint,
        "agent_service",
        SimpleNamespace(synchronize=lambda *_: SimpleNamespace(provider_id="agent-2", status="Completed", metadata={})),
    )
    client = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client:
            assert client.post(f"/api/v1/employees/{employee.id}/publish", headers={"Authorization": "Bearer a"}).status_code == 200
            assert employee.llm_provider == "persisted-provider"
            assert employee.llm_model == "persisted-model"
    finally:
        app.dependency_overrides.clear()


def test_manual_telugu_roman_script_save_returns_review_warning(employee_database, monkeypatch):
    db, _, _ = employee_database
    monkeypatch.setattr(employee_endpoint, "get_settings", lambda: SimpleNamespace(effective_llm_provider="groq", effective_llm_model="openai/gpt-oss-20b"))
    client = client_for(db, "employee-user-a", monkeypatch)
    roman_script = {section: "Nenu KMG Insurance nundi maatladutunnanu. Mee policy renewal gurinchi call chesanu." for section in (
        "Identity & Purpose", "Greeting & Intro", "Qualification", "Handling Objections", "Call to Action", "Closing"
    )}
    try:
        with client:
            created = client.post("/api/v1/employees", json={**payload(), "language": "Telugu"}, headers={"Authorization": "Bearer a"})
            employee_id = created.json()["id"]
            response = client.patch(
                f"/api/v1/employees/{employee_id}",
                json={"configuration": {"language": "Telugu", "call_script": roman_script}},
                headers={"Authorization": "Bearer a"},
            )
            assert response.status_code == 200
            validation = response.json()["configuration"]["script_language_validation"]
            assert validation["valid"] is False
            assert any(issue["type"] == "romanized_language" for issue in validation["issues"])
    finally:
        app.dependency_overrides.clear()


def test_publish_upgrades_the_legacy_assembled_telugu_fallback(employee_database, monkeypatch):
    db, tenant_a, _ = employee_database
    employee = AIEmployee(
        tenant_id=tenant_a.id,
        name="Wedding Assistant",
        purpose="Call contacts about a wedding.",
        call_type="outbound",
        llm_provider="internal",
        llm_model="default",
        language="Telugu",
        creation_mode="chat",
    )
    db.add(employee)
    db.flush()
    legacy_card = "\n".join((
        "Purpose: Deliver the requested call objective using the saved user context.",
        "Instructions: Use only the saved user context and approved call rules. Do not invent missing details.",
        "Spoken example: నమస్కారం అండి, Vaibhav and Muskan gari behalf lo AI assistant గా wedding గురించి call చేస్తున్నాను.",
        "Handling: Use the configured guardrails, disclose that the caller is an AI assistant when asked, and stop respectfully if asked to stop.",
    ))
    version = AIEmployeeVersion(
        tenant_id=tenant_a.id,
        employee_id=employee.id,
        version_number=1,
        status="draft",
        configuration={
            "name": employee.name,
            "purpose": employee.purpose,
            "original_requirement": "call contacts about my wedding on 15th of jan 2027",
            "host_name": "Vaibhav and Muskan",
            "language": "Telugu",
            "call_type": "outbound",
            "llm_provider": "internal",
            "llm_model": "default",
            "script_source": "assembled",
            "call_script": {section: legacy_card for section in (
                "Identity & Purpose", "Greeting & Intro", "Qualification",
                "Handling Objections", "Call to Action", "Closing",
            )},
        },
    )
    db.add(version)
    db.commit()
    monkeypatch.setattr(
        employee_endpoint,
        "agent_service",
        SimpleNamespace(synchronize=lambda *_: SimpleNamespace(provider_id="wedding-agent", status="Completed", metadata={})),
    )
    client = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client:
            published = client.post(f"/api/v1/employees/{employee.id}/publish", headers={"Authorization": "Bearer a"})
        assert published.status_code == 200, published.json()
        assert published.json()["configuration"]["script_language_validation"]["valid"] is False
    finally:
        app.dependency_overrides.clear()


def test_browser_cannot_relabel_an_assembled_draft_as_reviewed(employee_database, monkeypatch):
    db, tenant_a, _ = employee_database
    employee = AIEmployee(
        tenant_id=tenant_a.id, name="Draft", purpose="Explain a request", call_type="outbound",
        llm_provider="groq", llm_model="test-model", language="English", creation_mode="chat",
    )
    db.add(employee)
    db.flush()
    from app.services.employee_interview import RealLLMService, render_call_script_sections
    config = {"name": "Draft", "purpose": "Explain a request", "original_requirement": "Explain a request", "language": "English", "call_type": "outbound", "llm_provider": "groq", "llm_model": "test-model", "script_source": "assembled"}
    config["call_script"] = render_call_script_sections(RealLLMService._assemble_script_sections(config, "English", "outbound", config))
    db.add(AIEmployeeVersion(tenant_id=tenant_a.id, employee_id=employee.id, version_number=1, status="draft", configuration=config))
    db.commit()
    monkeypatch.setattr(
        employee_endpoint,
        "agent_service",
        SimpleNamespace(synchronize=lambda *_: SimpleNamespace(provider_id="draft-agent", status="Completed", metadata={})),
    )
    client = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client:
            response = client.patch(f"/api/v1/employees/{employee.id}", json={"configuration": {"script_source": "reviewed", "script_review_required": False}}, headers={"Authorization": "Bearer a"})
            assert response.status_code == 200, response.json()
            assert response.json()["configuration"]["script_source"] == "assembled"
            published = client.post(f"/api/v1/employees/{employee.id}/publish", headers={"Authorization": "Bearer a"})
            assert published.status_code == 200
            assert published.json()["configuration"]["script_source"] == "assembled"
    finally:
        app.dependency_overrides.clear()


def test_publish_allows_invalid_hindi_script_with_warning(employee_database, monkeypatch):
    db, tenant_a, _ = employee_database
    employee = AIEmployee(
        tenant_id=tenant_a.id,
        name="Hindi employee",
        purpose="Renew policies",
        call_type="outbound",
        llm_provider="internal",
        llm_model="default",
        language="Hindi",
        creation_mode="chat",
    )
    db.add(employee)
    db.flush()
    roman_script = {section: "Namaste ji, main KMG Insurance se bol raha hoon. Aapki renewal ke baare mein call kiya hai." for section in (
        "Identity & Purpose", "Greeting & Intro", "Qualification", "Handling Objections", "Call to Action", "Closing"
    )}
    db.add(AIEmployeeVersion(
        tenant_id=tenant_a.id,
        employee_id=employee.id,
        version_number=1,
        status="draft",
        configuration={
            "name": employee.name,
            "purpose": employee.purpose,
            "language": "Hindi",
            "call_type": "outbound",
            "llm_provider": "internal",
            "llm_model": "default",
            "call_script": roman_script,
        },
    ))
    db.commit()
    called = {"provider": False}
    def synchronize(*_):
        called["provider"] = True
        return SimpleNamespace(provider_id="hindi-agent", status="Completed", metadata={})
    monkeypatch.setattr(employee_endpoint, "agent_service", SimpleNamespace(synchronize=synchronize))
    client = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client:
            response = client.post(f"/api/v1/employees/{employee.id}/publish", headers={"Authorization": "Bearer a"})
            assert response.status_code == 200
            assert response.json()["configuration"]["script_language_validation"]["valid"] is False
            assert called["provider"] is True
    finally:
        app.dependency_overrides.clear()


def test_employee_delete_is_tenant_scoped(employee_database, monkeypatch):
    db, _, _ = employee_database
    client_a = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client_a:
            employee_id = client_a.post("/api/v1/employees", json=payload(), headers={"Authorization": "Bearer a"}).json()["id"]
            assert client_a.delete(f"/api/v1/employees/{employee_id}", headers={"Authorization": "Bearer a"}).status_code == 204
            assert db.get(AIEmployee, UUID(employee_id)) is None
    finally:
        app.dependency_overrides.clear()

    client_a = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client_a:
            employee_id = client_a.post("/api/v1/employees", json=payload(), headers={"Authorization": "Bearer a"}).json()["id"]
    finally:
        app.dependency_overrides.clear()
    client_b = client_for(db, "employee-user-b", monkeypatch)
    try:
        with client_b:
            assert client_b.delete(f"/api/v1/employees/{employee_id}", headers={"Authorization": "Bearer b"}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_tenant_cannot_access_or_modify_other_tenant_employee(employee_database, monkeypatch):
    db, _, _ = employee_database
    client_a = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client_a:
            created = client_a.post("/api/v1/employees", json=payload(), headers={"Authorization": "Bearer a"})
        employee_id = created.json()["id"]
    finally:
        app.dependency_overrides.clear()


def test_employee_draft_can_publish_and_published_version_is_archived_on_new_publish(employee_database, monkeypatch):
    db, _, _ = employee_database
    monkeypatch.setattr(
        employee_endpoint,
        "agent_service",
        SimpleNamespace(synchronize=lambda employee, version: SimpleNamespace(
            provider_id="test-agent-1", status="Completed", metadata={"status": "Completed"}
        )),
    )
    client = client_for(db, "employee-user-a", monkeypatch)
    try:
        with client:
            headers = {"Authorization": "Bearer a"}
            created = client.post("/api/v1/employees", json=payload(), headers=headers)
            employee_id = created.json()["id"]

            published = client.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert published.status_code == 200
            assert published.json()["status"] == "published"
            assert published.json()["published_version"]["version_number"] == 1
            assert client.get(f"/api/v1/employees/{employee_id}/draft", headers=headers).status_code == 404
            assert client.get(f"/api/v1/employees/{employee_id}/published", headers=headers).json()["version_number"] == 1

            edited = client.patch(
                f"/api/v1/employees/{employee_id}",
                json={"purpose": "Updated qualification workflow"},
                headers=headers,
            )
            assert edited.status_code == 200
            assert edited.json()["status"] == "published"
            assert edited.json()["draft_version"]["version_number"] == 2
            assert edited.json()["purpose"] == "Updated qualification workflow"
            assert client.get(f"/api/v1/employees/{employee_id}/draft", headers=headers).json()["version_number"] == 2

            versions = client.get(f"/api/v1/employees/{employee_id}/versions", headers=headers).json()
            assert [(item["version_number"], item["status"]) for item in versions] == [(1, "published"), (2, "draft")]
            assert versions[0]["configuration"]["purpose"] == payload()["purpose"]

            published_again = client.post(f"/api/v1/employees/{employee_id}/publish", headers=headers)
            assert published_again.status_code == 200
            assert published_again.json()["published_version"]["version_number"] == 2

            versions = client.get(f"/api/v1/employees/{employee_id}/versions", headers=headers).json()
            assert [(item["version_number"], item["status"]) for item in versions] == [(1, "archived"), (2, "published")]
            assert db.scalar(select(AIEmployeeVersion).where(AIEmployeeVersion.version_number == 1)).status == VersionStatus.archived.value
    finally:
        app.dependency_overrides.clear()


def test_employee_with_tone_only_patch_still_publishes_from_existing_required_fields(employee_database, monkeypatch):
    db, _, _ = employee_database
    client = client_for(db, "employee-user-a", monkeypatch)
    class Provider:
        def create_agent(self, payload):
            from types import SimpleNamespace
            return SimpleNamespace(provider_id="provider-1", status="ready", metadata={})
        def update_agent(self, provider_id, payload):
            from types import SimpleNamespace
            return SimpleNamespace(provider_id=provider_id, status="ready", metadata={})
        def get_agent(self, provider_id):
            return {}
    from app.api.v1.endpoints import employees as employee_endpoint
    from app.services.omnidimension_agents import OmniDimensionAgentService
    monkeypatch.setattr(employee_endpoint, "agent_service", OmniDimensionAgentService(Provider()))
    try:
        with client:
            headers = {"Authorization": "Bearer a"}
            created = client.post("/api/v1/employees", json=payload(), headers=headers)
            employee_id = created.json()["id"]
            response = client.patch(
                f"/api/v1/employees/{employee_id}",
                json={"configuration": {"tone": "warm"}},
                headers=headers,
            )
            assert response.status_code == 200
            from uuid import UUID
            from app.models.ai_employee import AIEmployee
            employee = db.get(AIEmployee, UUID(employee_id))
            employee.versions[0].configuration = {"tone": "warm"}
            db.commit()
            assert client.post(f"/api/v1/employees/{employee_id}/publish", headers=headers).status_code == 200
    finally:
        app.dependency_overrides.clear()

    client_b = client_for(db, "employee-user-b", monkeypatch)
    try:
        with client_b:
            headers = {"Authorization": "Bearer b"}
            assert client_b.get(f"/api/v1/employees/{employee_id}", headers=headers).status_code == 404
            assert client_b.patch(
                f"/api/v1/employees/{employee_id}",
                json={"name": "Cross Tenant Attempt"},
                headers=headers,
            ).status_code == 404
            assert client_b.post(f"/api/v1/employees/{employee_id}/publish", headers=headers).status_code == 404
            assert client_b.get("/api/v1/employees", headers=headers).json() == []
    finally:
        app.dependency_overrides.clear()
