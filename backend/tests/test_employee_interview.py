import json
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_db
from app.api.v1.endpoints import employee_interview as interview_endpoint
from app.main import app
from app.models import AIEmployee, Tenant, User
from app.services import auth as auth_service
from app.services.employee_interview import (
    DevelopmentLLMService,
    EmployeeInterviewService,
    InterviewGeneration,
    InterviewLLMResponse,
    LLMService,
    RealLLMService,
    build_default_llm_service,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def interview_database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    from app.db.base import Base
    Base.metadata.create_all(engine)
    # Add consumed_questions column (mirrors init_db migration for in-memory DB)
    from sqlalchemy import text
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(employee_interview_sessions)"))}
        if "consumed_questions" not in cols:
            conn.execute(text("ALTER TABLE employee_interview_sessions ADD COLUMN consumed_questions JSON NOT NULL DEFAULT '[]'"))
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant_a = Tenant(name="Interview A", slug="interview-a")
    tenant_b = Tenant(name="Interview B", slug="interview-b")
    db.add_all([tenant_a, tenant_b])
    db.flush()
    db.add_all([
        User(tenant_id=tenant_a.id, password_hash=auth_service.hash_password("password123"), email="a@interview.test", status="active"),
        User(tenant_id=tenant_b.id, password_hash=auth_service.hash_password("password123"), email="b@interview.test", status="active"),
    ])
    db.commit()
    try:
        yield db, tenant_a, tenant_b
    finally:
        db.close()


def employee_payload():
    return {
        "name": "Interview Assistant",
        "purpose": "Help customers choose products.",
        "call_type": "both",
        "llm_provider": "OpenAI",
        "llm_model": "gpt-4o-mini",
        "language": "English",
        "creation_mode": "chat",
        "selected_template_id": "pontis_sales_v1",
        "selected_template_version": 1,
        "template_values": {"business_name": "Test Business", "product_or_service": "Test service", "target_customer": "Test customers", "service_area": "Hyderabad", "lead_qualification_questions": "Need", "sales_team_contact": "100", "working_hours": "9-5"},
    }


class FakeInterviewLLMService(LLMService):
    def __init__(self):
        self.initial_calls: list[dict] = []
        self.next_calls: list[dict] = []

    def initial_question(self, employee):
        self.initial_calls.append({"name": employee.name, "purpose": employee.purpose})
        return InterviewGeneration(
            assistant_message="Let's shape the employee together.",
            suggested_next_question="What is the main goal for this employee?",
            progress=0,
            configuration_updates={},
            missing_topics=["goals"],
            is_complete=False,
            suggestions=[
                {"question": "Who are the target customers?", "reason": "Shapes the tone."},
                {"question": "What tone should it use?", "reason": "Affects all interactions."},
                {"question": "When should it transfer to a human?", "reason": "Prevents mishandling."},
            ],
        )

    def next_question(self, employee, questions, answers, configuration):
        self.next_calls.append({
            "name": employee.name,
            "questions": list(questions),
            "answers": list(answers),
            "configuration": dict(configuration),
        })
        latest_answer = (answers[-1].get("answer") or "").lower()
        if "support" in latest_answer:
            return InterviewGeneration(
                assistant_message="Got it, I'll focus the draft on support.",
                suggested_next_question="Which support issues should it handle first?",
                progress=45,
                configuration_updates={"goals": [answers[-1]["answer"]], "target_customers": ["customers needing support"]},
                missing_topics=["tone", "transfer_rules"],
                is_complete=False,
                suggestions=[
                    {"question": "What tone should it use?", "reason": "Affects all interactions."},
                    {"question": "When should it transfer to a human?", "reason": "Prevents mishandling."},
                ],
            )
        return InterviewGeneration(
            assistant_message="That is enough detail to prepare the draft.",
            suggested_next_question=None,
            progress=100,
            configuration_updates={"goals": [answers[0]["answer"]], "tone": {"description": "friendly and concise"}},
            missing_topics=[],
            is_complete=True,
            suggestions=[],
        )


def authenticated_client(db, user_index, monkeypatch):
    monkeypatch.setattr(auth_service, "decode_access_token", lambda token: db.query(User).all()[user_index].id)
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def create_employee(client):
    response = client.post("/api/v1/employees", json=employee_payload(), headers={"Authorization": "Bearer token"})
    assert response.status_code == 201
    return response.json()["id"]


def install_interview_service(monkeypatch, service: LLMService):
    monkeypatch.setattr(interview_endpoint, "interview_service", EmployeeInterviewService(service))


# ---------------------------------------------------------------------------
# Part A — Groq env variable mapping
# ---------------------------------------------------------------------------

def test_groq_env_vars_map_to_real_llm_service(monkeypatch):
    """GROQ_API_KEY / GROQ_MODEL / GROQ_BASE_URL must activate RealLLMService."""
    from app.core.config import Settings
    import app.services.employee_interview as svc_module
    monkeypatch.setattr(svc_module, "get_settings", lambda: Settings(
        GROQ_API_KEY="gsk_test",
        GROQ_MODEL="llama-3.3-70b-versatile",
        GROQ_BASE_URL="https://api.groq.com/openai/v1",
    ))
    svc = build_default_llm_service()
    assert isinstance(svc, RealLLMService)


def test_missing_llm_credentials_use_development_service(monkeypatch):
    import app.services.employee_interview as svc_module
    # Provide a settings object with no LLM credentials at all
    from types import SimpleNamespace
    empty = SimpleNamespace(
        effective_llm_provider=None,
        effective_llm_api_key=None,
        effective_llm_model=None,
    )
    monkeypatch.setattr(svc_module, "get_settings", lambda: empty)
    svc = build_default_llm_service()
    assert isinstance(svc, DevelopmentLLMService)


def test_real_llm_uses_groq_base_url_in_request():
    settings = SimpleNamespace(
        effective_llm_provider="openai",
        effective_llm_api_key="gsk_test",
        effective_llm_model="llama-3.3-70b-versatile",
        effective_llm_base_url="https://api.groq.com/openai/v1",
        llm_timeout_seconds=5.0,
    )
    seen_urls: list[str] = []

    def handler(request: httpx.Request):
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "assistant_message": "Hi", "next_question": "What is the goal?",
            "configuration_updates": {}, "missing_topics": ["goals"],
            "progress": 0, "is_complete": False, "ready_to_build": False, "suggestions": [],
        })}}]})

    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    employee = SimpleNamespace(name="Demo", purpose="Book appointments", call_type="both", language="en-US", llm_provider="OpenAI", llm_model="gpt-4o-mini")
    service.initial_question(employee)
    assert "groq.com" in seen_urls[0]


