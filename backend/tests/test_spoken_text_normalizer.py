from app.services.spoken_text_normalizer import normalize_spoken_text


def test_alphanumeric_product_name_is_preserved_with_english_number_word():
    assert normalize_spoken_text("Tell me about V2 Studio.") == "Tell me about V two Studio."
    assert normalize_spoken_text("A2 X200 V2.0") == "A two X two hundred V two point zero"


def test_quantities_and_mixed_language_text():
    assert normalize_spoken_text("22 locations and 2 years") == "twenty two locations and two years"
    assert normalize_spoken_text("మీకు 2 years experience ఉందా?") == "మీకు two years experience ఉందా?"
    assert normalize_spoken_text("आपके पास 3 years experience है?") == "आपके पास three years experience है?"


def test_phone_and_otp_are_digit_by_digit():
    assert normalize_spoken_text("OTP 482931") == "OTP four eight two nine three one"
    assert normalize_spoken_text("9876543210") == "nine eight seven six five four three two one zero"


def test_normalizer_is_idempotent_and_does_not_touch_structured_values():
    text = "V two Studio has twenty two locations."
    assert normalize_spoken_text(text) == text
    assert normalize_spoken_text(normalize_spoken_text(text)) == text
    record = {"price": 22000, "id": 42, "spoken": "V2 Studio"}
    assert record["price"] == 22000 and record["id"] == 42
