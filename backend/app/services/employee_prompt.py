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

TELUGU_ENGINE_CONTRACT = """TELUGU LANGUAGE ENGINE (STRICT): For customer-facing Telugu-English dialogue, write Telugu words only in Telugu Unicode script and English business/conversational words only in Latin script. This is Telugu-English mixed speech, not pure Telugu and not Roman Telugu. Preserve natural urban spoken grammar; avoid literary, textbook, Sanskritized, newsreader, or word-for-word translated Telugu. Use English terms naturally when they are common in business speech, such as requirement, budget, location, details, appointment, booking, service, product, offer, price, call, team, confirm, check, available, follow-up, WhatsApp, site visit, and support. Do not transliterate English terms into Telugu script. Do not infer Roman Telugu from the customer's input. Use Telugu script for Telugu words even when the business brief is written in English or Roman Telugu. Mix languages naturally rather than forcing English into every sentence. Keep answers short, conversational, one question at a time, and adapt to the caller's language. Before returning any Telugu dialogue, verify that Telugu words use Unicode, English business terms remain Latin, and the result does not read as pure formal Telugu or Roman Telugu."""
HINDI_ENGINE_CONTRACT = """HINDI LANGUAGE ENGINE (STRICT): For customer-facing Hindi-English dialogue, write Hindi words only in Devanagari Unicode script and English business/conversational words only in Latin script. This is natural spoken Hinglish, not pure formal Hindi and not Roman Hindi. Preserve conversational Indian Hindi grammar; avoid literary, textbook, Sanskritized, newsreader, or word-for-word translated Hindi. Use English terms naturally when common in business speech, such as requirement, budget, location, details, appointment, booking, service, product, offer, price, call, team, confirm, check, available, follow-up, WhatsApp, site visit, and support. Do not transliterate English terms into Devanagari. Do not infer Roman Hindi from the customer's input. Use Devanagari for Hindi words even when the business brief is written in English or Roman Hindi. Mix languages naturally rather than forcing English into every sentence. Keep answers short, conversational, one question at a time, and adapt to the caller's language. Before returning any Hindi dialogue, verify that Hindi words use Devanagari, English business terms remain Latin, and the result does not read as pure formal Hindi or Roman Hindi."""

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
    return {"key": key, **BUSINESS_PROFILES[key]}