# ---------------------------------------------------------------------------
# Part A — No silent fallback
# ---------------------------------------------------------------------------

def test_real_llm_service_error_propagates_without_fallback():
    """When RealLLMService raises, EmployeeInterviewService must NOT silently fall back."""
    settings = SimpleNamespace(
        effective_llm_provider="openai",
        effective_llm_api_key="key",
        effective_llm_model="model",
        effective_llm_base_url="https://example.invalid/v1",
        llm_timeout_seconds=1.0,
    )

    def handler(_: httpx.Request):
        return httpx.Response(500, json={"error": "boom"})

    real_svc = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    interview_svc = EmployeeInterviewService(real_svc)
    employee = SimpleNamespace(name="X", purpose="Y", call_type="both", language="en-US", llm_provider="OpenAI", llm_model="m")
    with pytest.raises(HTTPException) as exc_info:
        interview_svc._next_generation(employee, ["q"], [{"answer": "a"}], {})
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# Part A — Accumulated brief sent to LLM
# ---------------------------------------------------------------------------

def test_real_llm_receives_accumulated_context_and_returns_structured_suggestions():
    settings = SimpleNamespace(
        effective_llm_provider="openai",
        effective_llm_api_key="test-key",
        effective_llm_model="gpt-4o-mini",
        effective_llm_base_url="https://example.invalid/v1",
        llm_timeout_seconds=5.0,
    )
    seen_prompts: list[str] = []

    def handler(request: httpx.Request):
        prompt = json.loads(request.content)["messages"][1]["content"]
        seen_prompts.append(prompt)
        question = (
            "What treatment details should the employee collect before booking?"
            if "dental" in prompt
            else "What qualification details should it collect before booking a demo?"
        )
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "assistant_message": "I have a few focused details to clarify.",
            "next_question": question,
            "configuration_updates": {},
            "missing_topics": ["workflow"],
            "progress": 35,
            "is_complete": False,
            "ready_to_build": False,
            "suggestions": [{"question": question, "reason": "It changes the employee workflow."}],
        })}}]})

    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    employee = SimpleNamespace(name="Demo", purpose="Book appointments", call_type="both", language="en-US", llm_provider="OpenAI", llm_model="gpt-4o-mini")

    # Dental brief
    gen = service.next_question(employee, ["What is this employee for?"], [{"answer": "Call dental leads and book appointments."}], {})
    assert "Call dental leads and book appointments." in seen_prompts[0]
    assert gen.suggestions[0]["question"] == "What treatment details should the employee collect before booking?"

    # Real-estate brief — different suggestions
    gen2 = service.next_question(employee, ["What is this employee for?"], [{"answer": "Qualify real-estate leads for demos."}], {})
    assert "Qualify real-estate leads for demos." in seen_prompts[1]
    assert gen2.suggestions[0]["question"] != gen.suggestions[0]["question"]


