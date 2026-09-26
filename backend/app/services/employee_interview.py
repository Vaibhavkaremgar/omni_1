from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import hashlib
import re
import logging
import random
import time
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_employee import AIEmployee
from app.models.ai_employee_version import AIEmployeeVersion
from app.models.employee_interview_session import EmployeeInterviewSession
from app.services.employee_prompt import compose_employee_configuration
from app.services.call_script_master_prompt import CALL_SCRIPT_MASTER_SYSTEM_PROMPT
from app.services.script_language_validation import validate_customer_facing_script
from app.services.script_validation import validate_script

logger = logging.getLogger(__name__)

DEFAULT_GROQ_SCRIPT_MODEL = "openai/gpt-oss-120b"
SCRIPT_SECTION_TITLES = (
    "Opening",
    "Details",
    "Listening and acknowledging",
    "Main request",
    "Questions and exceptions",
    "Closing",
)


def render_call_script_sections(sections: list[dict[str, Any]]) -> dict[str, str]:
    """Render the validated section model into the editable six-card format."""
    return {
        str(section["title"]): "\n".join([
            f"Purpose: {section['purpose']}",
            f"Instructions: {section['instructions']}",
            *[f"Question: {question}" for question in section["questions"]],
            *[f"Spoken example: {example}" for example in section["examples"]],
            f"Handling: {section['handling']}",
        ])
        for section in sections
    }


def _failure_category(error: HTTPException) -> str:
    detail = str(error.detail).lower()
    if "http 429" in detail or "rate limit" in detail or "quota" in detail or "resource exhausted" in detail or "too many requests" in detail:
        return "provider_rate_limit_or_quota"
    if "http 503" in detail or "high demand" in detail or "temporarily unavailable" in detail:
        return "provider_unavailable"
    if "timed out" in detail:
        return "groq_timeout"
    if "api key" in detail or "authentication" in detail or "unauthorized" in detail:
        return "provider_authentication"
    if "json" in detail or "completion" in detail or "response" in detail:
        return "llm_response_error"
    return "llm_error"


def now() -> datetime:
    return datetime.now(timezone.utc)


class SuggestedQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=1000)


