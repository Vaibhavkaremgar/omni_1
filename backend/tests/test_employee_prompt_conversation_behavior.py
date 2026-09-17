from app.services.employee_prompt import SCRIPT_SECTION_NAMES, build_employee_prompt


def prompt(language, **extra):
    config = {"name": "Asha", "language": language, "purpose": "help customers choose residential plots"}
    config.update(extra)
    return build_employee_prompt(config)


def test_telugu_contract_has_natural_acknowledgements_and_variation():
    result = prompt("Telugu")
    assert "sare andi" in result and "ok andi" in result
    assert "inka" in result
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


def test_prompt_preserves_canonical_six_section_script_and_existing_knowledge_base():
    result = prompt("Hindi", knowledge_base_configured=True, call_script={name: f"configured {name}" for name in SCRIPT_SECTION_NAMES})
    assert "CANONICAL SIX-SECTION CALL SCRIPT" in result
    assert all(f"configured {name}" in result for name in SCRIPT_SECTION_NAMES)
    assert "KNOWLEDGE BASE POLICY" in result
