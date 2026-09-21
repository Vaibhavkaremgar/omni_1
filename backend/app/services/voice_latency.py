"""Non-invasive extraction of voice-turn timing data supplied by the provider."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


NOT_OBSERVABLE = "NOT_OBSERVABLE_FROM_PONTIS"
EVENT_KEYS = {
    "speech_end": ("speech_end_timestamp", "speech_end_at", "speech_end_time"),
    "stt_final": ("stt_final_timestamp", "stt_final_at", "stt_final_time"),
    "llm_start": ("llm_start_timestamp", "llm_start_at", "llm_start_time"),
    "llm_first_token": ("llm_first_token_timestamp", "llm_first_token_at", "llm_first_token_time"),
    "llm_complete": ("llm_complete_timestamp", "llm_complete_at", "llm_complete_time"),
    "tts_start": ("tts_start_timestamp", "tts_start_at", "tts_start_time"),
    "tts_first_audio": ("tts_first_audio_timestamp", "tts_first_audio_at", "tts_first_audio_time"),
    "response_complete": ("response_complete_timestamp", "response_complete_at", "response_complete_time"),
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _find_explicit_timing(payload: dict[str, Any], names: tuple[str, ...]) -> str | None:
    sources = [payload]
    for key in ("latency", "timings", "timing", "call_report", "metadata"):
        value = payload.get(key)
        if isinstance(value, dict):
            sources.append(value)
    for source in sources:
        for name in names:
            found = _timestamp(source.get(name))
            if found:
                return found
    return None


def measure_provider_timing(payload: dict[str, Any], received_at: str | None = None) -> dict[str, Any]:
    """Return only provider-supplied timestamps and arithmetic from valid pairs."""
    timestamps = {name: _find_explicit_timing(payload, keys) for name, keys in EVENT_KEYS.items()}
    timestamps["provider_event_received"] = _timestamp(received_at) or utc_now_iso()
    durations: dict[str, float | str] = {}

    def difference(label: str, start: str, end: str) -> None:
        if not timestamps.get(start) or not timestamps.get(end):
            durations[label] = NOT_OBSERVABLE
            return
        left = datetime.fromisoformat(timestamps[start])
        right = datetime.fromisoformat(timestamps[end])
        value = (right - left).total_seconds()
        durations[label] = round(value, 6) if value >= 0 else NOT_OBSERVABLE

    difference("stt_latency_seconds", "speech_end", "stt_final")
    difference("llm_first_token_latency_seconds", "stt_final", "llm_first_token")
    difference("llm_total_latency_seconds", "llm_start", "llm_complete")
    difference("tts_first_audio_latency_seconds", "llm_first_token", "tts_first_audio")
    difference("total_measurable_turn_latency_seconds", "speech_end", "response_complete")
    return {"timestamps": timestamps, "durations": durations}
