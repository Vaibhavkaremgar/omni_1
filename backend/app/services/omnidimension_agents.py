from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from app.integrations.omnidimension import OmniDimensionAgentProvider, ProviderAgent
from app.models.ai_employee import AIEmployee
from app.models.ai_employee_version import AIEmployeeVersion
from app.services.employee_prompt import build_employee_prompt
from app.services.voice_catalog import voice_catalog
from app.core.config import get_settings

logger = logging.getLogger(__name__)


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
        logger.info(
            "Omni agent payload employee_id=%s employee_name=%s language=%s sections=%d prompt_chars=%d "
            "welcome_chars=%d languages=%s dynamic_welcome=%s voice_configured=%s",
            employee.id, employee.name, (version.configuration or {}).get("language", employee.language),
            len(payload.get("context_breakdown", [])),
            sum(len(str(section.get("body", ""))) for section in payload.get("context_breakdown", [])),
            len(payload.get("welcome_message", "")), payload.get("languages"),
            payload.get("is_welcome_message_dynamic", False), "voice" in payload,
        )
        provider_agent = (
            self.provider.update_agent(version.provider_agent_id, payload)
            if version.provider_agent_id
            else self.provider.create_agent(payload)
        )
        try:
            readback = self.provider.get_agent(provider_agent.provider_id)
            sections = readback.get("context_breakdown") if isinstance(readback, dict) else None
            logger.info(
                "Omni agent readback agent_id=%s sections=%d prompt_chars=%d languages=%s welcome_chars=%d",
                provider_agent.provider_id,
                len(sections) if isinstance(sections, list) else 0,
                sum(len(str(item.get("body", ""))) for item in sections if isinstance(item, dict)) if isinstance(sections, list) else 0,
                readback.get("languages") if isinstance(readback, dict) else None,
                len(str(readback.get("welcome_message", ""))) if isinstance(readback, dict) else 0,
            )
        except Exception as exc:
            logger.warning("Omni agent readback unavailable agent_id=%s exception_class=%s", provider_agent.provider_id, type(exc).__name__)
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

    context.append({
        "title": "Multi-Turn Conversation Behavior",
        "body": (
            "Continue listening and responding after every caller turn. Ask the next relevant question when information is incomplete. "
            "Do not treat the first answer as task completion and do not end the call after one response. "
            "Only close when the objective is complete, the caller explicitly wants to end, or an explicit configured termination condition is satisfied."
        ),
        "is_enabled": True,
    })

    additional = configuration.get("additional_information") or configuration.get("other_information")
    if additional:
        context.append({"title": "Additional Context", "body": _text(additional), "is_enabled": True})

    system_prompt = configuration.get("system_prompt")
    if system_prompt and _text(system_prompt) != _text(configuration.get("direct_prompt")):
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
    language_rules = f"Speak in {lang}. Be clear, professional, and concise."
    if lang == "Telugu":
        language_rules += " Converse naturally in Telugu throughout the call. English business terms are allowed, but do not switch languages because of occasional English words; switch only when the caller explicitly asks for another language."
    context.append({
        "title": "Language & Communication Rules",
        "body": language_rules,
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
    canonical_prompt = _text(configuration.get("final_prompt")) or build_employee_prompt(configuration)
    if canonical_prompt:
        context.append({
            "title": "Complete Employee Instructions",
            "body": canonical_prompt,
            "is_enabled": True,
        })
    logger.info(
        "Canonical employee context employee_id=%s context_chars=%d context_words=%d language=%s model=%s welcome_chars=%d",
        getattr(employee, "id", "unknown"), len(canonical_prompt), len(canonical_prompt.split()), lang,
        _text(configuration.get("llm_model"), employee.llm_model),
        len(_welcome_message(employee, configuration, lang)),
    )
    settings = get_settings()
    webhook_url = f"{settings.backend_public_url.rstrip('/')}/api/v1/webhooks/omnidimension/post-call"
    post_call_actions: dict[str, Any] = {
        "webhook": {
            "url": webhook_url,
            "trigger_call_statuses": ["completed", "failed", "no_answer", "busy", "voicemail_detected"],
        }
    }
    extraction = configuration.get("post_call_extraction") or configuration.get("information_to_extract")
    if isinstance(extraction, list):
        variables = [{"key": f"field_{index + 1}", "prompt": str(item)} for index, item in enumerate(extraction) if item]
        if variables:
            post_call_actions["webhook"]["extracted_variables"] = variables
    payload: dict[str, Any] = {
        "name": _text(configuration.get("name"), employee.name),
        "welcome_message": _welcome_message(employee, configuration, lang),
        "context_breakdown": context,
        "model": {"model": _text(configuration.get("llm_model"), employee.llm_model)},
        "languages": [lang],
        "post_call_actions": post_call_actions,
        "is_welcome_message_dynamic": False,
        "is_welcome_message_interruption": True,
        "is_interruption_allowed": True,
    }

    voice = configuration.get("voice")
    if isinstance(voice, dict):
        selected = next((item for item in voice_catalog() if item["id"] == str(voice.get("id", ""))), None)
        if selected:
            payload["voice"] = {"provider": selected["provider"], "voice_id": selected["provider_voice_id"]}

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


def _welcome_message(employee: AIEmployee, configuration: dict[str, Any], language: str) -> str:
    configured = _text(configuration.get("greeting"))
    if configured and (language in {"English", "English (India)", "English (UK)"} or _contains_language_script(configured, language)):
        return configured
    purpose = _safe_purpose(configuration.get("purpose"), employee.purpose)
    name = employee.name
    if language == "Hindi":
        return f"नमस्ते, मैं {name} हूँ। मैं {purpose} में आपकी मदद करने के लिए यहाँ हूँ। आप किस बारे में जानकारी चाहते हैं?"
    if language == "Telugu":
        return f"నమస్కారం, నేను {name}. {purpose} విషయంలో మీకు సహాయం చేయడానికి ఇక్కడ ఉన్నాను. మీకు ఏ సమాచారం కావాలి?"
    if language == "Tamil":
        return f"வணக்கம், நான் {name}. {purpose} தொடர்பாக உங்களுக்கு உதவ இங்கே இருக்கிறேன். உங்களுக்கு என்ன தகவல் தேவை?"
    return f"Hello, I'm {name}. I'm here to help you with {purpose}. What would you like to know?"


def _safe_purpose(configured: Any, employee_purpose: Any) -> str:
    """Never expose an internal schema fallback in a caller-facing greeting."""
    placeholder = "to be defined through the builder"
    for value in (configured, employee_purpose):
        text = _text(value).rstrip(".")
        if text and text.casefold() != placeholder:
            return text
    return "your questions about our configured services"


def _contains_language_script(value: str, language: str) -> bool:
    ranges = {
        "Telugu": (0x0C00, 0x0C7F), "Hindi": (0x0900, 0x097F),
        "Tamil": (0x0B80, 0x0BFF), "Kannada": (0x0C80, 0x0CFF),
        "Malayalam": (0x0D00, 0x0D7F), "Bengali": (0x0980, 0x09FF),
        "Gujarati": (0x0A80, 0x0AFF), "Punjabi": (0x0A00, 0x0A7F),
        "Odia": (0x0B00, 0x0B7F), "Assamese": (0x0980, 0x09FF),
    }
    bounds = ranges.get(language)
    return bool(bounds and any(bounds[0] <= ord(char) <= bounds[1] for char in value))


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
        "or-IN": "Odia",
        "as-IN": "Assamese",
    }.get(value, value)