# ---------------------------------------------------------------------------
# Part A — llm_used flag
# ---------------------------------------------------------------------------

def test_llm_used_true_when_real_service(interview_database, monkeypatch):
    db, _, _ = interview_database
    fake = FakeInterviewLLMService()
    install_interview_service(monkeypatch, fake)
    # Patch the service to be a RealLLMService instance for llm_used check
    real_settings = SimpleNamespace(
        effective_llm_provider="openai", effective_llm_api_key="k",
        effective_llm_model="m", effective_llm_base_url="https://x.invalid/v1",
        llm_timeout_seconds=5.0,
    )
    real_svc = RealLLMService(settings=real_settings)
    svc = EmployeeInterviewService(real_svc)
    assert svc._using_real_llm is True


def test_llm_used_false_when_dev_service():
    svc = EmployeeInterviewService(DevelopmentLLMService())
    assert svc._using_real_llm is False


# ---------------------------------------------------------------------------
# Part B — Contextual suggestions differ by brief
# ---------------------------------------------------------------------------

def test_different_briefs_produce_different_suggestions():
    settings = SimpleNamespace(
        effective_llm_provider="openai",
        effective_llm_api_key="key",
        effective_llm_model="model",
        effective_llm_base_url="https://example.invalid/v1",
        llm_timeout_seconds=5.0,
    )

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"]
        if "dental" in prompt:
            suggestions = [
                {"question": "Which dental treatments should it ask about?", "reason": "Determines booking flow."},
                {"question": "Should it offer specific appointment slots?", "reason": "Affects scheduling logic."},
            ]
        else:
            suggestions = [
                {"question": "What property types should it qualify?", "reason": "Shapes lead scoring."},
                {"question": "What budget range qualifies a lead?", "reason": "Filters unqualified leads."},
            ]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "assistant_message": "Got it.",
            "next_question": suggestions[0]["question"],
            "configuration_updates": {},
            "missing_topics": ["details"],
            "progress": 30,
            "is_complete": False,
            "ready_to_build": False,
            "suggestions": suggestions,
        })}}]})

    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    employee = SimpleNamespace(name="E", purpose="p", call_type="both", language="en-US", llm_provider="OpenAI", llm_model="m")

    dental = service.next_question(employee, ["q"], [{"answer": "Book dental appointments."}], {})
    realestate = service.next_question(employee, ["q"], [{"answer": "Qualify real-estate leads."}], {})

    dental_qs = {s["question"] for s in dental.suggestions}
    realestate_qs = {s["question"] for s in realestate.suggestions}
    assert dental_qs != realestate_qs


