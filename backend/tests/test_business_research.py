from types import SimpleNamespace

import httpx

from app.services.business_research import ensure_business_research, research_fingerprint
from app.services.employee_configuration import public_employee_configuration


def test_research_reuses_matching_snapshot_without_call(monkeypatch):
    config = {"business_name": "Acme", "business_description": "Tools"}
    config["business_research"] = {"status": "success", "fingerprint": research_fingerprint(config), "facts": ["Known"]}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: (_ for _ in ()).throw(AssertionError("called"))))
    assert ensure_business_research(config, client)["business_research"]["facts"] == ["Known"]


def test_research_uses_google_search_and_persists_grounded_facts(monkeypatch):
    monkeypatch.setattr("app.services.business_research.get_settings", lambda: SimpleNamespace(
        gemini_api_key="test-key", gemini_model="gemini-2.5-flash-lite",
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta",
        gemini_research_timeout_seconds=5.0,
    ))
    def handler(request):
        body = request.read()
        assert b"google_search" in body
        assert request.headers["x-goog-api-key"] == "test-key"
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"summary":"Official","facts":["Open since 1999"],"sources":[]}'}]}, "groundingMetadata": {"groundingChunks": [{"web": {"title": "Official", "uri": "https://acme.example"}}]}}]})
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ensure_business_research({"business_name": "Acme", "business_description": "Tools"}, client)
    assert result["business_research"]["status"] == "success"
    assert result["business_research"]["facts"] == ["Open since 1999"]
    assert result["business_research"]["sources"][0]["uri"] == "https://acme.example"


def test_research_failure_is_explicit_and_public_config_has_no_source_metadata(monkeypatch):
    monkeypatch.setattr("app.services.business_research.get_settings", lambda: SimpleNamespace(gemini_api_key=None))
    result = ensure_business_research({"business_name": "Acme", "business_description": "Tools"})
    assert result["business_research"]["status"] == "unavailable"
    public = public_employee_configuration({"business_research": {"status": "success", "sources": [{"uri": "https://secret.example"}], "research_query": "private", "facts": ["x"]}})
    assert "sources" not in public["business_research"]
    assert "research_query" not in public["business_research"]
