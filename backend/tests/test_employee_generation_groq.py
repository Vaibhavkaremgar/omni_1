import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.services.business_research import ensure_business_research
from app.services.employee_interview import RealLLMService, strict_script_response_schema
from app.services.employee_prompt import compose_employee_configuration, normalize_business_identity
from app.services.script_validation import validate_script

REQUIREMENT = "You should invite my contact list for my wedding. Vaibhav and Muskan. 31st January 2027, Jalor, Rajasthan."


def llm_settings(**overrides):
    values = {
        "groq_api_key": "test-key", "groq_base_url": "https://api.groq.com/openai/v1",
        "groq_model": "openai/gpt-oss-20b", "groq_script_model": "openai/gpt-oss-120b", "groq_fallback_model": "llama-3.3-70b-versatile",
        "groq_api_key_2": None, "groq_base_url_2": None,
        "effective_llm_provider": "groq", "effective_llm_api_key": "test-key",
        "effective_llm_model": "openai/gpt-oss-20b", "effective_llm_base_url": "https://api.groq.com/openai/v1",
        "llm_timeout_seconds": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def response(language="English"):
    speech = {
        "English": [
            "Hello, I am calling about Vaibhav and Muskan's wedding invitation.",
            "The wedding date is 31st January 2027 in Jalor, Rajasthan.",
            "I understand your response, thank you.",
            "Please attend the wedding if you are available.",
            "I will answer only questions covered by the provided context.",
            "Thank you, have a good day.",
        ],
        "Telugu": [
            "నమస్కారం అండి, Vaibhav మరియు Muskan wedding invitation గురించి call చేస్తున్నాను.",
            "Wedding date 31st January 2027, Jalor, Rajasthan లో ఉంది.",
            "మీ మాట అర్థమైంది, thank you.",
            "మీరు available అయితే wedding కి attend అవ్వండి అండి.",
            "the context లో ఉన్న details మాత్రమే చెప్తాను అండి.",
            "Thank you అండి, మీ రోజు బాగుండాలి.",
        ],
        "Hindi": [
            "नमस्ते जी, Vaibhav और Muskan की wedding invitation के बारे में call है।",
            "Wedding date 31st January 2027, Jalor, Rajasthan है।",
            "आपकी बात समझ गया, thank you।",
            "अगर आप available हों तो wedding attend कीजिए।",
            "Provided context की details ही बताऊँगा।",
            "Thank you, आपका दिन अच्छा रहे।",
        ],
    }[language]
    return {"sections": [{
        "title": f"Invitation Step {index}", "purpose": "Invite contacts to Vaibhav and Muskan's wedding",
        "instructions": "Use only the stated wedding facts and do not invent event details.",
        "questions": [],
        "examples": [speech[index]], "handling": "Respect uncertainty or refusal and do not add unrelated qualification questions.",
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
    assert calls == ["openai/gpt-oss-120b"]


def test_primary_failure_repairs_with_same_model_once():
    service, calls = service_for((503, 200))
    generate(service)
    assert calls == ["openai/gpt-oss-120b", "openai/gpt-oss-120b"]


def test_three_failures_use_assembled_script_without_raising():
    service, calls = service_for((503, 503))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}
    assert len(generate(service, config=config)) == 6
    assert config["script_source"] == "assembled"
    assert config["assembled_source_context"] == REQUIREMENT
    assert len(calls) == 3


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
    assert no_company["business_research"]["reason"] == "personal_context"
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"error": "down"})))
    failed = ensure_business_research({"business_name": "ABC Motors", "original_requirement": "Call customers of ABC Motors and explain the new service."}, client)
    assert failed["business_research"]["status"] == "failed"
    service, _ = service_for()
    assert len(generate(service, config={**failed, "language": "English", "call_type": "outbound"})) == 6


def test_malformed_json_uses_assembled_script_after_bounded_attempts():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}
    assert len(generate(service, config=config)) == 6
    assert config["script_source"] == "assembled"
    assert len(calls) == 3


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
    assert len(result) == 6 and calls == ["openai/gpt-oss-120b", "openai/gpt-oss-120b"]


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