# ---------------------------------------------------------------------------
# Part C — Consumed questions
# ---------------------------------------------------------------------------

def test_consume_suggestion_removes_from_visible_list(interview_database, monkeypatch):
    db, _, _ = interview_database
    fake = FakeInterviewLLMService()
    install_interview_service(monkeypatch, fake)
    client = authenticated_client(db, 0, monkeypatch)
    try:
        with client:
            eid = create_employee(client)
            headers = {"Authorization": "Bearer a"}
            client.post(f"/api/v1/employees/{eid}/interview/start", json={}, headers=headers)

            # Manually seed suggestions into the session
            from app.models.employee_interview_session import EmployeeInterviewSession
            session = db.scalar(select(EmployeeInterviewSession).where(EmployeeInterviewSession.employee_id == UUID(eid)))
            session.suggested_questions = [
                {"question": "Who are the target customers?", "reason": "Shapes tone."},
                {"question": "What tone should it use?", "reason": "Affects interactions."},
            ]
            db.commit()

            resp = client.post(
                f"/api/v1/employees/{eid}/interview/consume",
                json={"question": "Who are the target customers?"},
                headers=headers,
            )
            assert resp.status_code == 200
            state = resp.json()
            questions = [s["question"] for s in state["suggested_questions"]]
            assert "Who are the target customers?" not in questions
            assert "What tone should it use?" in questions
            assert "Who are the target customers?" in state["consumed_questions"]
    finally:
        app.dependency_overrides.clear()


def test_consumed_questions_do_not_return_after_next_answer(interview_database, monkeypatch):
    db, _, _ = interview_database
    fake = FakeInterviewLLMService()
    install_interview_service(monkeypatch, fake)
    client = authenticated_client(db, 0, monkeypatch)
    try:
        with client:
            eid = create_employee(client)
            headers = {"Authorization": "Bearer a"}
            start = client.post(f"/api/v1/employees/{eid}/interview/start", json={}, headers=headers).json()

            # Consume "Who are the target customers?"
            client.post(
                f"/api/v1/employees/{eid}/interview/consume",
                json={"question": "Who are the target customers?"},
                headers=headers,
            )

            # Submit an answer — fake service returns suggestions including "Who are the target customers?"
            # but it must be filtered out because it is consumed
            answered = client.post(
                f"/api/v1/employees/{eid}/interview/answer",
                json={"answer": "Support customers with account issues.", "question": start["current_question"]},
                headers=headers,
            ).json()

            returned_questions = [s["question"] for s in answered["suggested_questions"]]
            assert "Who are the target customers?" not in returned_questions
    finally:
        app.dependency_overrides.clear()


def test_question_already_in_brief_is_filtered(interview_database, monkeypatch):
    db, _, _ = interview_database

    class BriefAwareFake(LLMService):
        def initial_question(self, employee):
            return InterviewGeneration(
                assistant_message="Hi", suggested_next_question="What is the goal?",
                progress=0, configuration_updates={}, missing_topics=["goals"], is_complete=False,
            )
        def next_question(self, employee, questions, answers, configuration):
            return InterviewGeneration(
                assistant_message="Got it.",
                suggested_next_question="Next question?",
                progress=30,
                configuration_updates={},
                missing_topics=["tone"],
                is_complete=False,
                suggestions=[
                    {"question": "What tone should it use?", "reason": "Affects interactions."},
                    # This question starts with text already in the brief
                    {"question": "Call dental leads and book appointments", "reason": "Already in brief."},
                ],
            )

    install_interview_service(monkeypatch, BriefAwareFake())
    client = authenticated_client(db, 0, monkeypatch)
    try:
        with client:
            eid = create_employee(client)
            headers = {"Authorization": "Bearer a"}
            client.post(f"/api/v1/employees/{eid}/interview/start", json={}, headers=headers)
            answered = client.post(
                f"/api/v1/employees/{eid}/interview/answer",
                json={"answer": "Call dental leads and book appointments for our clinic.", "question": "What is the goal?"},
                headers=headers,
            ).json()
            returned_questions = [s["question"] for s in answered["suggested_questions"]]
            # The suggestion whose text appears in the brief should be filtered
            assert "Call dental leads and book appointments" not in returned_questions
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Part C — Deduplication normalization
# ---------------------------------------------------------------------------

