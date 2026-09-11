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
    provider_call_id = _first_value(payload, {"provider_call_id", "requestId", "request_id", "call_log_id", "id"})
    provider_status = _first_value(payload, {"call_status"}) or _first_value(payload, {"status"})
    recording_url = _first_value(payload, {"recording_url"})
    return {
        "local_call_id": metadata.get("local_call_id"),
        "metadata_tenant_id": metadata.get("tenant_id"),
        "provider_call_id": str(provider_call_id) if provider_call_id is not None else None,
        "provider_status": provider_status,
        "status": normalize_status(provider_status),
        "duration_seconds": _parse_duration(_first_value(payload, {"call_duration", "duration_seconds"})),
        "transcript": _first_value(payload, {"call_conversation", "full_conversation", "transcript"}),
        "summary": _first_value(payload, {"summary", "call_summary"}),
        "recording_url": recording_url if isinstance(recording_url, str) else None,
        "sentiment": _first_value(payload, {"sentiment_score", "sentiment"}),
        "extracted_attributes": _first_value(payload, {"extracted_variables", "extracted_attributes"}),
        "ended_at": _parse_datetime(_first_value(payload, {"ended_at", "time_of_call", "create_date"})),
    }
