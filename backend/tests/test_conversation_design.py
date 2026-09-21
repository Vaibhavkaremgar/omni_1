import copy
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.services.conversation_design import ConversationDesign, GeneratedScript, assert_strict_response_schema, strict_response_schema, validate_question_context
from app.services.employee_interview import RealLLMService
from app.services.employee_prompt import compose_employee_configuration
from app.services.script_language_validation import extract_call_script_from_prompt, validation_summary
from app.services.omnidimension_agents import map_employee_configuration, _format_call_script, _sent_configuration, _returned_configuration
from design_fixtures import design_response

CASES = [
    ("Explain a bank statement entry", ["Locate Entry", "Describe Posting", "Explain Dates", "Check Understanding", "Handle Disputes", "Confirm Resolution"]),
    ("Explain hospital visiting hours", ["Identify Visitor Need", "Ward Information", "Visiting Windows", "Access Rules", "Answer Visitor Concerns", "Check Remaining Questions"]),
    ("Explain insurance claim document requirements", ["Locate Claim Request", "Required Documents", "Missing Documents", "Submission Guidance", "Clarify Evidence", "Confirm Understanding"]),
    ("Reschedule an existing appointment", ["Find Existing Appointment", "Requested Change", "Available Times", "Confirm Replacement", "Explain Confirmation", "Resolve Remaining Issues"]),
    ("Explain a library holiday closure", ["Closure Notice", "Affected Dates", "Remote Access", "Return Arrangements", "Clarify Exceptions", "Acknowledge Understanding"]),
]


def test_employee_conversation_schema_is_groq_strict_compatible():
    schema = strict_response_schema()
    assert_strict_response_schema(schema)
    question = schema["$defs"]["Question"]
    assert set(question["required"]) == set(question["properties"])
    assert question["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    for name in ("Section", "Understanding"):
        assert set(schema["$defs"][name]["required"]) == set(schema["$defs"][name]["properties"])

def service(responses, seen):
    def handler(request):
        seen.append(json.loads(request.content))
        response = responses[min(len(seen) - 1, len(responses) - 1)]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})
    settings = SimpleNamespace(effective_llm_provider="openai", effective_llm_api_key="test",
                               effective_llm_model="test", effective_llm_base_url="https://example.invalid",
                               llm_timeout_seconds=1)
    return RealLLMService(settings=settings, client=httpx.Client(transport=httpx.MockTransport(handler)))

@pytest.mark.parametrize("requirement,titles", CASES)
def test_context_design_survives_generation_edit_and_provider_mapping(requirement, titles):
    seen = []
    fixture = design_response(titles, job=requirement)
    employee = SimpleNamespace(name="Ava", language="English", call_type="inbound", purpose=requirement, llm_model="test")
    config = {"original_requirement": requirement, "purpose": requirement, "language": "English", "call_type": "inbound"}
    generated = service([fixture], seen).generate_call_script(employee, config)
    assert len(generated) == 6
    assert list(generated) == [section["title"] for section in fixture["sections"]]
    config = compose_employee_configuration({**config, "conversation_design": generated.design, "opening": fixture["opening"], "call_script": dict(generated)})
    assert validation_summary(config)["valid"]
    # Editing a body must not restore a fallback or change any generated title.
    config["call_script"][next(iter(generated))] += "\nUse only configured facts."
    edited = compose_employee_configuration(config)
    assert edited["call_script"] == config["call_script"]
    assert extract_call_script_from_prompt(_format_call_script(generated)) == generated
    mapped = map_employee_configuration(employee, edited)
    assert _sent_configuration(mapped)["six_section_titles"] == list(generated)
    assert _returned_configuration(mapped)["six_section_titles"] == list(generated)
    text = json.dumps(mapped).lower()
    for unwanted in ("budget range", "purchase intent", "site visit", "product qualification", "arrange a call"):
        # The design records irrelevant questions as explicit exclusions; no workflow should add them.
        assert unwanted not in _format_call_script(generated).lower()
    assert "Business Type Conversation Profile" not in text
    context = json.loads(seen[0]["messages"][1]["content"])
    assert context["USER_CONTEXT"]["original_requirement"] == requirement
    assert "existing_configuration" not in context

@pytest.mark.parametrize("question", ["What is your budget?", "Would you like to purchase?", "Can I book a site visit?", "What is your profession?", "Which product interests you?"])
def test_unrelated_questions_are_rejected_and_retried(question):
    requirement, titles = CASES[-1]
    response = design_response(titles, job=requirement)
    response["sections"][0]["questions"] = [{"text": question, "reason": "Collect this information", "source": requirement}]
    seen = []
    employee = SimpleNamespace(name="Ava", language="English", call_type="outbound")
    with pytest.raises(HTTPException) as exc:
        service([response], seen).generate_call_script(employee, {"original_requirement": requirement})
    assert exc.value.status_code == 502
    assert len(seen) == 2