def test_normalize_strips_punctuation_and_case():
    norm = EmployeeInterviewService._normalize
    assert norm("Who are the customers?") == norm("who are the customers")
    assert norm("What's the goal?") == norm("whats the goal")


def test_filter_suggestions_removes_consumed():
    consumed = ["Who are the target customers?"]
    suggestions = [
        {"question": "Who are the target customers?", "reason": "r1"},
        {"question": "What tone should it use?", "reason": "r2"},
    ]
    result = EmployeeInterviewService._filter_suggestions(suggestions, consumed, "")
    assert len(result) == 1
    assert result[0]["question"] == "What tone should it use?"


# ---------------------------------------------------------------------------
# Part K — Completion behavior
# ---------------------------------------------------------------------------

def test_ready_to_build_returns_no_suggestions_and_correct_message(interview_database, monkeypatch):
    db, _, _ = interview_database
    fake = FakeInterviewLLMService()
    install_interview_service(monkeypatch, fake)
    client = authenticated_client(db, 0, monkeypatch)
    try:
        with client:
            eid = create_employee(client)
            headers = {"Authorization": "Bearer a"}
            state = client.post(f"/api/v1/employees/{eid}/interview/start", json={}, headers=headers).json()
            # Two answers drives FakeInterviewLLMService to is_complete=True
            state = client.post(
                f"/api/v1/employees/{eid}/interview/answer",
                json={"answer": "Support customers with onboarding.", "question": state["current_question"]},
                headers=headers,
            ).json()
            state = client.post(
                f"/api/v1/employees/{eid}/interview/answer",
                json={"answer": "Handle billing issues too.", "question": state["current_question"]},
                headers=headers,
            ).json()
            assert state["is_complete"] is True
            assert state["progress"] == 100
            assert state["suggested_questions"] == []
            assert state["messages"][-1]["content"] == "Do you have anything more you'd like me to know?"
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Existing tests (preserved)
# ---------------------------------------------------------------------------

def test_llm_service_abstraction_works():
    service = DevelopmentLLMService()
    employee = SimpleNamespace(
        name="Demo", purpose="Qualify leads for demos", call_type="both",
        language="en-US", llm_provider="OpenAI", llm_model="gpt-4o-mini",
    )
    initial = service.initial_question(employee)
    assert initial.suggested_next_question == "What is this employee for?"
    assert initial.is_complete is False
    follow_up = service.next_question(employee, [initial.suggested_next_question], [{"answer": "Help them book demos."}], {})
    assert follow_up.configuration_updates
    assert isinstance(follow_up, InterviewGeneration)
    assert 3 <= len(follow_up.suggestions) <= 5
    assert all(set(item) == {"question", "reason"} for item in follow_up.suggestions)


def test_unauthenticated_interview_request_is_rejected():
    with TestClient(app) as client:
        response = client.post("/api/v1/employees/00000000-0000-0000-0000-000000000000/interview/start", json={})
    assert response.status_code == 401


