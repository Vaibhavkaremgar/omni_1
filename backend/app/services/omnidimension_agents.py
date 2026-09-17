from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from app.integrations.omnidimension import OmniDimensionAgentProvider, ProviderAgent
from app.models.ai_employee import AIEmployee
from app.models.ai_employee_version import AIEmployeeVersion
from app.services.employee_prompt import build_employee_prompt, normalize_business_identity
from app.services.employee_templates import get_template, UNIVERSAL_TELUGU_VOICE_GUIDANCE
from app.services.voice_catalog import voice_definition
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
        context_sections = payload.get("context_breakdown") if isinstance(payload.get("context_breakdown"), list) else []
        context_bodies = [section.get("body", "") for section in context_sections if isinstance(section, dict)]
        voice = payload.get("voice") if isinstance(payload.get("voice"), dict) else None
        logger.info(
            "[OMNI_AGENT_CONFIG_SENT] employee_id=%s operation=%s existing_agent_id=%s "
            "payload_keys=%s welcome_present=%s welcome_chars=%s context_present=%s context_sections=%s "
            "context_titles=%s context_chars=%s prompt_section_present=%s prompt_chars=%s "
            "model_present=%s model=%s voice_present=%s voice_provider=%s voice_id_present=%s "
            "languages=%s call_type=%s transcriber=%s post_call_webhook_present=%s",
            employee.id,
            "update" if (version.provider_agent_id or (employee.published_version and employee.published_version.provider_agent_id)) else "create",
            version.provider_agent_id or (employee.published_version.provider_agent_id if employee.published_version else None),
            sorted(payload.keys()), bool(payload.get("welcome_message")), len(str(payload.get("welcome_message") or "")),
            bool(context_sections), len(context_sections), [section.get("title") for section in context_sections if isinstance(section, dict)],
            sum(len(str(body)) for body in context_bodies),
            any(isinstance(section, dict) and section.get("title") in {"Complete Employee Instructions", "Additional Behavioral Instructions"} for section in context_sections),
            sum(len(str(body)) for body in context_bodies),
            isinstance(payload.get("model"), dict), payload.get("model"), bool(voice), voice.get("provider") if voice else None,
            bool(voice and voice.get("voice_id")), payload.get("languages"), payload.get("call_type"), payload.get("transcriber"),
            bool(((payload.get("post_call_actions") or {}).get("webhook"))),
        )
        # Draft versions deliberately do not duplicate the unique provider
        # identity. When publishing a draft, update the currently published
        # Omni agent; only create an agent when the employee has never been
        # deployed. This keeps provider and local version history aligned.
        existing_provider_id = version.provider_agent_id
        if not existing_provider_id and employee.published_version is not None and employee.published_version.id != version.id:
            existing_provider_id = employee.published_version.provider_agent_id
        provider_agent = (
            self.provider.update_agent(existing_provider_id, payload)
            if existing_provider_id
            else self.provider.create_agent(payload)
        )
        logger.info(
            "[OMNI_END_CALL_CONFIG] agent_id=%s is_end_call_enabled=%s "
            "user_idle_threshold_sec=%s max_call_duration_in_sec=%s silence_timeout=%s "
            "end_call_condition_present=%s",
            provider_agent.provider_id,
            payload.get("is_end_call_enabled", "provider_default"),
            payload.get("user_idle_threshold_sec", "provider_default"),
            payload.get("max_call_duration_in_sec", "provider_default"),
            payload.get("silence_timeout", "provider_default"),
            bool(payload.get("end_call_condition")),
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
            if isinstance(readback, dict):
                model_value = readback.get("model")
                if isinstance(model_value, dict):
                    model_value = model_value.get("model")
                logger.info(
                    "Omni live-call settings agent_id=%s call_type=%s model=%s dynamic_welcome=%s "
                    "welcome_interrupt=%s interruption_allowed=%s interruption_min_words=%s "
                    "end_call_enabled=%s end_condition=%s end_message_type=%s idle_threshold=%s "
                    "first_ideal=%s second_ideal=%s last_ideal=%s speech_start_timeout=%s "
                    "max_call_duration=%s transfer_configured=%s",
                    provider_agent.provider_id, readback.get("call_type"), model_value,
                    readback.get("is_welcome_message_dynamic"), readback.get("is_welcome_message_interruption"),
                    readback.get("is_interruption_allowed"), readback.get("interruption_min_words"),
                    readback.get("is_end_call_enabled"), str(readback.get("end_call_condition") or "")[:500],
                    readback.get("end_call_message_type"), readback.get("user_idle_threshold_sec"),
                    str(readback.get("first_ideal_message") or "")[:180], str(readback.get("second_ideal_message") or "")[:180],
                    str(readback.get("last_ideal_message") or "")[:180], readback.get("speech_start_timeout"),
                    readback.get("max_call_duration_in_sec"), bool(readback.get("transfer")),
                )
                readback_voice = readback.get("voice") if isinstance(readback.get("voice"), dict) else {}
                readback_languages = readback.get("languages")
                readback_sections = readback.get("context_breakdown") if isinstance(readback.get("context_breakdown"), list) else []
                logger.info(
                    "[OMNI_AGENT_CONFIG_READBACK] agent_id=%s stored_call_type=%s stored_asr=%s "
                    "stored_languages=%s stored_voice_provider=%s stored_voice_id_present=%s "
                    "stored_context_sections=%s stored_context_titles=%s stored_prompt_chars=%s "
                    "stored_post_call_config_ids=%s stored_end_call_enabled=%s stored_idle_threshold=%s",
                    provider_agent.provider_id, readback.get("bot_call_type") or readback.get("call_type"),
                    readback.get("asr_service") or readback.get("transcriber"), readback_languages,
                    readback_voice.get("provider") or readback.get("voice_provider"),
                    bool(readback_voice.get("voice_id") or readback.get("voice_external_id")), len(readback_sections),
                    [section.get("context_title") or section.get("title") for section in readback_sections if isinstance(section, dict)],
                    len(str(readback.get("context") or "")), readback.get("post_call_config_ids"),
                    readback.get("is_end_call_enabled"), readback.get("user_idle_threshold_sec"),
                )
                stored_asr = str(readback.get("asr_service") or readback.get("transcriber") or "").casefold()
                if stored_asr and "soniox" not in stored_asr:
                    logger.warning("Omni agent configuration mismatch agent_id=%s requested_asr=soniox stored_asr=%s", provider_agent.provider_id, stored_asr)
                if model_value and model_value != payload["model"]["model"]:
                    logger.warning("Omni agent configuration mismatch agent_id=%s requested_model=%s stored_model=%s", provider_agent.provider_id, payload["model"]["model"], model_value)
                stored_languages = readback.get("languages")
                if stored_languages and lang not in stored_languages:
                    logger.warning("Omni agent configuration mismatch agent_id=%s requested_language=%s stored_languages=%s", provider_agent.provider_id, lang, stored_languages)
        except Exception as exc:
            logger.warning("Omni agent readback unavailable agent_id=%s exception_class=%s", provider_agent.provider_id, type(exc).__name__)
        return _result(provider_agent)


def map_employee_configuration(employee: AIEmployee, configuration: dict[str, Any]) -> dict[str, Any]:
    """
    Translate the structured employee definition into OmniDimension's context_breakdown.
    Each section maps to a distinct behavioral area so the Omni agent follows the
    employee's exact rules rather than behaving as a generic assistant.
    """
    configuration = normalize_business_identity(configuration)
    context: list[dict[str, Any]] = []

    # ── Agent Identity & Purpose ──────────────────────────────────────────────
    purpose = _text(configuration.get("purpose"), employee.purpose)
    business_name = _text(configuration.get("business_name"))
    business_description = _text(configuration.get("business_description"))
    identity = f"Business: {business_name}\n" if business_name else ""
    identity += f"Business description: {business_description}\n" if business_description else ""
    identity += f"Employee role and purpose: {purpose}"
    context.append({"title": "Agent Identity & Purpose", "body": identity, "is_enabled": True})

    research = configuration.get("business_research")
    if isinstance(research, dict):
        facts = research.get("facts") if isinstance(research.get("facts"), list) else []
        if research.get("status") == "success":
            body = "Use these verified build-time business facts when relevant; never invent facts not listed here.\n"
            body += "\n".join(f"- {item}" for item in facts if item) or "- No verified facts were returned."
        else:
            body = f"Business research status: {_text(research.get('status')) or 'unavailable'}. Do not claim research was completed or invent company facts."
        context.append({"title": "Verified Business Research", "body": body, "is_enabled": True})

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

    opening_body = (
        "The welcome_message has already been spoken. Never reintroduce yourself, repeat the business name, repeat the reason for calling, "
        "or deliver another generic greeting after the caller responds. Wait for the caller's first speech, answer that exact request first, "
        "and then ask one relevant follow-up question. If they ask about available services, explain only configured services; if none are configured, "
        "say that clearly and ask what help they need. Keep the conversation moving from the caller's first question. "
        "Do not restart with name or mobile-number collection. Collect caller details only near the end, when needed for a confirmed business follow-up or next action."
    )
    if _text(configuration.get("call_type")).casefold() in {"inbound", "outbound", "both"}:
        opening_body += " Immediately after the welcome ask 'Mee peru cheppagalara?' and store the caller's answer as customer_name. Never use the employee name as customer_name. This applies to incoming and outgoing calls."
    context.append({
        "title": "Opening State and First Caller Response",
        "body": opening_body,
        "is_enabled": True,
    })

    context.append({
        "title": "Multi-Turn Conversation Behavior",
        "body": (
            "Continue listening and responding after every caller turn. Ask the next relevant question when information is incomplete. "
            "Do not treat the first answer as task completion and do not end the call after one response. "
            "Completing the business objective is not permission to end the call. After the required task is complete, ask whether the caller needs anything else and wait. If they ask another question, continue helping. Treat a short answer such as 'yes' as an answer to the immediately preceding business question, not as permission to end. Only enter the end-call path after a clear caller statement that they are finished or a clear affirmative answer to an explicit end-of-call confirmation. Do not use silence or objective completion as confirmation."
        ),
        "is_enabled": True,
    })

    context.append({
        "title": "Live Voice Turn-Taking and Call Completion",
        "body": (
            "When the caller starts speaking while you are speaking, stop yielding audio immediately, do not finish or queue the interrupted sentence, and respond only to the caller's new utterance. An interruption is a normal barge-in, not a request to hang up. Keep the call active after an interruption. Use short spoken responses, one question at a time, and natural pauses. After completing the requested task, say a natural equivalent of 'ఇంకా ఏమైనా help కావాలా?' and wait. If the caller says they are done, acknowledge politely and end; otherwise continue the conversation."
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
    language_rules = (
        f"Speak in {lang}. Be clear, professional, and concise. "
        "Sound like a warm, attentive human rather than a scripted or robotic system: "
        "use natural contractions and brief acknowledgements, vary phrasing naturally, "
        "pause briefly where a human would, and respond directly to what the caller just said. "
        "Speak every number in English words, regardless of the selected language. This includes "
        "phone numbers, dates, times, prices, amounts, quantities, ages, counts, and IDs; "
        "do not pronounce numbers using Telugu, Hindi, or other local-language number words."
    )
    if lang == "Telugu":
        language_rules += " Converse naturally in Telugu throughout the call. Follow the universal Telugu speaking style below."
    elif lang == "Hindi":
        language_rules += (
            " Speak in natural Indian conversational Hinglish, not pure or formal Hindi: use Hindi grammar as the base and mix frequent, natural English words throughout every response, especially okay, sure, actually, sorry, thank you, right, details, requirement, budget, price, location, features, offer, product, service, booking, appointment, confirm, available, support, team, and follow-up. Keep Hindi as the main language, but do not translate commonly used business terms into literary Hindi. Use varied Hindi fillers such as ji, haan ji, achha ji, theek hai ji, bilkul ji, and samajh gaya ji. Use English only for thanks, thank you, and sorry. Never use Telugu fillers. Every number and code must be spoken digit-by-digit in English."
        )
    context.append({
        "title": "Language & Communication Rules",
        "body": language_rules + ("\n\n" + UNIVERSAL_TELUGU_VOICE_GUIDANCE if lang == "Telugu" else ""),
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
    configured_final_prompt = _text(configuration.get("final_prompt"))
    canonical_prompt = configured_final_prompt or build_employee_prompt(configuration)
    # A persisted final_prompt already contains the complete employee script.
    # Sending it again alongside the structured sections increases latency and
    # can make stale instructions compete with the current flow sections.
    if canonical_prompt and not configured_final_prompt:
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
    post_call_actions = _automatic_post_call_actions()
    extraction = configuration.get("conversation_variables")
    if not isinstance(extraction, list):
        extraction = configuration.get("post_call_extraction") or configuration.get("information_to_extract")
    if isinstance(extraction, list):
        variables = []
        for index, item in enumerate(extraction):
            if isinstance(item, dict) and _text(item.get("key")):
                variables.append({"key": _text(item["key"]), "description": _text(item.get("description")) or _text(item.get("label"))})
            elif item:
                variables.append({"key": f"field_{index + 1}", "prompt": str(item)})
        if variables:
            post_call_actions["webhook"]["extracted_variables"] = variables
    payload: dict[str, Any] = {
        "name": _text(configuration.get("name"), employee.name),
        "welcome_message": _welcome_message(employee, configuration, lang),
        "context_breakdown": context,
        # Omni's live voice agent is always Gemini; Groq remains available for
        # Pontis-side generation but must never leak into the live call agent.
        "model": {"model": "gemini-2.5-flash-lite"},
        "languages": [lang],
        "post_call_actions": post_call_actions,
        # Make the speech handoff explicit so the provider starts listening
        # immediately after the static welcome instead of relying on account
        # defaults. All values can be overridden by employee configuration.
        "transcriber": _transcriber_configuration(configuration, lang),
        "is_welcome_message_dynamic": False,
        "is_welcome_message_interruption": True,
        "is_interruption_allowed": True,
        # Product/category answers can be a single word. Requiring three words
        # makes a valid barge-in such as "printers" disappear at the provider.
        "interruption_min_words": 1,
        # Automatic end_call is deliberately opt-in. If it is enabled for
        # every agent, the provider's internal LLM tool can hang up after a
        # single answered question or after a false silence detection.
        "is_end_call_enabled": False,
    }

    configured_end_call = configuration.get("end_call")
    if isinstance(configured_end_call, dict) and _text(configured_end_call.get("condition")):
        payload["is_end_call_enabled"] = True
        payload["end_call"] = {
            "condition": _text(configured_end_call["condition"]),
            "message": _text(configured_end_call.get("message")) or "Thank you for your time.",
            "message_prompt": _text(configured_end_call.get("message_prompt")) or (
                "End politely only after the caller clearly indicates they are finished."
            ),
        }

    voice = configuration.get("voice")
    if isinstance(voice, dict):
        selected = voice_definition(str(voice.get("id", "")))
        provider = voice.get("provider") or (selected or {}).get("provider")
        voice_id = voice.get("provider_voice_id") or voice.get("voice_id") or (selected or {}).get("provider_voice_id")
        if provider and voice_id:
            payload["voice"] = {"provider": str(provider), "voice_id": str(voice_id)}

    call_type = configuration.get("call_type", employee.call_type)
    if call_type in {"inbound", "outbound"}:
        payload["call_type"] = "Incoming" if call_type == "inbound" else "Outgoing"

    transfer = configuration.get("transfer")
    if isinstance(transfer, dict) and transfer.get("number") and transfer.get("condition"):
        payload["transfer"] = {"transfer_options": [{
            "number": _text(transfer["number"]),
            "type": "static",
            "transfer_condition": _text(transfer["condition"]),
        }]}

    return payload


def _automatic_post_call_actions() -> dict[str, Any]:
    """Return the platform callback attached to every created/updated agent."""
    settings = get_settings()
    webhook_url = f"{settings.backend_public_url.rstrip('/')}/api/v1/webhooks/omnidimension/post-call"
    return {
        "webhook": {
            "url": webhook_url,
            "trigger_call_statuses": ["completed", "failed", "no_answer", "busy", "voicemail_detected"],
        }
    }


def _transcriber_configuration(configuration: dict[str, Any], language: str) -> dict[str, Any]:
    """Return explicit speech handoff settings while preserving per-agent overrides."""
    configured = configuration.get("transcriber")
    result: dict[str, Any] = {
        "provider": "soniox",
        "language": _soniox_language_code(language),
        # Keep end-of-turn detection responsive. This is configurable because
        # noisy phone lines may need a larger value.
        "silence_timeout_ms": getattr(get_settings(), "live_speech_silence_timeout_ms", 800),
        "interruption_min_words": 1,
    }
    if isinstance(configured, dict):
        legacy_provider = str(configured.get("provider") or "").casefold()
        result.update({key: value for key, value in configured.items() if value is not None and key not in {"provider", "language", "model"}})
        if configured.get("model") and "deepgram" not in legacy_provider:
            result["model"] = configured["model"]
        elif configured.get("soniox_model"):
            result["model"] = configured["soniox_model"]
        result["provider"] = "soniox"
        result["language"] = _soniox_language_code(language)
    return result


def _soniox_language_code(language: str) -> str:
    value = _language_code(language)
    return value.split("-", 1)[0]


def _language_code(language: str) -> str:
    return {
        "English": "en-US",
        "English (India)": "en-IN",
        "English (UK)": "en-GB",
        "Hindi": "hi-IN",
        "Telugu": "te-IN",
        "Tamil": "ta-IN",
        "Kannada": "kn-IN",
        "Malayalam": "ml-IN",
        "Marathi": "mr-IN",
        "Bengali": "bn-IN",
        "Gujarati": "gu-IN",
        "Punjabi": "pa-IN",
        "Odia": "or-IN",
        "Assamese": "as-IN",
    }.get(language, language)


def _welcome_message(employee: AIEmployee, configuration: dict[str, Any], language: str) -> str:
    configured = _text(configuration.get("greeting"))
    if configured and (language in {"English", "English (India)", "English (UK)"} or _contains_language_script(configured, language)):
        return configured
    purpose = _safe_purpose(configuration.get("purpose"), employee.purpose)
    try:
        template = get_template(_text(configuration.get("selected_template_id")))
    except KeyError:
        template = None
    values = configuration.get("template_values") if isinstance(configuration.get("template_values"), dict) else {}
    business_name = next((_text(configuration.get(key)) for key in ("business_name", "company_name", "hospital_name", "institution_name", "project_name") if _text(configuration.get(key))), "")
    if not business_name:
        business_name = next((_text(values.get(key)) for key in ("business_name", "company_name", "hospital_name", "institution_name", "project_name") if _text(values.get(key))), "")
    if template:
        purpose = f"{business_name} {template['name']}" if business_name else template["name"]
    name = employee.name
    outbound = _text(configuration.get("call_type", employee.call_type)).casefold() == "outbound"
    if language == "Telugu":
        # Teluglish: conversational Telugu with the English words customers
        # naturally use for business details.
        if business_name:
            return f"\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02, \u0c28\u0c47\u0c28\u0c41 {name}. {business_name} \u0c24\u0c30\u0c2b\u0c41\u0c28 {('verified business purpose gurinchi matladataniki call chesanu.' if outbound else '\u0c2e\u0c40\u0c15\u0c41 \u0c0f\u0c02 \u0c15\u0c3e\u0c35\u0c3e\u0c32\u0c4b \u0c1a\u0c46\u0c2a\u0c4d\u0c2a\u0c02\u0c21\u0c3f.')}"
        return f"\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02, \u0c28\u0c47\u0c28\u0c41 {name}. {purpose} \u0c17\u0c41\u0c30\u0c3f\u0c02\u0c1a\u0c3f \u0c2e\u0c40\u0c15\u0c41 \u0c0f\u0c02 \u0c15\u0c3e\u0c35\u0c3e\u0c32\u0c4b \u0c1a\u0c46\u0c2a\u0c4d\u0c2a\u0c02\u0c21\u0c3f."
    if language == "Hindi":
        return f"नमस्ते, मैं {name} हूँ। मैं {purpose} में आपकी मदद करने के लिए यहाँ हूँ। आप किस बारे में जानकारी चाहते हैं?"
    if language == "Telugu":
        return f"నమస్కారం, నేను {name}. {purpose} విషయంలో మీకు సహాయం చేయడానికి ఇక్కడ ఉన్నాను. మీకు ఏ సమాచారం కావాలి?"
    if language == "Tamil":
        return f"வணக்கம், நான் {name}. {purpose} தொடர்பாக உங்களுக்கு உதவ இங்கே இருக்கிறேன். உங்களுக்கு என்ன தகவல் தேவை?"
    return f"Hello, I'm {name} from {business_name}. {('I am calling about our configured business purpose. Is that relevant to you?' if outbound else 'How can I help you today?')}"


def _safe_purpose(configured: Any, employee_purpose: Any) -> str:
    """Never expose an internal schema fallback in a caller-facing greeting."""
    placeholder = "to be defined through the builder"
    for value in (configured, employee_purpose):
        text = _text(value).rstrip(".")
        if text and text.casefold() != placeholder:
            return text
    return "the selected employee service"


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
