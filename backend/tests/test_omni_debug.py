from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.v1.endpoints.debug import (
    _call_id_from_record,
    _diagnostic_response,
    _find_request_record,
    _detail_record,
)


class FakeProvider:
    def __init__(self):
        self.list_calls = []
        self.detail_calls = []

    def list_call_logs(self, **kwargs):
        self.list_calls.append(kwargs)
        return {"call_log_data": [{
            "id": 90001,
            "agent_id": 255115,
            "call_request_id": {"id": 7554657},
            "call_status": "completed",
        }]}

    def get_call_log(self, call_id):
        self.detail_calls.append(call_id)
        return {"call_log_data": [{
            "id": call_id,
            "agent_id": 255115,
            "call_request_id": {"id": 7554657},
            "call_status": "completed",
            "call_duration_in_seconds": 19,
            "hangup_source": "provider",
            "hangup_reason": "silence",
            "call_conversation": "user: hello 9876543210\nLLM: Hi",
            "issues": ["sample issue"],
            "interactions": [{
                "interaction_sequence": 1,
                "user_query": "hello",
                "bot_response": "Hi",
                "tts_time": 0.3,
            }],
            "user_email": "caller@example.com",
            "call_sid": "secret-sid",
        }]}


def test_request_id_maps_to_provider_call_id_and_fetches_detail():
    provider = FakeProvider()
    summary = _find_request_record(provider, "7554657")
    assert summary["id"] == 90001
    detail = _detail_record(provider, summary)
    assert provider.detail_calls == ["90001"]
    assert _call_id_from_record(detail) == "90001"


def test_diagnostic_response_reports_speech_llm_and_tts_without_secrets_or_pii():
    provider = FakeProvider()
    summary = _find_request_record(provider, "7554657")
    detail = _detail_record(provider, summary)
    result = _diagnostic_response("7554657", summary, detail, detail_available=True)
    assert result["request_id"] == "7554657"
    assert result["provider_call_id"] == "90001"
    assert result["caller_speech_detected"] is True
    assert result["llm_response_after_caller_speech"] is True
    assert result["tts_audio_detected"] is True
    serialized = str(result)
    assert "9876543210" not in serialized
    assert "caller@example.com" not in serialized
    assert "secret-sid" not in serialized
    assert "authorization" not in serialized.casefold()
    assert "api_key" not in serialized.casefold()


def test_missing_call_id_skips_detail_lookup():
    provider = SimpleNamespace(get_call_log=lambda _: (_ for _ in ()).throw(AssertionError("must not call")))
    assert _detail_record(provider, {"call_request_id": {"id": 7554657}}) is None


def test_debug_endpoint_requires_authentication():
    from app.main import app

    response = TestClient(app).get("/api/v1/debug/omni-call-by-request/7554657")
    assert response.status_code == 401
