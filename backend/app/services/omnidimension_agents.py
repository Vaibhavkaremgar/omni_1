from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.integrations.omnidimension import OmniDimensionAgentProvider, ProviderAgent
from app.models.ai_employee import AIEmployee
from app.models.ai_employee_version import AIEmployeeVersion


@dataclass(frozen=True)
class AgentSyncResult:
    provider_id: str
    status: str
    metadata: dict[str, Any]


class OmniDimensionAgentService:
    def __init__(self, provider: OmniDimensionAgentProvider):
        self.provider = provider

    def synchronize(self, employee: AIEmployee, version: AIEmployeeVersion) -> AgentSyncResult:
        payload = map_employee_configuration(employee, version.configuration or {})
        provider_agent = (
            self.provider.update_agent(version.provider_agent_id, payload)
            if version.provider_agent_id
            else self.provider.create_agent(payload)
        )
        return _result(provider_agent)


def map_employee_configuration(employee: AIEmployee, configuration: dict[str, Any]) -> dict[str, Any]:
    """
    Translate the structured employee definition into OmniDimension's context_breakdown.
    Each section maps to a distinct behavioral area so the Omni agent follows the
    employee's exact rules rather than behaving as a generic assistant.
    """
    context: list[dict[str, Any]] = []

    # ── Agent Identity & Purpose ──────────────────────────────────────────────
    purpose = _text(configuration.get("purpose"), employee.purpose)
    context.append({"title": "Agent Identity & Purpose", "body": purpose, "is_enabled": True})

    # ── Responsibilities / Goals ──────────────────────────────────────────────
    goals = configuration.get("goals")
    if goals:
        context.append({"title": "Responsibilities", "body": _text(goals), "is_enabled": True})

    # ── Tasks (collected from interview answers) ──────────────────────────────
    tasks = configuration.get("tasks")
    if tasks:
        task_list = tasks if isinstance(tasks, list) else [tasks]
        body = "\n".join(f"- {t}" for t in task_list if t)
        if body:
            context.append({"title": "Tasks", "body": body, "is_enabled": True})

    # ── Products & Services ───────────────────────────────────────────────────
    for key, title in (
        ("products", "Products and Services"),
        ("products_services", "Products and Services"),
    ):
        val = configuration.get(key)
        if val:
            context.append({"title": title, "body": _text(val), "is_enabled": True})
            break

    # ── Target Customers ──────────────────────────────────────────────────────
    target = configuration.get("target_customers")
    if target:
        context.append({"title": "Target Customers", "body": _text(target), "is_enabled": True})

    # ── Conversation Behavior & Tone ──────────────────────────────────────────
    tone_parts: list[str] = []
    for key in ("tone", "personality", "communication_style", "conversation_behavior"):
        val = configuration.get(key)
        if val:
            tone_parts.append(_text(val))
    if tone_parts:
        context.append({"title": "Conversation Behavior", "body": "\n".join(tone_parts), "is_enabled": True})

    # Keep saved flow and explicit instructions as distinct context sections.
    # These come from the reviewed employee definition, never the campaign UI.
    flow_parts: list[str] = []
    for key in ("conversation_flow", "workflow", "call_flow"):
        val = configuration.get(key)
        if val:
            flow_parts.append(_text(val))
    if flow_parts:
        context.append({"title": "Conversation Flow", "body": "\n".join(flow_parts), "is_enabled": True})

    system_prompt = configuration.get("system_prompt")
    if system_prompt:
        context.append({"title": "Additional Behavioral Instructions", "body": _text(system_prompt), "is_enabled": True})

    # ── Qualification / Workflow Rules ────────────────────────────────────────
    qual_parts: list[str] = []
    for key in ("qualification_rules", "qualification_criteria"):
        val = configuration.get(key)
        if val:
            qual_parts.append(_text(val))
    if qual_parts:
        context.append({"title": "Qualification & Workflow Rules", "body": "\n".join(qual_parts), "is_enabled": True})

    # ── Objection Handling ────────────────────────────────────────────────────
    objections = configuration.get("objection_handling") or configuration.get("common_objections")
    if objections:
        context.append({"title": "Objection Handling", "body": _text(objections), "is_enabled": True})

    # ── Human Escalation / Handoff Rules ─────────────────────────────────────
    transfer_parts: list[str] = []
    for key in ("transfer_rules", "human_transfer_conditions"):
        val = configuration.get(key)
        if val:
            transfer_parts.append(_text(val))
    if transfer_parts:
        context.append({"title": "Human Escalation & Handoff Rules", "body": "\n".join(transfer_parts), "is_enabled": True})

    # ── Closing Behavior ──────────────────────────────────────────────────────
    closing = configuration.get("closing_behavior")
    if closing:
        context.append({"title": "Closing Behavior", "body": _text(closing), "is_enabled": True})

    # ── Post-Call Extraction ──────────────────────────────────────────────────
    for key in ("post_call_extraction", "information_to_extract", "lead_outcome_fields"):
        val = configuration.get(key)
        if val:
            context.append({"title": "Post-Call Information to Extract", "body": _text(val), "is_enabled": True})
            break

    # ── Constraints / Guardrails ──────────────────────────────────────────────
    constraints = configuration.get("constraints") or configuration.get("guardrails")
    if constraints:
        context.append({"title": "Constraints & Guardrails", "body": _text(constraints), "is_enabled": True})

    # ── Language / Communication Rules ───────────────────────────────────────
    lang = _language_name(_text(configuration.get("language"), employee.language))
    context.append({
        "title": "Language & Communication Rules",
        "body": f"Speak in {lang}. Be clear, professional, and concise.",
        "is_enabled": True,
    })

    # ── Desired Outcomes (legacy key support) ─────────────────────────────────
    for key, title in (
        ("desired_outcomes", "Desired Outcomes"),
        ("information_to_extract", "Information to Extract"),
    ):
        val = configuration.get(key)
        if val and not any(s["title"] == title for s in context):
            context.append({"title": title, "body": _text(val), "is_enabled": True})

    # ── Build the final payload ───────────────────────────────────────────────
    payload: dict[str, Any] = {
        "name": _text(configuration.get("name"), employee.name),
        "welcome_message": _text(
            configuration.get("greeting"),
            f"Hello! I'm {employee.name}. How can I help you today?",
        ),
        "context_breakdown": context,
        "model": {"model": _text(configuration.get("llm_model"), employee.llm_model)},
        "languages": [lang],
    }

    call_type = configuration.get("call_type", employee.call_type)
    if call_type in {"inbound", "outbound"}:
        payload["call_type"] = "Incoming" if call_type == "inbound" else "Outgoing"

    if closing:
        payload["end_call"] = {
            "condition": _text(closing),
            "message": "Thank you for your time. Have a great day!",
        }

    transfer = configuration.get("transfer")
    if isinstance(transfer, dict) and transfer.get("number") and transfer.get("condition"):
        payload["transfer"] = {"transfer_options": [{
            "number": _text(transfer["number"]),
            "type": "static",
            "transfer_condition": _text(transfer["condition"]),
        }]}

    return payload


def _result(agent: ProviderAgent) -> AgentSyncResult:
    return AgentSyncResult(agent.provider_id, agent.status, agent.metadata)


def _text(value: Any, fallback: str = "") -> str:
    if isinstance(value, list):
        return "\n".join(str(item) for item in value if item)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {item}" for key, item in value.items())
    return str(value).strip() if value is not None and str(value).strip() else fallback


def _language_name(value: str) -> str:
    return {
        "en-US": "English",
        "en-IN": "English (India)",
        "en-GB": "English (UK)",
        "hi-IN": "Hindi",
        "es-ES": "Spanish",
        "fr-FR": "French",
        "de-DE": "German",
        "te-IN": "Telugu",
        "ta-IN": "Tamil",
        "kn-IN": "Kannada",
        "ml-IN": "Malayalam",
        "mr-IN": "Marathi",
        "bn-IN": "Bengali",
        "gu-IN": "Gujarati",
        "pa-IN": "Punjabi",
    }.get(value, value)
