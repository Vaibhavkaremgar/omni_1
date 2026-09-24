from __future__ import annotations
from typing import Any

from app.services.employee_templates import get_template

SCRIPT_SECTION_NAMES = (
    "Identity & Purpose",
    "Greeting & Intro",
    "Qualification",
    "Handling Objections",
    "Call to Action",
    "Closing",
)
SUPPORTED_VARIABLE_TYPES = {"text", "number", "boolean", "date", "datetime", "phone", "email"}

TELUGU_ENGINE_CONTRACT = """TELUGU LANGUAGE ENGINE (STRICT): For customer-facing Telugu-English dialogue, write Telugu words only in Telugu Unicode script and English business/conversational words only in Latin script. Use natural modern spoken Telugu-English, not completely Telugu speech, Roman Telugu, old/archaic Telugu, grandhika, literary, Sanskrit-heavy, or translated Telugu. Include roughly one natural English word or short phrase every 5–7 spoken words where the sentence allows it. Use only context-relevant English terms. Do not transliterate Telugu into Latin letters or force English into every sentence. Keep answers short, conversational, and phone-natural. Before returning dialogue, verify that it is visibly Telugu-English mixed throughout and contains no old or literary Telugu wording."""
HINDI_ENGINE_CONTRACT = """HINDI LANGUAGE ENGINE (STRICT): For customer-facing Hindi-English dialogue, write Hindi words only in Devanagari Unicode script and English business/conversational words only in Latin script. Use natural Indian Hinglish, not completely Hindi speech, Roman Hindi, formal/literary Hindi, Sanskritized Hindi, or word-for-word translated Hindi. Include roughly one natural English word or short phrase every 5–7 spoken words where appropriate. Do not transliterate English terms into Devanagari or force English into every sentence. Keep answers short, conversational, one question at a time, and context-aware. Before returning dialogue, verify that Hindi words use Devanagari, English terms remain Latin, and the result sounds like a real phone conversation."""

