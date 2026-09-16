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
    termination_reason = _first_value(payload, {"end_reason", "termination_reason", "hangup_reason", "disconnect_reason", "call_end_reason", "reason"})
    termination_source = _first_value(payload, {"termination_source", "end_source", "hangup_source", "disconnected_by"})
    recording_url = payload.get("recording_url") or report.get("recording_url")
    transcript = (payload.get("call_conversation") or payload.get("full_conversation") or
                  payload.get("transcript") or report.get("full_conversation"))
    extracted = payload.get("extracted_variables") or payload.get("extracted_attributes") or report.get("extracted_variables")
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else report.get("analysis") if isinstance(report.get("analysis"), dict) else {}
    sentiment = payload.get("sentiment_score") or payload.get("sentiment") or report.get("sentiment") or analysis.get("sentiment")
    structured = payload.get("interactions") or payload.get("call_log_data") or report.get("interactions")
    transcript_data = _parse_turns(structured)
    extracted = extracted or analysis.get("extracted_variables") or analysis.get("extracted_attributes")
    return {
        "local_call_id": metadata.get("local_call_id"),
        "metadata_tenant_id": metadata.get("tenant_id"),
        "provider_call_id": str(provider_call_id) if provider_call_id is not None else None,
        "provider_status": provider_status,
        "termination_reason": str(termination_reason) if termination_reason is not None else None,
        "termination_source": str(termination_source) if termination_source is not None else None,
        "status": normalize_status(provider_status),
        "duration_seconds": _parse_duration(payload.get("call_duration") or payload.get("call_duration_in_seconds") or payload.get("duration_seconds") or report.get("duration")),
        "transcript": transcript,
        "transcript_data": transcript_data,
        "summary": payload.get("summary") or payload.get("call_summary") or report.get("summary") or analysis.get("summary"),
        "recording_url": recording_url if isinstance(recording_url, str) else None,
        "sentiment": sentiment,
        "extracted_attributes": extracted,
        "outcome": payload.get("outcome") or payload.get("call_outcome") or report.get("outcome") or analysis.get("outcome"),
        "customer_intent": payload.get("customer_intent") or analysis.get("customer_intent"),
        "key_points": payload.get("key_points") or analysis.get("key_points"),
        "action_items": payload.get("action_items") or analysis.get("action_items"),
        "follow_up_required": payload.get("follow_up_required") if payload.get("follow_up_required") is not None else analysis.get("follow_up_required"),
        "follow_up_notes": payload.get("follow_up_notes") or analysis.get("follow_up_notes"),
        "started_at": _parse_datetime(_first_value(payload, {"started_at", "start_time", "call_start_time"})),
        "ended_at": _parse_datetime(_first_value(payload, {"ended_at", "time_of_call", "create_date"})),
    }


def _parse_turns(value: Any) -> list[dict[str, str]] | None:
    """Preserve provider turn order while accepting known Omni field shapes."""
    if not isinstance(value, list):
        return None
    turns: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or item.get("role") or "").casefold()
        if speaker in {"user", "caller", "customer", "human"}:
            speaker = "customer"
        elif speaker in {"assistant", "agent", "ai", "bot", "llm"}:
            speaker = "assistant"
        pairs = [(speaker, item.get("text") or item.get("content"))] if speaker else []
        if not pairs:
            pairs = [("customer", item.get("user_query")), ("assistant", item.get("bot_response"))]
        for normalized_speaker, text in pairs:
            if text not in (None, ""):
                turns.append({"speaker": normalized_speaker, "text": str(text)})
    return turns or None
