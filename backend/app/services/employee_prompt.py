from __future__ import annotations
from typing import Any

from app.services.employee_templates import get_template

def _text(value: Any) -> str:
    if isinstance(value, list): return "\n".join(f"- {item}" for item in value if item)
    if isinstance(value, dict): return "\n".join(f"{key}: {item}" for key, item in value.items())
    return str(value).strip() if value is not None else ""

def build_employee_prompt(configuration: dict[str, Any]) -> str:
    sections: list[tuple[str, str]] = []
    name = _text(configuration.get("name")) or "AI employee"
    purpose = _text(configuration.get("purpose"))
    if purpose.casefold() == "to be defined through the builder":
        purpose = ""
    template_id = _text(configuration.get("selected_template_id"))
    template = None
    if template_id:
        try:
            template = get_template(template_id)
        except KeyError:
            template = None
    if template:
        sections.append(("TEMPLATE FOUNDATION", f"You are {name}, based on the Pontis template '{template['name']}'.\n{template['purpose']}"))
        sections.append(("TEMPLATE RESPONSIBILITIES", _text(template.get("responsibilities"))))
        sections.append(("TEMPLATE WORKFLOW", _text(template.get("workflow"))))
        values = configuration.get("template_values") or {}
        business = "\n".join(
            f"- {field['label']}: {_text(values.get(field['key']))}"
            for field in template["placeholders"]
            if _text(values.get(field["key"]))
        )
        if business:
            sections.append(("CUSTOMER PLACEHOLDER VALUES", business))
    if purpose: sections.append(("IDENTITY AND ROLE", f"You are {name}. {purpose}"))
    shabdha_brief = _text(configuration.get("original_shabdha_brief"))
    direct_prompt = _text(configuration.get("direct_prompt"))
    creation_mode = _text(configuration.get("creation_mode")).casefold()
    if shabdha_brief:
        sections.append(("ORIGINAL SHABDHA BRIEF", shabdha_brief))
    elif direct_prompt:
        title = "ORIGINAL CUSTOMER PROMPT" if creation_mode == "prompt" else "ORIGINAL SHABDHA BRIEF"
        sections.append((title, direct_prompt))
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
        if title == "ADDITIONAL INSTRUCTIONS" and value and direct_prompt and value == direct_prompt:
            value = ""
        if value: sections.append((title, value))
    if template:
        sections.append(("TEMPLATE GUARDRAILS", _text(template.get("safety_guardrails"))))
    language = _text(configuration.get("language"))
    if language:
        language_rule = f"Speak in {language}. Follow the caller's language preference when appropriate."
        if language == "Telugu":
            language_rule += " Use natural Telugu throughout; common English business words are allowed and do not trigger a language switch. Switch only when the caller explicitly requests another language."
        sections.append(("LANGUAGE", language_rule))
    # Preserve later-added business fields instead of silently dropping them.
    consumed = {
        "name", "purpose", "language", "creation_mode", "original_shabdha_brief", "direct_prompt", "final_prompt", "selected_template_id", "selected_template_version", "template_values", "llm_provider", "llm_model", "voice", "call_type", "greeting", "transfer", "end_call",
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


def compose_employee_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """Build the one deployable prompt while retaining all original customer input."""
    result = dict(configuration)
    generated = build_employee_prompt(result)
    reviewed = _text(result.get("final_prompt"))
    if reviewed:
        # Step 4 may contain deliberate customer edits. Keep them verbatim,
        # but never let edits remove the platform-owned safety sections.
        required_sections = [section for section in ("TEMPLATE GUARDRAILS", "LANGUAGE") if section in generated and section not in reviewed]
        if required_sections:
            generated_sections = generated.split("\n\n")
            reviewed = reviewed.rstrip() + "\n\n" + "\n\n".join(
                block for block in generated_sections if any(block.startswith(section) for section in required_sections)
            )
        result["final_prompt"] = reviewed
    else:
        result["final_prompt"] = generated
    return result