BUSINESS_PROFILES: dict[str, dict[str, str]] = {
    "hospital": {
        "domain": "hospital appointment support",
        "focus": "patient need, department or doctor, urgency, preferred appointment time, and safe handoff",
        "qualification": "Ask one question at a time about the patient's need, preferred department or doctor, urgency, and appointment timing. Never give medical advice or invent doctor availability.",
        "objection": "Acknowledge concerns about timing, cost, symptoms, or reports. Share only configured clinic information and escalate urgent or uncertain cases to the clinic team.",
        "cta": "When the caller is ready, confirm department, doctor or service, date/time preference, patient name, and callback number before booking or arranging clinic follow-up.",
        "telugu": "Hospital calls should sound caring and calm. Use natural terms like appointment, doctor, department, reports, emergency, available, and confirm in English when Telugu speakers commonly use them.",
    },
    "clinic": {
        "domain": "clinic appointment support",
        "focus": "patient need, service, preferred doctor, timing, and clinic follow-up",
        "qualification": "Ask one question at a time about the caller's health/service need, preferred doctor or service, and appointment timing. Never diagnose or invent availability.",
        "objection": "Acknowledge concerns about timings, fees, treatment, or waiting. Use configured clinic details only and offer a clinic-team follow-up when unsure.",
        "cta": "Confirm the requested appointment/service details and offer the configured booking or callback next step.",
        "telugu": "Clinic calls should be soft, practical, and reassuring. Mix Telugu with English words like appointment, doctor, reports, clinic, available, confirm, and follow-up.",
    },
    "insurance": {
        "domain": "insurance policy support",
        "focus": "policy type, renewal date, premium/payment status, coverage question, and compliant follow-up",
        "qualification": "Ask about the policy or renewal context, premium/payment status, and preferred next step only when relevant. Do not give financial advice or promise coverage, claims, discounts, or approval.",
        "objection": "Acknowledge concerns about premium, renewal timing, claims, or documents. Explain only configured policy information and arrange advisor follow-up for advice or uncertainty.",
        "cta": "When the next step is clear, summarize the renewal or policy action and confirm whether to send details, arrange a callback, or connect the insurance team.",
        "telugu": "Insurance Telugu should be modern and natural: keep insurance, policy, renewal, premium, claim, payment, documents, reminder, and advisor in English where natural.",
    },
    "real_estate": {
        "domain": "real estate lead follow-up",
        "focus": "property type, location, budget range, timeline, amenities, and site visit",
        "qualification": "Ask one question at a time about property type, location preference, budget range, timeline, and site-visit interest. Never invent price, availability, possession, or legal claims.",
        "objection": "Acknowledge concerns about budget, location, possession date, amenities, or trust. Use configured project details only and offer sales-team follow-up.",
        "cta": "When interest is clear, summarize requirements and confirm the configured site visit, callback, brochure, or sales follow-up.",
        "telugu": "Real-estate Telugu should use natural English fillers and terms like location, budget, project, plot, flat, amenities, price range, site visit, available, and booking.",
    },
    "education": {
        "domain": "education admissions counselling",
        "focus": "course interest, eligibility, fees, admission timeline, location, and counsellor handoff",
        "qualification": "Ask about course interest, eligibility/background, admission timeline, fees question, and counselling need. Do not invent admission decisions, scholarships, or guarantees.",
        "objection": "Acknowledge concerns about fees, eligibility, location, course fit, or deadlines. Share configured admission details only and offer counsellor follow-up.",
        "cta": "Confirm the course/admission interest and route to the configured counsellor, application step, or follow-up.",
        "telugu": "Education calls should sound like friendly counselling. Use course, admission, eligibility, fees, campus, counsellor, application, and details in English where natural.",
    },
    "support": {
        "domain": "customer support",
        "focus": "issue type, product/service, account/order reference if needed, troubleshooting status, and escalation",
        "qualification": "Understand the issue first, then ask only the details needed to troubleshoot or escalate. Do not ask for unrelated lead-qualification fields.",
        "objection": "Acknowledge frustration, apologize briefly when appropriate, and explain only supported policies or steps. Escalate unresolved or out-of-scope issues.",
        "cta": "Summarize the support action, ticket/callback path, or escalation and confirm the customer has nothing else pending.",
        "telugu": "Support Telugu should be empathetic and quick. Use issue, product, order, ticket, support, refund, replacement, status, and escalation in English where natural.",
    },
    "sales": {
        "domain": "sales lead qualification",
        "focus": "product/service interest, use case, budget, timeline, service area, and sales follow-up",
        "qualification": "Ask one question at a time about interest, use case, budget, timeline, and fit for the configured product/service. Do not invent prices, discounts, stock, or guarantees.",
        "objection": "Acknowledge concerns about price, fit, timing, or trust. Use configured offer/product details only and offer a sales-team follow-up.",
        "cta": "When interest is clear, summarize the need and confirm the configured purchase, demo, callback, WhatsApp details, or sales follow-up.",
        "telugu": "Sales Telugu should be friendly and direct. Use offer, product, service, price, budget, discount, demo, details, WhatsApp, and follow-up in English where natural.",
    },
    "appointment": {
        "domain": "appointment booking",
        "focus": "service type, preferred date/time, location, booking rules, and confirmation",
        "qualification": "Ask about the service needed, preferred date/time, location, and any configured booking rules. Never invent availability.",
        "objection": "Acknowledge timing or policy concerns. Explain configured booking/cancellation information only and offer human follow-up when needed.",
        "cta": "Confirm the service, date/time preference, and contact details required for the configured booking next step.",
        "telugu": "Appointment Telugu should be crisp and helpful. Use appointment, booking, slot, available, confirm, reschedule, and cancellation in English where natural.",
    },
    "collections": {
        "domain": "payment reminder support",
        "focus": "account/service type, due date, payment method, support need, and safe escalation",
        "qualification": "Confirm the configured payment context and ask only about payment support or preferred next step. Do not pressure, threaten, or invent penalties.",
        "objection": "Acknowledge payment difficulty or confusion respectfully. Share configured due-date/payment information only and offer support escalation.",
        "cta": "Summarize the payment support path, configured payment method, callback, or escalation and confirm the customer's next step.",
        "telugu": "Payment-reminder Telugu should be respectful, never pushy. Use payment, due date, account, reminder, support, link, and confirm in English where natural.",
    },
}


