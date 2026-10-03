from types import SimpleNamespace

from app.services.omnidimension_capability_probe import OmniDimensionCapabilityProbe


def test_probe_recursively_classifies_fields_and_redacts_shape():
    calls = SimpleNamespace(
        list_call_logs=lambda **_: {"call_log_data": [{"id": 4}]},
        get_call_log_by_request_id=lambda _: {
            "id": 4,
            "call_status": "completed",
            "nested": {"call_duration_in_minutes": 2, "recording_url": "https://secret"},
            "transcript": "private conversation",
        },
    )
    agents = SimpleNamespace(get_agent=lambda _: {"post_call_config_ids": None})
    result = OmniDimensionCapabilityProbe(calls, agents).run(request_id="req-1", agent_id="7")

    assert result["account"]["authentication"] == "SUCCESS"
    assert result["fields"]["duration_minutes"]["provider_returned"] is True
    assert result["fields"]["duration_minutes"]["pontis_currently_consumes"] is False
    assert result["fields"]["recording"]["paths"] == ["nested.recording_url"]
    assert result["sanitized_record_shape"]["type"] == "object"
    assert result["post_call"]["post_call_config_ids"] is None


def test_probe_reports_missing_call_without_inventing_one():
    calls = SimpleNamespace(list_call_logs=lambda **_: {"call_log_data": []}, get_call_log_by_request_id=lambda _: None)
    agents = SimpleNamespace(get_agent=lambda _: {"post_call_config_ids": []})
    result = OmniDimensionCapabilityProbe(calls, agents).run(request_id="missing")

    assert result["call_lookup"]["status"] == "NOT_FOUND"
    assert result["limitations"] == []
    assert result["post_call"]["status"] == "UNKNOWN"
