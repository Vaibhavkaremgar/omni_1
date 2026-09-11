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
