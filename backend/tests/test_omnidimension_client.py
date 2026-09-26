from types import SimpleNamespace

import httpx
import pytest

from app.integrations.omnidimension import (
    OmniDimensionAuthenticationError,
    OmniDimensionClient,
    OmniDimensionClientError,
    OmniDimensionConfigurationError,
    OmniDimensionNetworkError,
    OmniDimensionResponseError,
    OmniDimensionServerError,
    OmniDimensionCallProvider,
    normalize_omni_call_status,
)


API_KEY = "test-only-omnidimension-secret"


def settings(**overrides):
    values = {
        "omnidimension_api_key": API_KEY,
        "omnidimension_base_url": "https://provider.test/api/v1/",
        "omnidimension_timeout_seconds": 3.5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def client_for(handler):
    return OmniDimensionClient(settings=settings(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_authenticated_request_uses_configured_base_url_and_bearer_key():
    captured = {}

    def handler(request: httpx.Request):
        captured["request"] = request
        return httpx.Response(200, json={"ok": True})

    client = client_for(handler)
    try:
        assert client.get("/status", params={"verbose": "1"}) == {"ok": True}
        assert str(captured["request"].url) == "https://provider.test/api/v1/status?verbose=1"
        assert captured["request"].headers["Authorization"] == f"Bearer {API_KEY}"
    finally:
        client.close()


def test_http_verbs_and_json_body_are_supported():
    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json={"method": request.method})

    client = client_for(handler)
    try:
        assert client.post("agents", json={"name": "A"})["method"] == "POST"
        assert client.patch("agents/1", json={"name": "B"})["method"] == "PATCH"
        assert client.delete("agents/1")["method"] == "DELETE"
        assert requests[0].content == b'{"name":"A"}'
    finally:
        client.close()


def test_missing_configuration_is_controlled_and_secret_free():
    with pytest.raises(OmniDimensionConfigurationError) as error:
        OmniDimensionClient(settings(omnidimension_api_key=""))
    assert API_KEY not in str(error.value)


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_provider_responses_become_authentication_errors(status_code):
    client = client_for(lambda _: httpx.Response(status_code, json={"message": API_KEY}))
    try:
        with pytest.raises(OmniDimensionAuthenticationError) as error:
            client.get("status")
        assert API_KEY not in str(error.value)
    finally:
        client.close()


def test_4xx_provider_response_becomes_client_error():
    client = client_for(lambda _: httpx.Response(422, json={"message": "invalid"}))
    try:
        with pytest.raises(OmniDimensionClientError) as error:
            client.get("status")
        assert error.value.status_code == 422
        assert API_KEY not in str(error.value)
    finally:
        client.close()


def test_5xx_provider_response_becomes_server_error():
    client = client_for(lambda _: httpx.Response(503, text="provider unavailable"))
    try:
        with pytest.raises(OmniDimensionServerError) as error:
            client.get("status")
        assert error.value.status_code == 503
        assert API_KEY not in str(error.value)
    finally:
        client.close()


def test_timeout_and_network_failures_are_controlled():
    timeout_client = client_for(lambda _: (_ for _ in ()).throw(httpx.ReadTimeout("timed out")))
    network_client = client_for(lambda _: (_ for _ in ()).throw(httpx.ConnectError("offline")))
    try:
        with pytest.raises(OmniDimensionNetworkError):
            timeout_client.get("status")
        with pytest.raises(OmniDimensionNetworkError):
            network_client.get("status")
    finally:
        timeout_client.close()
        network_client.close()


def test_malformed_json_is_controlled_without_provider_body_leakage():
    client = client_for(lambda _: httpx.Response(200, text=API_KEY))
    try:
        with pytest.raises(OmniDimensionResponseError) as error:
            client.get("status")
        assert API_KEY not in str(error.value)
    finally:
        client.close()


def test_connectivity_method_is_authenticated_and_testable():
    seen = {}

    def handler(request: httpx.Request):
        seen["authorization"] = request.headers["Authorization"]
        return httpx.Response(200, json={"healthy": True})

    client = client_for(handler)
    try:
        assert client.check_connectivity("/") == {"healthy": True}
        assert seen["authorization"] == f"Bearer {API_KEY}"
    finally:
        client.close()


def test_bulk_call_status_methods_use_documented_paths_and_parse_lines():
    paths = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        if request.url.path.endswith("/live-status"):
            return httpx.Response(200, json={"campaign_status": "in_progress"})
        return httpx.Response(200, json={"records": [{"id": "line-1", "status": "completed"}]})

    client = client_for(handler)
    provider = OmniDimensionCallProvider(client)
    try:
        assert provider.get_bulk_call_live_status("bulk-1")["campaign_status"] == "in_progress"
        assert provider.get_bulk_call_lines("bulk-1")[0]["id"] == "line-1"
        assert paths == [
            "/api/v1/bulk-call/bulk-1/live-status",
            "/api/v1/calls/bulk_call/bulk-1/lines",
        ]
    finally:
        client.close()


def test_dispatch_does_not_treat_request_id_as_bulk_call_id():
    client = client_for(lambda _: httpx.Response(
        200,
        json={"success": True, "status": "dispatched", "requestId": 3166940},
    ))
    provider = OmniDimensionCallProvider(client)
    try:
        result = provider.dispatch(
            agent_id=1,
            to_number="+15551234567",
            from_number_id=2,
            call_context={},
            metadata={},
        )
        assert result.provider_request_id == "3166940"
        assert result.provider_bulk_call_id is None
        assert result.provider_line_id is None
        assert result.provider_call_id is None
    finally:
        client.close()


@pytest.mark.parametrize("field", ["bulk_call_id", "bulkCallId"])
def test_dispatch_persists_only_explicit_bulk_call_identifier(field):
    client = client_for(lambda _: httpx.Response(
        200,
        json={"requestId": 10, field: 20, "lineId": 30, "callId": 40},
    ))
    provider = OmniDimensionCallProvider(client)
    try:
        result = provider.dispatch(
            agent_id=1,
            to_number="+15551234567",
            from_number_id=2,
            call_context={},
            metadata={},
        )
        assert result.provider_request_id == "10"
        assert result.provider_bulk_call_id == "20"
        assert result.provider_line_id == "30"
        assert result.provider_call_id == "40"
    finally:
        client.close()


def test_call_log_lookup_uses_documented_call_log_data_and_request_id():
    paths = []

    def handler(request: httpx.Request):
        paths.append(request.url.path)
        if request.url.path.endswith("/calls/logs/99"):
            return httpx.Response(200, json={"call_log_data": [{"id": 99, "call_status": "completed"}]})
        return httpx.Response(200, json={
            "call_log_data": [
                {"id": 98, "call_request_id": {"id": 6}},
                {"id": 99, "call_request_id": {"id": 7}},
            ]
        })

    client = client_for(handler)
    provider = OmniDimensionCallProvider(client)
    try:
        record = provider.get_call_log_by_request_id("7")
        assert record["id"] == 99
        assert record["call_status"] == "completed"
        assert record["call_request_id"] == {"id": 7}
        assert paths == ["/api/v1/calls/logs", "/api/v1/calls/logs/99"]
    finally:
        client.close()


def test_call_log_lookup_finds_request_id_on_second_page():
    requests = []

    def handler(request: httpx.Request):
        requests.append((request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/calls/logs/199"):
            return httpx.Response(200, json={"call_log_data": [{"id": 199, "call_status": "completed"}]})
        page = request.url.params.get("pageno")
        if page == "1":
            return httpx.Response(200, json={
                "call_log_data": [
                    {"id": index, "call_request_id": {"id": index}}
                    for index in range(150)
                ]
            })
        return httpx.Response(200, json={
            "call_log_data": [{"id": 199, "call_request_id": {"id": 999}}]
        })

    client = client_for(handler)
    provider = OmniDimensionCallProvider(client)
    try:
        record = provider.get_call_log_by_request_id("999")
        assert record["id"] == 199
        assert record["call_status"] == "completed"
        assert requests == [
            ("/api/v1/calls/logs", {"pageno": "1", "pagesize": "150"}),
            ("/api/v1/calls/logs", {"pageno": "2", "pagesize": "150"}),
            ("/api/v1/calls/logs/199", {}),
        ]
    finally:
        client.close()


@pytest.mark.parametrize(
    ("provider_status", "internal_status"),
    [
        ("IN PROGRESS", "in_progress"),
        ("completed", "completed"),
        ("failed", "failed"),
        ("busy", "busy"),
        ("no-answer", "no_answer"),
        ("voicemail_detected", "voicemail"),
        ("cancelled", "canceled"),
        ("skipped", "skipped"),
    ],
)
def test_bulk_call_status_normalization(provider_status, internal_status):
    assert normalize_omni_call_status(provider_status) == internal_status


def test_unknown_bulk_call_status_is_not_guessed():
    assert normalize_omni_call_status("provider_new_state") is None