def _conversation_variables(configuration: dict[str, Any]) -> list[dict[str, Any]]:
    existing = configuration.get("conversation_variables")
    if isinstance(existing, list) and existing:
        return [item for item in existing if isinstance(item, dict) and _text(item.get("key"))]
    brief = " ".join(str(configuration.get(key, "")) for key in ("purpose", "original_requirement", "business_description", "products")).casefold()
    variables = [
        {"key": "customer_name", "label": "Customer Name", "description": "Name provided by the caller", "type": "text", "required": False},
        {"key": "mobile_number", "label": "Mobile Number", "description": "Phone number provided by the caller", "type": "phone", "required": False},
        {"key": "requirement", "label": "Requirement", "description": "What the caller needs", "type": "text", "required": False},
    ]
    if any(word in brief for word in ("printer", "product", "sell", "sales", "service")):
        variables += [
            {"key": "printer_type", "label": "Printer Type", "description": "Type of printer requested", "type": "text", "required": False},
            {"key": "quantity", "label": "Quantity", "description": "Number of units requested", "type": "number", "required": False},
            {"key": "budget", "label": "Budget", "description": "Budget shared by the caller", "type": "number", "required": False},
            {"key": "purchase_timeline", "label": "Purchase Timeline", "description": "When the caller intends to purchase", "type": "text", "required": False},
        ]
    elif any(word in brief for word in ("appointment", "booking", "doctor", "clinic")):
        variables += [{"key": "location", "label": "Location", "description": "Caller location", "type": "text", "required": False}, {"key": "preferred_datetime", "label": "Preferred Date and Time", "description": "Requested appointment time", "type": "datetime", "required": False}]
    return variables


def business_conversation_profile(configuration: dict[str, Any]) -> dict[str, str]:
    values = configuration.get("template_values") if isinstance(configuration.get("template_values"), dict) else {}
    template_id = _text(configuration.get("selected_template_id")).casefold()
    signal = " ".join(
        _text(value)
        for value in (
            configuration.get("business_type"),
            configuration.get("industry"),
            configuration.get("category"),
            values.get("business_type"),
            values.get("product_or_service"),
            values.get("products_services"),
            values.get("service_type"),
            values.get("services"),
            values.get("courses"),
            values.get("property_types"),
            configuration.get("business_name"),
            configuration.get("business_description"),
            configuration.get("products"),
            configuration.get("products_services"),
            configuration.get("purpose"),
            configuration.get("original_requirement"),
            configuration.get("direct_prompt"),
            template_id.replace("pontis_", "").replace("_v1", "").replace("_", " "),
        )
    ).casefold()
    checks = (
        ("hospital", ("hospital", "healthcare", "medical", "doctor", "patient", "department")),
        ("clinic", ("clinic", "diagnostic", "dental", "dentist", "pharmacy")),
        ("insurance", ("insurance", "policy", "renewal", "premium", "claim")),
        ("real_estate", ("real estate", "property", "plot", "villa", "flat", "apartment", "site visit", "project")),
        ("education", ("education", "school", "college", "course", "admission", "student", "counsellor", "counselor")),
        ("support", ("support", "refund", "replacement", "ticket", "complaint", "issue", "troubleshoot")),
        ("collections", ("collection", "payment reminder", "due date", "emi", "loan", "overdue")),
        ("appointment", ("appointment", "booking", "slot", "reschedule")),
        ("sales", ("sales", "sell", "lead", "product", "service", "printer", "offer", "discount", "demo")),
    )
    key = next((profile for profile, markers in checks if any(marker in signal for marker in markers)), "sales")
    if any(marker in signal for marker in ("election", "campaign", "candidate", "voter", "constituency", "ghmc", "political")):
        return {
            "key": "election_campaign",
            "domain": "election campaign outreach",
            "focus": "the campaign's stated civic message, constituency context, voter questions, and respectful opt-in engagement",
            "qualification": "Ask only campaign-relevant questions such as whether the voter wants more information or wishes to share a concern. Do not ask sales, budget, product, appointment, or booking questions.",
            "objection": "Acknowledge political concerns respectfully, use only verified campaign facts, avoid persuasion claims that are not configured, and offer campaign information when requested.",
            "cta": "Offer only the configured campaign next step, such as sharing verified information or recording a campaign-related concern. Do not arrange appointments or callbacks unless explicitly configured.",
            "telugu": "Election outreach should sound respectful and conversational. Keep campaign, candidate, ward, constituency, vote, issue, message, details, and update in English when natural.",
        }
    return {"key": key, **BUSINESS_PROFILES[key]}