def test_groq_request_contract_matches_validator_and_prompt():
    service, calls = service_for()
    generate(service)
    body = calls  # model capture remains deliberately secret-free
    assert body == ["openai/gpt-oss-120b"]
    schema = strict_script_response_schema()
    assert "minItems" not in schema["properties"]["sections"]
    assert "maxItems" not in schema["properties"]["sections"]
    assert schema["properties"]["sections"]["items"]["required"] == [
        "title", "purpose", "instructions", "questions", "examples", "handling"
    ]


def test_script_validator_enforces_exactly_six_sections_without_wire_cardinality_keywords():
    service, _ = service_for()
    too_short = response()
    too_short["sections"] = too_short["sections"][:5]
    with pytest.raises(HTTPException) as error:
        RealLLMService._validate_script_response(too_short, __import__("uuid").uuid4())
    assert error.value.detail["diagnostic"]["expected"] == "list[object] with exactly 6 items"


def test_malformed_section_reports_exact_index_field_and_type_without_content():
    service, _ = service_for()
    bad = response()
    bad["sections"][3]["questions"] = {"text": "wrong shape"}

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(bad)}}]})

    service = RealLLMService(settings=llm_settings(groq_fallback_model=None, groq_model_2=None, groq_api_key_2=None), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}
    assert len(generate(service, config=config)) == 6
    assert config["script_source"] == "model"


def test_schema_400_repair_then_json_object_then_assembled_ladder():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["response_format"]["type"])
        if len(calls) < 3:
            return httpx.Response(400, json={"error": {"message": "does not match expected schema", "failed_generation": "missing sections"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response("English"))}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}
    assert len(generate(service, config=config)) == 6
    assert calls == ["json_schema", "json_schema", "json_object"]
    assert config["script_source"] == "model"


def test_no_telugu_unicode_goes_to_repair_instead_of_immediate_502():
    calls = []

    def handler(request):
        calls.append(len(calls) + 1)
        payload = response("English") if len(calls) == 1 else response("Telugu")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "Telugu", "call_type": "outbound"}
    assert len(generate(service, language="Telugu", config=config)) == 6
    assert calls == [1, 2]
    assert config["script_source"] == "model"


def test_enforced_validators_reject_outbound_questions_and_missing_english():
    sections = response("Telugu")["sections"]
    sections[0]["examples"] = ["నమస్కారం అండి"]
    sections[3]["questions"] = ["Can you attend?"]
    issues = validate_script(sections, "Telugu", "outbound", user_context=REQUIREMENT, enforce=True)
    assert {item["rule"] for item in issues} >= {"outbound_questions", "english_mix_required"}


def test_research_is_skipped_for_wedding_even_with_business_name():
    def fail_if_called(request):
        raise AssertionError("research provider must not be called for personal wedding context")

    result = ensure_business_research(
        {"business_name": "Explicit Company", "original_requirement": REQUIREMENT},
        httpx.Client(transport=httpx.MockTransport(fail_if_called)),
    )
    assert result["business_research"]["reason"] == "personal_context"


def test_legacy_equivalent_section_fields_are_normalized_but_still_require_six_sections():
    legacy = response()
    legacy["sections"] = [{
        "title": item["title"], "purpose": item["purpose"], "objective": item["handling"],
        "content": item["instructions"], "questions": [{"text": q} for q in item["questions"]],
        "spoken_examples": item["examples"],
    } for item in legacy["sections"]]
    normalized = RealLLMService._validate_script_response(legacy, __import__("uuid").uuid4())
    assert all(set(("title", "purpose", "instructions", "questions", "examples", "handling")) <= set(section) for section in normalized["sections"])


def test_business_name_is_only_taken_from_explicit_identity_fields():
    wedding = normalize_business_identity({"original_requirement": REQUIREMENT})
    assert "business_name" not in wedding
    explicit = normalize_business_identity({"original_requirement": "Call customers of ABC Motors.", "company_name": "ABC Motors"})
    assert explicit["business_name"] == "ABC Motors"
