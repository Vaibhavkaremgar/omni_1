from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from app.integrations.omnidimension import OmniDimensionAgentProvider, ProviderAgent
from app.models.ai_employee import AIEmployee
from app.models.ai_employee_version import AIEmployeeVersion
from app.services.employee_prompt import (
    SCRIPT_SECTION_NAMES,
    build_employee_prompt,
    business_conversation_profile,
    language_conversation_guidance,
    natural_voice_conversation_behavior,
    normalize_business_identity,
)
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
        intended_configuration = _intended_configuration(employee, version.configuration or {})
        sent_configuration = _sent_configuration(payload)
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
        readback: dict[str, Any] | None = None
        readback_error: Exception | None = None
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
                requested_language = (payload.get("languages") or [None])[0] if isinstance(payload.get("languages"), list) else None
                if stored_languages and requested_language not in stored_languages:
                    logger.warning("Omni agent configuration mismatch agent_id=%s requested_language=%s stored_languages=%s", provider_agent.provider_id, requested_language, stored_languages)
        except Exception as exc:
            readback_error = exc
            logger.warning("Omni agent readback unavailable agent_id=%s exception_class=%s", provider_agent.provider_id, type(exc).__name__)
        verification = _provider_verification(
            intended_configuration,
            sent_configuration,
            _returned_configuration(readback),
            provider_agent.provider_id,
            readback_error=readback_error,
        )
        for field in verification.get("mismatches", []):
            logger.warning(
                "Omni agent verification mismatch agent_id=%s field=%s intended=%s sent=%s returned=%s",
                provider_agent.provider_id,
                field.get("field"),
                _log_value(field.get("intended")),
                _log_value(field.get("sent")),
                _log_value(field.get("returned")),
            )
        for field in verification.get("unverified_fields", []):
            logger.warning("Omni agent verification unverified agent_id=%s field=%s", provider_agent.provider_id, field.get("field"))
        return _result(provider_agent, verification=verification)


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
    identity += f"Employee role and purpose: {purpose}\nDo not volunteer these internal labels in spoken conversation; share relevant business information naturally only when asked or needed."
    context.append({"title": "Agent Identity & Purpose", "body": identity, "is_enabled": True})
    profile = business_conversation_profile(configuration)
    context.append({
        "title": "Business Type Conversation Profile",
        "body": (
            f"Business type: {profile['key']} ({profile['domain']}). "
            f"Shape the conversation around {profile['focus']}. "
            f"Qualification: {profile['qualification']} "
            f"Objection handling: {profile['objection']} "
            f"Next step: {profile['cta']} "
            f"Telugu style when applicable: {profile['telugu']}"
        ),
        "is_enabled": True,
    })

    saved_script = _canonical_call_script(configuration)
    if saved_script:
        context.append({
            "title": "Published Call Script Source of Truth",
            "body": (
                "Use these exact six Call Script sections as the authoritative spoken behavior. "
                "Do not replace them with a regenerated script. Runtime instructions may constrain pacing, safety, and provider behavior, "
                "but they must not modify or supersede the script below.\n\n"
                + _format_call_script(saved_script)
            ),
            "is_enabled": True,
        })

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
    if _text(configuration.get("call_type")).casefold() == "inbound":
        opening_body += " This is an inbound call: the caller initiated it. Ask why they called or what help they need, understand the caller's request, and answer or assist before qualifying. Do not assume the reason for the call or ask for identity details unless they become relevant to the requested action."
    else:
        opening_body += " This is an outbound call with customer details already configured. Never ask for the customer's name, phone number, profession, or any other detail already supplied; use those details silently and focus on the call purpose."
    context.append({
        "title": "Opening State and First Caller Response",
        "body": opening_body,
        "is_enabled": True,
    })

    context.append({
        "title": "Question Answering and No Repetition",
        "body": (
            "For every caller turn, first identify whether the caller is asking a question, correcting information, confirming interest, objecting, or changing topic. "
            "Answer the caller's exact question from the employee context, published call script, configured business details, Knowledge Base, and verified business research before asking any follow-up. "
            "If the answer is not available in context, say the detail is not configured and offer the configured callback, human follow-up, or next step. Never fabricate. "
            "Do not continue a script while ignoring the caller's question. "
            "Say each substantive sentence only once. Never repeat the same greeting, offer, explanation, question, or closing line back-to-back. "
            "If the caller asks again or seems confused, rephrase once in simpler conversational wording instead of repeating the same sentence."
        ),
        "is_enabled": True,
    })

    call_type = _text(configuration.get("call_type", employee.call_type)).casefold()
    selected_language = _language_name(_text(configuration.get("language"), employee.language))
    context.append({
        "title": "Authoritative Call Type Mode",
        "body": (
            "OUTBOUND MODE: The employee called the customer to promote the configured business. Identify the employee and company, explain what the business offers and the reason for calling, then ask only whether the customer is interested or wants more information. Answer doubts from configured facts. Never ask qualification, discovery, profile, budget, location, timeline, preference, contact, or personal-detail questions. Do not perform actions such as booking, purchase, transfer, or callback."
            if call_type == "outbound" else
            "INBOUND MODE: The customer initiated the call. Greet the caller, ask why they called or what help they need, understand the request, and answer or assist first. Do not use an outbound sales opening or assume the caller's purpose. Qualify only when relevant to resolving the request."
        ),
        "is_enabled": True,
    })

    context.append({
        "title": "Multi-Turn Conversation Behavior",
        "body": (
            "Continue listening and responding after every caller turn. Ask the next relevant question when information is incomplete. "
            "Do not treat the first answer as task completion and do not end the call after one response. "
            "Completing the business objective is not permission to end the call. After the required task is complete, ask whether the caller needs anything else and wait. If they ask another question, continue helping. Treat a short answer such as 'yes', 'okay', 'sure', a product name, or a budget amount as an answer to the immediately preceding business question, not as permission to end. Treat hesitation or filler sounds such as 'ahh', 'umm', 'uh', 'hmm', 'haa', 'actually', 'one second', 'wait', and equivalent Telugu/Hindi fillers as thinking or continuation cues, not goodbye or hang-up intent. Only enter the end-call path after a clear caller statement that they are finished or a clear affirmative answer to an explicit end-of-call confirmation. Do not use silence, hesitation, or objective completion as confirmation."
        ),
        "is_enabled": True,
    })

    context.append({
        "title": "Natural Conversation Behavior",
        "body": natural_voice_conversation_behavior(),
        "is_enabled": True,
    })
    context.append({
        "title": "Language-Aware Natural Conversation Contract",
        "body": language_conversation_guidance(selected_language),
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
    idle_phrase = "Vinipisthunda andi?" if lang == "Telugu" else "Kya aap wahan hain ji?" if lang == "Hindi" else "Are you still there?"
    context.append({"title": "Mandatory Idle and Repeated Hello Handling", "body": f"If the caller is silent or idle for 2–3 seconds, say exactly '{idle_phrase}', then stop speaking and wait silently for the caller's response. Never repeat the previous question or advance to the next question while waiting. If the caller repeats hello, use the same idle phrase and wait for the response.", "is_enabled": True})
    language_rules = (
        f"Speak in {lang}. Be clear, professional, and concise. "
        "Sound like a warm, attentive human rather than a scripted or robotic system: "
        "use natural contractions and brief acknowledgements, vary phrasing naturally, "
        "pause briefly where a human would, and respond directly to what the caller just said. "
        "Speak every numeric string digit-by-digit in English, regardless of the selected language. For example, 230 is 'two three zero', never 'two hundred thirty' or regional-language number words. This includes "
        "product and property phrases such as 2 BHK, 3 BHK, and 150 square yards, plus "
        "phone numbers, dates, times, prices, amounts, quantities, ages, counts, and IDs; "
        "do not pronounce numbers using Telugu, Hindi, or other local-language number words."
    )
    if lang == "Telugu":
        language_rules += " Converse naturally in Telugu throughout the call. Follow the universal Telugu speaking style below."
    elif lang == "Hindi":
        language_rules += (
            " Speak in natural Indian conversational Hinglish, not pure or formal Hindi: use Hindi grammar as the base and mix frequent, natural English words throughout every response, especially okay, sure, actually, sorry, thank you, right, details, requirement, budget, price, location, features, offer, product, service, booking, appointment, confirm, available, support, team, and follow-up. Keep Hindi as the main language, but do not translate commonly used business terms into literary Hindi. Use varied Hindi fillers such as ji, haan ji, achha ji, theek hai ji, bilkul ji, and samajh gaya ji. Use English only for thanks, thank you, and sorry. Never use Telugu fillers. Every numeric string and code must be spoken digit-by-digit in English."
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
    if canonical_prompt and (not configured_final_prompt or configuration.get("final_prompt_overridden")):
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
        # Provider-level idle handling: prompt the caller once after a short
        # pause and wait for speech instead of advancing the workflow.
        "user_idle_threshold_sec": 5,
        "first_ideal_message": (
            "Vinipisthunda andi?"
            if lang == "Telugu" else
            "Kya aap sun rahe hain ji?"
            if lang == "Hindi" else
            "Are you still there?"
        ),
        "second_ideal_message": (
            "Vinipisthunda andi?"
            if lang == "Telugu" else
            "Kya aap sun rahe hain ji?"
            if lang == "Hindi" else
            "Are you still there?"
        ),
        "last_ideal_message": (
            "Vinipisthunda andi?"
            if lang == "Telugu" else
            "Kya aap sun rahe hain ji?"
            if lang == "Hindi" else
            "Are you still there?"
        ),
    }

    configured_end_call = configuration.get("end_call")
    if isinstance(configured_end_call, dict) and _text(configured_end_call.get("condition")):
        payload["is_end_call_enabled"] = True
        payload["end_call"] = {
            "condition": _text(configured_end_call["condition"]),
            "message": _text(configured_end_call.get("message")) or _default_end_call_message(lang),
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


def _canonical_call_script(configuration: dict[str, Any]) -> dict[str, str]:
    script = configuration.get("call_script")
    if not isinstance(script, dict):
        return {}
    result = {title: _text(script.get(title)) for title in SCRIPT_SECTION_NAMES}
    return result if all(result.values()) else {}


def _format_call_script(script: dict[str, str]) -> str:
    return "\n\n".join(
        f"{index}. {title}\n{script[title]}"
        for index, title in enumerate(SCRIPT_SECTION_NAMES, 1)
    )


def _intended_configuration(employee: AIEmployee, configuration: dict[str, Any]) -> dict[str, Any]:
    configuration = normalize_business_identity(configuration)
    language = _language_name(_text(configuration.get("language"), employee.language))
    call_type = _text(configuration.get("call_type", employee.call_type)).casefold()
    voice = configuration.get("voice") if isinstance(configuration.get("voice"), dict) else {}
    selected_voice = voice_definition(str(voice.get("id", ""))) if voice else None
    end_call = configuration.get("end_call") if isinstance(configuration.get("end_call"), dict) else {}
    script = _canonical_call_script(configuration)
    voice_provider = voice.get("provider") or (selected_voice or {}).get("provider") if voice else None
    voice_id = voice.get("provider_voice_id") or voice.get("voice_id") or (selected_voice or {}).get("provider_voice_id") if voice else None
    return {
        "call_type": "Incoming" if call_type == "inbound" else "Outgoing" if call_type == "outbound" else None,
        "language": language,
        "model": "gemini-2.5-flash-lite",
        "transcriber_provider": "soniox",
        "transcriber_language": _soniox_language_code(language),
        "voice_provider": str(voice_provider) if voice_provider else None,
        "voice_id": str(voice_id) if voice_id else None,
        "welcome_message": _welcome_message(employee, configuration, language),
        "six_section_prompt": _format_call_script(script) if script else "",
        "six_section_titles": list(SCRIPT_SECTION_NAMES) if script else [],
        "interruption_enabled": True,
        "interruption_min_words": 1,
        "idle_threshold_sec": 5,
        "end_call_enabled": bool(end_call and _text(end_call.get("condition"))),
        "end_call_condition": _text(end_call.get("condition")) if end_call else None,
        "webhook_enabled": True,
        "webhook_url": _automatic_post_call_actions()["webhook"]["url"],
        "webhook_statuses": _automatic_post_call_actions()["webhook"]["trigger_call_statuses"],
    }


def _sent_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    transcriber = payload.get("transcriber") if isinstance(payload.get("transcriber"), dict) else {}
    voice = payload.get("voice") if isinstance(payload.get("voice"), dict) else {}
    model = payload.get("model")
    webhook = (payload.get("post_call_actions") or {}).get("webhook") if isinstance(payload.get("post_call_actions"), dict) else {}
    end_call = payload.get("end_call") if isinstance(payload.get("end_call"), dict) else {}
    return {
        "call_type": payload.get("call_type"),
        "language": _first(payload.get("languages")),
        "model": model.get("model") if isinstance(model, dict) else model,
        "transcriber_provider": transcriber.get("provider"),
        "transcriber_language": transcriber.get("language"),
        "voice_provider": voice.get("provider"),
        "voice_id": voice.get("voice_id"),
        "welcome_message": payload.get("welcome_message"),
        "six_section_prompt": _extract_six_section_prompt(payload.get("context_breakdown")),
        "six_section_titles": _extract_six_section_titles(_extract_six_section_prompt(payload.get("context_breakdown"))),
        "interruption_enabled": payload.get("is_interruption_allowed"),
        "interruption_min_words": payload.get("interruption_min_words"),
        "idle_threshold_sec": payload.get("user_idle_threshold_sec"),
        "end_call_enabled": payload.get("is_end_call_enabled"),
        "end_call_condition": end_call.get("condition"),
        "webhook_enabled": bool(webhook),
        "webhook_url": webhook.get("url") if isinstance(webhook, dict) else None,
        "webhook_statuses": webhook.get("trigger_call_statuses") if isinstance(webhook, dict) else None,
    }


def _returned_configuration(readback: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(readback, dict):
        return None
    transcriber = readback.get("transcriber") or readback.get("asr_service")
    transcriber_dict = transcriber if isinstance(transcriber, dict) else {}
    voice = readback.get("voice") if isinstance(readback.get("voice"), dict) else {}
    model = readback.get("model")
    webhook = (readback.get("post_call_actions") or {}).get("webhook") if isinstance(readback.get("post_call_actions"), dict) else {}
    end_call = readback.get("end_call") if isinstance(readback.get("end_call"), dict) else {}
    prompt = _extract_six_section_prompt(readback.get("context_breakdown")) or _extract_six_section_prompt(readback.get("context"))
    return {
        "call_type": readback.get("bot_call_type") or readback.get("call_type"),
        "language": _first(readback.get("languages")),
        "model": model.get("model") if isinstance(model, dict) else model,
        "transcriber_provider": transcriber_dict.get("provider") or (transcriber if isinstance(transcriber, str) else None),
        "transcriber_language": transcriber_dict.get("language") or readback.get("transcriber_language"),
        "voice_provider": voice.get("provider") or readback.get("voice_provider"),
        "voice_id": voice.get("voice_id") or readback.get("voice_external_id") or readback.get("provider_voice_id"),
        "welcome_message": readback.get("welcome_message"),
        "six_section_prompt": prompt,
        "six_section_titles": _extract_six_section_titles(prompt),
        "interruption_enabled": readback.get("is_interruption_allowed"),
        "interruption_min_words": readback.get("interruption_min_words"),
        "idle_threshold_sec": readback.get("user_idle_threshold_sec"),
        "end_call_enabled": readback.get("is_end_call_enabled"),
        "end_call_condition": end_call.get("condition") or readback.get("end_call_condition"),
        "webhook_enabled": bool(webhook) if "post_call_actions" in readback else None,
        "webhook_url": webhook.get("url") if isinstance(webhook, dict) else readback.get("webhook_url"),
        "webhook_statuses": webhook.get("trigger_call_statuses") if isinstance(webhook, dict) else readback.get("trigger_call_statuses"),
    }


def _provider_verification(
    intended: dict[str, Any],
    sent: dict[str, Any],
    returned: dict[str, Any] | None,
    agent_id: str,
    *,
    readback_error: Exception | None = None,
) -> dict[str, Any]:
    fields = [
        "call_type", "language", "model", "transcriber_provider", "transcriber_language",
        "voice_provider", "voice_id", "welcome_message", "six_section_prompt",
        "six_section_titles", "interruption_enabled", "interruption_min_words",
        "idle_threshold_sec", "end_call_enabled", "end_call_condition",
        "webhook_enabled", "webhook_url", "webhook_statuses",
    ]
    checks: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []
    for field in fields:
        item = {"field": field, "intended": intended.get(field), "sent": sent.get(field), "returned": returned.get(field) if returned else None}
        if not _values_match(field, intended.get(field), sent.get(field)):
            item["status"] = "mismatch"
            mismatches.append(item)
        elif _is_missing(intended.get(field)) and _is_missing(sent.get(field)) and (returned is None or _is_missing(returned.get(field))):
            item["status"] = "match"
        elif readback_error is not None or returned is None or _is_missing(returned.get(field)):
            item["status"] = "unverified"
            unverified.append(item)
        elif not _values_match(field, intended.get(field), returned.get(field)):
            item["status"] = "mismatch"
            mismatches.append(item)
        else:
            item["status"] = "match"
        checks.append(item)
    if readback_error is not None:
        status = "provider_readback_failed"
    elif mismatches:
        status = "mismatch"
    elif unverified:
        status = "partially_verified"
    else:
        status = "verified"
    return {
        "status": status,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "provider_agent_id": agent_id,
        "intended_configuration": intended,
        "sent_configuration": sent,
        "returned_configuration": returned,
        "fields": checks,
        "mismatches": mismatches,
        "unverified_fields": unverified,
        **({"readback_error_class": type(readback_error).__name__} if readback_error is not None else {}),
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
    outbound = _text(configuration.get("call_type", employee.call_type)).casefold() == "outbound"
    call_script = configuration.get("call_script")
    script_greeting = (
        _text(call_script.get("Greeting & Intro"))
        if isinstance(call_script, dict) else ""
    )
    # The reviewed second script section is the caller-facing source of truth.
    # Omni plays welcome_message before the LLM gets a turn, so it must receive
    # that exact opening rather than a separately reconstructed version.
    if script_greeting:
        return script_greeting
    # A saved generic greeting can otherwise undo the outbound offer-first
    # contract. Generate this opening from the verified employee configuration.
    if not outbound and configured and (language in {"English", "English (India)", "English (UK)"} or _contains_language_script(configured, language)):
        return configured
    # Hindi's generated six-section Greeting & Intro is the canonical spoken
    # opening. Reuse it here so OmniDimension does not receive a separately
    # generated formal-Hindi welcome that conflicts with the reviewed script.
    if language == "Hindi" and not outbound:
        call_script = configuration.get("call_script")
        if isinstance(call_script, dict) and _text(call_script.get("Greeting & Intro")):
            return _text(call_script["Greeting & Intro"])
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
    business_purpose = _outbound_offer_summary(configuration, purpose)
    if language == "Telugu":
        company = f", {business_name} \u0c28\u0c41\u0c02\u0c1a\u0c3f" if business_name else ""
        if outbound:
            reason = _telugu_outbound_reason(configuration, purpose) if business_name else f"{business_purpose} \u0c17\u0c41\u0c30\u0c3f\u0c02\u0c1a\u0c3f call \u0c1a\u0c47\u0c36\u0c3e\u0c28\u0c41."
            return f"\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02 \u0c05\u0c02\u0c21\u0c3f, \u0c28\u0c47\u0c28\u0c41 {name}{company} \u0c2e\u0c3e\u0c1f\u0c4d\u0c32\u0c3e\u0c21\u0c41\u0c24\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c28\u0c41. {reason} \u0c2e\u0c30\u0c3f\u0c02\u0c24 details \u0c35\u0c3f\u0c28\u0c21\u0c3e\u0c28\u0c3f\u0c15\u0c3f \u0c2e\u0c40\u0c15\u0c41 interest \u0c09\u0c02\u0c26\u0c3e?"
        return f"\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02 \u0c05\u0c02\u0c21\u0c3f, \u0c28\u0c47\u0c28\u0c41 {name}{company} \u0c2e\u0c3e\u0c1f\u0c4d\u0c32\u0c3e\u0c21\u0c41\u0c24\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c28\u0c41. \u0c2e\u0c40\u0c15\u0c41 \u0c0f\u0c02 help \u0c15\u0c3e\u0c35\u0c3e\u0c32\u0c3f \u0c05\u0c02\u0c21\u0c3f?"
    if language == "Hindi":
        company = f", {business_name} \u0938\u0947" if business_name else ""
        if outbound:
            return f"\u0928\u092e\u0938\u094d\u0924\u0947 \u091c\u0940, \u092e\u0948\u0902 {name}{company} \u092c\u094b\u0932 \u0930\u0939\u093e \u0939\u0942\u0901. {business_purpose} \u0915\u0947 \u092c\u093e\u0930\u0947 \u092e\u0947\u0902 call \u0915\u093f\u092f\u093e \u0939\u0948. \u0915\u094d\u092f\u093e \u0906\u092a details \u0938\u0941\u0928\u0928\u093e \u091a\u093e\u0939\u0947\u0902\u0917\u0947?"
        return f"\u0928\u092e\u0938\u094d\u0924\u0947 \u091c\u0940, \u092e\u0948\u0902 {name}{company} \u092c\u094b\u0932 \u0930\u0939\u093e \u0939\u0942\u0901. \u0906\u092a\u0915\u0940 help \u0915\u0948\u0938\u0947 \u0915\u0930 \u0938\u0915\u0924\u093e \u0939\u0942\u0901?"
    if language == "Telugu":
        # Teluglish: conversational Telugu with the English words customers
        # naturally use for business details.
        if business_name:
            if outbound:
                reason = _telugu_outbound_reason(configuration, purpose)
                return f"నమస్కారం, నేను {name}. {business_name} తరఫున మాట్లాడుతున్నాను. {reason} దీని గురించి మరింత తెలుసుకోవడానికి మీకు ఆసక్తి ఉందా?"
            return f"\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02, \u0c28\u0c47\u0c28\u0c41 {name}. {business_name} \u0c24\u0c30\u0c2b\u0c41\u0c28 {((business_purpose + ' gurinchi matladataniki call chesanu.') if outbound else '\u0c2e\u0c40\u0c15\u0c41 \u0c0f\u0c02 \u0c15\u0c3e\u0c35\u0c3e\u0c32\u0c4b \u0c1a\u0c46\u0c2a\u0c4d\u0c2a\u0c02\u0c21\u0c3f.')}"
        if outbound:
            return f"Namaskaram, nenu {name}. {business_purpose} gurinchi matladataniki call chesanu. Dini gurinchi meeru inka telusukovalani anukuntunnara?"
        return f"\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02, \u0c28\u0c47\u0c28\u0c41 {name}. {purpose} \u0c17\u0c41\u0c30\u0c3f\u0c02\u0c1a\u0c3f \u0c2e\u0c40\u0c15\u0c41 \u0c0f\u0c02 \u0c15\u0c3e\u0c35\u0c3e\u0c32\u0c4b \u0c1a\u0c46\u0c2a\u0c4d\u0c2a\u0c02\u0c21\u0c3f."
    if language == "Hindi":
        if outbound:
            return f"Hello ji, main {name}{(' ' + business_name + ' se') if business_name else ''} bol raha hoon. Main {business_purpose} ke baare mein call kar raha hoon. Kya aap iske baare mein aur jaanna chahenge?"
        return f"Hello ji, main {name} bol raha hoon. Main {purpose} ke regarding aapki help karne ke liye hoon. Aapki requirement kya hai?"
    if language == "Telugu":
        return f"నమస్కారం, నేను {name}. {purpose} విషయంలో మీకు సహాయం చేయడానికి ఇక్కడ ఉన్నాను. మీకు ఏ సమాచారం కావాలి?"
    if language == "Tamil":
        if outbound:
            return f"Vanakkam, naan {name}. {business_purpose} patri pesa azhaithen. Idhai patri melum therindhukolla virumbugireergala?"
        return f"வணக்கம், நான் {name}. {purpose} தொடர்பாக உங்களுக்கு உதவ இங்கே இருக்கிறேன். உங்களுக்கு என்ன தகவல் தேவை?"
    if outbound:
        company = f" from {business_name}" if business_name else ""
        return f"Hello, I'm {name}{company}. I'm calling to tell you about {business_purpose}. Would you like to hear more?"
    return f"Hello, I'm {name} from {business_name}. How can I help you today?"


def _outbound_offer_summary(configuration: dict[str, Any], purpose: str) -> str:
    """Return the customer-facing offer statement without inventing business facts."""
    values = configuration.get("template_values")
    values = values if isinstance(values, dict) else {}
    product = next((
        _text(configuration.get(key)) or _text(values.get(key))
        for key in ("product_or_service", "products", "products_services", "offer", "service")
        if _text(configuration.get(key)) or _text(values.get(key))
    ), "")
    brief = _text(configuration.get("business_description")) or purpose
    promotion = _promotion_from_brief(brief)
    if product:
        return f"{product} {promotion}".strip()
    if promotion:
        return promotion
    # Never speak the owner's workflow instruction (for example, "he needs to
    # call customers...") to a customer. A neutral fallback is preferable to
    # exposing internal wording when no specific offer has been configured.
    if _looks_like_internal_instruction(brief):
        return "our current printer offer"
    return brief


def _telugu_outbound_reason(configuration: dict[str, Any], purpose: str) -> str:
    """Turn the owner brief into a short caller-facing Telugu reason to call."""
    brief = _text(configuration.get("business_description")) or purpose
    promotion = _promotion_from_brief(brief)
    if promotion:
        return f"\u0c2e\u0c3e \u0c26\u0c17\u0c4d\u0c17\u0c30 {promotion} \u0c09\u0c02\u0c26\u0c3f."
    if "insurance" in brief.casefold() and "renewal" in brief.casefold():
        days = re.search(r"within\s+(\d+)\s+days?", brief, flags=re.IGNORECASE)
        timing = f"next {days.group(1)} days \u0c32\u0c4b " if days else ""
        return f"\u0c2e\u0c40 insurance renewal {timing}\u0c09\u0c02\u0c26\u0c3f. Renewal reminder \u0c15\u0c4b\u0c38\u0c02 call \u0c1a\u0c47\u0c36\u0c3e\u0c28\u0c41."
    if _looks_like_internal_instruction(brief):
        return "\u0c2e\u0c3e current offer \u0c17\u0c41\u0c30\u0c3f\u0c02\u0c1a\u0c3f call \u0c1a\u0c47\u0c36\u0c3e\u0c28\u0c41."
    return f"{brief} \u0c17\u0c41\u0c30\u0c3f\u0c02\u0c1a\u0c3f call \u0c1a\u0c47\u0c36\u0c3e\u0c28\u0c41."
    brief = _text(configuration.get("business_description")) or purpose
    promotion = _promotion_from_brief(brief)
    if promotion:
        return f"మా దగ్గర {promotion} ఉంది."
    if "insurance" in brief.casefold() and "renewal" in brief.casefold():
        days = re.search(r"within\s+(\d+)\s+days?", brief, flags=re.IGNORECASE)
        timing = f"next {days.group(1)} days lo " if days else ""
        return f"మీ insurance renewal {timing}ఉంది. Renewal reminder కోసం call చేశాను."
    # A job brief is input for generating a welcome, never speech to play as
    # the welcome. Use a short, neutral business reason if it has no known
    # customer-facing fact to surface.
    if _looks_like_internal_instruction(brief):
        return "మా current offer గురించి మాట్లాడటానికి call చేశాను."
    return f"{brief} గురించి మాట్లాడటానికి call చేశాను."


def _promotion_from_brief(brief: str) -> str:
    """Extract a customer-facing festival promotion from a free-form brief."""
    normalized = " ".join(brief.split())
    patterns = (
        # "Tell leads the current Vinayaka Chaturthi offer of 30% discount
        # on printers" -> Telugu-led offer phrase below.
        r"(?:the\s+)?(?:current\s+)?(?P<festival>(?:[A-Za-z]+\s+)?Chaturthi|festival\s+season)\s+offer\s*"
        r"(?:of\s+)?(?P<discount>[^.,;]*?(?:discount|off))\s+(?:on|for)\s+(?P<product>[A-Za-z][A-Za-z0-9 &/-]{1,60})",
        # "printers with 50% off on this festival season"
        r"(?P<product>printers?|products?|services?)\s+with\s+(?P<discount>[^.,;]*?(?:discount|off))\s+"
        r"(?:on|for)\s+(?:this\s+)?(?P<festival>[A-Za-z][A-Za-z ]{2,40})",
        r"(?:buy(?:ing)?\s+(?:the\s+)?)?(?P<product>[A-Za-z][A-Za-z0-9 &/-]{1,60}?)?\s*"
        r"(?:as\s+)?this\s+(?P<festival>[A-Za-z][A-Za-z ]{2,40}?)\s+"
        r"(?:we\s+are|we're)\s+(?:giving|offering)\s+(?P<discount>[^.,;]*?(?:discount|offer)[^.,;]*)",
    )
    match = next((re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in patterns if re.search(pattern, normalized, flags=re.IGNORECASE)), None)
    if not match:
        return ""
    product = (match.group("product") or "").strip(" -")
    # The pattern may include workflow words before the product; retain only a
    # clean final product phrase when it contains a known product noun.
    product_match = re.search(r"(printers?|products?|services?)\b", product, flags=re.IGNORECASE)
    product = product_match.group(1) if product_match else ""
    festival = match.group("festival").strip()
    discount = match.group("discount").strip().rstrip(".")
    # Keep the offer terms in English, but let the Telugu welcome own the
    # sentence structure: "maa daggara Ganesh Chaturthi offer lo printers
    # meeda 30% discount nadusthundhi."
    offer = f"{festival} offer lo {product} meeda {discount} nadusthundhi".strip()
    return re.sub(r"\s+", " ", offer)


def _looks_like_internal_instruction(value: str) -> bool:
    lowered = value.casefold()
    return any(marker in lowered for marker in (
        "needs to call", "need to call", "call the customer", "call customers",
        "explain about", "know if the customer", "know whether the customer",
        "employee must call", "employee should call", "tell the leads",
    ))


def _default_end_call_message(language: str) -> str:
    if language == "Telugu":
        return "thanks andi, have a nice day."
    return "Thank you for your time."


def _is_spoken_welcome(value: str) -> bool:
    """Exclude builder-generated instructions from the provider's spoken welcome."""
    if not value:
        return False
    lowered = value.casefold()
    instruction_markers = (
        "greet naturally", "identify yourself", "explain the verified",
        "ask how you can help", "ani natural ga greet cheyyandi",
        "munduga configured business purpose",
    )
    return not any(marker in lowered for marker in instruction_markers)


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


def _first(value: Any) -> Any:
    return value[0] if isinstance(value, list) and value else value


def _extract_six_section_prompt(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            title = item.get("context_title") or item.get("title")
            if title == "Published Call Script Source of Truth":
                return _script_body(item.get("body") or item.get("context") or item.get("content"))
        return ""
    return _script_body(value)


def _script_body(value: Any) -> str:
    text = _text(value)
    marker = "1. Identity & Purpose"
    index = text.find(marker)
    return text[index:] if index >= 0 else text


def _extract_six_section_titles(prompt: str) -> list[str]:
    titles: list[str] = []
    for title in SCRIPT_SECTION_NAMES:
        pattern = rf"(?:^|\n)\s*\d+\.\s*{re.escape(title)}(?:\n|$)"
        if re.search(pattern, prompt):
            titles.append(title)
    return titles


def _is_missing(value: Any) -> bool:
    return value is None or value == "" or value == []


def _normalize_for_compare(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()
    if isinstance(value, list):
        return [_normalize_for_compare(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_for_compare(item) for key, item in value.items()}
    return value


def _values_match(field: str, expected: Any, actual: Any) -> bool:
    if field == "transcriber_provider" and isinstance(actual, str):
        return str(expected).casefold() in actual.casefold()
    return _normalize_for_compare(expected) == _normalize_for_compare(actual)


def _log_value(value: Any) -> str:
    text = str(value)
    return text if len(text) <= 240 else text[:237] + "..."


def _safe_payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    voice = payload.get("voice") if isinstance(payload.get("voice"), dict) else {}
    transcriber = payload.get("transcriber") if isinstance(payload.get("transcriber"), dict) else payload.get("transcriber")
    sections = payload.get("context_breakdown") if isinstance(payload.get("context_breakdown"), list) else []
    return {
        "payload_keys": sorted(payload),
        "call_type": payload.get("call_type"),
        "languages": payload.get("languages"),
        "model": payload.get("model"),
        "voice_provider": voice.get("provider"),
        "voice_id_present": bool(voice.get("voice_id")),
        "transcriber": transcriber,
        "welcome_message": payload.get("welcome_message"),
        "context_titles": [item.get("title") for item in sections if isinstance(item, dict)],
        "context_chars": sum(len(str(item.get("body", ""))) for item in sections if isinstance(item, dict)),
        "is_interruption_allowed": payload.get("is_interruption_allowed"),
        "interruption_min_words": payload.get("interruption_min_words"),
        "is_end_call_enabled": payload.get("is_end_call_enabled"),
        "user_idle_threshold_sec": payload.get("user_idle_threshold_sec"),
    }


def _safe_readback_summary(readback: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(readback, dict):
        return None
    voice = readback.get("voice") if isinstance(readback.get("voice"), dict) else {}
    sections = readback.get("context_breakdown") if isinstance(readback.get("context_breakdown"), list) else []
    model = readback.get("model")
    return {
        "call_type": readback.get("bot_call_type") or readback.get("call_type"),
        "languages": readback.get("languages"),
        "model": model.get("model") if isinstance(model, dict) else model,
        "voice_provider": voice.get("provider") or readback.get("voice_provider"),
        "voice_id_present": bool(voice.get("voice_id") or readback.get("voice_external_id")),
        "transcriber": readback.get("asr_service") or readback.get("transcriber"),
        "welcome_message": readback.get("welcome_message"),
        "context_titles": [item.get("context_title") or item.get("title") for item in sections if isinstance(item, dict)],
        "is_interruption_allowed": readback.get("is_interruption_allowed"),
        "interruption_min_words": readback.get("interruption_min_words"),
        "is_end_call_enabled": readback.get("is_end_call_enabled"),
        "user_idle_threshold_sec": readback.get("user_idle_threshold_sec"),
    }


def _result(agent: ProviderAgent, *, verification: dict[str, Any] | None = None) -> AgentSyncResult:
    metadata = dict(agent.metadata or {})
    if verification is not None:
        metadata["provider_verification"] = verification
        metadata["intended_configuration"] = verification.get("intended_configuration")
        metadata["sent_configuration"] = verification.get("sent_configuration")
        metadata["returned_configuration"] = verification.get("returned_configuration")
        metadata["verification_mismatches"] = verification.get("mismatches", [])
        metadata["verification_status"] = verification.get("status")
    return AgentSyncResult(agent.provider_id, agent.status, metadata)


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
