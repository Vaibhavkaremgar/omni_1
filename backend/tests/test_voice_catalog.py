import json

from app.services import voice_catalog as voice_catalog_service


def test_public_voice_catalog_preserves_frontend_contract(monkeypatch):
    configured = [
        {"id": "basic_google_achird", "name": "Achird", "tier": "basic", "gender": "male", "provider": "google", "provider_voice_id": "provider-achird"},
        {"id": "basic_google_aoede", "name": "Aoede", "tier": "basic", "gender": "female", "provider": "google", "provider_voice_id": "provider-aoede"},
        {"id": "standard_google_charon", "name": "Charon", "tier": "standard", "gender": "male", "provider": "google", "provider_voice_id": "provider-charon"},
        {"id": "standard_google_kore", "name": "Kore", "tier": "standard", "gender": "female", "provider": "google", "provider_voice_id": "provider-kore"},
    ]
    monkeypatch.setattr(
        voice_catalog_service,
        "get_settings",
        lambda: type("Settings", (), {"omnidimension_voice_catalog_json": json.dumps(configured)})(),
    )

    response = voice_catalog_service.public_voice_catalog()

    assert len(response) == 6
    charan = next(item for item in response if item["name"] == "Charan - Clear Concierge")
    assert charan["provider"] == "cartesia"
    assert "provider_voice_id" not in charan
    assert [item["name"] for item in response] == ["Achird", "Aoede", "Charon", "Kore", "Charan - Clear Concierge", "Ramana"]
    assert all("id" in item and "provider_voice_id" not in item for item in response)
    assert all({"name", "tier", "gender", "provider", "supports_cloning"} <= item.keys() for item in response)


def test_global_provider_catalog_is_not_treated_as_account_cloned_voices(monkeypatch):
    class Settings:
        omnidimension_voice_catalog_json = "[]"
        omnidimension_api_key = "configured-but-not-used"

    class MustNotBeCalled:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("global provider catalog must not be used for clone discovery")

    monkeypatch.setattr(voice_catalog_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(voice_catalog_service, "OmniDimensionClient", MustNotBeCalled, raising=False)

    assert voice_catalog_service.public_voice_catalog() == [
        {key: value for key, value in voice_catalog_service.BUILTIN_VOICES[0].items() if key != "provider_voice_id"},
        {key: value for key, value in voice_catalog_service.BUILTIN_VOICES[1].items() if key != "provider_voice_id"},
    ]


def test_builtin_ramana_voice_is_available_without_catalog_configuration(monkeypatch):
    monkeypatch.setattr(
        voice_catalog_service,
        "get_settings",
        lambda: type("Settings", (), {"omnidimension_voice_catalog_json": ""})(),
    )

    ramana = next(item for item in voice_catalog_service.public_voice_catalog() if item["id"] == "cloned_cartesia_ramana")
    assert ramana["name"] == "Ramana"
    assert ramana["provider"] == "cartesia"
    assert ramana["is_cloned"] is True
    assert voice_catalog_service.provider_voice_id("cloned_cartesia_ramana") == "a56d7710-2e82-4522-b6d5-e3f2786630f9"


def test_configured_cloned_voices_are_normalized_and_resolved(monkeypatch):
    configured = [
        {"id": "cloned_cartesia_chitra", "name": "Chitra", "tier": "cloned", "gender": "female",
         "provider": "cartesia", "provider_voice_id": "3bcefec6-7e75-4a7d-8181-233c0d05cdb3",
         "supports_cloning": True, "languages": ["te"], "is_cloned": True},
        {"id": "cloned_cartesia_ramu", "name": "Ramu", "tier": "cloned", "gender": "male",
         "provider": "cartesia", "provider_voice_id": "5f8badc5-3e2e-4b97-bea8-109f0515639b",
         "supports_cloning": True, "languages": ["Telugu"], "is_cloned": True},
        {"id": "cloned_not_ready", "name": "Processing", "tier": "cloned", "status": "processing",
         "provider": "cartesia", "provider_voice_id": "not-ready", "is_cloned": True},
        {"id": "invalid_clone", "name": "Invalid", "tier": "cloned", "is_cloned": True},
    ]
    monkeypatch.setattr(
        voice_catalog_service,
        "get_settings",
        lambda: type("Settings", (), {"omnidimension_voice_catalog_json": json.dumps(configured)})(),
    )

    catalog = voice_catalog_service.public_voice_catalog()
    chitra = next(item for item in catalog if item["id"] == "cloned_cartesia_chitra")
    ramu = next(item for item in catalog if item["id"] == "cloned_cartesia_ramu")
    assert chitra["is_cloned"] is True and chitra["tier"] == "cloned"
    assert chitra["languages"] == ["Telugu"] and "provider_voice_id" not in chitra
    assert ramu["gender"] == "male"
    assert not any(item["id"] in {"cloned_not_ready", "invalid_clone"} for item in catalog)
    assert voice_catalog_service.provider_voice_id("cloned_cartesia_chitra") == "3bcefec6-7e75-4a7d-8181-233c0d05cdb3"
    assert voice_catalog_service.provider_voice_id("cloned_cartesia_ramu") == "5f8badc5-3e2e-4b97-bea8-109f0515639b"
