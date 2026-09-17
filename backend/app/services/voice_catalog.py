from __future__ import annotations
import json
import logging
from typing import Any
from app.core.config import get_settings

logger = logging.getLogger(__name__)

BUILTIN_VOICES = [{
    "id": "standard_cartesia_charan_clear_concierge",
    "name": "Charan - Clear Concierge",
    "tier": "standard",
    "gender": "unspecified",
    "provider": "cartesia",
    "provider_voice_id": "82c2afc8-ebbc-4802-8ccf-036dc0fa1e3b",
    "supports_cloning": False,
    "languages": ["English", "English (India)"],
    "is_cloned": False,
}, {
    "id": "cloned_cartesia_ramana",
    "name": "Ramana",
    "tier": "cloned",
    "gender": "male",
    "provider": "cartesia",
    "provider_voice_id": "9242c388-deef-42b1-b4dd-20552eff448b",
    "supports_cloning": True,
    "languages": ["Telugu"],
    "is_cloned": True,
}, {
    "id": "cloned_cartesia_rajeev",
    "name": "Rajeev",
    "tier": "cloned",
    "gender": "male",
    "provider": "cartesia",
    "provider_voice_id": "ea2ccf3e-0845-4af0-b23f-b625aae59c4f",
    "supports_cloning": True,
    "languages": ["Hindi"],
    "is_cloned": True,
}, {
    "id": "cloned_cartesia_anu",
    "name": "Anu",
    "tier": "cloned",
    "gender": "female",
    "provider": "cartesia",
    "provider_voice_id": "32ecc152-52ae-43ea-ace2-c12f5ca5c506",
    "supports_cloning": True,
    "languages": ["Hindi"],
    "is_cloned": True,
}, {
    "id": "cloned_cartesia_samantha",
    "name": "Samantha",
    "tier": "cloned",
    "gender": "female",
    "provider": "cartesia",
    "provider_voice_id": "ad2c6cbc-403e-4e3b-91be-8e8bc5765e85",
    "supports_cloning": True,
    "languages": ["Hindi"],
    "is_cloned": True,
}, {
    "id": "cloned_cartesia_manoj",
    "name": "Manoj",
    "tier": "cloned",
    "gender": "male",
    "provider": "cartesia",
    "provider_voice_id": "e205ebb9-6f8a-4773-b03d-8dae3dbeb51f",
    "supports_cloning": True,
    "languages": ["Telugu"],
    "is_cloned": True,
}]

def voice_catalog() -> list[dict[str, Any]]:
    raw = getattr(get_settings(), "omnidimension_voice_catalog_json", "") or ""
    if not raw.strip(): return BUILTIN_VOICES.copy()
    try: values = json.loads(raw)
    except json.JSONDecodeError: return BUILTIN_VOICES.copy()
    if not isinstance(values, list): return BUILTIN_VOICES.copy()
    configured = []
    for item in values:
        if not isinstance(item, dict) or not item.get("id") or not item.get("provider_voice_id"):
            continue
        is_cloned = bool(item.get("is_cloned", False) or str(item.get("tier", "")).casefold() in {"cloned", "custom"})
        status = str(item.get("status", "ready")).strip().casefold()
        if is_cloned and status != "ready":
            continue
        languages = list(item.get("languages") or item.get("supported_languages") or [])
        languages = ["Telugu" if str(language).casefold() in {"te", "tel", "telugu"} else str(language) for language in languages]
        configured.append({
            "id": str(item["id"]), "name": str(item.get("name", item["id"])),
            "tier": str(item.get("tier", "basic")), "gender": str(item.get("gender", "unspecified")),
            "provider": str(item.get("provider", "google")), "provider_voice_id": str(item["provider_voice_id"]),
            "supports_cloning": bool(item.get("supports_cloning", False)), "languages": languages,
            "is_cloned": is_cloned, "status": status, "available": True,
        })
    known = {item["id"] for item in configured}
    return configured + [item for item in BUILTIN_VOICES if item["id"] not in known]


def _dynamic_cloned_voices() -> list[dict[str, Any]]:
    """Return account-owned clones only when a safe provider source exists.

    Omni's global provider catalog is intentionally not queried here. The
    dashboard's account-clone route requires dashboard-session authentication
    and is not available through Pontis's server API key.
    """
    logger.info("Omni account cloned voice discovery unavailable reason=dashboard_clone_endpoint_requires_session_auth_not_server_api_key")
    return []


def public_voice_catalog() -> list[dict[str, Any]]:
    combined = voice_catalog() + [item for item in _dynamic_cloned_voices() if item["id"] not in {v["id"] for v in voice_catalog()}]
    return [{key: value for key, value in item.items() if key != "provider_voice_id"} for item in combined]


def provider_voice_id(voice_id: str) -> str | None:
    combined = voice_catalog() + _dynamic_cloned_voices()
    return next((item["provider_voice_id"] for item in combined if item["id"] == voice_id), None)


def voice_definition(voice_id: str) -> dict[str, Any] | None:
    combined = voice_catalog() + _dynamic_cloned_voices()
    return next((item for item in combined if item["id"] == voice_id), None)


def clear_provider_voice_cache() -> None:
    """Compatibility no-op retained for callers that clear provider caches."""
    return None
