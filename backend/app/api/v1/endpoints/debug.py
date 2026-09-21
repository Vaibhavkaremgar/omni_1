from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.config import get_settings
from app.integrations.omnidimension import OmniDimensionCallProvider, OmniDimensionClient
from app.integrations.omnidimension.exceptions import OmniDimensionError
from app.services.auth import AuthenticatedUser, require_admin


router = APIRouter(prefix="/debug", tags=["debug"])


def _admin(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    return require_admin(user)


def _provider() -> OmniDimensionCallProvider:
    return OmniDimensionCallProvider(OmniDimensionClient(get_settings()))


@router.get("/omni-call-by-request/{request_id}")
def get_omni_call_by_request(
    request_id: str,
    _: AuthenticatedUser = Depends(_admin),
) -> dict[str, Any]:
    request_id = request_id.strip()
    if not request_id or len(request_id) > 64 or not request_id.isdigit():
        raise HTTPException(status_code=400, detail="request_id must be a numeric OmniDimension request ID.")

    provider = _provider()
    try:
        summary = _find_request_record(provider, request_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="OmniDimension call request was not found.")
        detail = _detail_record(provider, summary)
    except HTTPException:
        raise
    except OmniDimensionError as exc:
        raise HTTPException(status_code=502, detail="Unable to retrieve the OmniDimension diagnostic record.") from exc
    finally:
        provider.client.close()

    record = detail or summary
    return _diagnostic_response(request_id, summary, record, detail_available=detail is not None)


def _find_request_record(provider: OmniDimensionCallProvider, request_id: str) -> dict[str, Any] | None:
    # The provider returns newest records first. Search all available pages so
    # an older test call is still discoverable without a second auth path.
    page = 1
    while page <= 20:
        response = provider.list_call_logs(page=page, page_size=150)
        rows = response.get("call_log_data") if isinstance(response, dict) else None
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict) and _request_id_from_record(row) == request_id:
                return row
        if len(rows) < 150:
            return None
        page += 1
    return None


def _detail_record(provider: OmniDimensionCallProvider, summary: dict[str, Any]) -> dict[str, Any] | None:
    call_id = _call_id_from_record(summary)
    if call_id is None:
        return None
    response = provider.get_call_log(call_id)
    if isinstance(response, dict):
        rows = response.get("call_log_data")
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows[0]
        return response
    return None


def _request_id_from_record(record: dict[str, Any]) -> str | None:
    value = record.get("call_request_id")
    if isinstance(value, dict):
        value = value.get("id")
    if value in (None, False, ""):
        value = record.get("requestId") or record.get("request_id")
    return str(value) if value not in (None, False, "") else None


def _call_id_from_record(record: dict[str, Any]) -> str | None:
    value = record.get("id") or record.get("call_log_id") or record.get("call_id")
    return str(value) if value not in (None, False, "") else None


def _diagnostic_response(
    request_id: str,
    summary: dict[str, Any],
    record: dict[str, Any],
    *,
    detail_available: bool,
) -> dict[str, Any]:
    interactions = record.get("interactions")
    if not isinstance(interactions, list):
        interactions = []
    speech_detected = any(
        isinstance(item, dict) and _has_text(item.get("user_query"))
        for item in interactions
    )
    response_after_user = any(
        isinstance(item, dict)
        and _has_text(item.get("user_query"))
        and _has_text(item.get("bot_response"))
        for item in interactions
    )
    tts_detected = any(
        isinstance(item, dict) and (_positive_number(item.get("tts_speaking_duration")) or _positive_number(item.get("tts_time")))
        for item in interactions
    )
    issues = record.get("issues")
    if not isinstance(issues, list):
        issues = [issues] if issues not in (None, False, "") else []
    provider_latency = OmniDimensionCallProvider.normalize_provider_latency(record)
    return {
        "request_id": request_id,
        "provider_call_id": _call_id_from_record(record) or _call_id_from_record(summary),
        "agent_id": record.get("agent_id") or record.get("bot_id") or summary.get("agent_id") or summary.get("bot_id"),
        "status": record.get("call_status") or record.get("status"),
        "duration": record.get("call_duration") or record.get("call_duration_in_seconds"),
        "hangup_source": record.get("hangup_source"),
        "hangup_reason": record.get("hangup_reason") or record.get("end_reason") or record.get("termination_reason"),
        "conversation": _safe_value(record.get("call_conversation")),
        "interactions": _safe_value(interactions),
        "caller_speech_detected": speech_detected,
        "llm_response_after_caller_speech": response_after_user,
        "tts_audio_detected": tts_detected,
        "errors_or_issues": _safe_value(issues),
        "timestamps": _safe_value({key: record.get(key) for key in ("time_of_call", "call_date", "start_time", "end_time", "created_at", "create_date") if record.get(key) not in (None, "", False)}),
        "trace": _safe_value({key: record.get(key) for key in ("p50_latency", "p99_latency", "metric_score_latency", "prompt_tokens", "completion_tokens", "total_tokens", "llm_prompt", "model_name", "model_type", "asr_service", "tts_service", "has_issue", "interaction_count_total") if key in record}),
        "provider_latency": _safe_value(provider_latency) if provider_latency else {"source": "omnidimension_call_logs", "measurement_type": "provider_reported", "status": "unavailable"},
        "detail_lookup": {"performed": detail_available, "summary_keys": sorted(summary.keys()), "detail_keys": sorted(record.keys())},
    }


def _has_text(value: Any) -> bool:
    return isinstance(value, str) and bool(re.sub(r"<[^>]+>", "", value).strip())


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _safe_value(value: Any) -> Any:
    sensitive_keys = {"phone", "phone_number", "from_number", "to_number", "mobile", "email", "user_email", "customer_phone_number", "recording_url", "internal_recording_url", "call_sid", "authorization", "api_key", "token", "password"}
    if isinstance(value, dict):
        return {str(key): "[redacted]" if str(key).casefold() in sensitive_keys else _safe_value(item) for key, item in value.items() if str(key).casefold() not in {"user_name", "customer_name"}}
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\b[+]?\d[\d\s().-]{6,}\d\b", "[redacted-number]", value)
        value = re.sub(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b", "[redacted-email]", value)
    return value
