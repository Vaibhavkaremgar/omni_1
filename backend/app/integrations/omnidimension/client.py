from __future__ import annotations

from collections.abc import Mapping
from typing import Any
import json
import re

import httpx
import logging

from app.core.config import Settings, get_settings

from .exceptions import (
    OmniDimensionAuthenticationError,
    OmniDimensionClientError,
    OmniDimensionConfigurationError,
    OmniDimensionNetworkError,
    OmniDimensionResponseError,
    OmniDimensionServerError,
)

logger = logging.getLogger(__name__)


class OmniDimensionClient:
    """Small authenticated HTTP client for future OmniDimension services."""

    def __init__(
        self,
        settings: Settings | Any | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.api_key = (getattr(self.settings, "omnidimension_api_key", None) or "").strip()
        self.base_url = (getattr(self.settings, "omnidimension_base_url", "") or "").strip().rstrip("/")
        self.timeout = float(getattr(self.settings, "omnidimension_timeout_seconds", 15.0))
        if not self.api_key:
            raise OmniDimensionConfigurationError("OmniDimension API key is not configured.")
        if not self.base_url:
            raise OmniDimensionConfigurationError("OmniDimension base URL is not configured.")
        if self.timeout <= 0:
            raise OmniDimensionConfigurationError("OmniDimension timeout must be positive.")
        self.client = client or httpx.Client(timeout=self.timeout)
        self.last_response: dict[str, Any] | None = None
        logger.info(
            "Omni configuration environment=%s omni_base_url=%s omni_auth_configured=%s auth_mechanism=bearer_api_key",
            getattr(self.settings, "environment", "unknown"), self.base_url, bool(self.api_key),
        )

    def get(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, *, json: Any = None, params: Mapping[str, Any] | None = None,
             headers: Mapping[str, str] | None = None) -> Any:
        return self._request("POST", path, json=json, params=params, extra_headers=headers)

    def patch(self, path: str, *, json: Any = None, params: Mapping[str, Any] | None = None) -> Any:
        return self._request("PATCH", path, json=json, params=params)

    def put(self, path: str, *, json: Any = None, params: Mapping[str, Any] | None = None) -> Any:
        return self._request("PUT", path, json=json, params=params)

    def delete(self, path: str, *, params: Mapping[str, Any] | None = None) -> Any:
        return self._request("DELETE", path, params=params)

    def check_connectivity(self, path: str = "/") -> Any:
        """Make a safe authenticated GET for internal readiness diagnostics."""

        return self.get(path)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "OmniDimensionClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        headers.update(kwargs.pop("extra_headers", None) or {})
        if "json" in kwargs and kwargs["json"] is not None:
            headers["Content-Type"] = "application/json"
            logger.info(
                "[OMNI_REQUEST_JSON] method=%s path=%s json=%s",
                method, "/" + path.lstrip("/"),
                json.dumps(_redact(kwargs["json"]), ensure_ascii=False, separators=(",", ":")),
            )
            _log_agent_payload_diagnostic(method, path, kwargs["json"])
        try:
            response = self.client.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            logger.warning(
                "Omni HTTP diagnostic method=%s path=%s params=%s exception_class=%s exception_message=%s",
                method, "/" + path.lstrip("/"), _safe_params(kwargs.get("params")), type(exc).__name__, _safe_exception(exc),
            )
            raise OmniDimensionNetworkError("Omni provider connection failed", exception_class=type(exc).__name__, exception_message=_safe_exception(exc)) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "Omni HTTP diagnostic method=%s path=%s params=%s exception_class=%s exception_message=%s",
                method, "/" + path.lstrip("/"), _safe_params(kwargs.get("params")), type(exc).__name__, _safe_exception(exc),
            )
            raise OmniDimensionNetworkError("Omni provider connection failed", exception_class=type(exc).__name__, exception_message=_safe_exception(exc)) from exc

        provider_code, provider_message = self._safe_error_details(response)
        self.last_response = {
            "method": method,
            "path": "/" + path.lstrip("/"),
            "status": response.status_code,
            "request_id": response.headers.get("x-request-id") or response.headers.get("request-id"),
            "body": _redact(_response_value(response)),
        }
        logger.warning(
            "Omni HTTP diagnostic method=%s path=%s params=%s response_status=%s response_body=%s",
            method, "/" + path.lstrip("/"), _safe_params(kwargs.get("params")), response.status_code, _safe_response_body(response),
        )
        response_marker = "[OMNI_GET_RESPONSE]" if method == "GET" and path.lstrip("/").startswith("agents/") else "[OMNI_RESPONSE]"
        logger.info(
            "%s method=%s path=%s status=%s request_id=%s body=%s",
            response_marker,
            method, "/" + path.lstrip("/"), response.status_code,
            response.headers.get("x-request-id") or response.headers.get("request-id") or "unknown",
            _safe_response_body(response, limit=None),
        )
        _log_provider_warnings(method, path, response)
        if response.status_code >= 400:
            logger.warning(
                "Omni provider response path=%s status=%s code=%s message=%s",
                "/" + path.lstrip("/"), response.status_code, provider_code, provider_message,
            )
        if response.status_code in (401, 403):
            raise OmniDimensionAuthenticationError(response.status_code, provider_message)
        if 400 <= response.status_code < 500:
            raise OmniDimensionClientError(response.status_code, provider_code, provider_message)
        if response.status_code >= 500:
            raise OmniDimensionServerError(response.status_code, provider_code, provider_message)
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except (ValueError, TypeError) as exc:
            raise OmniDimensionResponseError("OmniDimension returned malformed JSON.") from exc

    @staticmethod
    def _safe_error_details(response: httpx.Response) -> tuple[str | None, str | None]:
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return None, None
        if not isinstance(payload, dict):
            return None, None
        code = payload.get("code") or payload.get("error_code")
        message = payload.get("message") or payload.get("error") or payload.get("detail")
        return (str(code)[:120] if isinstance(code, (str, int)) else None,
                str(message)[:500] if isinstance(message, str) else None)