def test_required_question_supported_by_context_is_accepted():
    requirement = "Ask which appointment time the person wants to reschedule"
    response = design_response(CASES[3][1], job=requirement)
    response["sections"][0]["questions"] = [{"text": "Which appointment time should change?", "reason": "Identify the appointment", "source": requirement}]
    validate_question_context(ConversationDesign.model_validate(response), {"original_requirement": requirement})

@pytest.mark.parametrize("language,speech", [
    ("Telugu", "నమస్కారం అండి, మీ request గురించి details చెప్పండి."),
    ("Hindi", "नमस्ते जी, आपकी request के details बताइए।"),
    ("English", "Hello, please describe your request."),
])
def test_only_spoken_content_is_language_validated(language, speech):
    fixture = design_response(CASES[0][1], speech=speech)
    generated = GeneratedScript(ConversationDesign.model_validate(fixture))
    config = {"language": language, "business_name": "English Company Name", "purpose": "English requirement", "conversation_design": generated.design, "opening": speech, "call_script": generated}
    assert validation_summary(config)["valid"]
    if language != "English":
        config["opening"] = "Tell me your request."
        assert not validation_summary(config)["valid"]

def test_empty_draft_is_not_filled_with_synthetic_sales_sections():
    config = compose_employee_configuration({"purpose": "Explain the library closure", "language": "Telugu"})
    assert config["call_script"] == {}
    assert config["conversation_variables"] == []

def test_parser_rejects_duplicates_missing_and_extra_sections():
    valid = design_response(CASES[0][1])
    for count in (5, 7):
        invalid = copy.deepcopy(valid)
        invalid["sections"] = (invalid["sections"] * 2)[:count]
        with pytest.raises(ValueError):
            ConversationDesign.model_validate(invalid)
    invalid = copy.deepcopy(valid)
    invalid["sections"][1]["title"] = invalid["sections"][0]["title"]
    with pytest.raises(ValueError):
        ConversationDesign.model_validate(invalid)


def hdfc_kyc_response(language="English"):
    speech = "Hello, I am calling about your KYC document update."
    if language == "Telugu":
        speech = "\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02 \u0c05\u0c02\u0c21\u0c3f, KYC documents update \u0c17\u0c41\u0c30\u0c3f\u0c02\u0c1a\u0c3f quick call \u0c1a\u0c47\u0c36\u0c3e\u0c28\u0c41."
    response = design_response(
        ["Bank Call Identity", "KYC Update Reason", "Document Process Guidance", "Customer Questions", "Escalation Boundaries", "Completion Check"],
        speech=speech,
        job="Guide existing bank customers through required KYC document updates",
    )
    response["understanding"]["organization"] = "HDFC Bank"
    response["understanding"]["activity"] = "KYC document update guidance"
    response["understanding"]["information_needed"] = ["Whether the customer needs the KYC update process explained"]
    response["understanding"]["irrelevant_questions"] = ["Budget", "purchase intent", "site visit", "product qualification"]
    response["sections"][1]["questions"] = [{
        "text": "\u0c2e\u0c40\u0c15\u0c41 KYC document update process details \u0c1a\u0c46\u0c2a\u0c4d\u0c2a\u0c2e\u0c02\u0c1f\u0c3e\u0c30\u0c3e?" if language == "Telugu" else "Would you like me to explain the KYC document update process?",
        "reason": "The requirement is to guide existing customers through the required process.",
        "source": "KYC documents need updating",
    }]
    response["sections"][2]["content"] = "Explain only the configured KYC document update process. Internal instructions may stay in English."
    return response


def test_hdfc_kyc_generation_succeeds_when_research_failed_and_logs_design(caplog):
    requirement = "Create an outbound employee for a bank that calls existing customers whose KYC documents need updating and guides them through the required process."
    seen = []
    caplog.set_level(logging.INFO, logger="app.services.employee_interview")
    employee = SimpleNamespace(name="KYC Assistant", language="English", call_type="outbound", purpose=requirement)
    config = {
        "business_name": "HDFC Bank",
        "original_requirement": requirement,
        "purpose": "Call existing customers whose KYC documents need updating and guide them through the required process.",
        "language": "English",
        "call_type": "outbound",
        "business_research": {"status": "failed", "reason": "provider_error"},
    }
    generated = service([hdfc_kyc_response()], seen).generate_call_script(employee, config)
    assert list(generated) == [section["title"] for section in hdfc_kyc_response()["sections"]]
    assert len(generated) == 6
    assert "LLM generated employee design before validation" in caplog.text
    sent = json.loads(seen[0]["messages"][1]["content"])
    assert sent["RESEARCH"] == {"status": "unavailable"}
    assert sent["USER_CONTEXT"]["business_name"] == "HDFC Bank"


