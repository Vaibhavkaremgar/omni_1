import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.services.business_research import ensure_business_research
from app.services.employee_interview import RealLLMService, _gemini_schema, strict_script_response_schema
from app.services.employee_prompt import compose_employee_configuration, normalize_business_identity
from app.services.script_validation import validate_script

REQUIREMENT = "You should invite my contact list for my wedding. Vaibhav and Muskan. 31st January 2027, Jalor, Rajasthan."


def llm_settings(**overrides):
    values = {
        "gemini_api_key": None, "gemini_model": "gemini-2.5-flash-lite",
        "gemini_script_model": "gemini-3.6-flash",
        "gemini_fallback_model": "gemini-2.5-flash",
        "gemini_base_url": "https://generativelanguage.googleapis.com/v1beta",
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
            "Hello అండి, నేను AI assistant ని. Vaibhav and Muskan wedding కి మిమ్మల్ని invite చేయడానికి call చేశాను.",
            "Wedding January 31st, 2027 న Jalor, Rajasthan లో ఉంది అండి.",
            "మీ response అర్థమైంది అండి.",
            "Wedding కి మీరు రావాలని request చేస్తున్నాను అండి.",
            "Wedding details గురించి ఏమైనా doubt ఉంటే చెప్పండి అండి.",
            "Thank you. Have a nice day.",
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


def test_primary_failure_advances_to_the_next_configured_tier():
    service, calls = service_for((503, 200))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}
    generate(service, config=config)
    assert calls == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    assert config["generation_source"] == "groq"


def test_all_configured_tier_failures_use_assembled_script_without_raising():
    service, calls = service_for((503, 503))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}
    assert len(generate(service, config=config)) == 6
    assert config["script_source"] == "assembled"
    assert config["assembled_source_context"] == REQUIREMENT
    assert len(calls) == 2
    assert config["generation_source"] == "fallback_template"


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
    assert len(calls) == 2
    assert config["generation_source"] == "fallback_template"


def test_invalid_primary_language_is_advisory_and_stops_the_ladder():
    calls = []
    valid = response("Telugu")

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        payload = response("English") if len(calls) == 1 else valid
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "Telugu", "call_type": "outbound"}
    result = service.generate_call_script(SimpleNamespace(name="Invite Assistant", purpose=REQUIREMENT, call_type="outbound", language="Telugu"), config)
    assert len(result) == 6 and calls == ["openai/gpt-oss-120b"]
    assert config["generation_source"] == "primary"
    assert config["script_generation_warnings"]


def test_persistent_content_warnings_are_returned_for_review_instead_of_discarding_script(caplog):
    calls = []
    caplog.set_level(logging.INFO, logger="app.services.employee_interview")

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response("English"))}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "Telugu", "call_type": "outbound"}
    result = generate(service, language="Telugu", config=config)

    assert len(result) == 6
    assert calls == ["openai/gpt-oss-120b"]
    assert config["script_source"] == "model"
    assert config["generation_source"] == "primary"
    assert config["script_generation_warnings"]
    assert "stopping_ladder=true" in caplog.text
    assert "previous_policy_discard_risk=True" in caplog.text


def test_outbound_questions_are_hard_failure_and_advance_to_next_tier():
    calls = []
    invalid = response("English")
    invalid["sections"][3]["questions"] = ["Can you attend?"]

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        payload = invalid if len(calls) == 1 else response("English")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}
    result = generate(service, config=config)

    assert len(result) == 6
    assert calls == ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"]
    assert config["generation_source"] == "groq"
    assert "script_generation_warnings" not in config


@pytest.mark.parametrize("language,codepoint", [("Telugu", 0x0C00), ("Hindi", 0x0900)])
def test_regional_language_output_uses_native_script(language, codepoint):
    service, _ = service_for(language=language)
    result = generate(service, language=language)
    assert any(any(codepoint <= ord(char) < codepoint + 0x80 for char in value) for value in result.values())


def test_legacy_provider_configuration_cannot_select_employee_generation():
    service = RealLLMService(settings=llm_settings(groq_api_key=None, effective_llm_provider="legacy-provider", effective_llm_api_key="ignored", effective_llm_model="legacy-model"), client=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))))
    assert service._configured_llm_attempts() == []


def test_configuration_requires_gemini_primary_and_groq_fallback_without_exposing_secrets():
    settings = Settings(GEMINI_API_KEY="", GROQ_API_KEY="configured", GROQ_MODEL="openai/gpt-oss-20b", GROQ_BASE_URL="https://api.groq.com/openai/v1", GROQ_FALLBACK_MODEL="", ENVIRONMENT="development")
    assert settings.employee_llm_configuration_error == "Employee LLM configuration is incomplete: missing GEMINI_API_KEY."
    complete = Settings(GEMINI_API_KEY="gemini-configured", GEMINI_SCRIPT_MODEL="gemini-3.6-flash", GROQ_API_KEY="groq-configured", GROQ_MODEL="openai/gpt-oss-20b", ENVIRONMENT="development")
    assert complete.employee_llm_configuration_error is None
    assert complete.effective_llm_provider == "gemini"
    assert complete.effective_script_model == "gemini-3.6-flash"
    assert complete.gemini_fallback_model == "gemini-2.5-flash"