def test_interview_uses_configured_llm_service_and_passes_history(interview_database, monkeypatch):
    db, tenant_a, _ = interview_database
    fake_service = FakeInterviewLLMService()
    install_interview_service(monkeypatch, fake_service)
    client_a = authenticated_client(db, 0, monkeypatch)
    try:
        with client_a:
            employee_id = create_employee(client_a)
            started = client_a.post(f"/api/v1/employees/{employee_id}/interview/start", json={}, headers={"Authorization": "Bearer a"})
            assert started.status_code == 200
            assert started.json()["progress"] == 0
            assert started.json()["suggested_next_question"] == "What is the main goal for this employee?"
            assert fake_service.initial_calls[0]["name"] == "Interview Assistant"

            answered = client_a.post(
                f"/api/v1/employees/{employee_id}/interview/answer",
                json={"answer": "Support customers with account issues."},
                headers={"Authorization": "Bearer a"},
            )
            assert answered.status_code == 200
            state = answered.json()
            assert state["answers"][-1]["answer"] == "Support customers with account issues."
            assert state["extracted_configuration"]["goals"] == ["Support customers with account issues."]

            recorded_call = fake_service.next_calls[0]
            assert recorded_call["answers"][0]["answer"] == "Support customers with account issues."

            employee = db.scalar(select(AIEmployee).where(AIEmployee.id == UUID(employee_id)))
            assert employee.tenant_id == tenant_a.id
    finally:
        app.dependency_overrides.clear()


def test_other_tenant_cannot_access_interview(interview_database, monkeypatch):
    db, _, _ = interview_database
    fake_service = FakeInterviewLLMService()
    install_interview_service(monkeypatch, fake_service)
    client_a = authenticated_client(db, 0, monkeypatch)
    try:
        with client_a:
            employee_id = create_employee(client_a)
    finally:
        app.dependency_overrides.clear()

    client_b = authenticated_client(db, 1, monkeypatch)
    try:
        with client_b:
            headers = {"Authorization": "Bearer b"}
            assert client_b.post(f"/api/v1/employees/{employee_id}/interview/start", json={}, headers=headers).status_code == 404
            assert client_b.get(f"/api/v1/employees/{employee_id}/interview", headers=headers).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_structured_response_validation_and_recovery():
    settings = SimpleNamespace(
        effective_llm_provider="openai", effective_llm_api_key="test-key",
        effective_llm_model="gpt-4o-mini", effective_llm_base_url="https://example.invalid/v1",
        llm_timeout_seconds=5.0,
    )

    def handler(request: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content":
            '```json\n{"assistant_message":"Intro","next_question":"What is your goal?","configuration_updates":{},"missing_topics":["goals"],"progress":0,"is_complete":false}\n```'
        }}]})

    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    employee = SimpleNamespace(name="Demo", purpose="Qualify leads", call_type="both", language="en-US", llm_provider="OpenAI", llm_model="gpt-4o-mini")
    generation = service.initial_question(employee)
    assert generation.suggested_next_question == "What is this employee for?"
    assert generation.missing_topics == ["goals"]


def test_malformed_llm_response_is_handled():
    settings = SimpleNamespace(
        effective_llm_provider="openai", effective_llm_api_key="test-key",
        effective_llm_model="gpt-4o-mini", effective_llm_base_url="https://example.invalid/v1",
        llm_timeout_seconds=5.0,
    )

    def handler(_: httpx.Request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "this is not json"}}]})

    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    employee = SimpleNamespace(name="Demo", purpose="Qualify leads", call_type="both", language="en-US", llm_provider="OpenAI", llm_model="gpt-4o-mini")
    with pytest.raises(HTTPException) as excinfo:
        service.initial_question(employee)
    assert excinfo.value.status_code == 502


def test_provider_api_failure_is_handled():
    settings = SimpleNamespace(
        effective_llm_provider="openai", effective_llm_api_key="test-key",
        effective_llm_model="gpt-4o-mini", effective_llm_base_url="https://example.invalid/v1",
        llm_timeout_seconds=5.0,
    )

    def handler(_: httpx.Request):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    employee = SimpleNamespace(name="Demo", purpose="Qualify leads", call_type="both", language="en-US", llm_provider="OpenAI", llm_model="gpt-4o-mini")
    with pytest.raises(HTTPException) as excinfo:
        service.initial_question(employee)
    assert excinfo.value.status_code == 503