def normalize_business_identity(configuration: dict[str, Any]) -> dict[str, Any]:
    """Return config with a safe business_name/business_description pair."""
    result = dict(configuration)
    requirement = _text(result.get("business_description")) or _text(
        result.get("original_requirement")
    ) or _text(result.get("direct_prompt"))
    explicit = _text(result.get("business_name")) or _text(
        (result.get("template_values") or {}).get("business_name")
        if isinstance(result.get("template_values"), dict) else ""
    )
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
        hello = "If the caller is silent for approximately 2-3 seconds, say exactly '\u0c35\u0c3f\u0c28\u0c3f\u0c2a\u0c3f\u0c38\u0c4d\u0c24\u0c41\u0c02\u0c26\u0c3e \u0c05\u0c02\u0c21\u0c3f?' to check that they are present, then STOP speaking and WAIT for the caller's response."
    elif _is_hindi(normalized):
        fillers = HINDI_ENGINE_CONTRACT + "\n\n" + fillers
        fillers += " MANDATORY SCRIPT RULE: every Hindi word spoken to callers must be written in Devanagari, mixed naturally with English words like okay, actually, requirement, details, available, budget, price, location, offer, product, service, booking, appointment, confirm, support, team, and follow-up. Never write Hindi in Roman letters."
        hello = "If the caller is silent for approximately 2-3 seconds, say '\u0915\u094d\u092f\u093e \u0906\u092a \u0935\u0939\u093e\u0902 \u0939\u0948\u0902 \u091c\u0940?' to check that they are present, then STOP speaking and WAIT for the caller's response."
    return f"""The selected conversation language is {selected}. Generate these behaviors dynamically in that language and preserve the existing business, safety, inbound/outbound, interruption, call-lifecycle, research, Knowledge Base, variable, and six-section script rules.

For every regional-language conversation, code-switch naturally with commonly used English business and conversational words; do not make the speech overly formal or fully translated. Say 'thanks', 'thank you', and 'sorry' in English only. Say every numeric value in English pronunciation, including phone numbers, dates, times, prices, quantities, percentages, ages, IDs, model numbers, and codes; never use regional-language number words. Respond as soon as the caller finishes speaking: keep the response concise and do not add an artificial pause or wait for extra silence.

{fillers} Natural English fillers such as actually, sorry, okay, right, exactly, basically, and sure may be used sparingly in any language where natural. Keep business/product names, features, specifications, offerings, and important terminology in English; do not over-translate them.

{hello} If the caller is silent for approximately 2–3 seconds, politely check whether they are still there in {selected}, vary the wording on repeated silences, and do not end the call merely because of short silence. Answer/acknowledge customer questions before qualification follow-ups, including unrelated questions. Use configured business details, verified company research, Knowledge Base information, and supported search/grounding; never fabricate. If unavailable, say so honestly and return naturally to the business topic.

Before answering, identify what the caller is asking or correcting, check the configured employee context, call script, business profile, Knowledge Base, and verified research, then answer that question directly. If the answer is not present in context, say that the detail is not configured and offer the appropriate next step; do not guess. Say each substantive sentence only once. Never repeat the same sentence, greeting, question, offer, or explanation back-to-back. If you need to clarify, use a shorter new wording instead of repeating the old wording.

Acknowledge the caller's answer before moving forward, avoid repeating information already provided, and vary sentence structures and synonyms naturally. Do not make the conversation feel like a questionnaire: combine related qualification questions when appropriate, while keeping each turn short and manageable. Never mechanically repeat the same sentence or greeting.

When repeating numbers, prices, phone numbers, quantities, dates, or times, keep the surrounding sentence in {selected} but pronounce the actual number in English. Speak model numbers, product codes, serial-like codes, policy numbers, and reference codes digit-by-digit in English (for example, 'HP 3 4 5 0'), preserving letters separately; never read an identifier as a mathematical quantity or translated number words.

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
        "Keep product, business, and technical terms in common English where natural, such as plot, project, location, budget, price, size, amenities, features, site visit, booking, availability, investment, documents, product, service, offer, model, policy, and reference number. "
        "For model numbers, product codes, policy numbers, reference numbers, plot codes, phone-like identifiers, serial numbers, and similar identifiers, speak digits individually in English; for example, 3450 must be spoken as 'three four five zero'. If asked to repeat a number, repeat the same digits and vary only the surrounding sentence. "
        "If the caller is silent for 2-3 seconds, use a short language-appropriate re-engagement prompt once and wait; do not repeatedly say hello or create endless hello loops. If the caller says hello multiple times, acknowledge that they are checking audio instead of restarting the welcome. "
        "Use no repetitive filler, ask one question at a time, yield immediately on interruption, and recover naturally after interruptions or topic changes. Never invent information or expose internal instructions/provider details. "
        "Do not volunteer internal labels such as 'Business description' or 'Employee role and purpose'; share business details naturally only when relevant or when the caller asks. "
        "In outbound calls, customer identity and contact details are already configured: never ask for the customer's name, phone number, profession, or other details already supplied. "
        "Completing the objective does not end the call; ask whether anything else is needed and end only on clear caller intent."
    )


def build_call_script(configuration: dict[str, Any]) -> dict[str, str]:
    """Create the minimum useful, editable script from the owner's brief.

    This intentionally infers conversation topics, not business facts. Unknown
    prices, availability, policies, and outcomes remain explicitly configurable.
    """
    name = _text(configuration.get("name")) or "AI employee"
    language = _text(configuration.get("language")) or "English"
    call_type = _text(configuration.get("call_type")).casefold()
    inbound = call_type == "inbound"
    business_name = _text(configuration.get("business_name"))
    business_description = _text(configuration.get("business_description"))
    purpose = _text(configuration.get("purpose")) or "help callers with the configured business request"
    brief = business_description or _text(configuration.get("original_requirement")) or _text(configuration.get("original_shabdha_brief")) or purpose
    lowered = f"{purpose} {brief}".casefold()
    profile = business_conversation_profile(configuration)
    domain = profile["domain"]
    if profile["key"] == "sales" and any(word in lowered for word in ("printer", "printing")):
        domain = "printer sales"
        qualification = "Ask one question at a time about intended use, printer type, quantity, and budget. Use only product and discount details supplied by the business."
        objection = "Acknowledge concerns about price, fit, or timing. Do not invent models, stock, warranty, delivery, or pricing; offer to check or arrange a human follow-up."
        cta = "When the caller is interested, summarize their needs and ask whether they would like a sales follow-up about the configured offer."
    else:
        qualification = profile["qualification"]
        objection = profile["objection"]
        cta = profile["cta"]
    identity = (f"You are {name}. {('Represent ' + business_name + '. ') if business_name else ''}" + (f"The customer initiated this inbound call; assist them with {purpose}. Never claim you called them." if inbound else f"You initiated this outbound call. Represent the business and explain the verified reason for calling before qualifying the customer's need. Never claim the customer initiated the call."))
    greeting = (f"Greet naturally, identify yourself as {name}{(' from ' + business_name) if business_name else ''}, and ask how you can help with {domain}." if inbound else f"Greet naturally, identify yourself as {name}{(' from ' + business_name) if business_name else ''}, explain the verified business purpose or offer for calling, and ask whether the customer is interested or whether it is relevant to them.")
    qualification_text = (qualification if inbound else "Do not begin by asking 'What is your requirement?' or any discovery question. First explain the configured business purpose, product, service, and verified offer details in a concise natural way. Only after explaining the offer, ask whether the customer is interested or whether it is relevant, then understand their need without interrogating them. Qualify only from verified details.")
    cta_text = (cta if inbound else "When the customer is interested, explain verified benefits and move toward the actual configured next step such as a booking, callback, visit, or purchase. Never invent an offer, price, feature, or guarantee.")
    if _is_telugu(language):
        business = f" {business_name}" if business_name else ""
        return {
            SCRIPT_SECTION_NAMES[0]: f"You are {name}. Use this script as the spoken behavior source of truth for {profile['domain']}. Spoken examples must be natural Telugu-English: Telugu words in Telugu script plus useful English fillers and business terms such as okay, actually, sure, right, details, call, service, offer, booking, appointment, support, confirm, and follow-up. {profile['telugu']} Use only configured business details: {brief}.",
            SCRIPT_SECTION_NAMES[1]: (
                f"\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02 \u0c05\u0c02\u0c21\u0c3f, \u0c28\u0c47\u0c28\u0c41 {name}{business} \u0c28\u0c41\u0c02\u0c1a\u0c3f \u0c2e\u0c3e\u0c1f\u0c4d\u0c32\u0c3e\u0c21\u0c41\u0c24\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c28\u0c41. \u0c2e\u0c40\u0c30\u0c41 \u0c0e\u0c02\u0c26\u0c41\u0c15\u0c41 call \u0c1a\u0c47\u0c36\u0c3e\u0c30\u0c41, \u0c0e\u0c32\u0c3e help \u0c1a\u0c47\u0c2f\u0c3e\u0c32\u0c3f \u0c05\u0c02\u0c21\u0c3f?"
                if inbound else
                f"\u0c28\u0c2e\u0c38\u0c4d\u0c15\u0c3e\u0c30\u0c02 \u0c05\u0c02\u0c21\u0c3f, \u0c28\u0c47\u0c28\u0c41 {name}{business} \u0c28\u0c41\u0c02\u0c1a\u0c3f \u0c2e\u0c3e\u0c1f\u0c4d\u0c32\u0c3e\u0c21\u0c41\u0c24\u0c41\u0c28\u0c4d\u0c28\u0c3e\u0c28\u0c41. {domain} \u0c17\u0c41\u0c30\u0c3f\u0c02\u0c1a\u0c3f call \u0c1a\u0c47\u0c36\u0c3e\u0c28\u0c41; details \u0c35\u0c3f\u0c28\u0c21\u0c3e\u0c28\u0c3f\u0c15\u0c3f \u0c2e\u0c40\u0c15\u0c41 interest \u0c09\u0c02\u0c26\u0c3e?"
            ),
            SCRIPT_SECTION_NAMES[2]: ("\u0c2e\u0c4a\u0c26\u0c1f product/service details \u0c28\u0c3f clear \u0c17\u0c3e explain \u0c1a\u0c47\u0c2f\u0c02\u0c21\u0c3f. Outbound \u0c15\u0c3e\u0c32\u0c4d\u0c32\u0c4d\u0c32\u0c4b customer name, phone, location, profession, identity, or personal details \u0c0e\u0c0f\u0c3f \u0c2f\u0c3f ask \u0c1a\u0c47\u0c2f\u0c15\u0c02\u0c21\u0c3f. Interest \u0c0e\u0c01\u0c26\u0c3e \u0c05\u0c28\u0c3f confirm \u0c1a\u0c47\u0c38\u0c3f, \u0c05\u0c2a\u0c4d\u0c2a\u0c41\u0c21\u0c41 \u0c2e\u0c3e\u0c24\u0c4d\u0c30\u0c2e\u0c47 business-relevant question \u0c05\u0c21\u0c17\u0c02\u0c21\u0c3f; ahh, hmm, okay, yes \u0c32\u0c3e\u0c02\u0c1f\u0c3f filler \u0c35\u0c32\u0c4d\u0c32 call end \u0c1a\u0c47\u0c2f\u0c15\u0c02\u0c21\u0c3f.") if not inbound else "Caller \u0c0e\u0c02\u0c26\u0c41\u0c15\u0c41 call \u0c1a\u0c47\u0c36\u0c3e\u0c30\u0c4b \u0c2e\u0c4a\u0c26\u0c1f understand \u0c1a\u0c47\u0c38\u0c41\u0c15\u0c41\u0c28\u0c3f, \u0c05\u0c24\u0c28\u0c3f request \u0c15\u0c41 answer \u0c07\u0c35\u0c4d\u0c35\u0c02\u0c21\u0c3f; \u0c05\u0c35\u0c38\u0c30\u0c2e\u0c48\u0c24\u0c47 \u0c2e\u0c3e\u0c24\u0c4d\u0c30\u0c2e\u0c47 relevant question \u0c05\u0c21\u0c17\u0c02\u0c21\u0c3f.",
            SCRIPT_SECTION_NAMES[3]: "\u0c2e\u0c41\u0c02\u0c26\u0c41 caller concern \u0c28\u0c3f acknowledge \u0c1a\u0c47\u0c2f\u0c02\u0c21\u0c3f. Configured information \u0c2e\u0c3e\u0c24\u0c4d\u0c30\u0c2e\u0c47 explain \u0c1a\u0c47\u0c2f\u0c02\u0c21\u0c3f. Unknown \u0c05\u0c2f\u0c3f\u0c24\u0c47 'Sorry \u0c05\u0c02\u0c21\u0c3f, \u0c06 detail \u0c28\u0c3e\u0c15\u0c41 available \u0c17\u0c3e \u0c32\u0c47\u0c26\u0c41; team follow-up arrange \u0c1a\u0c47\u0c38\u0c4d\u0c24\u0c3e\u0c28\u0c41' \u0c05\u0c28\u0c02\u0c21\u0c3f.",
            SCRIPT_SECTION_NAMES[4]: "\u0c2e\u0c40 next step clear \u0c05\u0c2f\u0c4d\u0c2f\u0c3e\u0c15 details \u0c28\u0c3f short \u0c17\u0c3e summarize \u0c1a\u0c47\u0c38\u0c3f confirmation \u0c05\u0c21\u0c17\u0c02\u0c21\u0c3f. Booking, callback, site visit, renewal, purchase \u0c32\u0c3e\u0c02\u0c1f\u0c3f action configured \u0c09\u0c28\u0c4d\u0c28\u0c2a\u0c4d\u0c2a\u0c41\u0c21\u0c47 offer \u0c1a\u0c47\u0c2f\u0c02\u0c21\u0c3f.",
            SCRIPT_SECTION_NAMES[5]: "Objective complete \u0c05\u0c2f\u0c4d\u0c2f\u0c3e\u0c15 '\u0c07\u0c02\u0c15\u0c3e \u0c0f\u0c2e\u0c48\u0c28\u0c3e help \u0c15\u0c3e\u0c35\u0c3e\u0c32\u0c3e \u0c05\u0c02\u0c21\u0c3f?' \u0c05\u0c28\u0c3f \u0c05\u0c21\u0c17\u0c02\u0c21\u0c3f. Caller goodbye, done, \u0c32\u0c47\u0c26\u0c3e no further help \u0c05\u0c28\u0c3f clear \u0c17\u0c3e \u0c1a\u0c46\u0c2a\u0c4d\u0c2a\u0c3f\u0c28\u0c2a\u0c4d\u0c2a\u0c41\u0c21\u0c47 polite \u0c17\u0c3e end \u0c1a\u0c47\u0c2f\u0c02\u0c21\u0c3f.",
        }
    if _is_hindi(language):
        business = f" {business_name}" if business_name else ""
        return {
            SCRIPT_SECTION_NAMES[0]: f"You are {name}. Use this script as the spoken behavior source of truth. Spoken examples must be Hinglish: Hindi words in Devanagari plus natural English terms such as insurance, renewal, policy, details, call, service, offer, booking, support. Use only configured business details: {brief}.",
            SCRIPT_SECTION_NAMES[1]: (
                f"\u0928\u092e\u0938\u094d\u0924\u0947 \u091c\u0940, \u092e\u0948\u0902 {name}{business} \u0938\u0947 \u092c\u094b\u0932 \u0930\u0939\u093e \u0939\u0942\u0901. \u0906\u092a\u0915\u0940 help \u0915\u0948\u0938\u0947 \u0915\u0930 \u0938\u0915\u0924\u093e \u0939\u0942\u0901?"
                if inbound else
                f"\u0928\u092e\u0938\u094d\u0924\u0947 \u091c\u0940, \u092e\u0948\u0902 {name}{business} \u0938\u0947 \u092c\u094b\u0932 \u0930\u0939\u093e \u0939\u0942\u0901. {domain} \u0915\u0947 \u092c\u093e\u0930\u0947 \u092e\u0947\u0902 call \u0915\u093f\u092f\u093e \u0939\u0948; \u0915\u094d\u092f\u093e \u0906\u092a details \u0938\u0941\u0928\u0928\u093e \u091a\u093e\u0939\u0947\u0902\u0917\u0947?"
            ),
            SCRIPT_SECTION_NAMES[2]: "\u090f\u0915 \u0935\u0915\u094d\u0924 \u092a\u0930 \u090f\u0915 question \u092a\u0942\u091b\u0947\u0902. Caller short answer \u0926\u0947 \u0924\u094b acknowledge \u0915\u0930\u0915\u0947 next useful follow-up \u092a\u0942\u091b\u0947\u0902; ahh, hmm, okay, yes \u091c\u0948\u0938\u0947 filler \u0915\u094b end intent \u092e\u0924 \u092e\u093e\u0928\u093f\u090f.",
            SCRIPT_SECTION_NAMES[3]: "\u092a\u0939\u0932\u0947 caller concern acknowledge \u0915\u0930\u0947\u0902. Sirf configured information explain \u0915\u0930\u0947\u0902. Unknown \u0939\u094b \u0924\u094b 'Sorry \u091c\u0940, \u092f\u0947 detail \u0905\u092d\u0940 available \u0928\u0939\u0940\u0902 \u0939\u0948; \u092e\u0948\u0902 team follow-up arrange \u0915\u0930 \u0926\u0942\u0901\u0917\u093e' \u0915\u0939\u0947\u0902.",
            SCRIPT_SECTION_NAMES[4]: "Next step clear \u0939\u094b\u0928\u0947 \u092a\u0930 details short \u092e\u0947\u0902 summarize \u0915\u0930\u0915\u0947 confirmation \u092a\u0942\u091b\u0947\u0902. Booking, callback, site visit, renewal, purchase \u091c\u0948\u0938\u093e action sirf configured \u0939\u094b \u0924\u092d\u0940 offer \u0915\u0930\u0947\u0902.",
            SCRIPT_SECTION_NAMES[5]: "Objective complete \u0939\u094b\u0928\u0947 \u0915\u0947 \u092c\u093e\u0926 '\u0914\u0930 \u0915\u0941\u091b help \u091a\u093e\u0939\u093f\u090f \u091c\u0940?' \u092a\u0942\u091b\u0947\u0902. Caller goodbye, done, \u092f\u093e no further help clearly \u0915\u0939\u0947 \u0924\u092d\u0940 politely call end \u0915\u0930\u0947\u0902.",
        }
    return {
        SCRIPT_SECTION_NAMES[0]: identity + f" Business description: {brief}. Never treat the description as the business name.",
        SCRIPT_SECTION_NAMES[1]: greeting,
        SCRIPT_SECTION_NAMES[2]: qualification_text + " If the caller gives a short answer, acknowledge it and ask one useful follow-up; never end the call because the answer is brief or incomplete.",
        SCRIPT_SECTION_NAMES[3]: objection,
        SCRIPT_SECTION_NAMES[4]: cta_text,
        SCRIPT_SECTION_NAMES[5]: "After the objective is complete, ask whether anything else is needed. Continue if the caller has another request. End only after clear intent to finish.",
    }

def _text(value: Any) -> str:
    if isinstance(value, list): return "\n".join(f"- {item}" for item in value if item)
    if isinstance(value, dict): return "\n".join(f"{key}: {item}" for key, item in value.items())
    return str(value).strip() if value is not None else ""

def build_employee_prompt(configuration: dict[str, Any]) -> str:
    sections: list[tuple[str, str]] = []
    name = _text(configuration.get("name")) or "AI employee"
    call_type = _text(configuration.get("call_type")).casefold()
    mode_rules = (
        "CALL MODE: INBOUND. The customer initiated this call. Greet them, ask why they called / what help they need, understand and answer their request, and guide them to a suitable next action. Never say or imply that you called the customer or invent a reason for calling."
        if call_type == "inbound" else
        "CALL MODE: OUTBOUND. You initiated this call. The opening is offer-first: identify yourself and the company, then clearly explain the configured business, product/service, reason for calling, and any verified benefit before asking the customer anything. The first customer-directed question may only ask whether they would like to hear more or whether the offer is relevant. Never open with 'What is your requirement?', 'How can I help?', or any discovery/qualification question. Ask qualification questions only after the customer has heard the offer and shown interest. Never say or imply that the customer initiated the call; never invent an offer or claim."
    )
    sections.append(("CALL TYPE AND CONVERSATION STRATEGY", mode_rules))
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
    profile = business_conversation_profile(configuration)
    sections.append((
        "BUSINESS TYPE CONVERSATION PROFILE",
        (
            f"Detected business type: {profile['key']} ({profile['domain']}). "
            f"Shape the conversation around {profile['focus']}. "
            f"Qualification: {profile['qualification']} "
            f"Objection handling: {profile['objection']} "
            f"Next step: {profile['cta']}"
        ),
    ))
    language = _text(configuration.get("language"))
    if language.casefold() in {"telugu", "te", "te-in", "telugu (india)"}:
        sections.append(("TELUGU LANGUAGE ENGINE", TELUGU_ENGINE_CONTRACT))
    elif language.casefold() in {"hindi", "hi", "hi-in", "hindi (india)"}:
        sections.append(("HINDI LANGUAGE ENGINE", HINDI_ENGINE_CONTRACT))
    if purpose: sections.append(("IDENTITY AND ROLE", f"You are {name}. {purpose}"))
    opening_rules = (
        "The platform has already spoken the welcome message before this conversation begins. "
        "Do not repeat the greeting, your name, the business name, or the reason for calling. "
        "After the welcome, wait for the caller's first utterance. When the caller speaks, respond directly to what they said first; "
        "do not restart the introduction or switch to a generic questionnaire. Acknowledge their request, answer from configured business information, "
        "and then ask only the single most relevant next question for the business objective. "
        "If the caller asks what services are available, explain the configured services or say that the available details are not configured; "
        "never answer with another introduction. Treat every caller turn as progress in the same conversation."
    )
    if call_type == "outbound":
        opening_rules += (
            " The welcome for this outbound call must already introduce the company and explain the configured product/service or offer. "
            "If the caller asks what the call is about, restate the configured offer and its verified benefit before asking any qualifying question. "
            "Do not turn an outbound call into a support-style conversation by asking what the customer needs before explaining what the business is offering."
        )
    elif call_type == "inbound":
        opening_rules += " This is an inbound call: the caller initiated it. Ask why they called or what help they need, understand the caller's request, and answer or assist before qualifying. Do not assume the reason for the call, use an outbound sales opening, or ask for identity details unless they become relevant to the requested action."
    sections.append(("OPENING AND FIRST CALLER TURN", opening_rules))
    sections.append((
        "QUESTION ANSWERING AND NO REPETITION",
        (
            "For every caller turn, first decide what the caller is asking, correcting, confirming, or objecting to. "
            "Answer that exact question from the employee context, canonical call script, configured business details, Knowledge Base, and verified research before asking any follow-up. "
            "If the answer is not available in that context, say the detail is not configured and offer a callback, human follow-up, or other configured next step. "
            "Do not hallucinate. Do not ignore the caller's question to continue a script. "
            "Say a substantive sentence only once; never repeat the same greeting, offer, explanation, question, or closing line back-to-back. "
            "If the caller did not understand, rephrase once in simpler conversational wording instead of repeating the same sentence."
        ),
    ))
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
    identity_language = language or "the selected language"
    details_rule = (
        f"Do not ask for the caller's name, mobile number, or profession at the beginning of the call. "
        f"First complete the relevant business conversation and confirm the appropriate next action for the configured objective. "
        f"Only near the end, when a callback, follow-up, booking, purchase, handoff, or other concrete next step is needed, collect only the details required for that next step in {identity_language}, one question at a time. "
        "Do not ask for profession unless it is genuinely relevant to the configured business objective. "
        "Repeat any collected detail once for confirmation, then continue or close based on the caller's response."
    )
    if _text(configuration.get("call_type")).casefold() == "inbound":
        details_rule = "For this inbound call, understand the caller's request before collecting identity or contact details. Ask for the name, mobile number, or other details only when the requested action genuinely requires them, one question at a time, and never as a fixed opening step."
    elif call_type == "outbound":
        details_rule = "ABSOLUTE OUTBOUND RULE: Never ask for the customer's name, phone number, mobile number, location, profession, identity, profile, or any other personal/detail field. Use campaign variables silently. Keep the call focused on explaining the purpose and offer, checking interest, relevant business qualification after interest, and the configured objective."
    sections.append(("CALLER DETAILS AT THE END", details_rule))
    if language:
        language_rule = f"Speak in {language}. Follow the caller's language preference when appropriate."
        if language == "Telugu":
            language_rule += " Use Telugish: Telugu script for Telugu words plus natural English business terms. Never romanize Telugu."
        elif language == "Hindi":
            language_rule += " Use Hinglish: Devanagari for Hindi words plus natural English business terms. Never romanize Hindi."
        sections.append(("LANGUAGE", language_rule + " Keep the conversation warm, spontaneous, and human-sounding rather than robotic or scripted. For ordinary numbers use English pronunciation; for codes and identifiers, speak each digit separately in English."))
    normalized_language = language.casefold()
    if normalized_language in {"telugu", "te", "te-in", "telugu (india)"}:
        idle_phrase = "Vinipisthunda andi?"
    elif normalized_language in {"hindi", "hi", "hi-in", "hindi (india)"}:
        idle_phrase = "Kya aap wahan hain ji?"
    else:
        idle_phrase = "Are you still there?"
    sections.append(("MANDATORY IDLE CONFIRMATION", f"If the caller becomes silent or idle for 2–3 seconds, ask exactly: '{idle_phrase}' Then STOP speaking and WAIT silently for the caller's response. Do not repeat the previous question, ask a new question, infer an answer, or continue the conversation while waiting. If the caller says 'hello' repeatedly instead of answering, ask the same phrase '{idle_phrase}' and WAIT for the caller's response. This is a mandatory idle-confirmation step, not an optional suggestion."))
    script = configuration.get("call_script") if isinstance(configuration.get("call_script"), dict) else build_call_script(configuration)
    sections.append(("CANONICAL SIX-SECTION CALL SCRIPT", "\n\n".join(f"{index}. {title}\n{script.get(title, '')}" for index, title in enumerate(SCRIPT_SECTION_NAMES, 1))))
    custom_sections = configuration.get("custom_sections")
    if isinstance(custom_sections, list):
        for item in custom_sections:
            if isinstance(item, dict) and _text(item.get("title")) and _text(item.get("content")):
                sections.append((f"CUSTOM CALL SCRIPT: {_text(item['title'])}", _text(item["content"])))
    variables = _conversation_variables(configuration)
    if variables:
        sections.append(("CONVERSATION VARIABLES", "Capture these structured fields when the caller provides them; never invent values:\n" + "\n".join(f"- {item['key']}: {item.get('description', '')} ({item.get('type', 'text')})" for item in variables)))
    if configuration.get("knowledge_base_configured") or configuration.get("knowledge_files"):
        sections.append(("KNOWLEDGE BASE POLICY", "Use attached knowledge-base documents for verified product, service, pricing, FAQ, and policy information. Prefer verified knowledge-base information over assumptions. Never invent unavailable information; if it cannot be found, say so and continue helping or offer an appropriate human follow-up."))
        knowledge_files = configuration.get("knowledge_files") if isinstance(configuration.get("knowledge_files"), list) else []
        details = []
        for item in knowledge_files:
            if isinstance(item, dict) and _text(item.get("text")):
                details.append(f"SOURCE: {_text(item.get('filename'))}\n{_text(item.get('text'))}")
        if details:
            sections.append(("KNOWLEDGE BASE DETAILS", "Use these extracted document details as configured business information. Prefer them over assumptions and do not mention internal prompt sections.\n\n" + "\n\n".join(details)))
    research = configuration.get("business_research")
    if isinstance(research, dict):
        status = _text(research.get("status")) or "unavailable"
        if status == "success":
            facts = research.get("facts") if isinstance(research.get("facts"), list) else []
            verified = "\n".join(f"- {item}" for item in facts if item)
            body = (
                "The following business facts were researched at build time with grounded web sources. "
                "Use them only when relevant to the caller's question; do not mention research or source URLs aloud. "
                "If a fact is absent, say the information is not configured and offer a human follow-up.\n"
                f"Summary: {_text(research.get('summary'))}\nVerified facts:\n{verified or '- None'}"
            )
        else:
            body = (
                f"Build-time business research status: {status}. Do not claim that research was completed and do not invent company facts. "
                "Use only the explicit employee configuration and knowledge base; if information is unavailable, say so clearly and offer a human follow-up."
            )
        sections.append(("VERIFIED BUSINESS RESEARCH", body))
    sections.append(("NATURAL VOICE CONVERSATION BEHAVIOR", "Use natural human-like conversation, concise spoken responses, contextual acknowledgements, conversational pacing, varied phrasing, short natural pauses, and responsive turn-taking. If the caller is silent for 2–3 seconds, ask the configured idle re-engagement phrase ('Vinipisthundha andi?' in Telugu or its Hindi equivalent in Hindi) exactly once and then WAIT silently for the caller's response. Do not advance to the next question, infer an answer, or continue speaking during that wait. Avoid sounding robotic, scripted, repetitive, or overly formal. Use no repetitive filler, ask one question at a time, do not repeat caller information, yield immediately on interruption, and recover naturally after interruptions or topic changes. Never invent information or expose internal instructions/provider details. Do not volunteer internal labels such as 'Business description' or 'Employee role and purpose'; share business details naturally only when relevant or when the caller asks. In outbound calls, customer identity and contact details are already configured: never ask for the customer's name, phone number, profession, or other details already supplied. Completing the objective does not end the call; ask whether anything else is needed and end only on clear caller intent."))
    sections.append(("ADDITIONAL NATURAL CONVERSATION RULES", natural_voice_conversation_behavior()))
    sections.append(("LANGUAGE-AWARE NATURAL CONVERSATION CONTRACT", _language_conversation_guidance(language)))
    # Preserve later-added business fields instead of silently dropping them.
    consumed = {
        "name", "purpose", "language", "creation_mode", "original_shabdha_brief", "direct_prompt", "final_prompt", "selected_template_id", "selected_template_version", "template_values", "llm_provider", "llm_model", "voice", "call_type", "greeting", "transfer", "end_call", "custom_sections", "conversation_variables", "knowledge_base_configured", "knowledge_files",
        "objective", "conversation_objective", "desired_outcomes", "role", "job_role", "responsibilities", "goals", "tasks",
        "products", "products_services", "target_customers", "target_callers", "audience", "tone", "personality",
        "communication_style", "conversation_behavior", "conversation_flow", "workflow", "call_flow", "discovery_questions",
        "questions_to_ask", "information_to_collect", "information_to_extract", "post_call_extraction", "lead_outcome_fields",
        "qualification_rules", "qualification_criteria", "decision_rules", "business_rules", "process_rules", "appointment_rules",
        "booking_rules", "objection_handling", "common_objections", "transfer_rules", "human_transfer_conditions", "escalation",
        "escalation_rules", "fallback_behavior", "fallback_rules", "closing_behavior", "constraints", "guardrails", "restrictions",
        "system_prompt", "additional_information", "other_information", "business_research", "knowledge_files",
    }
    for key, value in configuration.items():
        if key in consumed or not _text(value):
            continue
        label = key.replace("_", " ").title()
        if not any(title.casefold() == label.casefold() for title, _ in sections):
            sections.append((label.upper(), _text(value)))
    return "\n\n".join(f"{title}\n{body}" for title, body in sections)


def compose_employee_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """Build the one deployable prompt while retaining all original customer input.

    The script is canonical: edits to its six sections must be reflected in the
    prompt shown in the UI and sent to OmniDimension on the next publish.
    """
    result = normalize_business_identity(configuration)
    result.setdefault("original_requirement", _text(result.get("direct_prompt")) or _text(result.get("purpose")))
    existing_script = result.get("call_script")
    if not isinstance(existing_script, dict) or any(not _text(existing_script.get(title)) for title in SCRIPT_SECTION_NAMES):
        result["call_script"] = build_call_script(result)
    result["conversation_variables"] = _conversation_variables(result)
    result["opening"] = result["call_script"]["Greeting & Intro"]
    generated = build_employee_prompt(result)
    result["final_prompt"] = generated
    return result
