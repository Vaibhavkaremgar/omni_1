from app.integrations.omnidimension.calls import OmniDimensionCallProvider


def test_normalizes_call_and_ordered_interaction_latency_without_pii():
    result = OmniDimensionCallProvider.normalize_provider_latency({
        "p50_latency": 1.2,
        "p99_latency": 3.4,
        "metric_score_latency": 2.0,
        "interactions": [
                {"interaction_sequence": 2, "asr_time": 0.2, "llm2_time": 0.8, "tts_time": 0.1, "total_response_time": 1.1, "tts_speaking_duration": 2.0, "time_of_call": "2026-09-19 10:00:02", "user_query": "private"},
            {"interaction_sequence": 1, "asr_time": 0.1, "latency_llm": 0.5, "latency_tts": 0.2, "total_response_time": 0.8, "tts_speaking_duration": 1.0, "time_of_call": "2026-09-19 10:00:01"},
        ],
    })
    assert result["source"] == "omnidimension_call_logs"
    assert [item["interaction_sequence"] for item in result["interactions"]] == [2, 1]
    assert "user_query" not in result["interactions"][0]
    assert result["interactions"][0]["latency_llm"] == 0.8
    assert result["interactions"][0]["latency_tts"] == 0.1


def test_omits_missing_and_malformed_latency_values_without_zero_defaults():
    result = OmniDimensionCallProvider.normalize_provider_latency({
        "p50_latency": "not-a-number",
        "p99_latency": -1,
        "interactions": [{"interaction_sequence": 1, "asr_time": 0, "latency_llm": None, "latency_tts": "bad"}],
    })
    assert result["interactions"] == [{"interaction_sequence": 1, "asr_time": 0}]
    assert "p50_latency" not in result
    assert "p99_latency" not in result


def test_missing_latency_section_returns_none():
    assert OmniDimensionCallProvider.normalize_provider_latency({"call_status": "completed"}) is None
