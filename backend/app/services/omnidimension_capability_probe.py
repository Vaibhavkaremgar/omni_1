from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.integrations.omnidimension import OmniDimensionAgentProvider, OmniDimensionCallProvider
from app.integrations.omnidimension.exceptions import OmniDimensionError


FIELD_ALIASES = {
    "call_id": {"id", "call_id", "call_log_id", "callid"},
    "request_id": {"requestid", "request_id", "call_request_id"},
    "call_status": {"status", "call_status"},
    "call_start_time": {"time_of_call", "start_time", "started_at", "call_start_time"},
    "call_end_time": {"end_time", "ended_at", "call_end_time", "create_date"},
    "duration_seconds": {"call_duration_in_seconds", "duration_seconds"},
    "duration_minutes": {"call_duration_in_minutes", "duration_minutes"},
    "call_duration": {"call_duration", "duration"},
    "conversation": {"call_conversation", "transcript", "full_conversation", "conversation"},
    "interactions": {"interactions"},
    "recording": {"recording_url", "internal_recording_url", "recording"},
    "extracted_variables": {"extracted_variables", "extracted_attributes", "custom_variables"},
    "sentiment": {"sentiment", "sentiment_score"},
    "summary": {"summary", "call_summary"},
    "outcome": {"outcome", "call_outcome", "intent", "customer_intent"},
    "metadata": {"metadata"},
    "agent_id": {"agent_id", "agentid", "bot_id"},
    "phone_numbers": {"phone", "phone_number", "from_number", "to_number", "mobile"},
    "direction": {"direction"},
    "error": {"error", "errors", "issues", "error_message"},
}

USED_FIELDS = {
    "call_id", "request_id", "call_status", "call_start_time", "duration_seconds",
    "conversation", "recording", "extracted_variables", "sentiment", "summary", "outcome",
}


def _paths(value: Any, prefix: str = "") -> list[tuple[str, str, Any]]:
    found: list[tuple[str, str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            found.append((str(key).casefold(), path, child))
            found.extend(_paths(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:20]):
            found.extend(_paths(child, f"{prefix}[{index}]"))
    return found


def _contains(record: Any, aliases: set[str]) -> tuple[bool, list[str]]:
    matches = [path for key, path, _ in _paths(record) if key in aliases]
    return bool(matches), matches[:10]


def _safe_shape(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"type": "string", "length": len(value)}
    if isinstance(value, list):
        return {"type": "array", "length": len(value)}
    if isinstance(value, Mapping):
        return {"type": "object", "keys": sorted(str(k) for k in value)[:100]}
    return {"type": type(value).__name__}


def _safe_agent_config(value: Any, expected_webhook_url: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {"returned": False}
    configs = value.get("post_call_config_ids")
    if configs is None:
        return {"returned": "post_call_config_ids" in value, "post_call_config_ids": None, "count": 0}
    if not isinstance(configs, list):
        configs = [configs]
    output = []
    for item in configs:
        if not isinstance(item, Mapping):
            output.append({"type": type(item).__name__})
            continue
        row = {"id": item.get("id"), "webhook_url": "[REDACTED]" if item.get("webhook_url") else None}
        if expected_webhook_url is not None:
            row["webhook_matches_pontis"] = item.get("webhook_url") == expected_webhook_url
        for key in ("trigger_call_statuses", "trigger_statuses", "extracted_variables", "extracted_attributes"):
            if key in item:
                row[key] = _safe_shape(item[key]) if key.startswith("extracted") else item[key]
        output.append(row)
    return {"returned": True, "count": len(output), "configurations": output}


class OmniDimensionCapabilityProbe:
    def __init__(self, calls: OmniDimensionCallProvider, agents: OmniDimensionAgentProvider):
        self.calls = calls
        self.agents = agents

    def run(self, *, request_id: str | None = None, agent_id: str | None = None) -> dict[str, Any]:
        report: dict[str, Any] = {"report": "OMNIDIMENSION CAPABILITY REPORT", "read_only": True, "account": {}, "endpoints": [], "fields": {}, "provider_fields_returned_but_unused": [], "limitations": []}
        try:
            logs = self.calls.list_call_logs(page=1, page_size=1)
            report["account"] = {"authentication": "SUCCESS", "api_reachable": True}
            report["endpoints"].append({"endpoint": "GET /calls/logs", "accessible": True})
            record = None
            if request_id:
                record = self.calls.get_call_log_by_request_id(request_id)
                if record is None:
                    report["call_lookup"] = {"request_id": request_id, "status": "NOT_FOUND"}
                else:
                    report["call_lookup"] = {"request_id": request_id, "status": "SUCCESS"}
                    report["endpoints"].append({"endpoint": "GET /calls/logs/{id}", "accessible": True})
            else:
                report["limitations"].append("A completed call request_id is required for call-field inspection.")
            if record is not None:
                for name, aliases in FIELD_ALIASES.items():
                    present, paths = _contains(record, aliases)
                    report["fields"][name] = {"provider_returned": present, "pontis_currently_consumes": name in USED_FIELDS, "pontis_currently_stores": name in USED_FIELDS, "paths": paths}
                report["provider_fields_returned_but_unused"] = [name for name, item in report["fields"].items() if item["provider_returned"] and not item["pontis_currently_consumes"]]
                report["sanitized_record_shape"] = _safe_shape(record)
            else:
                report["fields"] = {name: {"provider_returned": "UNKNOWN", "pontis_currently_consumes": name in USED_FIELDS, "pontis_currently_stores": name in USED_FIELDS} for name in FIELD_ALIASES}
        except OmniDimensionError as exc:
            report["account"] = {"authentication": "ERROR_RETRIEVING", "api_reachable": False, "error_type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}

        target_agent_id = agent_id
        if target_agent_id is None and isinstance(record, Mapping):
            candidate = record.get("agent_id") or record.get("bot_id")
            target_agent_id = str(candidate) if candidate not in (None, "") else None
        if target_agent_id:
            try:
                agent = self.agents.get_agent(target_agent_id)
                report["endpoints"].append({"endpoint": "GET /agents/{agent_id}", "accessible": True})
                from app.services.omnidimension_agents import _automatic_post_call_actions
                expected = _automatic_post_call_actions()["webhook"]["url"]
                report["post_call"] = _safe_agent_config(agent, expected)
                report["post_call"]["webhook_configuration_verified"] = any(item.get("webhook_matches_pontis") is True for item in report["post_call"].get("configurations", []))
            except OmniDimensionError as exc:
                report["post_call"] = {"status": "ERROR_RETRIEVING", "error_type": type(exc).__name__, "status_code": getattr(exc, "status_code", None)}
        else:
            report["post_call"] = {"status": "UNKNOWN", "limitation": "agent_id is required to verify persisted post-call configuration."}
        return report


def pontis_consumed_field_names() -> set[str]:
    return set(USED_FIELDS)