def test_gemini_is_primary_and_receives_only_system_and_user_prompts(caplog):
    captured = []
    caplog.set_level(logging.INFO, logger="app.services.employee_interview")

    def handler(request):
        body = json.loads(request.content)
        captured.append((str(request.url), body))
        return httpx.Response(200, json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(response())}]}}],
            "usageMetadata": {
                "promptTokenCount": 6226, "candidatesTokenCount": 716,
                "thoughtsTokenCount": 384, "cachedContentTokenCount": 0,
                "totalTokenCount": 7326,
            },
        })

    settings = llm_settings(
        gemini_api_key="gemini-key",
        effective_llm_provider="gemini",
        effective_llm_api_key="gemini-key",
        effective_llm_model="gemini-3.6-flash",
        effective_llm_base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}

    assert len(generate(service, config=config)) == 6
    assert len(captured) == 1
    url, body = captured[0]
    assert url.endswith("/models/gemini-3.6-flash:generateContent")
    assert set(body) == {"systemInstruction", "contents", "generationConfig"}
    system_prompt = body["systemInstruction"]["parts"][0]["text"]
    user_prompt = body["contents"][0]["parts"][0]["text"]
    assert "UNIVERSAL OUTBOUND VOICE AGENT" in system_prompt
    assert "{{USER_CONTEXT}}" not in system_prompt
    assert json.loads(user_prompt)["USER_CONTEXT"]["original_requirement"] == REQUIREMENT
    assert body["generationConfig"]["responseFormat"]["text"]["mimeType"] == "APPLICATION_JSON"
    assert "schema" in body["generationConfig"]["responseFormat"]["text"]
    assert "input_tokens=6226 output_tokens=716 thinking_tokens=384 cached_tokens=0 total_tokens=7326" in caplog.text
    assert "request_count=1 retry_count=0" in caplog.text


def test_groq_is_used_after_primary_and_fallback_gemini_failures(caplog):
    calls = []
    caplog.set_level(logging.INFO, logger="app.services.employee_interview")

    def handler(request):
        body = json.loads(request.content)
        calls.append((str(request.url), body.get("model")))
        if "generativelanguage.googleapis.com" in str(request.url):
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(response())}}],
            "usage": {"prompt_tokens": 6300, "completion_tokens": 800, "total_tokens": 7100},
        })

    settings = llm_settings(
        gemini_api_key="gemini-key",
        effective_llm_provider="gemini",
        effective_llm_api_key="gemini-key",
        effective_llm_model="gemini-3.6-flash",
        effective_llm_base_url="https://generativelanguage.googleapis.com/v1beta",
    )
    service = RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

    assert len(generate(service)) == 6
    assert [model for _, model in calls] == [None, None, "openai/gpt-oss-120b"]
    assert calls[0][0].endswith("/models/gemini-3.6-flash:generateContent")
    assert calls[1][0].endswith("/models/gemini-2.5-flash:generateContent")
    assert "api.groq.com" in calls[2][0]
    assert "request_count=3 retry_count=2" in caplog.text
    assert "provider=groq model=openai/gpt-oss-120b input_tokens=6300 output_tokens=800" in caplog.text


def test_503_unavailable_backs_off_before_gemini_fallback(monkeypatch, caplog):
    calls = []
    sleeps = []
    caplog.set_level(logging.INFO, logger="app.services.employee_interview")
    monkeypatch.setattr("app.services.employee_interview.random.uniform", lambda _low, _high: 1.0)
    monkeypatch.setattr("app.services.employee_interview.time.sleep", sleeps.append)

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(503, json={"error": {"status": "UNAVAILABLE", "message": "high demand"}})
        return httpx.Response(200, json={
            "candidates": [{"content": {"parts": [{"text": json.dumps(response())}]}}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 50, "totalTokenCount": 150},
        })

    service = RealLLMService(
        settings=llm_settings(gemini_api_key="gemini-key", effective_llm_provider="gemini"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert len(generate(service)) == 6
    assert calls[0].endswith("/models/gemini-3.6-flash:generateContent")
    assert calls[1].endswith("/models/gemini-2.5-flash:generateContent")
    assert sleeps == [2.0]
    assert "winning_tier=fallback_2_5" in caplog.text


def test_non_503_failure_does_not_back_off(monkeypatch):
    sleeps = []
    calls = []
    monkeypatch.setattr("app.services.employee_interview.time.sleep", sleeps.append)

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(400, json={"error": {"status": "INVALID_ARGUMENT", "message": "bad schema"}})
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(response())}]}}]})

    service = RealLLMService(
        settings=llm_settings(gemini_api_key="gemini-key", effective_llm_provider="gemini"),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert len(generate(service)) == 6
    assert sleeps == []


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


def test_gemini_schema_preserves_required_application_field_names():
    schema = _gemini_schema(strict_script_response_schema())
    section = schema["properties"]["sections"]["items"]

    assert "examples" in section["properties"]
    assert set(section["required"]).issubset(section["properties"])
    assert "minLength" not in section["properties"]["title"]


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
    assert config["script_source"] == "assembled"
    assert config["generation_source"] == "fallback_template"


def test_schema_400_exhausts_configured_tiers_then_uses_template():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["response_format"]["type"])
        return httpx.Response(400, json={"error": {"message": "does not match expected schema", "failed_generation": "missing sections"}})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "English", "call_type": "outbound"}
    assert len(generate(service, config=config)) == 6
    assert calls == ["json_schema", "json_schema"]
    assert config["script_source"] == "assembled"
    assert config["generation_source"] == "fallback_template"


