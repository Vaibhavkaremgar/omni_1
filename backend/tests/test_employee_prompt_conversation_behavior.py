from app.services.employee_prompt import SCRIPT_SECTION_NAMES, build_call_script, build_employee_prompt


def prompt(language, **extra):
    config = {"name": "Asha", "language": language, "purpose": "help customers choose residential plots"}
    config.update(extra)
    return build_employee_prompt(config)


def test_telugu_contract_has_natural_acknowledgements_and_variation():
    result = prompt("Telugu")
    assert "sare andi" in result and "ok andi" in result
    assert "inka" in result
    assert "thanks andi" in result
    assert "Never translate thanks into Telugu" in result
    assert "never mechanically repeat" in result.lower()
    assert "acknowledge" in result.lower()


def test_hindi_contract_is_language_specific_and_excludes_telugu_fillers():
    result = prompt("Hindi")
    assert "haan ji" in result and "samajh gaya ji" in result
    assert "Never use Telugu fillers" in result
    assert "sare andi" not in result


def test_contract_covers_silence_questions_grounding_and_codes():
    result = prompt("Telugu")
    lowered = result.lower()
    for phrase in ("2–3 seconds", "unrelated questions", "verified company research", "digit-by-digit", "english"):
        assert phrase.lower() in lowered
    assert "model numbers" in lowered
    assert "230 must be spoken as 'two three zero'" in result


def test_prompt_preserves_canonical_six_section_script_and_existing_knowledge_base():
    result = prompt("Hindi", knowledge_base_configured=True, call_script={name: f"configured {name}" for name in SCRIPT_SECTION_NAMES})
    assert "CANONICAL SIX-SECTION CALL SCRIPT" in result
    assert all(f"configured {name}" in result for name in SCRIPT_SECTION_NAMES)
    assert "KNOWLEDGE BASE POLICY" in result


def test_prompt_and_script_change_with_business_type():
    insurance = {
        "name": "Asha",
        "language": "Telugu",
        "call_type": "outbound",
        "business_name": "KMG Insurance",
        "business_type": "insurance",
        "purpose": "Call customers whose renewal date is within 7 days.",
    }
    real_estate = {
        "name": "Asha",
        "language": "Telugu",
        "call_type": "outbound",
        "business_name": "Urban Nest",
        "business_type": "real estate",
        "purpose": "Follow up with customers interested in residential plots.",
    }

    insurance_prompt = build_employee_prompt(insurance)
    real_estate_prompt = build_employee_prompt(real_estate)
    assert "insurance policy support" in insurance_prompt
    assert "policy type, renewal date, premium/payment status" in insurance_prompt
    assert "real estate lead follow-up" in real_estate_prompt
    assert "property type, location, budget range" in real_estate_prompt
    assert insurance_prompt != real_estate_prompt

    insurance_script = build_call_script(insurance)
    real_estate_script = build_call_script(real_estate)
    assert "insurance, policy, renewal, premium" in insurance_script["Identity & Purpose"]
    assert "location, budget, project" in real_estate_script["Identity & Purpose"]
    assert insurance_script["Greeting & Intro"] != real_estate_script["Greeting & Intro"]


def test_prompt_requires_contextual_question_answering_without_repetition():
    result = prompt(
        "Telugu",
        business_name="Charan Care Hospital",
        business_type="hospital",
        knowledge_base_configured=True,
    )
    assert "QUESTION ANSWERING AND NO REPETITION" in result
    assert "first decide what the caller is asking" in result or "first identify what the caller is asking" in result
    assert "Answer that exact question from the employee context" in result
    assert "Say a substantive sentence only once" in result
    assert "never repeat the same greeting, offer, explanation, question, or closing line back-to-back" in result
