from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import re
import logging
import time
from typing import Any
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

logger = logging.getLogger(__name__)


def _failure_category(error: HTTPException) -> str:
    detail = str(error.detail).lower()
    if "rate limit" in detail:
        return "groq_rate_limit"
    if "timed out" in detail:
        return "groq_timeout"
    if "api key" in detail:
        return "groq_authentication"
    if "unavailable" in detail:
        return "groq_network_error"
    if "json" in detail or "completion" in detail or "response" in detail:
        return "llm_response_error"
    return "llm_error"


def now() -> datetime:
    return datetime.now(timezone.utc)


class SuggestedQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=1000)


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
        ("goals", "What should this employee achieve for the business?"),
        ("products", "Which products or services should it focus on?"),
        ("target_customers", "Who are the typical customers it should speak with?"),
        ("tone", "What personality and tone should it use with customers?"),
        ("qualification_rules", "What should it ask or learn before considering a customer qualified?"),
        ("objection_handling", "How should it respond when a customer has a concern or objection?"),
        ("transfer_rules", "When should it transfer the conversation to a human?"),
        ("closing_behavior", "How should it close the conversation and handle follow-up?"),
        ("post_call_extraction", "What information should be captured after each conversation?"),
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
            elif topic == "qualification_rules" and any(word in context for word in ("demo", "lead", "sales")):
                question = "What details make a demo lead worth booking with the sales team?"
                reason = "This lets the employee qualify leads consistently."
            elif topic == "transfer_rules" and any(word in context for word in ("support", "issue", "billing")):
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
                ("qualification_rules", "What issue details should it collect before escalating?"),
                ("transfer_rules", "When should it hand off to a human agent?"),
                ("closing_behavior", "How should it end a support conversation?"),
                ("post_call_extraction", "What should be recorded after the call?"),
            )
        if any(word in purpose for word in ("lead", "sales", "qualify", "book", "demo")):
            return (
                ("goals", "What should this employee accomplish for the business?"),
                ("products", "What offer or service should it talk about?"),
                ("target_customers", "Who is the ideal customer or lead?"),
                ("qualification_rules", "What makes a lead qualified?"),
                ("objection_handling", "How should it respond to price or timing objections?"),
                ("closing_behavior", "What should it do when a lead is qualified?"),
                ("transfer_rules", "When should it transfer to a human closer?"),
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
        if "customer" in question or "audience" in question or "lead" in question:
            updates["target_customers"] = [answer]
        if any(word in question for word in ("personality", "tone", "frustrated")):
            updates["tone"] = {"description": answer}
        if any(word in question for word in ("qualif", "issue details", "ask or learn")):
            updates["qualification_rules"] = [answer]
        if any(word in question for word in ("objection", "concern", "price")):
            updates["objection_handling"] = [answer]
        if "transfer" in question or "human" in question or "hand off" in question:
            updates["transfer_rules"] = [answer]
        if any(word in question for word in ("close", "follow-up", "end a support")):
            updates["closing_behavior"] = [answer]
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
        if not provider:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM provider is not configured")
        if not model:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="LLM model is not configured")
        if not self.settings.effective_llm_api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LLM API credentials are not configured on the backend.",
            )

        system_prompt = self._build_system_prompt(employee)
        user_prompt = self._build_user_prompt(employee, questions, answers, configuration, initial_turn)
        request_id = uuid4()
        started = time.perf_counter()
        payload = self._build_request(provider, model, system_prompt, user_prompt)
        try:
            raw_response = self._perform_request(provider, payload)
        except HTTPException as first_error:
            fallback_key = getattr(self.settings, "groq_api_key_2", None)
            if not fallback_key or provider.casefold() not in {"openai", "open-ai", "groq"}:
                logger.warning(
                    "[INTERVIEW_ANSWER_ERROR] request_id=%s provider=%s model=%s stage=llm_generation "
                    "failure_category=%s exception_class=%s elapsed_ms=%d retry_attempted=false",
                    request_id, provider, model, _failure_category(first_error), type(first_error).__name__,
                    (time.perf_counter() - started) * 1000,
                )
                raise
            fallback_model = getattr(self.settings, "groq_model_2", None) or model
            fallback_base_url = getattr(self.settings, "groq_base_url_2", None) or self.settings.effective_llm_base_url
            logger.info(
                "[INTERVIEW_ANSWER_RETRY] request_id=%s provider=%s model=%s failure_category=%s",
                request_id, provider, model, _failure_category(first_error),
            )
            fallback_payload = self._build_request(
                provider, fallback_model, system_prompt, user_prompt,
                api_key=fallback_key, base_url=fallback_base_url,
            )
            try:
                raw_response = self._perform_request(provider, fallback_payload)
            except HTTPException as second_error:
                logger.warning(
                    "[INTERVIEW_ANSWER_ERROR] request_id=%s provider=%s model=%s stage=llm_generation "
                    "failure_category=%s exception_class=%s elapsed_ms=%d retry_attempted=true",
                    request_id, provider, fallback_model, _failure_category(second_error), type(second_error).__name__,
                    (time.perf_counter() - started) * 1000,
                )
                raise
        try:
            return self._parse_response(raw_response)
        except HTTPException as error:
            logger.warning(
                "[INTERVIEW_ANSWER_ERROR] request_id=%s provider=%s model=%s stage=llm_json_parse "
                "failure_category=%s exception_class=%s elapsed_ms=%d retry_attempted=%s",
                request_id, provider, model, _failure_category(error), type(error).__name__,
                (time.perf_counter() - started) * 1000, bool(getattr(self.settings, "groq_api_key_2", None)),
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
            f"Employee name: {employee.name}\n"
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
                "name": employee.name,
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
    ) -> dict[str, Any]:
        normalized = provider.casefold()
        if normalized in {"openai", "open-ai", "groq"}:
            api_key = api_key or self.settings.effective_llm_api_key
            base_url = (base_url or self.settings.effective_llm_base_url or "https://api.openai.com/v1").rstrip("/")
            return {
                "method": "POST",
                "url": f"{base_url}/chat/completions",
                "headers": {
                    "Authorization": f"Bearer {self.settings.effective_llm_api_key}",
                    "Content-Type": "application/json",
                },
                "json": {
                    "model": model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "response_format": {"type": "json_object"},
                },
            }
        if normalized in {"anthropic", "claude"}:
            base_url = (self.settings.effective_llm_base_url or "https://api.anthropic.com/v1").rstrip("/")
            return {
                "method": "POST",
                "url": f"{base_url}/messages",
                "headers": {
                    "x-api-key": self.settings.effective_llm_api_key,
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

        if provider.casefold() in {"anthropic", "claude"}:
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
            extracted_configuration={},
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
        session.extracted_configuration = {
            **session.extracted_configuration,
            **generation.configuration_updates,
        }
        ready_to_build = generation.ready_to_build or generation.is_complete
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
                    "system_prompt": "Follow the reviewed role, responsibilities, customer handling rules, tone, and escalation guidance synthesized from this builder session.",
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
