import json
from types import SimpleNamespace

import httpx

from app.services.employee_interview import RealLLMService


def test_script_prompt_fills_identity_placeholders_and_uses_script_model():
    seen = {}
    response = {
        "sections": [
            {"title": f"Step {i}", "purpose": "Purpose", "instructions": "Instructions", "questions": [], "examples": [f"hello line {i}"], "handling": "Handling"}
            for i in range(1, 7)
        ]
    }

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})

    settings = SimpleNamespace(
        effective_llm_provider="groq", effective_llm_api_key="test-key", effective_llm_model="default-model",
        effective_script_model="script-model", effective_llm_base_url="https://api.groq.com/openai/v1",
        groq_api_key="test-key", groq_model="default-model", groq_script_model="script-model",
        groq_fallback_model=None, groq_model_2=None, groq_api_key_2=None, groq_base_url="https://api.groq.com/openai/v1",
        groq_base_url_2=None, llm_timeout_seconds=5,
    )
    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    service.generate_call_script(
        SimpleNamespace(name="Unused Employee Name", purpose="Invite contacts", call_type="outbound", language="English"),
        {"purpose": "Invite contacts", "original_requirement": "Invite contacts", "language": "English", "call_type": "outbound", "host_name": "", "business_name": ""},
    )

    assert seen["model"] == "script-model"
    assert seen["response_format"]["type"] == "json_schema"
    schema = seen["response_format"]["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["sections"]["items"]["required"]) == {"title", "purpose", "instructions", "questions", "examples", "handling"}
    assert "{{" not in seen["messages"][0]["content"]
    assert "Unused Employee Name" not in seen["messages"][0]["content"]


def test_empty_host_does_not_promote_business_name_to_host():
    seen = {}
    response = {
        "sections": [
            {"title": f"Step {i}", "purpose": "Purpose", "instructions": "Instructions", "questions": [], "examples": [f"hello line {i}"], "handling": "Handling"}
            for i in range(1, 7)
        ]
    }

    def handler(request: httpx.Request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})

    settings = SimpleNamespace(
        effective_llm_provider="groq", effective_llm_api_key="test-key", effective_llm_model="default-model",
        effective_script_model="script-model", effective_llm_base_url="https://api.groq.com/openai/v1",
        groq_api_key="test-key", groq_model="default-model", groq_script_model="script-model",
        groq_fallback_model=None, groq_model_2=None, groq_api_key_2=None, groq_base_url="https://api.groq.com/openai/v1",
        groq_base_url_2=None, llm_timeout_seconds=5,
    )
    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    service.generate_call_script(
        SimpleNamespace(name="Employee Name", purpose="Invite contacts", call_type="outbound", language="English"),
        {"purpose": "Invite contacts", "original_requirement": "Invite contacts", "language": "English", "call_type": "outbound", "host_name": "", "business_name": "Acme"},
    )

    system = seen["messages"][0]["content"]
    assert "HOST_NAME: Acme" not in system
    assert "HOST_NAME:" in system
    assert "BUSINESS_NAME: Acme" in system