def normalize_business_identity(configuration: dict[str, Any]) -> dict[str, Any]:
    """Return config with a safe business_name/business_description pair."""
    result = dict(configuration)
    values = result.get("template_values") if isinstance(result.get("template_values"), dict) else {}
    requirement = _text(result.get("business_description")) or _text(
        result.get("original_requirement")
    ) or _text(result.get("direct_prompt"))
    # Only fields whose meaning is explicitly an organization/project identity
    # may populate business_name.  In particular, never mine nouns from the
    # free-form requirement ("wedding", "customers", etc.).
    identity_keys = ("business_name", "company_name", "hospital_name", "institution_name", "project_name")
    explicit = next((_text(result.get(key)) for key in identity_keys if _text(result.get(key))), "")
    if not explicit:
        explicit = next((_text(values.get(key)) for key in identity_keys if _text(values.get(key))), "")
    if explicit:
        result["business_name"] = explicit
        result.setdefault("business_description", requirement)
    elif requirement and _text(result.get("original_requirement")):
        # A free-form brief is context, never a guessed company identity.
        result.setdefault("business_description", requirement)
    if not _text(result.get("purpose")) or _text(result.get("purpose")).casefold() in {"to be defined through the builder", requirement.casefold()}:
        lowered = requirement.casefold()
        if "printer" in lowered or "printing" in lowered:
            result["purpose"] = "printer sales and customer qualification"
    return result


def _is_telugu(language: str) -> bool:
    return language.casefold() in {"telugu", "te", "te-in", "telugu (india)"}


def _is_hindi(language: str) -> bool:
    return language.casefold() in {"hindi", "hi", "hi-in", "hindi (india)"}


