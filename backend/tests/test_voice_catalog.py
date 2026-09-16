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

    assert len(response) == 5
    charan = next(item for item in response if item["name"] == "Charan - Clear Concierge")
    assert charan["provider"] == "cartesia"
    assert "provider_voice_id" not in charan
    assert [item["name"] for item in response] == ["Achird", "Aoede", "Charon", "Kore", "Charan - Clear Concierge"]
    assert all("id" in item and "provider_voice_id" not in item for item in response)
    assert all({"name", "tier", "gender", "provider", "supports_cloning"} <= item.keys() for item in response)
