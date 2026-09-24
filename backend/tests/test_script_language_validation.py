import pytest

from app.services.employee_prompt import SCRIPT_SECTION_NAMES
from app.services.script_language_validation import validate_customer_facing_script
from app.api.v1.endpoints.employees import _upgrade_legacy_assembled_script, _validate_publish, _validate_script_language_or_422


def script(text: str) -> dict[str, str]:
    return {section: text for section in SCRIPT_SECTION_NAMES}


def rendered_generated_script(spoken: str) -> dict[str, str]:
    return {
        section: "\n".join((
            "Purpose: Deliver the requested call objective using the saved user context.",
            "Instructions: Use only saved facts and approved call rules.",
            f"Spoken example: {spoken}",
            "Handling: Stop respectfully when asked to stop.",
        ))
        for section in SCRIPT_SECTION_NAMES
    }


def legacy_assembled_script(spoken: str) -> dict[str, str]:
    return {
        section: "\n".join((
            "Purpose: Deliver the requested call objective using the saved user context.",
            "Instructions: Use only the saved user context and approved call rules. Do not invent missing details.",
            f"Spoken example: {spoken}",
            "Handling: Use the configured guardrails, disclose that the caller is an AI assistant when asked, and stop respectfully if asked to stop.",
        ))
        for section in SCRIPT_SECTION_NAMES
    }


@pytest.mark.parametrize("text", [
    "నమస్కారం అండి, నేను KMG Insurance నుంచి మాట్లాడుతున్నాను. మీ policy renewal గురించి quick call చేశాను.",
    "మీకు మా product గురించి details కావాలంటే నేను explain చేస్తాను.",
    "ఈ offer గురించి మీకు interest ఉందా?",
    "మీ phone number 9876543210 కి policy details WhatsApp చేస్తాను.",
    "₹25,000 premium July 15 లోపు pay చేస్తే renewal active ఉంటుంది.",
])
def test_telugu_allows_native_script_with_english_business_terms_numbers_prices_dates(text):
    result = validate_customer_facing_script(script(text), "Telugu")
    assert result.valid, result.as_dict()


@pytest.mark.parametrize("text", [
    "Nenu KMG Insurance nundi maatladutunnanu. Mee policy renewal gurinchi call chesanu.",
    "నమస్కారం అండి, nenu KMG nundi maatladutunnanu, mee policy gurinchi call chesanu.",
    "This is a customer-facing conversation about insurance renewal and support details.",
])
def test_telugu_rejects_romanized_or_english_only_conversation(text):
    result = validate_customer_facing_script(script(text), "Telugu")
    assert not result.valid
    assert result.issues[0].type in {"romanized_language", "english_only", "insufficient_native_script"}


@pytest.mark.parametrize("text", [
    "नमस्ते जी, मैं KMG Insurance से बोल रहा हूँ. आपकी insurance renewal के बारे में call किया है.",
    "आपको product details चाहिए तो मैं explain कर सकता हूँ.",
    "₹25,000 premium July 15 तक pay कर दीजिए, policy active रहेगी.",
])
def test_hindi_allows_devanagari_with_english_business_terms_numbers_prices_dates(text):
    result = validate_customer_facing_script(script(text), "Hindi")
    assert result.valid, result.as_dict()


@pytest.mark.parametrize("text", [
    "Namaste ji, main KMG Insurance se bol raha hoon. Aapki renewal ke baare mein call kiya hai.",
    "नमस्ते जी, main KMG se bol raha hoon, aapki policy ke liye call kiya hai.",
])
def test_hindi_rejects_roman_hindi(text):
    result = validate_customer_facing_script(script(text), "Hindi")
    assert not result.valid
    assert any(issue.type == "romanized_language" for issue in result.issues)


@pytest.mark.parametrize("text", [
    "Hi, this is Ava from KMG Insurance about policy renewal. Would you like details?",
    "Please visit https://example.com or email support@example.com with product code HP230.",
])
def test_english_accepts_business_terms_urls_emails_codes(text):
    result = validate_customer_facing_script(script(text), "English")
    assert result.valid, result.as_dict()


def test_publish_validation_ignores_english_internal_metadata_in_rendered_telugu_cards():
    call_script = rendered_generated_script(
        "Hello అండి, AI assistant గా wedding invitation కోసం call చేస్తున్నాను."
    )
    configuration = {"language": "Telugu", "call_script": call_script}

    # This is the same helper used by the publish endpoint.  Purpose,
    # Instructions, and Handling are internal English metadata, not speech.
    _validate_script_language_or_422(configuration)
    assert validate_customer_facing_script(call_script, "Telugu").valid


def test_rendered_card_still_rejects_roman_telugu_in_the_spoken_example():
    call_script = rendered_generated_script(
        "Hello andi, nenu wedding invitation kosam call chestunnanu."
    )

    result = validate_customer_facing_script(call_script, "Telugu")
    assert not result.valid
    assert any(issue.type == "romanized_language" for issue in result.issues)


def test_publish_upgrades_only_the_known_legacy_assembled_telugu_fallback():
    configuration = {
        "script_source": "assembled",
        "original_requirement": "call contacts about my wedding on 15th of jan 2027",
        "host_name": "Vaibhav and Muskan",
        "language": "Telugu",
        "call_type": "outbound",
        "call_script": legacy_assembled_script(
            "నమస్కారం అండి, Vaibhav and Muskan gari behalf lo AI assistant గా wedding గురించి call చేస్తున్నాను."
        ),
    }

    upgraded = _upgrade_legacy_assembled_script(configuration)
    spoken = "\n".join(upgraded["call_script"].values())

    assert upgraded["assembled_script_version"] == 2
    assert "gari behalf lo" not in spoken.casefold()
    assert "Vaibhav and Muskan" not in "\n".join(example for section in upgraded["conversation_sections"] for example in section["examples"])
    assert upgraded["script_review_required"] is True
    assert not validate_customer_facing_script(upgraded["call_script"], "Telugu").valid


def test_review_only_draft_cannot_publish_until_all_six_spoken_examples_are_written():
    from types import SimpleNamespace
    from fastapi import HTTPException

    config = {
        "script_source": "reviewed", "assembled_source_context": "Explain my request",
        "name": "Agent", "llm_provider": "groq", "llm_model": "test-model",
        "language": "English", "call_type": "outbound", "purpose": "Explain my request",
        "call_script": {f"Section {index}": "Purpose: Draft\nInstructions: Write the speech\nHandling: Respect refusal" for index in range(6)},
    }
    with pytest.raises(HTTPException) as incomplete:
        _validate_publish(SimpleNamespace(), SimpleNamespace(configuration=config))
    assert incomplete.value.status_code == 422
    assert "spoken example" in incomplete.value.detail

    config["call_script"] = {f"Section {index}": f"Purpose: Call\nSpoken example: This is line {index}.\nHandling: Respect refusal" for index in range(6)}
    _validate_publish(SimpleNamespace(), SimpleNamespace(configuration=config))