def _language_conversation_guidance(language: str) -> str:
    selected = language or "the selected language"
    normalized = selected.casefold()
    if normalized in {"telugu", "te", "te-in", "telugu (india)"}:
        fillers = "Use Telugu conversational expressions such as 'artham ayyindhi andi', 'sare andi', 'ok andi', 'tappakunda andi', and 'avunu andi'. Alternate 'sare andi' and 'ok andi' naturally; do not force fillers into every sentence. Prefer 'inka' over repeatedly using 'mariyu' where grammatically appropriate."
        hello = "If the caller is silent for approximately 2–3 seconds, say exactly 'Vinipisthunda andi?' to check that they are present, then STOP speaking and WAIT for the caller's response. Do not repeat the previous question or continue to the next question. If the caller repeatedly says hello, also say exactly 'Vinipisthunda andi?' and wait for the caller's response before continuing."
    elif normalized in {"hindi", "hi", "hi-in", "hindi (india)"}:
        fillers = "Speak in natural Indian conversational Hinglish: Hindi is the base language, but freely and naturally mix common English words used in Indian business conversations. Avoid formal, literary, Sanskritized, old-fashioned, news-anchor, or textbook Hindi and avoid translated English-to-Hindi phrasing. Prefer words such as okay, ok, sure, actually, sorry, thank you, yes, no problem, right, correct, exactly, definitely, available, requirement, details, information, location, area, budget, price, range, size, plot, project, property, offer, features, benefits, option, booking, site visit, appointment, schedule, confirm, call, message, update, support, team, sales team, customer, service, product, model, number, code, document, process, payment, EMI, discount, deal, contact, follow-up, time, date, address, WhatsApp, website, and link when natural. Use Hindi fillers such as 'ji', 'haan ji', 'achha ji', 'theek hai ji', 'bilkul ji', 'samajh gaya ji', and 'zaroor ji', plus Okay, Actually, Sure, Right, and Exactly; vary acknowledgements and do not put a filler in every sentence. Say 'thanks', 'thank you', and 'sorry' in English only; never translate them. Never use Telugu fillers. Keep Hindi grammar and connective words primary; do not turn the call into mostly English. Sentence construction must visibly combine Hindi grammar with English words and connectors in the same utterance, not produce pure Hindi paragraphs and not produce separate all-English sentences. Use connectors like actually, okay, so, right, sure, definitely, basically, and no problem between Hindi phrases where natural. Preferred style examples: 'Okay ji, aapki requirement samajh gaya. Actually, is location mein kuch options available hain.', 'Sure ji, main details check karke aapko update karta hoon.', 'Aapka budget kis range mein hai?', 'Right ji, main isko confirm karke team se follow-up karwa dunga.' Generate this mixed style throughout the conversation, while keeping Hindi as the primary grammar."
        hello = "If the caller is silent for approximately 2–3 seconds, say 'Kya aap wahan hain ji?' to check that they are present, then STOP speaking and WAIT for the caller's response. Do not repeat the previous question or continue to the next question. If the caller repeatedly says hello, say 'Kya aap wahan hain ji?' and wait for the caller's response before continuing. Do not repeatedly restart the welcome and never use Telugu fillers."
    else:
        fillers = f"Use natural conversational acknowledgements and fillers from {selected}; never import Telugu or Hindi-specific fillers unless that is the selected language."
        hello = f"For repeated hello or attention-seeking, acknowledge the caller with varied, natural responses in {selected}, rather than repeating the same greeting."
    if _is_telugu(normalized):
        fillers = TELUGU_ENGINE_CONTRACT + "\n\n" + fillers
        fillers += " Use correct modern Telugu grammar around English terms. Preferred pattern: 'నమస్కారం అండి, నేను Akshay, KMG Insurance నుంచి మాట్లాడుతున్నాను. మీ insurance premium గురించి ఒక quick update ఇవ్వడానికి call చేశాను. మీకు ఈ offer గురించి details కావాలా?' Use 'నేను Akshay, KMG Insurance నుంచి మాట్లాడుతున్నాను', not 'నేను Akshay మాట్లాడుతున్నాను' when introducing the company. Keep insurance, premium, quick update, call, offer, and details in English; keep Telugu postpositions and verbs in Telugu script."
        fillers += " MANDATORY SCRIPT RULE: every Telugu word spoken to callers must be written in Telugu script, mixed naturally with English words like insurance, renewal, policy, details, available, call, support, service, offer, booking, appointment, price, budget, product, team, and follow-up. Never write Telugu in Roman letters."
        fillers += " TELUGU THANKS RULE: close gratitude in English only, for example 'Thank you. Have a nice day.' Never translate thanks or have-a-nice-day into Telugu and do not add Telugu suffixes to the thanks message."
        hello = "If the caller is silent for approximately 2-3 seconds, say exactly '\u0c35\u0c3f\u0c28\u0c3f\u0c2a\u0c3f\u0c38\u0c4d\u0c24\u0c41\u0c02\u0c26\u0c3e \u0c05\u0c02\u0c21\u0c3f?' to check that they are present, then STOP speaking and WAIT for the caller's response."
    elif _is_hindi(normalized):
        fillers = HINDI_ENGINE_CONTRACT + "\n\n" + fillers
        fillers += " MANDATORY SCRIPT RULE: every Hindi word spoken to callers must be written in Devanagari, mixed naturally with English words like okay, actually, requirement, details, available, budget, price, location, offer, product, service, booking, appointment, confirm, support, team, and follow-up. Never write Hindi in Roman letters."
        hello = "If the caller is silent for approximately 2-3 seconds, say '\u0915\u094d\u092f\u093e \u0906\u092a \u0935\u0939\u093e\u0902 \u0939\u0948\u0902 \u091c\u0940?' to check that they are present, then STOP speaking and WAIT for the caller's response."
    return f"""The selected conversation language is {selected}. Generate these behaviors dynamically in that language and preserve the existing business, safety, inbound/outbound, interruption, call-lifecycle, research, Knowledge Base, variable, and six-section script rules.

For every regional-language conversation, code-switch naturally with commonly used English business and conversational words; do not make the speech overly formal or fully translated. Say 'thanks', 'thank you', and 'sorry' in English only. Speak phone numbers, OTPs, model numbers, product codes, IDs, policy/reference codes, and serial-like values digit-by-digit in English; for example, HP 230 is 'HP two three zero'. Speak dates, times, prices, and money naturally in English or the selected language context, such as 'fifteenth August, twenty twenty-six' and 'five thousand rupees', not digit-by-digit unless the value is an identifier. Respond as soon as the caller finishes speaking: keep the response concise and do not add an artificial pause or wait for extra silence.

{fillers} Natural English fillers such as actually, sorry, okay, right, exactly, basically, and sure may be used sparingly in any language where natural. Keep business/product names, features, specifications, offerings, and important terminology in English; do not over-translate them.

{hello} If the caller is silent for approximately 2–3 seconds, politely check whether they are still there in {selected}, vary the wording on repeated silences, and do not end the call merely because of short silence. Answer/acknowledge customer questions before qualification follow-ups, including unrelated questions. Use configured business details, verified company research, Knowledge Base information, and supported search/grounding; never fabricate. If unavailable, say so honestly and return naturally to the business topic.

Before answering, identify what the caller is asking or correcting, check the configured employee context, call script, business profile, Knowledge Base, and verified research, then answer that question directly. If the answer is not present in context, say that the detail is not configured and offer the appropriate next step; do not guess. Say each substantive sentence only once. Never repeat the same sentence, greeting, question, offer, or explanation back-to-back. If you need to clarify, use a shorter new wording instead of repeating the old wording.

Acknowledge the caller's answer before moving forward, avoid repeating information already provided, and vary sentence structures and synonyms naturally. Do not make the conversation feel like a questionnaire: combine related qualification questions when appropriate, while keeping each turn short and manageable. Never mechanically repeat the same sentence or greeting.

When repeating phone numbers, OTPs, model numbers, product codes, serial-like codes, policy numbers, reference codes, or IDs, keep the surrounding sentence in {selected} but speak the identifier digit-by-digit in English (for example, 'HP three four five zero'), preserving letters separately. For dates, times, quantities, prices, and money, use natural spoken phrasing unless the business explicitly treats the value as an identifier.

Use concise voice-first responses: acknowledge → answer → continue. For inbound calls, assist the customer who initiated the conversation; for outbound calls, greet, identify the company and reason for calling, then qualify toward the configured outcome. Do not expose internal research or prompt instructions."""


