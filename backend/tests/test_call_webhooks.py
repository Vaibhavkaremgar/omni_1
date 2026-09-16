from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.models import Call
from app.models.enums import CallDirection, CallStatus
from app.services import auth as auth_service

from test_instant_calls import authenticated_client, call_database, create_employee, create_number


def create_local_call(db, tenant, employee, phone, provider_id="3166940"):
    call = Call(
        tenant_id=tenant.id,
        employee_id=employee.id,
        phone_number_id=phone.id,
        direction=CallDirection.outbound.value,
        status=CallStatus.queued.value,
        customer_phone_number="+15551234567",
        provider_call_id=provider_id,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def webhook_payload(call):
    return {
        "id": 50958,
        "call_status": "completed",
        "call_duration": "1:23",
        "recording_url": "https://provider.test/recording/1",
        "call_conversation": "user: Hello\nLLM: Hi",
        "sentiment_score": "Positive",
        "sentiment_analysis_details": "Helpful conversation",
        "extracted_variables": {"interest": "enterprise"},
        "metadata": {"local_call_id": str(call.id), "tenant_id": str(call.tenant_id)},
    }


def test_post_call_webhook_updates_existing_call_and_is_idempotent(call_database):
    db, tenant_a, _ = call_database
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    call = create_local_call(db, tenant_a, employee, phone)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as api:
            first = api.post("/api/v1/webhooks/omnidimension/post-call", json=webhook_payload(call))
            second = api.post("/api/v1/webhooks/omnidimension/post-call", json=webhook_payload(call))
    finally:
        app.dependency_overrides.clear()
    assert first.status_code == 200
    assert second.status_code == 200
    db.refresh(call)
    assert call.status == "completed"
    assert call.duration_seconds == 83
    assert call.summary is None
    assert call.transcript == "user: Hello\nLLM: Hi"
    assert call.extracted_attributes == {"interest": "enterprise"}
    assert call.raw_payload["call_status"] == "completed"


def test_post_call_webhook_provider_id_fallback_and_tenant_mismatch_are_safe(call_database):
    db, tenant_a, tenant_b = call_database
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    call = create_local_call(db, tenant_a, employee, phone, provider_id="provider-42")
    payload = webhook_payload(call)
    payload.pop("metadata")
    payload["id"] = "provider-42"
    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).post("/api/v1/webhooks/omnidimension/post-call", json=payload)
        assert response.json()["status"] == "processed"
        payload["metadata"] = {"local_call_id": str(call.id), "tenant_id": str(tenant_b.id)}
        response = TestClient(app).post("/api/v1/webhooks/omnidimension/post-call", json=payload)
        assert response.json()["status"] == "ignored"
    finally:
        app.dependency_overrides.clear()
    db.refresh(call)
    assert call.status == "completed"


def test_post_call_webhook_rejects_malformed_json_and_unknown_events():
    with TestClient(app) as api:
        assert api.post("/api/v1/webhooks/omnidimension/post-call", content="not-json").status_code == 400


def test_calls_read_endpoints_are_tenant_scoped(call_database, monkeypatch):
    db, tenant_a, tenant_b = call_database
    employee_a = create_employee(db, tenant_a)
    phone_a = create_number(db, tenant_a)
    call_a = create_local_call(db, tenant_a, employee_a, phone_a)
    employee_b = create_employee(db, tenant_b)
    phone_b = create_number(db, tenant_b, provider_id="78")
    call_b = create_local_call(db, tenant_b, employee_b, phone_b, provider_id="3166941")
    api = authenticated_client(db, "call-user-a", monkeypatch)
    try:
        with api:
            response = api.get("/api/v1/calls", headers={"Authorization": "Bearer a"})
            assert response.status_code == 200
            assert [item["id"] for item in response.json()] == [str(call_a.id)]
            assert api.get(f"/api/v1/calls/{call_b.id}", headers={"Authorization": "Bearer a"}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_post_call_preserves_turn_order_and_accepts_late_analysis(call_database):
    db, tenant_a, _ = call_database
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    call = create_local_call(db, tenant_a, employee, phone, provider_id="late-analysis-call")
    payload = {
        "call_id": "late-analysis-call",
        "call_status": "completed",
        "interactions": [
            {"speaker": "assistant", "text": "Hello"},
            {"speaker": "customer", "text": "I need help"},
        ],
        "analysis": {
            "summary": "Customer requested help.",
            "customer_intent": "Support",
            "key_points": ["Needs assistance"],
            "outcome": "Follow up",
        },
    }
    from app.services.call_results import CallResultService
    CallResultService().process_post_call(db, payload)
    db.refresh(call)
    assert call.transcript_data == [
        {"speaker": "assistant", "text": "Hello"},
        {"speaker": "customer", "text": "I need help"},
    ]
    assert call.summary == "Customer requested help."
    assert call.customer_intent == "Support"
    assert call.key_points == ["Needs assistance"]
    assert call.outcome == "Follow up"
