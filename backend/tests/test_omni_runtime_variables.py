"""Focused contract tests for per-call Omni runtime variables."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from app.integrations.omnidimension import OmniDimensionCallProvider, OmniDimensionClient
from app.services.omnidimension_agents import map_employee_configuration


def _outbound_sections(first_example: str, *, duplicate_first: bool = False) -> list[dict]:
    sections = [
        {"title": f"Section {index}", "purpose": "Purpose", "instructions": "Instructions", "questions": [], "examples": [first_example if index == 0 else f"Line {index}"], "handling": "Handling"}
        for index in range(6)
    ]
    if duplicate_first:
        sections[1]["examples"] = [first_example]
    return sections


def test_outbound_welcome_uses_only_valid_first_opening_example():
    employee = SimpleNamespace(name="Assistant", purpose="Invite contacts", language="English", call_type="outbound")
    opening = "Hello, I am calling about the wedding invitation."
    payload = map_employee_configuration(employee, {
        "purpose": employee.purpose, "original_requirement": employee.purpose,
        "language": "English", "call_type": "outbound", "conversation_sections": _outbound_sections(opening),
    })
    assert payload["welcome_message"] == opening


def test_reviewed_outbound_opening_comes_from_edited_spoken_example():
    employee = SimpleNamespace(name="Assistant", purpose="Invite contacts", language="Telugu", call_type="outbound")
    opening = "Hello అండి, నేను Maya. Vaibhav and Muskan wedding కి మిమ్మల్ని invite చేయడానికి call చేశాను."
    cards = {f"Section {index}": f"Spoken example: {opening if index == 0 else f'Line {index} అండి.'}" for index in range(6)}
    payload = map_employee_configuration(employee, {
        "purpose": employee.purpose, "language": "Telugu", "call_type": "outbound",
        "agent_name": "Maya", "script_source": "reviewed", "call_script": cards,
    })
    assert payload["welcome_message"] == opening


def test_outbound_welcome_falls_back_for_placeholder_opening():
    employee = SimpleNamespace(name="Assistant", purpose="Invite contacts", language="English", call_type="outbound")
    payload = map_employee_configuration(employee, {
        "purpose": employee.purpose, "language": "English", "call_type": "outbound",
        "conversation_sections": _outbound_sections("Hello {{name}}, I am calling about the wedding."),
    })
    assert "{{" not in payload["welcome_message"]
    assert payload["welcome_message"].startswith("Hello, I am an AI assistant")


def test_outbound_welcome_falls_back_for_permission_question():
    employee = SimpleNamespace(name="Assistant", purpose="Invite contacts", language="English", call_type="outbound")
    payload = map_employee_configuration(employee, {
        "purpose": employee.purpose, "language": "English", "call_type": "outbound",
        "conversation_sections": _outbound_sections("Can I ask if this is a good time?"),
    })
    assert "Can I ask" not in payload["welcome_message"]
    assert "?" not in payload["welcome_message"]


def test_outbound_welcome_falls_back_for_duplicate_opening():
    employee = SimpleNamespace(name="Assistant", purpose="Invite contacts", language="English", call_type="outbound")
    opening = "Hello, I am calling about the wedding invitation."
    payload = map_employee_configuration(employee, {
        "purpose": employee.purpose, "language": "English", "call_type": "outbound",
        "conversation_sections": _outbound_sections(opening, duplicate_first=True),
    })
    assert payload["welcome_message"] != opening
    assert payload["welcome_message"].startswith("Hello, I am an AI assistant")


def test_outbound_welcome_does_not_use_company_as_host_when_host_is_empty():
    employee = SimpleNamespace(name="Assistant", purpose="Invite contacts", language="English", call_type="outbound")
    payload = map_employee_configuration(employee, {
        "purpose": employee.purpose, "business_name": "Acme Company", "host_name": "",
        "language": "English", "call_type": "outbound",
    })
    assert "Acme Company" not in payload["welcome_message"]
    assert "on behalf of" not in payload["welcome_message"]


def test_display_employee_name_is_not_voice_identity_or_model_context():
    employee = SimpleNamespace(name="బాబు dark wedding planners", purpose="Invite contacts", language="English", call_type="inbound")
    payload = map_employee_configuration(employee, {
        "name": employee.name, "purpose": employee.purpose, "language": "English", "call_type": "inbound",
    })
    context = "\n".join(section["body"] for section in payload["context_breakdown"])
    assert employee.name not in context
    assert employee.name not in payload["welcome_message"]
    assert payload["name"] == employee.name


def _provider(handler):
    client = OmniDimensionClient(
        settings=SimpleNamespace(
            omnidimension_api_key="runtime-variable-test-key",
            omnidimension_base_url="https://provider.test/api/v1",
            omnidimension_timeout_seconds=5,
        ),
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return OmniDimensionCallProvider(client)


def test_published_agent_declares_name_slot_and_references_it_in_runtime_prompt():
    employee = SimpleNamespace(name="Maya", purpose="Discuss property enquiries", language="English", call_type="outbound")
    script = {
        "Identity": "You are Maya from GreenNest.",
        "Greeting": "Introduce yourself naturally.",
        "Discovery": "Understand the enquiry.",
        "Questions": "Ask only relevant questions.",
        "Next step": "Offer the configured next step.",
        "Closing": "Close politely.",
    }

    payload = map_employee_configuration(employee, {
        "name": "Maya", "purpose": employee.purpose, "language": "English",
        "call_type": "outbound", "call_script": script,
    })

    assert payload["dynamic_variables"] == {"name": ""}
    runtime = next(item["body"] for item in payload["context_breakdown"] if item["title"] == "Employee Runtime Rules")
    assert "{{name}}" in runtime
    assert "DISPATCH RUNTIME CONTEXT" in runtime
    assert "no dispatch context is supplied" in runtime
    assert "location, project, configuration, budget, and status" in runtime
    assert "Rahul" not in str(payload)
    assert payload["welcome_message"] == "Hello, I am an AI assistant."


def test_campaign_runtime_values_are_sent_per_call_without_leakage():
    sent: list[dict] = []

    def handler(request: httpx.Request):
        assert request.url.path == "/api/v1/calls/dispatch"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True, "status": "dispatched", "requestId": len(sent)})

    provider = _provider(handler)
    common = {"location": "Hyderabad", "project": "GreenNest Meadows", "configuration": "2 BHK", "budget": "₹55 lakh", "status": "Site Visit Requested"}
    provider.dispatch(agent_id=77, to_number="+919876543210", from_number_id=88,
                      call_context={"name": "Rahul Sharma", **common}, metadata={"local_call_id": "one"})
    provider.dispatch(agent_id=77, to_number="+919876543211", from_number_id=88,
                      call_context={"name": "Priya Kapoor", **common}, metadata={"local_call_id": "two"})

    assert len(sent) == 2
    assert sent[0]["to_number"] == "+919876543210"
    assert sent[0]["call_context"] == {"name": "Rahul Sharma", **common}
    assert sent[1]["to_number"] == "+919876543211"
    assert sent[1]["call_context"] == {"name": "Priya Kapoor", **common}
    assert "Rahul Sharma" not in sent[1]["call_context"].values()
    assert "phone_number" not in sent[0]["call_context"]