class SuggestedConversationVariable(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    type: Literal["text", "number", "phone", "email", "date", "datetime", "boolean"]
    required: bool = False


class ConversationVariableSuggestions(BaseModel):
    variables: list[SuggestedConversationVariable] = Field(default_factory=list, max_length=12)


def strict_script_response_schema() -> dict[str, Any]:
    """The exact wire contract used by six-section script generation.

    Keep this deliberately small and provider-compatible.  The validator below
    remains the source of truth for semantic/size checks and bounded legacy
    normalization; this schema prevents Groq from being asked for a looser
    ``json_object`` than the application can consume.
    """
    section = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1},
            "purpose": {"type": "string", "minLength": 1},
            "instructions": {"type": "string", "minLength": 1},
            "questions": {"type": "array", "items": {"type": "string"}},
            "examples": {"type": "array", "items": {"type": "string"}},
            "handling": {"type": "string", "minLength": 1},
        },
        "required": ["title", "purpose", "instructions", "questions", "examples", "handling"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        # Groq's structured-output validator rejects minItems/maxItems for
        # this model.  Exact cardinality is enforced by _validate_script_response
        # after the provider returns JSON.
        "properties": {"sections": {"type": "array", "items": section}},
        "required": ["sections"],
        "additionalProperties": False,
    }


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep only JSON Schema keywords supported by Gemini structured output."""
    unsupported = {"minLength", "maxLength", "pattern", "default", "examples"}

    def normalize(value: Any, *, property_map: bool = False) -> Any:
        if isinstance(value, dict):
            if property_map:
                return {key: normalize(item) for key, item in value.items()}
            return {
                key: normalize(item, property_map=key == "properties")
                for key, item in value.items()
                if key not in unsupported
            }
        if isinstance(value, list):
            return [normalize(item) for item in value]
        return value

    return normalize(schema)


def _render_script_system_prompt(template: str, **values: str) -> str:
    """Fill every script-prompt placeholder before any provider request."""
    rendered = template
    for key in ("USER_CONTEXT", "LANGUAGE", "CALL_DIRECTION", "HOST_NAME", "AGENT_NAME", "BUSINESS_NAME", "KNOWLEDGE"):
        rendered = rendered.replace("{{" + key + "}}", values.get(key, "") or values.get(key.lower(), "") or "")
    return rendered


def _log_script_violations(request_id: UUID, violations: list[dict[str, str]]) -> None:
    for violation in violations:
        logger.warning(
            "LLM script validation violation request_id=%s section=%s rule=%s line=%s",
            request_id, violation.get("section"), violation.get("rule"), violation.get("line", "")[:240],
        )
def _safe_script_shape(value: Any) -> dict[str, Any]:
    """Return structural diagnostics without logging model/customer content."""
    if not isinstance(value, dict):
        return {"top_level_type": type(value).__name__}
    sections = value.get("sections")
    result: dict[str, Any] = {"top_level_type": "dict", "top_level_keys": sorted(str(key) for key in value)}
    if not isinstance(sections, list):
        result["sections"] = {"type": type(sections).__name__}
        return result
    result["sections"] = {"type": "list", "length": len(sections), "items": []}
    for index, section in enumerate(sections):
        item: dict[str, Any] = {"index": index, "type": type(section).__name__}
        if isinstance(section, dict):
            item["keys"] = sorted(str(key) for key in section)
            item["field_types"] = {str(key): type(section[key]).__name__ for key in section}
        result["sections"]["items"].append(item)
    return result


def _provider_token_usage(body: dict[str, Any]) -> dict[str, int | None]:
    """Normalize Gemini, OpenAI/Groq, and Anthropic usage metadata for logs."""
    usage = body.get("usageMetadata") or body.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    completion_details = usage.get("completion_tokens_details") or {}
    prompt_details = usage.get("prompt_tokens_details") or {}
    completion_details = completion_details if isinstance(completion_details, dict) else {}
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}

    def first(*keys: str) -> int | None:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
        return None

    reasoning_tokens = completion_details.get("reasoning_tokens")
    cached_tokens = prompt_details.get("cached_tokens")

    return {
        "input": first("promptTokenCount", "inputTokenCount", "total_input_tokens", "prompt_tokens", "input_tokens"),
        "output": first("candidatesTokenCount", "outputTokenCount", "total_output_tokens", "completion_tokens", "output_tokens"),
        "thinking": first("thoughtsTokenCount", "total_thought_tokens")
        if first("thoughtsTokenCount", "total_thought_tokens") is not None
        else (reasoning_tokens if isinstance(reasoning_tokens, int) and not isinstance(reasoning_tokens, bool) else None),
        "cached": first("cachedContentTokenCount", "total_cached_tokens")
        if first("cachedContentTokenCount", "total_cached_tokens") is not None
        else (cached_tokens if isinstance(cached_tokens, int) and not isinstance(cached_tokens, bool) else None),
        "total": first("totalTokenCount", "total_tokens"),
    }


def _request_model(payload: dict[str, Any]) -> str | None:
    model = payload.get("json", {}).get("model")
    if model:
        return str(model)
    match = re.search(r"/models/([^/:]+)", str(payload.get("url") or ""))
    return match.group(1) if match else None


def _script_spoken_lines(sections: list[dict[str, Any]]) -> list[str]:
    return [
        str(line).strip()
        for section in sections
        if isinstance(section, dict)
        for field in ("questions", "examples")
        for line in section.get(field) or []
        if str(line).strip()
    ]


def _script_language_violations(sections: list[dict[str, Any]], language: str) -> list[dict[str, str]]:
    normalized = str(language or "").casefold()
    if normalized in {"telugu", "te", "te-in", "telugu (india)"}:
        native_start, native_end, label = 0x0C00, 0x0C7F, "Telugu"
        roman_markers = {"nenu", "mee", "meeru", "meeku", "gurinchi", "chesanu", "andi", "namaskaram"}
    elif normalized in {"hindi", "hi", "hi-in", "hindi (india)"}:
        native_start, native_end, label = 0x0900, 0x097F, "Hindi"
        roman_markers = {"namaste", "main", "mein", "aap", "aapki", "se", "bol", "raha", "hai", "kya"}
    else:
        return []
    lines = _script_spoken_lines(sections)
    issues: list[dict[str, str]] = []
    if not any(any(native_start <= ord(char) <= native_end for char in line) for line in lines):
        roman = any(marker in {word.casefold() for word in re.findall(r"[A-Za-z]+", line)} for line in lines for marker in roman_markers)
        reason = (
            f"The model returned Roman-script {label}; write examples in {label} script mixed with English words."
            if roman else
            f"The model returned no {label} Unicode; write examples in {label} script mixed with English words."
        )
        issues.append({"rule": "native_script", "reason": reason, "severity": "error"})
    return issues


def _repair_feedback(issues: list[dict[str, str]]) -> str:
    return "\n".join(
        f"- {item.get('rule', 'script_rule')}: {item.get('reason', 'Correct the script rule.')}; "
        f"line={item.get('line', '')[:240]}"
        for item in issues
    )


def _normalise_json_object_script(value: Any) -> dict[str, Any]:
    """Convert permissive json_object output into the six-section contract."""
    if not isinstance(value, dict):
        return {"sections": []}
    raw_sections = value.get("sections")
    if isinstance(raw_sections, list):
        sections = raw_sections
    else:
        sections = [{"title": key, "content": item} for key, item in value.items() if key not in {"source", "metadata"}]
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(sections[:6]):
        if not isinstance(item, dict):
            item = {"content": item}
        title = str(item.get("title") or SCRIPT_SECTION_TITLES[index]).strip()
        content = str(item.get("instructions") or item.get("content") or "").strip()
        purpose = str(item.get("purpose") or f"Complete the {title.lower()} part of the call.").strip()
        instructions = content or "Use only the stated user context and the approved call rules."
        questions = item.get("questions") if isinstance(item.get("questions"), list) else []
        examples = item.get("examples") if isinstance(item.get("examples"), list) else []
        handling = str(item.get("handling") or "If the person is busy, confused, or asks to stop, respond respectfully and end when appropriate.").strip()
        if not examples and content:
            matches = re.findall(r"(?:Spoken example|Example):\s*(.+?)(?=\s+(?:Handling|Purpose|Instructions|Question):|$)", content, re.IGNORECASE)
            examples = [match.strip() for match in matches if match.strip()]
        normalized.append({
            "title": title,
            "purpose": purpose,
            "instructions": instructions,
            "questions": [str(item).strip() for item in questions if str(item).strip()],
            "examples": [str(item).strip() for item in examples if str(item).strip()],
            "handling": handling,
        })
    while len(normalized) < 6:
        index = len(normalized)
        normalized.append({
            "title": SCRIPT_SECTION_TITLES[index],
            "purpose": f"Complete the {SCRIPT_SECTION_TITLES[index].lower()} part of the call.",
            "instructions": "Use only the stated user context and the approved call rules.",
            "questions": [],
            "examples": [],
            "handling": "If the person is busy, confused, or asks to stop, respond respectfully and end when appropriate.",
        })
    return {"sections": normalized}


class InterviewLLMResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    assistant_message: str = Field(min_length=1, max_length=4000)
    next_question: str | None = Field(default=None, max_length=2000)
    configuration_updates: dict[str, Any] = Field(default_factory=dict)
    missing_topics: list[str] = Field(default_factory=list)
    progress: int = Field(ge=0, le=100)
    is_complete: bool
    suggestions: list[SuggestedQuestion] = Field(default_factory=list, max_length=5)
    ready_to_build: bool = False


@dataclass(frozen=True)
class InterviewGeneration:
    assistant_message: str
    suggested_next_question: str | None
    progress: int
    configuration_updates: dict[str, Any]
    missing_topics: list[str]
    is_complete: bool
    suggestions: list[dict[str, str]] = field(default_factory=list)
    ready_to_build: bool = False


@dataclass(frozen=True)
class LLMAttempt:
    provider: str
    model: str
    api_key: str
    base_url: str | None = None


class LLMService(ABC):
    @abstractmethod
    def initial_question(self, employee: AIEmployee) -> InterviewGeneration:
        raise NotImplementedError

    @abstractmethod
    def next_question(
        self,
        employee: AIEmployee,
        questions: list[str],
        answers: list[dict],
        configuration: dict,
    ) -> InterviewGeneration:
        raise NotImplementedError


class DevelopmentLLMService(LLMService):
    """Offline fallback used only when a provider-backed LLM is unavailable locally."""

    question_plan = (
        ("goals", "What job should this employee complete?"),
        ("audience", "Who will it speak with?"),
        ("facts", "What exact facts, rules, or process steps should it use?"),
        ("questions", "What information is truly necessary to ask for this job?"),
        ("tone", "What tone should it use?"),
        ("handoff_rules", "When should it hand off or stop instead of continuing?"),
        ("completion", "How should it know the task is complete?"),
        ("post_call_extraction", "What information should be recorded after each conversation?"),
    )
    completion_turns = 6

    def initial_question(self, employee: AIEmployee) -> InterviewGeneration:
        return InterviewGeneration(
            assistant_message=f"I’ll help shape {employee.name} into a useful employee. Let’s start with the most important goal.",
            suggested_next_question="What is this employee for?",
            progress=0,
            configuration_updates={},
            missing_topics=["goals"],
            is_complete=False,
        )

    def next_question(self, employee, questions, answers, configuration):
        latest_question = questions[-1].lower() if questions else ""
        latest_answer = (answers[-1].get("answer") or "").strip()
        updates = self.extract_configuration(latest_question, latest_answer)
        merged_keys = set(configuration) | set(updates)
        answered = len(answers)
        plan = self._question_plan_for(employee)
        missing_topics = [key for key, _ in plan if key not in merged_keys]
        is_complete = answered >= self.completion_turns or not missing_topics
        next_question = None if is_complete else self._question_for_topic(employee, missing_topics[0])

        progress = min(100, round((answered / self.completion_turns) * 100))
        if is_complete:
            progress = 100

        return InterviewGeneration(
            assistant_message=(
                "I understand. I’ve captured that and I’ll use it in the draft."
                if not is_complete
                else "I have enough to prepare the employee configuration for review."
            ),
            suggested_next_question=next_question,
            progress=progress,
            configuration_updates=updates,
            missing_topics=missing_topics,
            is_complete=is_complete,
            suggestions=[] if is_complete else self._contextual_suggestions(employee, missing_topics, answers),
            ready_to_build=is_complete,
        )

    def _contextual_suggestions(
        self, employee: AIEmployee, missing_topics: list[str], answers: list[dict]
    ) -> list[dict[str, str]]:
        """Keep local development usable without presenting a static shortcut list."""
        context = " ".join(str(item.get("answer", "")) for item in answers).casefold()
        suggestions: list[dict[str, str]] = []
        for topic in missing_topics[:5]:
            question = self._question_for_topic(employee, topic)
            if not question:
                continue
            reason = "This fills in a detail needed to make the employee useful."
            if topic == "target_customers" and any(word in context for word in ("clinic", "patient", "doctor")):
                question = "Which patients should it speak with, and which should be routed to the clinic team?"
                reason = "Patient routing changes the questions and escalation path."
            elif topic == "questions" and any(word in context for word in ("appointment", "booking", "reschedule")):
                question = "Which appointment details are necessary to change or confirm the booking?"
                reason = "The appointment task determines which details are actually required."
            elif topic == "handoff_rules" and any(word in context for word in ("support", "issue", "billing")):
                question = "Which support cases should be handed to a human immediately?"
                reason = "Clear handoffs keep sensitive cases from being mishandled."
            suggestions.append({"question": question, "reason": reason})
        return suggestions

    def _question_plan_for(self, employee: AIEmployee) -> tuple[tuple[str, str], ...]:
        purpose = employee.purpose.lower()
        if any(word in purpose for word in ("support", "help desk", "service")):
            return (
                ("goals", "What should this employee accomplish for customers?"),
                ("target_customers", "Which customers or account types will it support?"),
                ("tone", "What tone should it use when customers are frustrated?"),
                ("questions", "What issue details should it collect before escalating?"),
                ("handoff_rules", "When should it hand off to a human agent?"),
                ("completion", "How should it know the support conversation is complete?"),
                ("post_call_extraction", "What should be recorded after the call?"),
            )
        if any(word in purpose for word in ("lead", "sales", "qualify", "book", "demo")):
            return (
                ("goals", "What job should this employee complete?"),
                ("facts", "What offer, service, or process facts may it state?"),
                ("audience", "Who should it speak with?"),
                ("questions", "Which questions are necessary for this specific job?"),
                ("completion", "What should it do when the job is complete?"),
                ("handoff_rules", "When should it hand off to a human?"),
                ("post_call_extraction", "What information should be captured after each conversation?"),
            )
        return self.question_plan

    def _first_question(self, employee: AIEmployee) -> str:
        plan = self._question_plan_for(employee)
        return plan[0][1] if plan else "What should this employee do for the business?"

    def _question_for_topic(self, employee: AIEmployee, topic: str) -> str | None:
        for key, question in self._question_plan_for(employee):
            if key == topic:
                return question
        return None

    def extract_configuration(self, question: str, answer: str) -> dict[str, Any]:
        if not answer:
            return {}
        updates: dict[str, Any] = {}
        if any(word in question for word in ("accomplish", "goal", "purpose", "employee for")):
            updates["goals"] = [answer]
        if any(word in question for word in ("product", "service", "offer")):
            updates["products"] = [answer]
        if "customer" in question or "audience" in question or "lead" in question or "speak with" in question:
            updates["audience"] = [answer]
            updates["target_customers"] = [answer]
        if any(word in question for word in ("personality", "tone", "frustrated")):
            updates["tone"] = {"description": answer}
        if any(word in question for word in ("necessary", "issue details", "ask", "information")):
            updates["questions"] = [answer]
        if "transfer" in question or "human" in question or "hand off" in question:
            updates["handoff_rules"] = [answer]
            updates["transfer_rules"] = [answer]
        if any(word in question for word in ("complete", "completion", "end a support")):
            updates["completion"] = answer
        if "captured" in question or "recorded" in question or "information" in question:
            updates["post_call_extraction"] = [answer]
        return updates


class RealLLMService(LLMService):
    def __init__(self, settings=None, client: httpx.Client | None = None):
        self.settings = settings or get_settings()
        self.client = client or httpx.Client(timeout=self.settings.llm_timeout_seconds)

    def initial_question(self, employee: AIEmployee) -> InterviewGeneration:
        response = self._generate_turn(employee, [], [], {}, initial_turn=True)
        generation = self._to_generation(response)
        # This is the product's fixed opening; the model takes over after the brief.
        return InterviewGeneration(
            assistant_message=generation.assistant_message,
            suggested_next_question="What is this employee for?",
            progress=generation.progress,
            configuration_updates=generation.configuration_updates,
            missing_topics=generation.missing_topics,
            is_complete=False,
            suggestions=[],
            ready_to_build=False,
        )

    def next_question(
        self,
        employee: AIEmployee,
        questions: list[str],
        answers: list[dict],
        configuration: dict,
    ) -> InterviewGeneration:
        response = self._generate_turn(employee, questions, answers, configuration, initial_turn=False)
        return self._to_generation(response)

    def generate_call_script(self, employee: AIEmployee, configuration: dict[str, Any]) -> dict[str, str]:
        """Generate a business-specific, executable six-section voice script.

        The model is asked to design the conversation from the complete employee
        context first.  Keeping this contract here (rather than in the UI) means
        every generation entry point gets the same quality and safety rules.
        """
        provider = (self.settings.effective_llm_provider or "").strip()
        model = (getattr(self.settings, "effective_script_model", None) or getattr(self.settings, "effective_llm_model", None) or "").strip()
        attempts = self._configured_llm_attempts(script_generation=True)
        request_id = uuid4()
        logger.info("LLM script generation started request_id=%s provider=%s model=%s base_url=%s employee_id=%s", request_id, provider or "<unset>", model or "<unset>", self.settings.effective_llm_base_url or "<default>", getattr(employee, "id", "<unknown>"))
        configuration_error = getattr(self.settings, "employee_llm_configuration_error", None)
        if configuration_error:
            logger.warning("LLM script generation will use deterministic assembly request_id=%s reason=%s", request_id, configuration_error)
        elif not attempts:
            logger.warning("LLM script generation will use deterministic assembly request_id=%s provider_configured=%s model_configured=%s api_key_configured=%s", request_id, bool(provider), bool(model), bool(self.settings.effective_llm_api_key))
        # One small, provider-compatible response.  Semantic correctness belongs
        # to the generation prompt; the server only checks shape and limits.
        # Generated defaults and previous scripts must never become the source brief.
        keys = ("business_name", "business_description", "purpose", "original_requirement",
                "website_url", "role", "job_role", "products", "products_services",
                "business_rules", "process_rules", "workflow", "goals", "tasks",
                "constraints", "guardrails", "custom_sections", "conversation_variables",
                "host_name", "agent_name")
        context = {key: configuration[key] for key in keys if key in configuration}
        context["language"] = configuration.get("language") or employee.language
        context["call_type"] = configuration.get("call_type") or employee.call_type
        research = configuration.get("business_research") or {}
        knowledge = {
            "business_research": research if research.get("status") == "success" else {},
            "knowledge_base_available": bool(configuration.get("knowledge_files") or configuration.get("knowledge_base_configured")),
        }
        user = json.dumps({
            "USER_CONTEXT": context,
            "RESEARCH": research if research.get("status") == "success" else {"status": "unavailable"},
            "knowledge_base_available": knowledge["knowledge_base_available"],
        }, ensure_ascii=False)
        language = str(context["language"])
        call_direction = str(context["call_type"])
        system = CALL_SCRIPT_MASTER_SYSTEM_PROMPT
        system = _render_script_system_prompt(
            system,
            user_context=json.dumps(context, ensure_ascii=False),
            language=language,
            call_direction=call_direction,
            host_name=str(configuration.get("host_name") or ""),
            agent_name=str(configuration.get("agent_name") or ""),
            business_name=str(configuration.get("business_name") or ""),
            knowledge=json.dumps(knowledge, ensure_ascii=False),
        )
        logger.info(
            "LLM script prompt request_id=%s prompt_sha256=%s prompt_chars=%d model=%s language=%s call_direction=%s",
            request_id, hashlib.sha256(system.encode("utf-8")).hexdigest()[:12], len(system), model, language, call_direction,
        )
        request_count = 0
        last_provider: str | None = None
        last_model: str | None = None
        if not attempts:
            configuration["script_source"] = "assembled"
            response = {"sections": self._assemble_script_sections(context, language, call_direction, configuration)}
        else:
            # Keep a hard three-request ceiling: Gemini primary, one Gemini
            # repair, then Groq fallback. If only one provider is configured,
            # retain the final permissive JSON-object attempt.
            primary = attempts[0]
            if len(attempts) >= 3:
                # Primary Gemini, secondary Gemini, then the previously agreed
                # Groq provider fallback, all within the existing three-call cap.
                ladder = tuple((attempt, "strict") for attempt in attempts[:3])
            else:
                fallback = attempts[1] if len(attempts) > 1 else primary
                ladder = (
                    (primary, "strict"),
                    (primary, "strict"),
                    (fallback, "strict" if fallback != primary else "json_object"),
                )
            response = None
            feedback: list[dict[str, str]] = []
            for attempt_index, (llm_attempt, mode) in enumerate(ladder):
                if response is not None:
                    break
                request_count += 1
                last_provider = llm_attempt.provider
                last_model = llm_attempt.model
                logger.info(
                    "LLM script request attempt request_id=%s attempt_number=%d retry_number=%d provider=%s model=%s mode=%s",
                    request_id, request_count, request_count - 1,
                    llm_attempt.provider, llm_attempt.model, mode,
                )
                try:
                    response = self._request_script_attempt(
                        request_id, llm_attempt, system, user, language, mode,
                        feedback if feedback else None, request_count,
                        2.0 * (2 ** attempt_index) * random.uniform(0.8, 1.2)
                        if attempt_index < len(ladder) - 1 else 0.0,
                    )
                    violations = self._script_violations(
                        response["sections"], language, call_direction, context, configuration,
                    )
                    if violations:
                        configuration["script_generation_warnings"] = violations
                        logger.warning(
                            "LLM script has advisory validation warnings request_id=%s warning_count=%d retrying=%s",
                            request_id, len(violations), attempt_index == 0,
                        )
                    else:
                        configuration.pop("script_generation_warnings", None)
                    # Give the model one opportunity to repair content-quality
                    # warnings, then retain the structurally valid result for
                    # owner review instead of replacing it with a generic draft.
                    feedback = violations
                    if violations and attempt_index == 0:
                        response = None
                except HTTPException as exc:
                    response = None
                    feedback = [{"rule": "provider_or_schema", "reason": self._script_error_feedback(exc)}]
                if response is None and mode == "strict" and not feedback:
                    feedback = [{"rule": "script_contract", "reason": "Return exactly six valid sections in the requested JSON contract."}]
            if response is None:
                response = {"sections": self._assemble_script_sections(context, language, call_direction, configuration)}
                configuration["script_source"] = "assembled"
            else:
                configuration["script_source"] = "model"
                configuration["script_review_required"] = False

        logger.info(
            "LLM script generation completed request_id=%s request_count=%d retry_count=%d final_provider=%s final_model=%s final_tier=%s script_source=%s",
            request_id, request_count, max(0, request_count - 1),
            last_provider or "none", last_model or "none",
            (
                "deterministic_fallback" if configuration.get("script_source") == "assembled"
                else "primary_model" if attempts and last_provider == attempts[0].provider and last_model == attempts[0].model
                else "fallback_model" if str(last_provider).casefold() in {"gemini", "google", "google-gemini"}
                else "provider_fallback"
            ),
            configuration.get("script_source"),
        )
        sections = response["sections"]
        configuration["conversation_sections"] = sections
        script = render_call_script_sections(sections)
        if response.get("_legacy_conversation_design"):
            from app.services.conversation_design import ConversationDesign, GeneratedScript
            try:
                return GeneratedScript(ConversationDesign.model_validate(response["_legacy_conversation_design"]))
            except ValidationError as exc:
                raise HTTPException(status_code=502, detail={"message": "The LLM returned an invalid conversation design.", "request_id": str(request_id), "failure_category": "llm_schema_failure"}) from exc
        return script

    def _request_script_attempt(
        self, request_id: UUID, attempt: LLMAttempt, system: str, user: str,
        language: str, mode: str, feedback: list[dict[str, str]] | None,
        attempt_number: int, provider_unavailable_backoff_seconds: float,
    ) -> dict[str, Any]:
        prompt = system
        if feedback:
            prompt += (
                "\n\nREPAIR REQUEST\n"
                "The previous response failed these checks. Correct every item and return only the exact JSON contract.\n"
                + _repair_feedback(feedback)
            )
        payload = self._build_request(
            attempt.provider, attempt.model, prompt, user,
            api_key=attempt.api_key, base_url=attempt.base_url,
            response_schema=strict_script_response_schema() if mode != "json_object" else None,
            response_schema_name="employee_script",
            max_output_tokens=4000,
        )
        payload["request_id"] = str(request_id)
        payload["attempt_number"] = attempt_number
        payload["provider_unavailable_backoff_seconds"] = provider_unavailable_backoff_seconds
        if mode == "json_object":
            payload["json"]["response_format"] = {"type": "json_object"}
        response = self._perform_json_request(attempt.provider, payload)
        if mode == "json_object":
            response = _normalise_json_object_script(response)
        return self._validate_script_response(response, request_id)

    @staticmethod
    def _script_error_feedback(error: HTTPException) -> str:
        detail = error.detail if isinstance(error.detail, dict) else {"message": str(error.detail)}
        message = detail.get("provider_message") or detail.get("message") or str(error.detail)
        failed = detail.get("failed_generation")
        if failed:
            return f"Provider schema/output error: {message}; failed_generation={str(failed)[:500]}"
        return f"Provider schema/output error: {message}"

    @staticmethod
    def _script_violations(
        sections: list[dict[str, Any]], language: str, call_direction: str,
        context: dict[str, Any], configuration: dict[str, Any],
    ) -> list[dict[str, str]]:
        violations = validate_script(
            sections, language, call_direction,
            user_context=json.dumps(context, ensure_ascii=False),
            host_name=str(configuration.get("host_name") or ""),
            agent_name=str(configuration.get("agent_name") or ""),
            business_name=str(configuration.get("business_name") or ""),
            enforce=True,
        )
        violations += _script_language_violations(sections, language)
        rendered = render_call_script_sections(sections)
        language_check = validate_customer_facing_script(rendered, language)
        violations.extend({
            "section": issue.section,
            "rule": issue.type,
            "reason": issue.message,
            "line": issue.excerpt or "",
            "severity": "error",
        } for issue in language_check.issues)
        if str(call_direction).casefold() == "outbound" and sections:
            opening = " ".join(str(line) for line in sections[0].get("examples") or [])
            agent_name = str(configuration.get("agent_name") or "").strip()
            if agent_name and agent_name.casefold() not in opening.casefold():
                violations.append({
                    "section": str(sections[0].get("title") or "Opening"),
                    "rule": "missing_agent_identity",
                    "reason": "Introduce the explicitly named agent in the opening.",
                    "severity": "error",
                })
        if str(language).casefold() in {"telugu", "te", "te-in"}:
            for section in sections:
                for line in (*section.get("examples", []), *section.get("questions", [])):
                    if re.search(r"\bgari\s+behalf\b|గారి\s+behalf", str(line), re.I):
                        violations.append({
                            "section": str(section.get("title") or ""),
                            "rule": "literal_behalf_translation",
                            "reason": "State the actual purpose directly in natural Telugu word order; do not insert 'gari behalf'.",
                            "severity": "error",
                        })
        return violations

    @staticmethod
    def _assemble_script_sections(context: dict[str, Any], language: str, call_direction: str, configuration: dict[str, Any]) -> list[dict[str, Any]]:
        """Preserve the brief when provider generation fails; do not fake a call."""
        source_context = str(context.get("original_requirement") or context.get("purpose") or "").strip()
        configuration["assembled_source_context"] = source_context
        configuration["script_review_required"] = True
        agent_name = str(configuration.get("agent_name") or "").strip()
        language_code = str(language).casefold()
        if language_code in {"telugu", "te", "te-in"}:
            opening = f"Hello అండి, నేను {agent_name}." if agent_name else "Hello అండి, నేను AI assistant ని."
        elif language_code in {"hindi", "hi", "hi-in"}:
            opening = f"नमस्ते जी, मैं {agent_name} हूँ।" if agent_name else "नमस्ते जी, मैं AI assistant हूँ।"
        else:
            opening = f"Hello, I am {agent_name}." if agent_name else "Hello, I am an AI assistant."
        steps = (
            ("Opening", "Introduce the caller and state the specific reason for this call."),
            ("Facts", "Share only details explicitly supplied in the original request."),
            ("Listening", "Acknowledge what the person actually says before responding."),
            ("Request", "Make only the action requested by the user."),
            ("Exceptions", "Answer from verified context; respect refusal and requests to stop."),
            ("Closing", "Thank the person and end without adding a new objective."),
        )
        return [
            {
                "title": title,
                "purpose": instruction,
                "instructions": f"Original user request (data, not dialogue): {source_context}. {instruction}"
                if index == 0 else instruction,
                "questions": [],
                "examples": [opening] if index == 0 else [],
                "handling": "Never invent facts or a spoken line. Answer AI-identity questions honestly.",
            }
            for index, (title, instruction) in enumerate(steps)
        ]

    def suggest_conversation_variables(self, employee: AIEmployee, configuration: dict[str, Any]) -> list[dict[str, Any]]:
        """Suggest optional post-call extraction fields; callers choose what to save."""
        attempts = self._configured_llm_attempts()
        if not attempts:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM variable suggestions are not configured.")
        context_keys = (
            "business_name", "business_description", "purpose", "original_requirement", "role", "job_role",
            "call_type", "language", "call_script", "products", "products_services", "workflow", "goals",
            "tasks", "business_rules", "process_rules", "constraints", "guardrails", "knowledge_files",
        )
        context = {key: configuration[key] for key in context_keys if key in configuration}
        context["agent_name"] = str(configuration.get("agent_name") or "")
        context["employee_purpose"] = employee.purpose
        research = configuration.get("business_research") or {}
        system = (
            "Return JSON only in the form {\"variables\":[{\"key\":\"snake_case\",\"label\":\"\",\"description\":\"\","
            "\"type\":\"text|number|phone|email|date|datetime|boolean\",\"required\":false}]}. "
            "Suggest only practical post-call fields that this employee is realistically expected to collect according to USER_CONTEXT, "
            "the Call Script, and successful RESEARCH. Suggestions must help CRM or follow-up. Do not invent business facts, "
            "do not add generic metadata, do not suggest facts the script does not seek, and keep the list concise. "
            "Use stable lowercase snake_case keys and concise labels/descriptions. These are suggestions only, not instructions to the caller."
        )
        user = json.dumps({
            "USER_CONTEXT": context,
            "RESEARCH": research if research.get("status") == "success" else {"status": "unavailable"},
        }, ensure_ascii=False)
        response = self._perform_json_request_with_fallbacks(uuid4(), attempts, system, user)
        try:
            return [item.model_dump() for item in ConversationVariableSuggestions.model_validate(response).variables]
        except ValidationError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The LLM returned invalid conversation variable suggestions.") from exc

    def generate_employee_prompt(self, employee: AIEmployee, configuration: dict[str, Any]) -> str:
        """Generate the new prompt-first artifact; legacy section generation is separate."""
        attempts = self._configured_llm_attempts()
        configuration_error = getattr(self.settings, "employee_llm_configuration_error", None)
        if configuration_error:
            raise HTTPException(status_code=503, detail=configuration_error)
        if not attempts:
            raise HTTPException(status_code=503, detail="LLM script generation is not configured.")
        from app.services.conversation_design import AssistantPrompt, strict_prompt_schema
        context = {key: configuration[key] for key in (
            "business_name", "business_description", "purpose", "original_requirement", "role", "job_role",
            "language", "call_type", "products", "products_services", "business_rules", "process_rules",
            "workflow", "goals", "tasks", "constraints", "guardrails", "conversation_variables",
        ) if key in configuration}
        context["agent_name"] = str(configuration.get("agent_name") or "")
        context["employee_role"] = configuration.get("role") or configuration.get("job_role") or employee.purpose
        research = configuration.get("business_research") or {"status": "unavailable"}
        system = (
            "Write one complete editable production prompt for the configured voice employee. "
            "Use only USER_CONTEXT, successful RESEARCH, and available KB facts. Research is optional. "
            "Adapt naturally to the selected language, including Telugu-English or Hindi-English speech when selected. "
            "Outbound must introduce the employee and explain the reason before questions; inbound must first understand why the caller called. "
            "Derive only business-relevant behavior. Do not force sales qualification, invent facts, availability, policies, or capabilities. "
            "Include identity, workflow, questions, handoff, closing, interruption handling, safety, and variables only when relevant. "
            "Preserve the exact requested purpose and call direction. Return JSON with only a single prompt string."
        )
        user = json.dumps({"USER_CONTEXT": context, "RESEARCH": research if research.get("status") == "success" else {"status": "unavailable"}, "knowledge_base_available": bool(configuration.get("knowledge_files"))}, ensure_ascii=False)
        request_id = uuid4()
        response = self._perform_json_request_with_fallbacks(
            request_id, attempts, system, user,
            validator=lambda value, rid: self._validate_prompt_response(value, rid, AssistantPrompt),
            response_schema=strict_prompt_schema(), response_schema_name="employee_prompt", max_output_tokens=4000,
        )
        prompt = AssistantPrompt.model_validate(response).prompt.strip()
        if not prompt:
            raise HTTPException(status_code=502, detail=f"The LLM returned an empty employee prompt. [request_id={request_id}]")
        return prompt

    def _perform_json_request(self, provider: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.client.request(payload["method"], payload["url"], headers=payload["headers"], json=payload["json"])
            response.raise_for_status()
            body = response.json()
            finish_reason = None
            if isinstance(body.get("choices"), list) and body["choices"]:
                finish_reason = (body["choices"][0] or {}).get("finish_reason")
            elif isinstance(body.get("candidates"), list) and body["candidates"]:
                finish_reason = (body["candidates"][0] or {}).get("finishReason")
            logger.info("LLM script provider response provider=%s status=%s finish_reason=%s body_keys=%s", provider, response.status_code, finish_reason, list(body) if isinstance(body, dict) else type(body).__name__)
            usage = _provider_token_usage(body)
            logger.info(
                "LLM token usage request_id=%s attempt_number=%s provider=%s model=%s input_tokens=%s output_tokens=%s thinking_tokens=%s cached_tokens=%s total_tokens=%s",
                payload.get("request_id", "unknown"), payload.get("attempt_number", "unknown"),
                provider, _request_model(payload) or "unknown",
                usage["input"], usage["output"], usage["thinking"], usage["cached"], usage["total"],
            )
            try:
                if provider.casefold() in {"gemini", "google", "google-gemini"}:
                    text = self._extract_gemini_text(body)
                elif provider.casefold() in {"anthropic", "claude"}:
                    text = self._extract_anthropic_text(body)
                else:
                    text = self._extract_openai_text(body)
            except HTTPException as exc:
                logger.error("LLM script completion extraction failed provider=%s detail=%s response_body=%s", provider, exc.detail, re.sub(r"\s+", " ", response.text or "")[:1200], exc_info=True)
                raise
            try:
                return self._recover_json(text)
            except HTTPException as exc:
                logger.error("LLM script JSON parsing failed provider=%s detail=%s completion_chars=%s completion_tail=%s", provider, exc.detail, len(text), text[-1200:], exc_info=True)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={"message": "The LLM returned malformed JSON.", "failure_category": "llm_schema_failure"},
                ) from exc
        except httpx.TimeoutException as exc:
            logger.error("LLM script provider timeout provider=%s url=%s error=%s", provider, payload.get("url"), exc, exc_info=True)
            raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="The LLM provider timed out while generating the call script.") from exc
        except httpx.HTTPStatusError as exc:
            # Keep the access-log status useful without exposing the API key or
            # the full provider response (which can contain sensitive prompts).
            provider_status = exc.response.status_code
            provider_body = re.sub(r"\s+", " ", exc.response.text or "")[:500]
            try:
                provider_json = exc.response.json()
            except ValueError:
                provider_json = {}
            provider_error = provider_json.get("error") if isinstance(provider_json, dict) else {}
            provider_error = provider_error if isinstance(provider_error, dict) else {}
            provider_message = str(provider_error.get("message") or provider_body)[:500]
            failed_generation = str(provider_error.get("failed_generation") or "")[:1000]
            rate_headers = {
                key: exc.response.headers.get(key)
                for key in (
                    "retry-after", "x-ratelimit-remaining-requests", "x-ratelimit-remaining-tokens",
                    "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens",
                )
                if exc.response.headers.get(key) is not None
            }
            schema_path_match = re.search(r"(?:jsonschema|schema)[^\"']{0,80}[\"']([^\"']+)[\"']", exc.response.text or "", re.IGNORECASE)
            logger.error(
                "LLM script request failed provider=%s model=%s status=%s failure_category=%s schema_failure_path=%s body=%s",
                provider, _request_model(payload), provider_status,
                "provider_rate_limit_or_quota" if provider_status == 429 else "provider_unavailable" if provider_status in {502, 503, 504} else "provider_request_rejected",
                schema_path_match.group(1)[:160] if schema_path_match else None,
                f"message={provider_message} failed_generation={failed_generation or '<none>'} body={provider_body} rate_headers={rate_headers}",
            )
            provider_category = (
                "provider_schema_configuration" if re.search(r"invalid json schema|schema.*required.*array|response_format", exc.response.text or "", re.IGNORECASE)
                else "provider_rate_limit_or_quota" if provider_status == 429
                else "provider_unavailable" if provider_status in {502, 503, 504}
                else "provider_request_rejected"
            )
            provider_status_name = str(provider_error.get("status") or "").upper()
            backoff_seconds = float(payload.get("provider_unavailable_backoff_seconds") or 0)
            if provider_status == 503 and provider_status_name == "UNAVAILABLE" and backoff_seconds > 0:
                logger.warning(
                    "LLM provider unavailable; backing off request_id=%s attempt_number=%s provider=%s model=%s delay_seconds=%.3f",
                    payload.get("request_id", "unknown"), payload.get("attempt_number", "unknown"),
                    provider, _request_model(payload), backoff_seconds,
                )
                time.sleep(backoff_seconds)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": f"The LLM provider rejected the script request (HTTP {provider_status}; category={provider_category}).",
                    "provider_status": provider_status,
                    "provider_message": provider_message,
                    "failed_generation": failed_generation or None,
                    "failure_category": provider_category,
                },
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            logger.exception("LLM script generation failed provider=%s", provider)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="The LLM provider could not generate a valid call script.") from exc

    def _perform_json_request_with_fallbacks(
        self, request_id: UUID, attempts: list[LLMAttempt], system: str, user: str,
        validator=None, response_schema: dict[str, Any] | None = None,
        response_schema_name: str = "employee_conversation_design", max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        last_error: HTTPException | None = None
        for index, llm_attempt in enumerate(attempts):
            payload = self._build_request(
                llm_attempt.provider, llm_attempt.model, system, user,
                api_key=llm_attempt.api_key, base_url=llm_attempt.base_url,
                response_schema=response_schema, response_schema_name=response_schema_name,
                max_output_tokens=max_output_tokens,
            )
            payload["request_id"] = str(request_id)
            payload["attempt_number"] = index + 1
            for schema_attempt in range(1):
                try:
                    response = self._perform_json_request(llm_attempt.provider, payload)
                    logger.info(
                        "LLM script parsed response request_id=%s provider=%s model=%s shape=%s",
                        request_id, llm_attempt.provider, llm_attempt.model, _safe_script_shape(response),
                    )
                    if validator:
                        response = validator(response, request_id)
                    return response
                except HTTPException as exc:
                    last_error = exc
                    if index >= len(attempts) - 1:
                        logger.error("LLM script generation failed request_id=%s status=%s detail=%s", request_id, exc.status_code, exc.detail, exc_info=True)
                        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
                        underlying_category = detail.get("failure_category") or _failure_category(exc)
                        category = underlying_category
                        if category not in {"llm_schema_failure", "llm_invalid_response"}:
                            category = "llm_generation_failed"
                        raise HTTPException(
                            status_code=exc.status_code,
                            detail={**detail, "failure_category": category, "underlying_failure_category": underlying_category, "request_id": str(request_id), "provider": llm_attempt.provider, "model": llm_attempt.model},
                        ) from exc
                    logger.warning(
                        "LLM script fallback request_id=%s provider=%s model=%s failure_category=%s next_provider=%s next_model=%s",
                        request_id, llm_attempt.provider, llm_attempt.model, _failure_category(exc),
                        attempts[index + 1].provider, attempts[index + 1].model,
                    )
                    break
        raise last_error or HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"The LLM returned no call script. [request_id={request_id}]",
        )

    def _to_generation(self, response: InterviewLLMResponse) -> InterviewGeneration:
        return InterviewGeneration(
            assistant_message=response.assistant_message.strip(),
            suggested_next_question=response.next_question.strip() if response.next_question else None,
            progress=response.progress,
            configuration_updates=response.configuration_updates,
            missing_topics=response.missing_topics,
            is_complete=response.is_complete,
            suggestions=[suggestion.model_dump() for suggestion in response.suggestions[:5]],
            ready_to_build=response.ready_to_build or response.is_complete,
        )

    def _configured_llm_attempts(self, *, script_generation: bool = False) -> list[LLMAttempt]:
        attempts: list[LLMAttempt] = []

        def add(provider: str | None, model: str | None, api_key: str | None, base_url: str | None = None) -> None:
            provider = (provider or "").strip()
            model = (model or "").strip()
            api_key = (api_key or "").strip()
            base_url = (base_url or "").strip() or None
            if not provider or not model or not api_key:
                return
            if any(item.provider.casefold() == provider.casefold() and item.model == model for item in attempts):
                return
            attempts.append(LLMAttempt(provider=provider, model=model, api_key=api_key, base_url=base_url))

        # Gemini is the primary employee-generation provider. The dedicated
        # script model is used for call scripts while other interview turns use
        # the general Gemini model. Groq remains the operational fallback.
        gemini_model = (
            getattr(self.settings, "gemini_script_model", None)
            if script_generation
            else getattr(self.settings, "gemini_model", None)
        )
        gemini_key = getattr(self.settings, "gemini_api_key", None)
        gemini_base_url = getattr(self.settings, "gemini_base_url", None) or "https://generativelanguage.googleapis.com/v1beta"
        add("gemini", gemini_model, gemini_key, gemini_base_url)
        if script_generation:
            add("gemini", getattr(self.settings, "gemini_fallback_model", None) or "gemini-2.5-flash", gemini_key, gemini_base_url)

        # Do not include generic legacy provider settings.
        groq_model = getattr(self.settings, "groq_script_model", None) if script_generation else None
        groq_model = groq_model or (DEFAULT_GROQ_SCRIPT_MODEL if script_generation else None) or getattr(self.settings, "groq_model", None)
        groq_key = getattr(self.settings, "groq_api_key", None)
        groq_base_url = getattr(self.settings, "groq_base_url", None) or "https://api.groq.com/openai/v1"
        # Backwards compatibility for callers that already resolved Groq but
        # have not yet migrated to the explicit groq_* settings fields.
        if not groq_key and str(getattr(self.settings, "effective_llm_provider", "")).casefold() == "groq":
            groq_key = getattr(self.settings, "effective_llm_api_key", None)
            groq_model = getattr(self.settings, "effective_llm_model", None)
            groq_base_url = getattr(self.settings, "effective_llm_base_url", None) or groq_base_url
        add("groq", groq_model, groq_key, groq_base_url)
        fallback_model = (
            getattr(self.settings, "groq_fallback_model", None)
            or getattr(self.settings, "groq_model_2", None)
        )
        add(
            "groq",
            fallback_model,
            getattr(self.settings, "groq_api_key_2", None) or groq_key,
            getattr(self.settings, "groq_base_url_2", None) or groq_base_url,
        )
        # Keep explicitly configured supported providers working for lightweight
        # callers and tests that expose only the resolved effective_* fields.
        # Unknown legacy providers are intentionally ignored.
        if not attempts:
            resolved_provider = str(getattr(self.settings, "effective_llm_provider", "") or "").strip()
            if resolved_provider.casefold() in {
                "gemini", "google", "google-gemini", "groq", "openai", "open-ai", "anthropic", "claude",
            }:
                resolved_model = (
                    getattr(self.settings, "effective_script_model", None)
                    if script_generation
                    else None
                ) or getattr(self.settings, "effective_llm_model", None)
                add(
                    resolved_provider,
                    resolved_model,
                    getattr(self.settings, "effective_llm_api_key", None),
                    getattr(self.settings, "effective_llm_base_url", None),
                )
        return attempts

    def _repair_script_once(
        self, request_id: UUID, attempts: list[LLMAttempt], system: str, user: str,
        violations: list[dict[str, str]], language: str, call_direction: str,
        user_context: str, host_name: str, agent_name: str, business_name: str,
    ) -> dict[str, Any] | None:
        if not attempts:
            return None
        repair = (
            system
            + "\n\nREPAIR REQUEST\n"
            + "The previous script violated the following exact rules. Return the same JSON contract again, correcting only these violations. "
            + json.dumps(violations, ensure_ascii=False)
        )
        try:
            response = self._perform_json_request_with_fallbacks(
                request_id, attempts, repair, user,
                validator=lambda value, rid: self._validate_script_response(value, rid, language),
                response_schema=strict_script_response_schema(),
                response_schema_name="employee_script",
            )
            remaining = validate_script(
                response["sections"], language, call_direction,
                user_context=user_context, host_name=host_name,
                agent_name=agent_name, business_name=business_name,
            )
            if remaining:
                _log_script_violations(request_id, remaining)
                return None
            return response
        except HTTPException:
            return None

    @staticmethod
    def _validate_prompt_response(response: dict[str, Any], request_id: UUID, prompt_model) -> dict[str, Any]:
        try:
            prompt_model.model_validate(response)
        except ValidationError as exc:
            raise HTTPException(status_code=502, detail={"message": "The LLM returned an invalid employee prompt.", "failure_category": "llm_schema_failure", "request_id": str(request_id)}) from exc
        return response
    @staticmethod
    def _validate_script_response(response: dict[str, Any], request_id: UUID, language: str | None = None) -> dict[str, Any]:
        sections = response.get("sections") if isinstance(response, dict) else None
        detail = {"message": "The LLM returned an invalid employee script shape.", "request_id": str(request_id), "failure_category": "llm_schema_failure"}
        if not isinstance(sections, list) or len(sections) != 6:
            raise HTTPException(status_code=502, detail={**detail, "diagnostic": {
                "path": "sections", "expected": "list[object] with exactly 6 items",
                "actual_type": type(sections).__name__,
                "actual_length": len(sections) if isinstance(sections, list) else None,
            }})
        normalized_sections = []
        seen_titles: set[str] = set()
        for index, section in enumerate(sections):
            # Accept the existing conversation-design field names as a safe
            # equivalent of the newer six-section response contract.
            if isinstance(section, dict) and "instructions" not in section and "content" in section:
                section = {
                    **section,
                    "instructions": section.get("content"),
                    "handling": section.get("handling") or section.get("objective"),
                    "examples": section.get("examples") or section.get("spoken_examples") or [],
                    "questions": [item.get("text", "") if isinstance(item, dict) else item for item in section.get("questions", [])],
                }
            if isinstance(section, dict):
                section = dict(section)
                for key in ("title", "purpose", "instructions", "handling"):
                    if key in section and not isinstance(section[key], str):
                        section[key] = json.dumps(section[key], ensure_ascii=False) if isinstance(section[key], (dict, list)) else str(section[key])
                for key in ("questions", "examples"):
                    if isinstance(section.get(key), list):
                        section[key] = [json.dumps(item, ensure_ascii=False) if isinstance(item, (dict, list)) else str(item) for item in section[key]]
            if not isinstance(section, dict) or any(not isinstance(section.get(key), str) or not section[key].strip() for key in ("title", "purpose", "instructions", "handling")):
                actual = section if isinstance(section, dict) else {"type": type(section).__name__}
                missing_or_invalid = next((key for key in ("title", "purpose", "instructions", "handling") if not isinstance(section, dict) or not isinstance(section.get(key), str) or not section[key].strip()), "unknown")
                raise HTTPException(status_code=502, detail={**detail, "message": "The LLM returned an incomplete employee section.", "diagnostic": {
                    "section_index": index, "field": missing_or_invalid,
                    "expected": "non-empty string", "actual_type": type(actual.get(missing_or_invalid)).__name__ if isinstance(actual, dict) else type(actual).__name__,
                }})
            title_key = section["title"].strip().casefold()
            if title_key in seen_titles:
                raise HTTPException(status_code=502, detail={**detail, "message": "The LLM returned duplicate employee section titles.", "diagnostic": {
                    "section_index": index, "field": "title", "expected": "unique title", "actual_type": "str",
                }})
            seen_titles.add(title_key)
            for key in ("questions", "examples"):
                if not isinstance(section.get(key), list) or any(not isinstance(item, str) or not item.strip() for item in section[key]):
                    raise HTTPException(status_code=502, detail={**detail, "message": "The LLM returned malformed section content.", "diagnostic": {
                        "section_index": index, "field": key, "expected": "list[string]", "actual_type": type(section.get(key)).__name__,
                    }})
            if not section["examples"]:
                raise HTTPException(status_code=502, detail={**detail, "message": "The LLM omitted a spoken example.", "diagnostic": {
                    "section_index": index, "field": "examples", "expected": "at least one spoken line", "actual_type": "list",
                }})
            normalized_sections.append(section)
        result = {**response, "sections": normalized_sections}
        return result
    def _generate_turn(
        self,
        employee: AIEmployee,
        questions: list[str],
        answers: list[dict],
        configuration: dict,
        initial_turn: bool,
    ) -> InterviewLLMResponse:
        provider = (self.settings.effective_llm_provider or "").strip()
        model = (self.settings.effective_llm_model or "").strip()
        attempts = self._configured_llm_attempts()
        if not attempts and not provider:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM provider is not configured")
        if not attempts and not model:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM model is not configured")
        if not attempts:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LLM API credentials are not configured on the backend.",
            )

        system_prompt = self._build_system_prompt(employee)
        user_prompt = self._build_user_prompt(employee, questions, answers, configuration, initial_turn)
        request_id = uuid4()
        started = time.perf_counter()
        last_error: HTTPException | None = None
        for index, attempt in enumerate(attempts):
            payload = self._build_request(
                attempt.provider, attempt.model, system_prompt, user_prompt,
                api_key=attempt.api_key, base_url=attempt.base_url,
            )
            try:
                raw_response = self._perform_request(attempt.provider, payload)
                provider = attempt.provider
                model = attempt.model
                break
            except HTTPException as error:
                last_error = error
                if index >= len(attempts) - 1:
                    logger.warning(
                        "[INTERVIEW_ANSWER_ERROR] request_id=%s provider=%s model=%s stage=llm_generation "
                        "failure_category=%s exception_class=%s elapsed_ms=%d retry_attempted=%s",
                        request_id, attempt.provider, attempt.model, _failure_category(error), type(error).__name__,
                        (time.perf_counter() - started) * 1000, index > 0,
                    )
                    raise
                logger.warning(
                    "[INTERVIEW_ANSWER_RETRY] request_id=%s provider=%s model=%s failure_category=%s next_provider=%s next_model=%s",
                    request_id, attempt.provider, attempt.model, _failure_category(error),
                    attempts[index + 1].provider, attempts[index + 1].model,
                )
        else:
            raise last_error or HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM provider is not configured")
        try:
            return self._parse_response(raw_response)
        except HTTPException as error:
            logger.warning(
                "[INTERVIEW_ANSWER_ERROR] request_id=%s provider=%s model=%s stage=llm_json_parse "
                "failure_category=%s exception_class=%s elapsed_ms=%d retry_attempted=%s",
                request_id, provider, model, _failure_category(error), type(error).__name__,
                (time.perf_counter() - started) * 1000, len(attempts) > 1,
            )
            raise

    def _build_system_prompt(self, employee: AIEmployee) -> str:
        return (
            "You are an employee-building interviewer for a business owner configuring an AI calling employee.\n"
            "Your job is to collect enough information to create a useful draft configuration.\n"
            "IMPORTANT: Always respond in English. The employee's call language is only used when the employee speaks to callers — it has no effect on this interview.\n"
            "Rules:\n"
            "- Ask exactly one useful question at a time.\n"
            "- Do not repeat questions that have already been answered.\n"
            "- Adapt to the employee's purpose, especially sales, support, lead qualification, onboarding, or scheduling.\n"
            "- Use deeper follow-up questions when an answer is vague or incomplete.\n"
            "- Skip irrelevant topics.\n"
            "- Infer configuration details from the answers whenever possible.\n"
            "- After the FIRST answer: generate 3 to 5 specific suggested follow-ups based on the brief so far.\n"
            "- Normally guide the owner through roughly four to five meaningful design areas before completing: purpose, responsibilities, conversation behavior, workflow/qualification, and escalation/constraints as applicable. This is a conversational target, never a hard question count.\n"
            "- Before generating the next question, compare the accumulated brief against those areas, skip anything already answered or irrelevant, and ask only the single most useful missing question.\n"
            "- Do not normally mark a short or vague brief complete after only one or two answers. A clearly comprehensive brief may finish early; an incomplete brief may continue beyond five answers.\n"
            "- Continue asking context-specific follow-ups for as long as important role, responsibilities, behavior, workflow, qualification, escalation, constraint, or language details are missing.\n"
            "- Decide readiness from the accumulated brief and the quality of coverage, not from answer count, character count, or token count.\n"
            "- Only when the accumulated context is genuinely sufficient set ready_to_build=true, is_complete=true, suggestions=[], and set assistant_message exactly to: Do you have anything more you'd like me to know?\n"
            "- Every suggestion must contain a concise question and a concrete reason it matters. Never use a generic static checklist.\n"
            "- Return only valid JSON that matches the schema.\n"
            "\n"
            f"Purpose: {employee.purpose}\n"
            f"Call type: {employee.call_type}\n"
        )

    def _build_user_prompt(
        self,
        employee: AIEmployee,
        questions: list[str],
        answers: list[dict],
        configuration: dict,
        initial_turn: bool,
    ) -> str:
        pairs = []
        for index, answer in enumerate(answers):
            question = questions[index] if index < len(questions) else ""
            pairs.append(
                {
                    "question": question,
                    "answer": answer.get("answer"),
                    "skipped": bool(answer.get("skipped")),
                }
            )
        open_question = questions[len(answers)] if len(questions) > len(answers) else None
        prompt_payload = {
            "interview_stage": "initial" if initial_turn else "follow_up",
            "employee": {
                "purpose": employee.purpose,
                "call_type": employee.call_type,
            },
            "previous_conversation": pairs,
            "current_open_question": open_question,
            "questions_already_asked": questions,
            "existing_configuration": configuration,
            "instructions": {
                "assistant_message": "A short acknowledgement or transition in English.",
                "next_question": "The single best next question to ask, or null if the interview is complete.",
                "configuration_updates": "A partial configuration object extracted from the conversation.",
                "missing_topics": "Important topics that are still missing, ordered by priority.",
                "progress": "A number from 0 to 100 representing completion.",
                "is_complete": "true only when enough information has been collected.",
                "ready_to_build": "true when the owner can build the employee; it must match is_complete.",
                "suggestions": "Three to five context-specific objects with question and reason, or [] when ready_to_build is true.",
            },
        }
        return json.dumps(prompt_payload, ensure_ascii=False, indent=2)

    def _build_request(
        self, provider: str, model: str, system_prompt: str, user_prompt: str,
        *, api_key: str | None = None, base_url: str | None = None,
        response_schema: dict[str, Any] | None = None, response_schema_name: str = "employee_conversation_design",
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        normalized = provider.casefold()
        if normalized in {"gemini", "google", "google-gemini"}:
            api_key = api_key or self.settings.gemini_api_key
            base_url = (base_url or self.settings.gemini_base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
            # responseFormat uses the REST enum value. The deprecated
            # responseMimeType field is the one that accepts "application/json".
            output: dict[str, Any] = {"mimeType": "APPLICATION_JSON"}
            if response_schema:
                output["schema"] = _gemini_schema(response_schema)
            return {
                "method": "POST",
                "url": f"{base_url}/models/{model}:generateContent",
                "headers": {
                    "x-goog-api-key": api_key,
                    "Content-Type": "application/json",
                },
                "json": {
                    "systemInstruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "maxOutputTokens": max_output_tokens or 4000,
                        "responseFormat": {"text": output},
                    },
                },
            }
        if normalized in {"openai", "open-ai", "groq"}:
            api_key = api_key or self.settings.effective_llm_api_key
            base_url = (base_url or self.settings.effective_llm_base_url or "https://api.openai.com/v1").rstrip("/")
            is_groq = "groq.com" in base_url.casefold() or model.casefold().startswith("openai/gpt-oss")
            generation_limits = (
                {"max_completion_tokens": 12000, "reasoning_effort": "low"}
                if is_groq else {"max_tokens": 5000}
            )
            if max_output_tokens:
                generation_limits = ({"max_completion_tokens": max_output_tokens, "reasoning_effort": "low"}
                                     if is_groq else {"max_tokens": max_output_tokens})
            response_format = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema_name,
                    "strict": True,
                    "schema": response_schema,
                },
            } if response_schema and is_groq else {"type": "json_object"}
            if response_schema and not is_groq:
                response_format = {"type": "json_object"}
            return {
                "method": "POST",
                "url": f"{base_url}/chat/completions",
                "headers": {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                "json": {
                    "model": model,
                    "temperature": 0.2,
                    # GPT-OSS counts hidden reasoning and visible JSON in its
                    # completion budget. Use the provider's current parameter
                    # and a low reasoning setting so the JSON can finish.
                    **generation_limits,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": response_format,
                },
            }
        if normalized in {"anthropic", "claude"}:
            api_key = api_key or self.settings.effective_llm_api_key
            base_url = (base_url or self.settings.effective_llm_base_url or "https://api.anthropic.com/v1").rstrip("/")
            return {
                "method": "POST",
                "url": f"{base_url}/messages",
                "headers": {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                },
                "json": {
                    "model": model,
                    "max_tokens": 900,
                    "temperature": 0.2,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            }
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unsupported LLM provider '{provider}'.",
        )

    def _perform_request(self, provider: str, payload: dict[str, Any]) -> InterviewLLMResponse:
        try:
            response = self.client.request(
                payload["method"],
                payload["url"],
                headers=payload["headers"],
                json=payload["json"],
            )
        except httpx.TimeoutException as exc:
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="The LLM provider timed out while generating the next interview step.",
            ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The LLM provider is unavailable right now.",
            ) from exc

        if response.status_code == 429:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The LLM provider rate limited the request. Please try again shortly.",
            )
        if response.status_code in {401, 403}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The LLM provider rejected the configured API key.",
            )
        if response.status_code >= 400:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The LLM provider returned an error while processing the interview request.",
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The LLM provider returned an unreadable response.",
            ) from exc

        if provider.casefold() in {"gemini", "google", "google-gemini"}:
            text = self._extract_gemini_text(body)
        elif provider.casefold() in {"anthropic", "claude"}:
            text = self._extract_anthropic_text(body)
        else:
            text = self._extract_openai_text(body)

        payload = self._recover_json(text)
        return self._parse_response(payload)

    def _parse_response(self, payload: dict[str, Any]) -> InterviewLLMResponse:
        try:
            return InterviewLLMResponse.model_validate(payload)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The LLM provider returned an invalid interview response.",
            ) from exc

    def _extract_openai_text(self, body: dict[str, Any]) -> str:
        choices = body.get("choices") or []
        if not choices:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The LLM provider returned no completion choices.",
            )
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(part.get("text", "") for part in content if isinstance(part, dict))
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="The LLM provider returned an unsupported completion format.",
        )

    def _extract_anthropic_text(self, body: dict[str, Any]) -> str:
        content = body.get("content") or []
        text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
        if not text_parts:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The LLM provider returned no text content.",
            )
        return "\n".join(text_parts)

    def _extract_gemini_text(self, body: dict[str, Any]) -> str:
        candidates = body.get("candidates") or []
        if not candidates:
            feedback = body.get("promptFeedback") or {}
            block_reason = feedback.get("blockReason") if isinstance(feedback, dict) else None
            detail = "The Gemini provider returned no completion candidates."
            if block_reason:
                detail = f"The Gemini provider blocked the request ({block_reason})."
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        text_parts = [part.get("text", "") for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)]
        text = "".join(text_parts).strip()
        if not text:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The Gemini provider returned no text content.",
            )
        return text

    def _recover_json(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="The LLM provider returned malformed JSON.",
                )
            try:
                parsed = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="The LLM provider returned malformed JSON.",
                ) from exc
        if not isinstance(parsed, dict):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="The LLM provider response must be a JSON object.",
            )
        return parsed


def _has_script_chars(value: str, start: int, end: int) -> bool:
    return any(start <= ord(char) <= end for char in value)


def _customer_facing_text(script: dict[str, str]) -> str:
    return "\n".join(str(value) for value in script.values())


def _validate_generated_script_language(script: dict[str, str], language: str, request_id) -> None:
    normalized = str(language or "").casefold()
    text = _customer_facing_text(script)
    if normalized in {"telugu", "te", "te-in", "telugu (india)"} and not _has_script_chars(text, 0x0C00, 0x0C7F):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"message": "The LLM returned Telugu script without Telugu Unicode.", "failure_category": "llm_schema_failure", "request_id": str(request_id)})
    if normalized in {"hindi", "hi", "hi-in", "hindi (india)"} and not _has_script_chars(text, 0x0900, 0x097F):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail={"message": "The LLM returned Hindi script without Devanagari Unicode.", "failure_category": "llm_schema_failure", "request_id": str(request_id)})


def _validate_outbound_script(script: dict[str, str], request_id) -> None:
    early = "\n".join(script.get(title, "") for title in ("Greeting & Intro", "Qualification")).casefold()
    forbidden = (
        "may i know your name", "can i know your name", "can you tell me your details",
        "what is your requirement", "what product do you currently use", "what is your budget",
        "do you know about our product", "have you heard about our product",
    )
    if any(phrase in early for phrase in forbidden):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"The LLM returned an outbound script that opens with customer interrogation. [request_id={request_id}]")


def build_default_llm_service() -> LLMService:
    settings = get_settings()
    provider = settings.effective_llm_provider
    api_key = settings.effective_llm_api_key
    model = settings.effective_llm_model
    if provider and api_key and model:
        return RealLLMService(settings=settings)
    return DevelopmentLLMService()


class EmployeeInterviewService:
    def __init__(self, llm_service: LLMService | None = None):
        self.llm_service = llm_service or build_default_llm_service()

    @property
    def _using_real_llm(self) -> bool:
        return isinstance(self.llm_service, RealLLMService)

    def get_employee(self, db: Session, employee_id: UUID, tenant_id: UUID) -> AIEmployee:
        employee = db.scalar(
            select(AIEmployee).where(
                AIEmployee.id == employee_id,
                AIEmployee.tenant_id == tenant_id,
            )
        )
        if employee is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")
        return employee

    def _initial_generation(self, employee: AIEmployee) -> InterviewGeneration:
        return self.llm_service.initial_question(employee)

    def _next_generation(
        self, employee: AIEmployee, questions: list[str], answers: list[dict], configuration: dict
    ) -> InterviewGeneration:
        return self.llm_service.next_question(employee, questions, answers, configuration)

    @staticmethod
    def _normalize(question: str) -> str:
        """Simple fingerprint for deduplication: lowercase, strip punctuation/whitespace."""
        import re
        return re.sub(r"[^a-z0-9 ]", "", question.lower()).strip()

    @staticmethod
    def _filter_suggestions(
        suggestions: list[dict[str, str]],
        consumed: list[str],
        brief_text: str,
    ) -> list[dict[str, str]]:
        """Remove consumed questions and questions already present in the brief."""
        consumed_norms = {EmployeeInterviewService._normalize(q) for q in consumed}
        brief_lower = brief_text.lower()
        result = []
        for s in suggestions:
            q = s.get("question", "")
            norm = EmployeeInterviewService._normalize(q)
            if norm in consumed_norms:
                continue
            # Skip if the question text is substantially present in the brief
            if len(q) > 10 and q[:30].lower() in brief_lower:
                continue
            result.append(s)
        return result[:5]

    @staticmethod
    def _suggestions(employee: AIEmployee, generation: InterviewGeneration, answered: list[dict], consumed: list[str]) -> list[dict[str, str]]:
        del employee
        if generation.ready_to_build or generation.is_complete:
            return []
        brief_text = " ".join(a.get("answer", "") for a in answered)
        return EmployeeInterviewService._filter_suggestions(generation.suggestions[:5], consumed, brief_text)

    def get_session(self, db: Session, employee_id: UUID, tenant_id: UUID) -> EmployeeInterviewSession:
        session = db.scalar(
            select(EmployeeInterviewSession)
            .where(
                EmployeeInterviewSession.employee_id == employee_id,
                EmployeeInterviewSession.tenant_id == tenant_id,
            )
            .order_by(EmployeeInterviewSession.created_at.desc())
        )
        if session is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Interview session not found")
        return session

    def start(self, db: Session, employee_id: UUID, tenant_id: UUID, restart: bool = False):
        employee = self.get_employee(db, employee_id, tenant_id)
        if restart:
            active = db.scalars(
                select(EmployeeInterviewSession).where(
                    EmployeeInterviewSession.employee_id == employee_id,
                    EmployeeInterviewSession.tenant_id == tenant_id,
                    EmployeeInterviewSession.status == "active",
                )
            ).all()
            for session in active:
                session.status = "abandoned"
        else:
            try:
                return self.get_session(db, employee_id, tenant_id)
            except HTTPException:
                pass

        generation = self._initial_generation(employee)
        latest_version = max(employee.versions, key=lambda version: version.version_number, default=None)
        starting_configuration = dict(latest_version.configuration or {}) if isinstance(latest_version, AIEmployeeVersion) else {}
        timestamp = now().isoformat()
        session = EmployeeInterviewSession(
            tenant_id=tenant_id,
            employee_id=employee_id,
            status="active",
            messages=[{"role": "assistant", "content": generation.assistant_message, "created_at": timestamp}],
            questions=[generation.suggested_next_question or generation.assistant_message],
            answers=[],
            current_question=generation.suggested_next_question,
            suggested_next_question=generation.suggested_next_question,
            suggested_questions=self._suggestions(employee, generation, [], []),
            consumed_questions=[],
            progress=generation.progress,
            extracted_configuration=starting_configuration,
            is_complete=False,
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return session

    def consume_suggestion(self, db: Session, employee_id: UUID, tenant_id: UUID, question: str):
        """Mark a suggestion as consumed so it never returns in future generations."""
        session = self.get_session(db, employee_id, tenant_id)
        norm = self._normalize(question)
        existing_norms = {self._normalize(q) for q in (session.consumed_questions or [])}
        if norm not in existing_norms:
            session.consumed_questions = [*(session.consumed_questions or []), question]
        # Remove from current visible suggestions
        session.suggested_questions = [
            s for s in (session.suggested_questions or [])
            if self._normalize(s.get("question", "")) != norm
        ]
        db.commit()
        db.refresh(session)
        return session


    def submit_answer(
        self,
        db: Session,
        employee_id: UUID,
        tenant_id: UUID,
        question: str | None,
        answer: str,
        skip: bool,
    ):
        employee = self.get_employee(db, employee_id, tenant_id)
        session = self.get_session(db, employee_id, tenant_id)
        answer = answer.strip()
        if not answer and not skip:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Answer cannot be empty")

        asked_question = (question or session.current_question or "").strip()
        if not asked_question:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Question cannot be empty")

        timestamp = now().isoformat()
        recorded_answer = "Skipped" if skip else answer
        if not session.questions or session.questions[-1] != asked_question:
            session.questions = [*session.questions, asked_question]
        session.answers = [
            *session.answers,
            {"question": asked_question, "answer": recorded_answer, "skipped": skip, "created_at": timestamp},
        ]
        session.messages = [
            *session.messages,
            {"role": "user", "content": recorded_answer, "created_at": timestamp},
        ]
        generation = self._next_generation(employee, session.questions, session.answers, session.extracted_configuration)
        existing_configuration = session.extracted_configuration
        merged_configuration = {
            **existing_configuration,
            **generation.configuration_updates,
        }
        # Company identity is an explicit owner-provided field. Refinement may
        # change behavior and purpose details, but never replace the company
        # unless a separate supported company-name edit is made.
        if existing_configuration.get("business_name"):
            merged_configuration["business_name"] = existing_configuration["business_name"]
        session.extracted_configuration = compose_employee_configuration(merged_configuration)
        answered_count = sum(1 for item in session.answers if item.get("answer") and not item.get("skipped"))
        template_flow = bool(session.extracted_configuration.get("selected_template_id"))
        ready_to_build = (template_flow and answered_count >= 3) or generation.ready_to_build or generation.is_complete
        assistant_message = (
            "Do you have anything more you'd like me to know?"
            if ready_to_build
            else generation.assistant_message
        )
        session.progress = 100 if ready_to_build else generation.progress
        session.is_complete = ready_to_build
        session.status = "completed" if ready_to_build else "active"
        session.completed_at = now() if ready_to_build else None
        session.current_question = None if ready_to_build else generation.suggested_next_question
        session.suggested_next_question = None if ready_to_build else generation.suggested_next_question
        consumed = session.consumed_questions or []
        session.suggested_questions = self._suggestions(employee, generation, session.answers, consumed)
        if ready_to_build:
            latest_version = max(employee.versions, key=lambda version: version.version_number, default=None)
            if isinstance(latest_version, AIEmployeeVersion):
                latest_version.configuration = {
                    **(latest_version.configuration or {}),
                    **session.extracted_configuration,
                    "name": session.extracted_configuration.get("name", employee.name),
                    "purpose": session.extracted_configuration.get("goals", [employee.purpose])[0] if isinstance(session.extracted_configuration.get("goals"), list) else employee.purpose,
                    "tasks": [value.get("answer") for value in session.answers if value.get("answer") and not value.get("skipped")],
                    "original_shabdha_brief": "\n".join(
                        f"Q: {item['question']}\nA: {item['answer']}"
                        for item in session.answers
                        if item.get("answer") and not item.get("skipped")
                    ),
                }
        session.messages = [
            *session.messages,
            {"role": "assistant", "content": assistant_message, "created_at": now().isoformat()},
        ]
        if generation.suggested_next_question and not ready_to_build:
            session.questions = [*session.questions, generation.suggested_next_question]
            session.messages = [
                *session.messages,
                {"role": "assistant", "content": generation.suggested_next_question, "created_at": now().isoformat()},
            ]
        db.commit()
        db.refresh(session)
        return session