def test_no_telugu_unicode_is_advisory_and_stops_the_ladder():
    calls = []

    def handler(request):
        calls.append(len(calls) + 1)
        payload = response("English") if len(calls) == 1 else response("Telugu")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "Telugu", "call_type": "outbound"}
    assert len(generate(service, language="Telugu", config=config)) == 6
    assert calls == [1]
    assert config["script_source"] == "model"
    assert config["generation_source"] == "primary"
    assert config["script_generation_warnings"]


def test_telugu_opening_introduces_explicit_agent_before_purpose_without_literal_behalf():
    payload = response("Telugu")
    payload["sections"][0]["examples"] = [
        "Hello అండి, నేను Maya. Vaibhav and Muskan wedding కి మిమ్మల్ని invite చేయడానికి call చేశాను."
    ]
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "Telugu", "call_type": "outbound", "agent_name": "Maya", "host_name": "Vaibhav and Muskan"}
    result = generate(service, language="Telugu", config=config)
    assert config["script_source"] == "model"
    assert calls == ["openai/gpt-oss-120b"]
    assert payload["sections"][0]["examples"][0] in result["Invitation Step 0"]


def test_literal_behalf_opening_is_advisory_and_stops_the_ladder():
    bad = response("Telugu")
    bad["sections"][0]["examples"] = [
        "Hello అండి, Vaibhav and Muskan గారి behalf లో AI assistant గా wedding invitation కోసం call చేస్తున్నాను."
    ]
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["response_format"]["type"])
        payload = bad if len(calls) == 1 else response("Telugu")
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]})

    service = RealLLMService(settings=llm_settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))
    config = {"original_requirement": REQUIREMENT, "language": "Telugu", "call_type": "outbound"}
    generate(service, language="Telugu", config=config)
    assert calls == ["json_schema"]
    assert config["script_source"] == "model"
    assert config["generation_source"] == "primary"
    assert config["script_generation_warnings"]


def test_enforced_validators_reject_outbound_questions_without_forcing_english():
    sections = response("Telugu")["sections"]
    sections[0]["examples"] = ["నమస్కారం అండి"]
    sections[3]["questions"] = ["Can you attend?"]
    issues = validate_script(sections, "Telugu", "outbound", user_context=REQUIREMENT, enforce=True)
    assert "outbound_questions" in {item["rule"] for item in issues}
    assert "english_mix_required" not in {item["rule"] for item in issues}


def test_research_is_skipped_for_wedding_even_with_business_name():
    def fail_if_called(request):
        raise AssertionError("research provider must not be called for personal wedding context")

    result = ensure_business_research(
        {"business_name": "Explicit Company", "original_requirement": REQUIREMENT},
        httpx.Client(transport=httpx.MockTransport(fail_if_called)),
    )
    assert result["business_research"]["reason"] == "personal_context"


def test_assembled_fallback_preserves_context_as_review_only_draft_without_invented_speech():
    wedding = {"original_requirement": "call contacts about my wedding on 15th of jan 2027", "language": "Telugu", "call_type": "outbound", "host_name": ""}
    election = {"original_requirement": "call voters about the upcoming ghmc elections", "language": "Telugu", "call_type": "outbound", "host_name": ""}
    wedding_sections = RealLLMService._assemble_script_sections(wedding, "Telugu", "outbound", wedding)
    election_sections = RealLLMService._assemble_script_sections(election, "Telugu", "outbound", election)
    wedding_text = json.dumps(wedding_sections, ensure_ascii=False)
    election_text = json.dumps(election_sections, ensure_ascii=False)
    assert "15th of jan 2027" in wedding_text
    assert wedding["script_review_required"] is True
    assert "ghmc elections" in election_text
    assert election["script_review_required"] is True
    assert [section["title"] for section in wedding_sections] == [
        "Opening", "Facts", "Listening", "Request", "Exceptions", "Closing",
    ]
    assert not validate_script(
        wedding_sections, "Telugu", "outbound",
        user_context=wedding["original_requirement"], enforce=True,
    )
    spoken = "\n".join(example for section in wedding_sections for example in section["examples"])
    assert spoken == "Hello అండి, నేను AI assistant ని."
    assert "gari" not in spoken.casefold() and "behalf lo" not in spoken.casefold()


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
