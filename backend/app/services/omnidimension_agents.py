from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import json
from typing import Any

from app.integrations.omnidimension import OmniDimensionAgentProvider, ProviderAgent
from app.integrations.omnidimension.exceptions import OmniDimensionResponseError
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
from app.services.employee_templates import UNIVERSAL_TELUGU_VOICE_GUIDANCE
from app.services.voice_catalog import voice_definition
from app.core.config import get_settings

logger = logging.getLogger(__name__)

OMNI_LIVE_MODEL = "gpt-4.1-mini"


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
        logger.info(
            "[OMNI_REQUEST_JSON] %s",
            json.dumps({"post_call_actions": payload.get("post_call_actions")}, ensure_ascii=False, sort_keys=True),
        )
        # Draft versions deliberately do not duplicate the unique provider
        # identity. When publishing a draft, update the currently published
        # Omni agent; only create an agent when the employee has never been
        # deployed. This keeps provider and local version history aligned.
        existing_provider_id = version.provider_agent_id
        if not existing_provider_id and employee.published_version is not None and employee.published_version.id != version.id:
            existing_provider_id = employee.published_version.provider_agent_id
        if existing_provider_id:
            # Preserve provider-side post-call actions while enforcing exactly
            # one Pontis webhook configuration during every republish.
            existing = self.provider.get_agent(existing_provider_id)
            existing_actions = existing.get("post_call_actions") if isinstance(existing, dict) else None
            if isinstance(existing_actions, dict):
                payload["post_call_actions"] = {**existing_actions, **payload["post_call_actions"]}
                payload["post_call_actions"]["webhook"] = _automatic_post_call_actions()["webhook"]
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
            verification_summary = _verify_post_call_persistence(
                agent_id=provider_agent.provider_id,
                expected_webhook=_automatic_post_call_actions()["webhook"],
                response=readback,
            )
            logger.info("[OMNI_POST_CALL_VERIFICATION] %s", json.dumps(verification_summary, ensure_ascii=False, sort_keys=True))
            if verification_summary["configured"]:
                logger.info(
                    "[OMNI_POST_CALL_CONFIGURED] %s",
                    json.dumps(verification_summary, ensure_ascii=False, sort_keys=True),
                )
            else:
                logger.warning(
                    "[OMNI_POST_CALL_NOT_PERSISTED] %s",
                    json.dumps(verification_summary, ensure_ascii=False, sort_keys=True),
                )
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
        webhook_mismatch = any(item.get("field") in {"webhook_enabled", "webhook_url", "webhook_statuses"} for item in verification.get("mismatches", []))
        if webhook_mismatch:
            logger.warning(
                "OmniDimension post-call webhook was not verified agent_id=%s; continuing because public API persistence is unconfirmed.",
                provider_agent.provider_id,
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
    from app.services.conversation_design import runtime_rules
    configuration = dict(configuration)
    lang = _language_name(_text(configuration.get("language"), employee.language))
    saved_script = _canonical_call_script(configuration)
    # Generated scripts retain structured metadata internally. Once an owner
    # edits a card, the save path removes conversation_sections and the edited
    # six-card script becomes the source of truth.
    sections = configuration.get("conversation_sections")
    edited_script = configuration.get("call_script")
    if isinstance(sections, list) and len(sections) == 6:
        canonical_prompt = "\n\n".join(
            f"SECTION {index} — {item.get('title', '')}\nPurpose: {item.get('purpose', '')}\n"
            f"Instructions: {item.get('instructions', item.get('flow', ''))}\n"
            f"Questions: {'; '.join(item.get('questions', []))}\n"
            f"Spoken examples: {'; '.join(item.get('examples', []))}\n"
            f"Handling: {item.get('handling', '')}"
            for index, item in enumerate(sections, 1) if isinstance(item, dict)
        )
    elif isinstance(edited_script, dict) and len(edited_script) == 6:
        canonical_prompt = "\n\n".join(
            f"SECTION {index} — {title}\n{body}"
            for index, (title, body) in enumerate(edited_script.items(), 1)
        )
    else:
        canonical_prompt = str(configuration.get("final_prompt") or build_employee_prompt(configuration))
    context = [
        {"title": "Published Call Script Source of Truth", "body": _format_call_script(saved_script), "is_enabled": True},
        {"title": "Employee Runtime Rules", "body": runtime_rules(configuration), "is_enabled": True},
        {"title": "Complete Employee Instructions", "body": canonical_prompt, "is_enabled": True},
    ]
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
        # Omni substitutes {{name}} from the dispatch call_context at call
        # time.  Declare the slot on the published agent without assigning a
        # contact value: each call supplies its own value in call_context.
        "dynamic_variables": {"name": ""},
        "context_breakdown": context,
        # Omni's live voice agent uses OpenAI. Script-generation providers are
        # separate and must never leak into the live call-agent configuration.
        # Keep responses deterministic for concise, low-variance call turns.
        "model": {"model": OMNI_LIVE_MODEL, "temperature": 0.1},
        "languages": [lang],
        "post_call_actions": post_call_actions,
        # Make the speech handoff explicit so the provider starts listening
        # immediately after the static welcome instead of relying on account
        # defaults. All values can be overridden by employee configuration.
        "transcriber": _transcriber_configuration(configuration, lang),
        "is_welcome_message_dynamic": True,
        "is_welcome_message_interruption": True,
        "is_interruption_allowed": True,
        "interruption_min_words": 3,
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
    # As of September 2026, real create/update + GET probes have shown this
    # documented field can be silently ignored. Dashboard configuration may be
    # required for delivery; retain this payload for compatibility and logging,
    # but do not treat its absence in GET as a publish-blocking failure.
    settings = get_settings()
    if settings.environment.casefold() == "production" and not settings.backend_public_url.strip():
        raise RuntimeError("BACKEND_PUBLIC_URL is required in production to configure the OmniDimension post-call webhook.")
    webhook_url = f"{settings.backend_public_url.rstrip('/')}/api/v1/webhooks/omnidimension/post-call"
    return {
        "webhook": {
            "url": webhook_url,
            "extracted_variables": [],
            "trigger_call_statuses": ["completed", "failed", "no_answer", "busy"],
        }
    }


def _verify_post_call_persistence(*, agent_id: str, expected_webhook: dict[str, Any], response: Any) -> dict[str, Any]:
    """Verify the documented GET /agents post_call_config_ids representation."""
    configs = response.get("post_call_config_ids") if isinstance(response, dict) else None
    expected_url = str(expected_webhook["url"])
    expected_statuses = set(expected_webhook["trigger_call_statuses"])
    sanitized_configs = [_sanitize_post_call_config(config) for config in configs] if isinstance(configs, list) else configs
    matching_config: dict[str, Any] | None = None
    for config in configs if isinstance(configs, list) else []:
        if not isinstance(config, dict) or config.get("webhook_url") != expected_url:
            continue
        statuses = config.get("trigger_call_statuses")
        if isinstance(statuses, list) and expected_statuses.issubset(set(statuses)):
            matching_config = config
            break
    return {
        "agent_id": str(agent_id),
        "create_or_update_status": 200,
        "configured": matching_config is not None,
        "post_call_config_id": str(matching_config["id"]) if isinstance(matching_config, dict) and matching_config.get("id") is not None else None,
        "webhook_url": matching_config.get("webhook_url") if matching_config else None,
        "expected_webhook_url": expected_url,
        "trigger_call_statuses": matching_config.get("trigger_call_statuses") if matching_config else None,
        "post_call_config_ids": sanitized_configs,
    }


def _sanitize_post_call_config(config: Any) -> Any:
    if not isinstance(config, dict):
        return config
    return {
        key: config.get(key)
        for key in (
            "id", "delivery_method", "webhook_url", "payload_transformation_type",
            "extracted_variables", "trigger_call_statuses",
        )
    }


def _find_nested_key(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_nested_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_nested_key(child, key)
            if found is not None:
                return found
    return None


def _canonical_call_script(configuration: dict[str, Any]) -> dict[str, str]:
    script = configuration.get("call_script")
    if not isinstance(script, dict):
        return {}
    result = {title: _text(body) for title, body in script.items()}
    return result if len(result) == 6 and all(result.values()) else {}


def _format_call_script(script: dict[str, str]) -> str:
    return "\n\n".join(
        f"{index}. {title}\n{script[title]}"
        for index, title in enumerate(script, 1)
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
        "model": OMNI_LIVE_MODEL,
        "transcriber_provider": "soniox",
        "transcriber_language": _soniox_language_code(language),
        "voice_provider": str(voice_provider) if voice_provider else None,
        "voice_id": str(voice_id) if voice_id else None,
        "welcome_message": _welcome_message(employee, configuration, language),
        "is_welcome_message_dynamic": True,
        "six_section_prompt": _format_call_script(script) if script else "",
        "six_section_titles": list(script) if script else [],
        "interruption_enabled": True,
        "interruption_min_words": 3,
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
    post_call_configs = readback.get("post_call_config_ids")
    if not webhook and isinstance(post_call_configs, list):
        webhook = next(
            (config for config in post_call_configs if isinstance(config, dict) and isinstance(config.get("webhook_url"), str)),
            {},
        )
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
        "webhook_enabled": bool(webhook) if ("post_call_actions" in readback or isinstance(post_call_configs, list)) else None,
        "webhook_url": (webhook.get("url") or webhook.get("webhook_url")) if isinstance(webhook, dict) else readback.get("webhook_url"),
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
        "silence_timeout_ms": getattr(get_settings(), "live_speech_silence_timeout_ms", 500),
        "interruption_min_words": 3,
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


def _remove_builder_instruction(text: str) -> str:
    """Keep UI-only employee-builder instructions out of Omni's spoken greeting."""
    cleaned = re.sub(
        r"\s*Describe\s+the\s+job,\s*business\s+process,\s*customers,\s+or\s+goal\.?\s*",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _spoken_examples_from_card(card: Any) -> list[str]:
    """Read new script-first cards while preserving legacy card support."""
    examples: list[str] = []
    for line in _text(card).splitlines():
        match = re.match(r"^\s*(?:Spoken example|AI):\s*(.+?)\s*$", line)
        if not match:
            continue
        candidate = match.group(1).strip()
        if len(candidate) >= 2 and candidate[0] == candidate[-1] == '"':
            candidate = candidate[1:-1].strip()
        if candidate:
            examples.append(candidate)
    return examples


def _welcome_message(employee: AIEmployee, configuration: dict[str, Any], language: str) -> str:
    """Build the static greeting sent to Omni, excluding builder-only copy."""
    outbound = _text(configuration.get("call_type", employee.call_type)).casefold() == "outbound"
    if outbound and configuration.get("script_source") == "reviewed":
        cards = configuration.get("call_script")
        if isinstance(cards, dict) and len(cards) == 6:
            reviewed_sections = []
            for card in cards.values():
                examples = _spoken_examples_from_card(card)
                reviewed_sections.append({"examples": examples, "questions": []})
            first_examples = reviewed_sections[0]["examples"]
            if first_examples and _is_valid_outbound_opening(first_examples[0], reviewed_sections):
                return first_examples[0]
    generated_sections = configuration.get("conversation_sections")
    if outbound and isinstance(generated_sections, list) and len(generated_sections) == 6:
        first_examples = generated_sections[0].get("examples") if isinstance(generated_sections[0], dict) else None
        if isinstance(first_examples, list) and first_examples:
            candidate = _text(first_examples[0])
            if _is_valid_outbound_opening(candidate, generated_sections):
                return candidate
        return _static_outbound_welcome(
            configuration,
            purpose=_safe_purpose(configuration.get("purpose"), employee.purpose),
            language=language,
        )
    if outbound:
        return _static_outbound_welcome(
            configuration,
            purpose=_safe_purpose(configuration.get("purpose"), employee.purpose),
            language=language,
        )
    base = _remove_builder_instruction(_raw_welcome_message(employee, configuration, language))
    if language == "Telugu":
        return (
            "Generate the opening greeting using the caller name variable when it is available. "
            "Start naturally with: నమస్కారం {{name}} గారు. "
            "If the name is missing or blank, omit {{name}} గారు entirely, start with నమస్కారం అండి, "
            "and continue without asking for the caller's name or leaving an awkward gap. "
            f"Then continue naturally with this approved opening: {base}"
        )
    return (
        "Generate the opening greeting using the caller name variable when it is available. "
        "Start naturally with: Hello {{name}}. "
        "If the name is missing or blank, omit {{name}} entirely, use a generic greeting, "
        "and continue without asking for the caller's name or leaving an awkward gap. "
        f"Then continue naturally with this approved opening: {base}"
    )


def _is_valid_outbound_opening(candidate: str, sections: list[Any]) -> bool:
    """Accept only a safe, unique first Opening example for Omni's greeting."""
    candidate = _text(candidate)
    if not candidate or re.search(r"\{\{[^}]+\}\}", candidate) or re.search(r"[?？]$", candidate):
        return False
    lowered = candidate.casefold()
    if any(phrase in lowered for phrase in (
        "is this a good time", "do you have time", "can i ask", "may i", "are you available",
    )):
        return False
    spoken_lines: list[str] = []
    for section in sections:
        if isinstance(section, dict):
            for field in ("questions", "examples"):
                spoken_lines.extend(_text(line) for line in section.get(field) or [])
    normalized = re.sub(r"\s+", " ", candidate).strip().casefold()
    return sum(re.sub(r"\s+", " ", line).strip().casefold() == normalized for line in spoken_lines) == 1


def _static_outbound_welcome(configuration: dict[str, Any], *, purpose: str, language: str) -> str:
    """Safe non-question fallback used when the generated Opening is unusable."""
    agent_name = _text(configuration.get("agent_name"))
    if language == "Telugu":
        return f"Hello అండి, నేను {agent_name}." if agent_name else "Hello అండి, నేను AI assistant ని."
    if language == "Hindi":
        return f"नमस्ते जी, मैं {agent_name} हूँ।" if agent_name else "नमस्ते जी, मैं AI assistant हूँ।"
    return f"Hello, I am {agent_name}." if agent_name else "Hello, I am an AI assistant."


def _raw_welcome_message(employee: AIEmployee, configuration: dict[str, Any], language: str) -> str:
    """Use approved inbound speech or a neutral greeting; never speak the brief."""
    if configuration.get("conversation_design"):
        return _text(configuration.get("opening"))
    sections = configuration.get("conversation_sections")
    if isinstance(sections, list) and len(sections) == 6 and isinstance(sections[0], dict):
        examples = sections[0].get("examples")
        if isinstance(examples, list) and examples and _text(examples[0]):
            return _text(examples[0])
    greeting = _text(configuration.get("greeting"))
    if greeting and (language.startswith("English") or _contains_language_script(greeting, language)):
        return greeting
    cards = configuration.get("call_script")
    if isinstance(cards, dict):
        card = _text(cards.get("Greeting & Intro") or next(iter(cards.values()), ""))
        examples = _spoken_examples_from_card(card)
        candidate = examples[0] if examples else ""
        if candidate and not _contains_raw_description(candidate, configuration):
            return candidate
    name = _text(configuration.get("agent_name"))
    if language == "Telugu":
        return f"Hello అండి, నేను {name}. మీకు ఎలా help చేయగలను?" if name else "Hello అండి, నేను AI assistant ని. మీకు ఎలా help చేయగలను?"
    if language == "Hindi":
        return f"नमस्ते जी, मैं {name} हूँ। आपकी कैसे help कर सकता हूँ?" if name else "नमस्ते जी, मैं AI assistant हूँ। आपकी कैसे help कर सकता हूँ?"
    return f"Hello, I am {name}. How can I help?" if name else "Hello, I am an AI assistant. How can I help?"


def _contains_raw_description(text: str, configuration: dict[str, Any], minimum_words: int = 16) -> bool:
    """Detect an owner brief being copied into caller-facing welcome text."""
    descriptions = [
        _text(configuration.get(key))
        for key in ("business_description", "description", "original_requirement", "purpose")
    ]
    normalized_text = re.findall(r"[\w%'-]+", text.casefold())
    if not normalized_text:
        return False
    for description in descriptions:
        words = re.findall(r"[\w%'-]+", description.casefold())
        if len(words) < minimum_words:
            continue
        for start in range(0, len(words) - minimum_words + 1):
            if words[start:start + minimum_words] == normalized_text[:minimum_words]:
                return True
            phrase = words[start:start + minimum_words]
            if " ".join(phrase) in text.casefold():
                return True
    return False
def _default_end_call_message(language: str) -> str:
    if language == "Telugu":
        return "Thank you. Have a nice day."
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
    match = re.search(r"(?m)^1\. [^\n]+$", text)
    return text[match.start():] if match else text


def _extract_six_section_titles(prompt: str) -> list[str]:
    return re.findall(r"(?m)^[1-6]\. ([^\n]+)$", prompt)


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
