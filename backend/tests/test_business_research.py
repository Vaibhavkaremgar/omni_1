from types import SimpleNamespace

import httpx

from app.services.business_research import ensure_business_research, research_fingerprint, validate_public_research_url
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


def test_research_http_error_is_non_fatal_and_records_status(monkeypatch):
    monkeypatch.setattr("app.services.business_research.get_settings", lambda: SimpleNamespace(
        gemini_api_key="test-key", gemini_model="gemini-test",
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta",
        gemini_research_timeout_seconds=5.0,
    ))
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(403, json={"error": "forbidden"})))
    result = ensure_business_research({"business_name": "HDFC Bank", "business_description": "KYC update calls"}, client)
    assert result["business_research"]["status"] == "failed"
    assert result["business_research"]["reason"] == "provider_error"
    assert result["business_research"]["status_code"] == 403


def test_research_timeout_is_non_fatal(monkeypatch):
    monkeypatch.setattr("app.services.business_research.get_settings", lambda: SimpleNamespace(
        gemini_api_key="test-key", gemini_model="gemini-test",
        gemini_base_url="https://generativelanguage.googleapis.com/v1beta",
        gemini_research_timeout_seconds=5.0,
    ))
    def handler(request):
        raise httpx.TimeoutException("timed out", request=request)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ensure_business_research({"business_name": "HDFC Bank", "business_description": "KYC update calls"}, client)
    assert result["business_research"]["status"] == "failed"
    assert result["business_research"]["reason"] == "provider_error"


def test_research_fingerprint_changes_when_business_context_changes():
    base = {"business_name": "Acme", "business_description": "Tools", "purpose": "Support buyers"}
    assert research_fingerprint(base) != research_fingerprint({**base, "purpose": "Sell tools"})
    assert research_fingerprint(base) != research_fingerprint({**base, "website_url": "https://acme.example"})


def test_research_url_rejects_private_targets():
    for value in ("http://127.0.0.1:8000", "http://10.0.0.1", "file:///etc/passwd", "https://user:pass@example.com"):
        try:
            validate_public_research_url(value)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe URL accepted: {value}")


def test_research_snapshot_keeps_context_and_website_in_grounded_query(monkeypatch):
    monkeypatch.setattr("app.services.business_research.get_settings", lambda: SimpleNamespace(
        gemini_api_key="test-key", gemini_model="gemini-test", gemini_base_url="https://generativelanguage.googleapis.com/v1beta", gemini_research_timeout_seconds=5.0,
    ))
    class Client:
        def post(self, url, **kwargs):
            assert "https://acme.example" in kwargs["json"]["contents"][0]["parts"][0]["text"]
            class Response:
                def raise_for_status(self):
                    return None
                def json(self):
                    return {"candidates": [{"content": {"parts": [{"text": '{"summary":"Official","facts":["Open"],"sources":[]}'}]}, "groundingMetadata": {"groundingChunks": [{"web": {"title": "Acme", "uri": "https://acme.example"}}]}}]}
            return Response()
    result = ensure_business_research({"business_name": "Acme", "business_description": "Tools", "purpose": "Support", "website_url": "https://acme.example"}, Client())
    assert result["business_research"]["context"]["website_url"] == "https://acme.example"
