from app.services.voice_latency import NOT_OBSERVABLE, measure_provider_timing


def test_missing_live_voice_events_are_explicitly_unobservable():
    result = measure_provider_timing({"status": "completed"}, "2026-09-19T10:00:00Z")
    assert result["timestamps"]["provider_event_received"] == "2026-09-19T10:00:00+00:00"
    assert result["timestamps"]["speech_end"] is None
    assert result["durations"]["stt_latency_seconds"] == NOT_OBSERVABLE
    assert result["durations"]["total_measurable_turn_latency_seconds"] == NOT_OBSERVABLE


def test_provider_supplied_turn_timestamps_are_measured_without_transcript_logging():
    payload = {
        "timings": {
            "speech_end_at": "2026-09-19T10:00:00Z",
            "stt_final_at": "2026-09-19T10:00:00.200Z",
            "llm_start_at": "2026-09-19T10:00:00.210Z",
            "llm_first_token_at": "2026-09-19T10:00:00.500Z",
            "llm_complete_at": "2026-09-19T10:00:01Z",
            "tts_start_at": "2026-09-19T10:00:00.520Z",
            "tts_first_audio_at": "2026-09-19T10:00:00.700Z",
            "response_complete_at": "2026-09-19T10:00:01.100Z",
        }
    }
    result = measure_provider_timing(payload, "2026-09-19T10:00:02Z")
    assert result["durations"] == {
        "stt_latency_seconds": 0.2,
        "llm_first_token_latency_seconds": 0.3,
        "llm_total_latency_seconds": 0.79,
        "tts_first_audio_latency_seconds": 0.2,
        "total_measurable_turn_latency_seconds": 1.1,
    }
