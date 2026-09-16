from __future__ import annotations
from typing import Any

def _text(value: Any) -> str:
    if isinstance(value, list): return "\n".join(f"- {item}" for item in value if item)
    if isinstance(value, dict): return "\n".join(f"{key}: {item}" for key, item in value.items())
    return str(value).strip() if value is not None else ""

def build_employee_prompt(configuration: dict[str, Any]) -> str:
    sections: list[tuple[str, str]] = []
    name = _text(configuration.get("name")) or "AI employee"
    purpose = _text(configuration.get("purpose"))
    if purpose: sections.append(("IDENTITY AND ROLE", f"You are {name}. {purpose}"))
    direct_prompt = _text(configuration.get("direct_prompt"))
    if direct_prompt:
        sections.append(("ORIGINAL SHABDHA BRIEF", direct_prompt))
    for title, keys in ((
        ("OBJECTIVE", ("objective", "conversation_objective", "desired_outcomes")),
        ("ROLE", ("role", "job_role")),
        ("RESPONSIBILITIES", ("responsibilities", "goals")),
        ("TASKS", ("tasks",)),
        ("PRODUCTS AND SERVICES", ("products", "products_services")),
        ("TARGET CUSTOMERS", ("target_customers", "target_callers", "audience")),
        ("CONVERSATION BEHAVIOR", ("tone", "personality", "communication_style", "conversation_behavior")),
        ("CONVERSATION FLOW", ("conversation_flow", "workflow", "call_flow", "discovery_questions", "questions_to_ask")),
        ("INFORMATION TO COLLECT", ("information_to_collect", "information_to_extract", "post_call_extraction", "lead_outcome_fields")),
        ("QUALIFICATION AND DECISION RULES", ("qualification_rules", "qualification_criteria", "decision_rules")),
        ("BUSINESS AND PROCESS RULES", ("business_rules", "process_rules", "appointment_rules", "booking_rules")),
        ("OBJECTION HANDLING", ("objection_handling", "common_objections")),
        ("ESCALATION AND HANDOFF", ("transfer_rules", "human_transfer_conditions", "escalation", "escalation_rules")),
        ("FALLBACK BEHAVIOR", ("fallback_behavior", "fallback_rules")),
        ("CLOSING BEHAVIOR", ("closing_behavior",)),
        ("CONSTRAINTS AND GUARDRAILS", ("constraints", "guardrails", "restrictions")),
        ("ADDITIONAL INSTRUCTIONS", ("system_prompt", "additional_information", "other_information")),
    )):
        value = next((_text(configuration.get(key)) for key in keys if _text(configuration.get(key))), "")
        if value: sections.append((title, value))
    language = _text(configuration.get("language"))
    if language: sections.append(("LANGUAGE", f"Speak in {language}. Follow the caller's language preference when appropriate."))
    # Preserve later-added business fields instead of silently dropping them.
    consumed = {
        "name", "purpose", "language", "llm_provider", "llm_model", "voice", "call_type", "greeting", "transfer", "end_call",
        "objective", "conversation_objective", "desired_outcomes", "role", "job_role", "responsibilities", "goals", "tasks",
        "products", "products_services", "target_customers", "target_callers", "audience", "tone", "personality",
        "communication_style", "conversation_behavior", "conversation_flow", "workflow", "call_flow", "discovery_questions",
        "questions_to_ask", "information_to_collect", "information_to_extract", "post_call_extraction", "lead_outcome_fields",
        "qualification_rules", "qualification_criteria", "decision_rules", "business_rules", "process_rules", "appointment_rules",
        "booking_rules", "objection_handling", "common_objections", "transfer_rules", "human_transfer_conditions", "escalation",
        "escalation_rules", "fallback_behavior", "fallback_rules", "closing_behavior", "constraints", "guardrails", "restrictions",
        "system_prompt", "additional_information", "other_information",
    }
    for key, value in configuration.items():
        if key in consumed or not _text(value):
            continue
        label = key.replace("_", " ").title()
        if not any(title.casefold() == label.casefold() for title, _ in sections):
            sections.append((label.upper(), _text(value)))
    return "\n\n".join(f"{title}\n{body}" for title, body in sections)