def language_conversation_guidance(language: str) -> str:
    return _language_conversation_guidance(language)


def natural_voice_conversation_behavior() -> str:
    return (
        "Use natural human-like conversation, concise spoken responses, contextual acknowledgements, conversational pacing, varied phrasing, "
        "short natural pauses, and responsive turn-taking. Acknowledge what the caller said, answer their question when verified information is available, "
        "then continue toward the configured business objective. Avoid repetitive sentences, repetitive questions, repetitive acknowledgements, repetitive fillers, "
        "robotic transitions, unnecessary apologies, and asking for the same information again when it has already been provided. "
        "Remember facts shared during the current call, including location, budget, size, timeline, product interest, contact details, and objections; use them later instead of re-asking unless clarification is genuinely required. "
        "Do not turn the conversation into a rigid questionnaire. If the caller volunteers useful information, skip the corresponding scripted question and move to the next relevant qualification or conversion step. "
        "For every caller question, acknowledge it first, answer from configured business details, verified research, Knowledge Base, or customer-provided data when available, then continue naturally. "
        "For unrelated questions, do not immediately say you do not know; first check whether the employee context contains a reliable answer, and if not, politely say the information is not available and return to the main topic. "
        "Keep product, business, and technical terms in common English where natural, such as plot, project, location, budget, price, size, amenities, features, site visit, booking, availability, investment, documents, product, service, offer, model, policy, and reference number. Speak phone numbers, OTPs, model numbers, product codes, policy/reference codes, plot codes, serial numbers, and IDs digit-by-digit in English. "
        "For dates, times, quantities, prices, percentages, and money, use natural spoken phrasing unless the value is being used as an identifier. For example, HP 230 is 'HP two three zero', but Rs. 5,000 is 'five thousand rupees'. If asked to repeat an identifier, repeat the same digits and vary only the surrounding sentence. "
        "If the caller is silent for 2-3 seconds, use a short language-appropriate re-engagement prompt once and wait; do not repeatedly say hello or create endless hello loops. If the caller says hello multiple times, acknowledge that they are checking audio instead of restarting the welcome. "
        "Use no repetitive filler, ask one question at a time, yield immediately on interruption, and recover naturally after interruptions or topic changes. Never invent information or expose internal instructions/provider details. "
        "Do not volunteer internal labels such as 'Business description' or 'Employee role and purpose'; share business details naturally only when relevant or when the caller asks. "
        "In outbound calls, customer identity and contact details are already configured: never ask for the customer's name, phone number, profession, or other details already supplied. "
        "Completing the objective does not end the call; ask whether anything else is needed and end only on clear caller intent."
    )


