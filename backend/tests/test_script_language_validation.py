import pytest

from app.services.employee_prompt import SCRIPT_SECTION_NAMES
from app.services.script_language_validation import validate_customer_facing_script


def script(text: str) -> dict[str, str]:
    return {section: text for section in SCRIPT_SECTION_NAMES}


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