def _safe_params(params: Any) -> dict[str, str]:
    if not isinstance(params, Mapping):
        return {}
    return {key: str(params[key])[:80] for key in ("region", "carrier") if key in params}


def _log_agent_payload_diagnostic(method: str, path: str, payload: Any) -> None:
    if not isinstance(payload, Mapping) or not isinstance(payload.get("context_breakdown"), list):
        return
    sections = payload["context_breakdown"]
    details = [
        {"title": str(item.get("title", "")), "chars": len(str(item.get("body", "")))}
        for item in sections if isinstance(item, Mapping)
    ]
    context_text = " ".join(str(item.get("body", "")) for item in sections if isinstance(item, Mapping))
    logger.info(
        "Omni serialized agent payload method=%s path=%s name=%s language=%s model=%s voice=%s "
        "sections=%d section_details=%s context_chars=%d context_words=%d welcome_chars=%d "
        "post_call_webhook=%s post_call_webhook_config=%s context_breakdown_present=%s",
        method, "/" + path.lstrip("/"), str(payload.get("name", "")), payload.get("languages"),
        payload.get("model"), bool(payload.get("voice")), len(details), details, len(context_text),
        len(context_text.split()), len(str(payload.get("welcome_message", ""))),
        bool((payload.get("post_call_actions") or {}).get("webhook")),
        _redact((payload.get("post_call_actions") or {}).get("webhook")), True,
    )


def _log_provider_warnings(method: str, path: str, response: httpx.Response) -> None:
    """Surface provider warnings embedded in an otherwise successful response."""
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return
    if not isinstance(payload, Mapping):
        return
    warnings = payload.get("warnings") or payload.get("warning") or payload.get("issues")
    if warnings:
        logger.warning(
            "[OMNI_PROVIDER_WARNING] method=%s path=%s status=%s warnings=%s",
            method, "/" + path.lstrip("/"), response.status_code,
            json.dumps(_redact(warnings), ensure_ascii=False),
        )


def _safe_response_body(response: httpx.Response, *, limit: int | None = 1000) -> str:
    value = _response_value(response)
    body = json.dumps(_redact(value), ensure_ascii=False)
    return body if limit is None else body[:limit]


def _response_value(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (ValueError, TypeError):
        return response.text


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): ("[redacted]" if str(key).lower() in {"authorization", "api_key", "token", "password", "pan", "aadhaar", "aadhar", "otp", "mobile", "email"} else _redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _safe_exception(exc: Exception) -> str:
    return re.sub(r"(?i)(bearer\s+|api[_-]?key=|token=)[^\s]+", r"\1[redacted]", str(exc))[:500]
