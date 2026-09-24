import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.services.business_research import ensure_business_research
from app.services.employee_interview import RealLLMService
from app.services.employee_prompt import compose_employee_configuration

REQUIREMENT = "You should invite my contact list for my wedding. Vaibhav and Muskan. 31st January 2027, Jalor, Rajasthan."


def llm_settings(**overrides):
    values = {
        "groq_api_key": "test-key", "groq_base_url": "https://api.groq.com/openai/v1",
        "groq_model": "openai/gpt-oss-20b", "groq_fallback_model": "llama-3.3-70b-versatile",
        "groq_api_key_2": None, "groq_base_url_2": None,
        "effective_llm_provider": "groq", "effective_llm_api_key": "test-key",
        "effective_llm_model": "openai/gpt-oss-20b", "effective_llm_base_url": "https://api.groq.com/openai/v1",
        "llm_timeout_seconds": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def response(language="English"):
    speech = {
        "English": "Hello, I am calling to invite you to Vaibhav and Muskan's wedding on 31st January 2027 in Jalor, Rajasthan.",
        "Telugu": "\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02 \u0c05\u0c02\u0c21\u0c3f, Vaibhav \u0c2e\u0c30\u0c3f\u0c2f\u0c41 Muskan wedding \u0c15\u0c3f invite \u0c1a\u0c47\u0c2f\u0c21\u0c3e\u0c28\u0c3f\u0c15\u0c3f call \u0c1a\u0c47\u0c36\u0c3e\u0c28\u0c41.",
        "Hindi": "\u0928\u092e\u0938\u094d\u0924\u0947 \u091c\u0940, Vaibhav \u0914\u0930 Muskan \u0915\u0940 wedding \u0915\u0947 \u0932\u093f\u090f invite \u0915\u0930\u0928\u0947 \u0915\u0947 \u0932\u093f\u090f call \u0915\u093f\u092f\u093e \u0939\u0948.",
    }[language]
    return {"sections": [{
        "title": f"Invitation Step {index}", "purpose": "Invite contacts to Vaibhav and Muskan's wedding",
        "instructions": "Use only the stated wedding facts and do not invent event details.",
        "questions": ["Can you confirm whether you can attend?"] if index == 3 else [],
        "examples": [speech], "handling": "Respect uncertainty or refusal and do not add unrelated qualification questions.",
    } for index in range(6)]}


def service_for(statuses=(200,), language="English"):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "temporary"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response(language), ensure_ascii=False)}}]})

    return RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler))), calls


def generate(service, language="English", config=None):
    config = config or {"original_requirement": REQUIREMENT, "language": language, "call_type": "outbound"}
    return service.generate_call_script(SimpleNamespace(name="Invite Assistant", purpose=REQUIREMENT, call_type="outbound", language=language), config)


def test_groq_primary_success_does_not_call_fallback():
    service, calls = service_for()
    assert len(generate(service)) == 6
    assert calls == ["openai/gpt-oss-20b"]


def test_primary_failure_calls_configured_groq_fallback_once():
    service, calls = service_for((503, 200))
    generate(service)
    assert calls == ["openai/gpt-oss-20b", "llama-3.3-70b-versatile"]


def test_both_groq_models_fail_with_generation_error():
    service, calls = service_for((503, 503))
    with pytest.raises(HTTPException) as error:
        generate(service)
    assert error.value.detail["failure_category"] == "llm_generation_failed"
    assert len(calls) == 2


def test_wedding_generation_is_context_first_and_persistable_shape():
    service, _ = service_for()
    config = {"original_requirement": REQUIREMENT, "purpose": REQUIREMENT, "language": "English", "call_type": "outbound"}
    script = generate(service, config=config)
    persisted = compose_employee_configuration({**config, "call_script": script, "conversation_sections": config["conversation_sections"]})
    text = json.dumps({"conversation_sections": persisted["conversation_sections"], "call_script": persisted["call_script"]}, ensure_ascii=False).casefold()
    assert len(config["conversation_sections"]) == 6 and len(persisted["call_script"]) == 6
    for forbidden in ("wedding planner", "guest count", "catering", "decoration", "budget", "venue booking", "postage"):
        assert forbidden not in text
    assert "vaibhav" in text and "muskan" in text and "31st january 2027" in text


@pytest.mark.parametrize("status", [400, 429, 500])
def test_research_skips_missing_company_and_failure_is_non_fatal(status):
    no_company = ensure_business_research({"original_requirement": REQUIREMENT})
    assert no_company["business_research"]["reason"] == "business_name_missing"
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "down"})))
    failed = ensure_business_research({"business_name": "ABC Motors", "original_requirement": "Call customers of ABC Motors and explain the new service."}, client)
    assert failed["business_research"]["status"] == "failed"
    service, _ = service_for()
    assert len(generate(service, config={**failed, "language": "English", "call_type": "outbound"})) == 6


def test_malformed_json_is_reported_as_schema_failure_after_bounded_attempts():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(HTTPException) as error:
        generate(service)
    assert error.value.detail["failure_category"] == "llm_schema_failure"
    assert calls == ["openai/gpt-oss-20b", "llama-3.3-70b-versatile"]


def test_invalid_primary_language_uses_bounded_fallback_correction():
    calls = []
    valid = response("Telugu")

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        payload = response("English") if len(calls) == 1 else valid
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "Telugu", "call_type": "outbound"}
    result = service.generate_call_script(SimpleNamespace(name="Invite Assistant", purpose=REQUIREMENT, call_type="outbound", language="Telugu"), config)
    assert len(result) == 6 and calls == ["openai/gpt-oss-20b", "llama-3.3-70b-versatile"]


@pytest.mark.parametrize("language,codepoint", [("Telugu", 0x0C00), ("Hindi", 0x0900)])
def test_regional_language_output_uses_native_script(language, codepoint):
    service, _ = service_for(language=language)
    result = generate(service, language=language)
    assert any(any(codepoint <= ord(char) < codepoint + 0x80 for char in value) for value in result.values())


def test_legacy_provider_configuration_cannot_select_employee_generation():
    service = RealLLMService(settings=llm_settings(groq_api_key=None, effective_llm_provider="legacy-provider", effective_llm_api_key="ignored", effective_llm_model="legacy-model"), client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    assert service._configured_llm_attempts() == []


def test_production_configuration_reports_missing_fallback_without_exposing_secrets():
    settings = Settings(GROQ_API_KEY="configured", GROQ_MODEL="openai/gpt-oss-20b", GROQ_BASE_URL="https://api.groq.com/openai/v1", GROQ_FALLBACK_MODEL="", ENVIRONMENT="development")
    assert settings.employee_llm_configuration_error == "Employee LLM configuration is incomplete: missing GROQ_FALLBACK_MODEL (or legacy GROQ_MODEL_2)."
    legacy = Settings(GROQ_API_KEY="configured", GROQ_MODEL="openai/gpt-oss-20b", GROQ_MODEL_2="legacy-fallback", ENVIRONMENT="development")
    assert legacy.employee_llm_configuration_error is None
