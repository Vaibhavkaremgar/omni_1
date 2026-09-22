"""Focused contract tests for per-call Omni runtime variables."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx

from app.integrations.omnidimension import OmniDimensionCallProvider, OmniDimensionClient
from app.services.omnidimension_agents import map_employee_configuration


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
    assert "Rahul" not in str(payload)
    assert "{{name}}" in payload["welcome_message"]


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
