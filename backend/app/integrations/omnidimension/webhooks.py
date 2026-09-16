from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models.enums import CallStatus


TERMINAL_STATUSES = {
    CallStatus.completed.value,
    CallStatus.failed.value,
    CallStatus.no_answer.value,
    CallStatus.busy.value,
    CallStatus.voicemail.value,
    CallStatus.canceled.value,
}


def _first_value(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key] not in (None, ""):
                return value[key]
        for child in value.values():
            found = _first_value(child, keys)
            if found not in (None, ""):
                return found
    elif isinstance(value, list):
        for child in value:
            found = _first_value(child, keys)
            if found not in (None, ""):
                return found
    return None


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("metadata")
    return value if isinstance(value, dict) else {}


def _parse_duration(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit():
        return int(text)
    parts = text.split(":")
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 60 + int(parts[1])
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for parser in (
        lambda item: datetime.fromisoformat(item.replace("Z", "+00:00")),
        lambda item: datetime.strptime(item, "%d/%m/%Y %H:%M:%S"),
        lambda item: datetime.strptime(item, "%Y-%m-%d %H:%M:%S"),
    ):
        try:
            return parser(text)
        except ValueError:
            continue
    return None


def normalize_status(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "dispatched": CallStatus.queued.value,
        "ringing": CallStatus.ringing.value,
        "in_progress": CallStatus.in_progress.value,
        "completed": CallStatus.completed.value,
        "failed": CallStatus.failed.value,
        "no_answer": CallStatus.no_answer.value,
        "busy": CallStatus.busy.value,
        "voicemail": CallStatus.voicemail.value,
        "canceled": CallStatus.canceled.value,
        "cancelled": CallStatus.canceled.value,
    }.get(normalized)


def parse_post_call(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = _metadata(payload)
    report = payload.get("call_report") if isinstance(payload.get("call_report"), dict) else {}
    provider_call_id = (payload.get("call_log_id") or payload.get("call_id") or
                        payload.get("requestId") or payload.get("request_id") or
                        payload.get("id") or _first_value(payload, {"provider_call_id"}))
    provider_status = payload.get("call_status") or payload.get("status") or report.get("status")
    recording_url = payload.get("recording_url") or report.get("recording_url")
    transcript = (payload.get("call_conversation") or payload.get("full_conversation") or
                  payload.get("transcript") or report.get("full_conversation"))
    extracted = payload.get("extracted_variables") or payload.get("extracted_attributes") or report.get("extracted_variables")
    sentiment = payload.get("sentiment_score") or payload.get("sentiment") or report.get("sentiment")
    structured = payload.get("interactions") or payload.get("call_log_data")
    if isinstance(structured, list):
        transcript_data = [
            {"speaker": "customer", "text": item.get("user_query", "")}
            for item in structured if isinstance(item, dict) and item.get("user_query")
        ] + [
            {"speaker": "assistant", "text": item.get("bot_response", "")}
            for item in structured if isinstance(item, dict) and item.get("bot_response")
        ]
    else:
        transcript_data = None
    return {
        "local_call_id": metadata.get("local_call_id"),
        "metadata_tenant_id": metadata.get("tenant_id"),
        "provider_call_id": str(provider_call_id) if provider_call_id is not None else None,
        "provider_status": provider_status,
        "status": normalize_status(provider_status),
        "duration_seconds": _parse_duration(payload.get("call_duration") or payload.get("call_duration_in_seconds") or payload.get("duration_seconds") or report.get("duration")),
        "transcript": transcript,
        "transcript_data": transcript_data,
        "summary": payload.get("summary") or payload.get("call_summary") or report.get("summary"),
        "recording_url": recording_url if isinstance(recording_url, str) else None,
        "sentiment": sentiment,
        "extracted_attributes": extracted,
        "ended_at": _parse_datetime(_first_value(payload, {"ended_at", "time_of_call", "create_date"})),
    }