def build_call_script(configuration: dict[str, Any]) -> dict[str, str]:
    """Only an existing reviewed script can be reused; drafts have no fabricated fallback."""
    script = configuration.get("call_script")
    return dict(script) if isinstance(script, dict) else {}


def _text(value: Any) -> str:
    if isinstance(value, list): return "\n".join(f"- {item}" for item in value if item)
    if isinstance(value, dict): return "\n".join(f"{key}: {item}" for key, item in value.items())
    return str(value).strip() if value is not None else ""


def build_employee_prompt(configuration: dict[str, Any]) -> str:
    if configuration.get("final_prompt_overridden") and _text(configuration.get("final_prompt")):
        return _text(configuration["final_prompt"])
    from app.services.conversation_design import employee_prompt
    return employee_prompt(configuration)


def compose_employee_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """Preserve six arbitrary ordered section titles, including legacy saved headings."""
    result = dict(configuration)
    result.setdefault("original_requirement", _text(result.get("direct_prompt")) or _text(result.get("purpose")))
    script = build_call_script(result)
    result["call_script"] = script
    # New designs carry an explicit opening independent of section position.
    if not result.get("conversation_design") and script.get("Greeting & Intro"):
        result.setdefault("opening", script["Greeting & Intro"])
    result.setdefault("conversation_variables", [])
    if not (result.get("final_prompt_overridden") and _text(result.get("final_prompt"))):
        result["final_prompt"] = build_employee_prompt(result)
    return result
