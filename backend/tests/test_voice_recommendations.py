from app.services.voice_recommendations import recommend_voices


VOICES = [
    {"id": "cloned_female", "name": "Private female", "tier": "cloned", "gender": "female", "languages": ["Telugu"], "is_cloned": True, "status": "ready"},
    {"id": "cloned_male", "name": "Private male", "tier": "cloned", "gender": "male", "languages": ["te"], "is_cloned": True, "status": "ready"},
    {"id": "standard_english", "name": "English standard", "tier": "standard", "gender": "female", "languages": ["English"], "status": "ready"},
]


def test_telugu_recommendations_include_all_ready_clones():
    result = recommend_voices(VOICES, language="Telugu")
    assert {item["id"] for item in result[:2]} == {"cloned_female", "cloned_male"}


def test_gender_preference_prioritizes_matching_clone():
    assert recommend_voices(VOICES, language="te", requirement="female Telugu voice")[0]["id"] == "cloned_female"
    assert recommend_voices(VOICES, language="Telugu", gender="male")[0]["id"] == "cloned_male"


def test_no_gender_preference_keeps_both_and_excludes_wrong_language():
    result = recommend_voices(VOICES, language="Telugu")
    assert {item["id"] for item in result} == {"cloned_female", "cloned_male"}


def test_future_catalog_clone_is_automatically_eligible():
    future = {"id": "cloned_future", "name": "Future", "tier": "cloned", "gender": "unspecified", "languages": ["Telugu"], "is_cloned": True, "status": "ready"}
    assert any(item["id"] == "cloned_future" for item in recommend_voices([future], language="Telugu"))


def test_not_ready_voice_is_not_recommended():
    pending = {"id": "pending", "tier": "cloned", "is_cloned": True, "languages": ["Telugu"], "status": "processing"}
    assert recommend_voices([pending], language="Telugu") == []