def test_hdfc_kyc_generation_uses_successful_research_as_enrichment_only():
    requirement = "Call existing customers whose KYC documents need updating and guide them through the required process."
    seen = []
    employee = SimpleNamespace(name="KYC Assistant", language="English", call_type="outbound", purpose=requirement)
    config = {
        "business_name": "HDFC Bank",
        "original_requirement": requirement,
        "purpose": requirement,
        "language": "English",
        "call_type": "outbound",
        "business_research": {"status": "success", "facts": ["HDFC Bank is a bank"], "sources": [{"uri": "https://example.test"}]},
    }
    generated = service([hdfc_kyc_response()], seen).generate_call_script(employee, config)
    assert len(generated) == 6
    sent = json.loads(seen[0]["messages"][1]["content"])
    assert sent["RESEARCH"]["status"] == "success"
    assert "HDFC Bank" in sent["USER_CONTEXT"]["business_name"]


def test_hdfc_kyc_telugu_spoken_examples_validate_while_internal_english_is_allowed():
    requirement = "Call existing customers whose KYC documents need updating and guide them through the required process."
    employee = SimpleNamespace(name="KYC Assistant", language="Telugu", call_type="outbound", purpose=requirement)
    config = {"business_name": "HDFC Bank", "original_requirement": requirement, "purpose": requirement, "language": "Telugu", "call_type": "outbound"}
    generated = service([hdfc_kyc_response("Telugu")], []).generate_call_script(employee, config)
    composed = compose_employee_configuration({**config, "conversation_design": generated.design, "opening": generated.design["opening"], "call_script": dict(generated)})
    assert validation_summary(composed)["valid"]
    assert any("Internal instructions may stay in English" in body for body in generated.values())


def test_product_word_is_allowed_but_product_qualification_is_rejected():
    requirement = "Call existing bank customers whose KYC documents need updating"
    response = hdfc_kyc_response()
    response["sections"][0]["questions"] = [{
        "text": "Which bank product or account needs the KYC update?",
        "reason": "Use the existing customer banking context to route the KYC update guidance.",
        "source": "existing bank customers",
    }]
    validate_question_context(ConversationDesign.model_validate(response), {"original_requirement": requirement})

    response["sections"][0]["questions"][0]["text"] = "What product qualification details should I capture?"
    with pytest.raises(ValueError, match="product/service qualification"):
        validate_question_context(ConversationDesign.model_validate(response), {"original_requirement": requirement})


def test_translated_question_uses_semantic_rationale_without_verbatim_source():
    requirement = "Call existing customers whose KYC documents need updating and guide them through the KYC update process."
    response = hdfc_kyc_response("Telugu")
    response["sections"][0]["questions"] = [{
        "text": "మీ KYC documents update గురించి help కావాలా?",
        "reason": "Understand whether the existing customer needs guidance through the stated KYC update process.",
        "support_type": "directly_necessary",
        "requirement_basis": "The call exists to guide existing customers through their KYC document update process.",
    }]
    design = ConversationDesign.model_validate(response)
    validate_question_context(design, {"original_requirement": requirement})


def test_submission_channels_are_rejected_when_not_configured_but_location_is_contextual():
    response = hdfc_kyc_response()
    response["sections"][0]["questions"] = [{
        "text": "Which branch or online portal will you use?",
        "reason": "Choose a submission channel for the update.",
        "support_type": "operationally_required",
        "requirement_basis": "The KYC update requires a submission channel.",
    }]
    with pytest.raises(ValueError, match="submission channel"):
        validate_question_context(ConversationDesign.model_validate(response), {"original_requirement": "Call existing customers whose KYC documents need updating and guide them through the KYC update process."})

    response["sections"][0]["questions"][0] = {
        "text": "Can you confirm the address for technician arrival?",
        "reason": "Confirm the address required for the stated service appointment.",
        "support_type": "explicit_requirement",
        "requirement_basis": "The requirement explicitly asks to confirm the address for technician arrival.",
    }
    validate_question_context(ConversationDesign.model_validate(response), {"original_requirement": "Call customers in Hyderabad whose service appointment is scheduled tomorrow and confirm their address for technician arrival."})
