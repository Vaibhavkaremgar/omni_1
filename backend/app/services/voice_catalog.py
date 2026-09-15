from __future__ import annotations
import json
from typing import Any
from app.core.config import get_settings

def voice_catalog() -> list[dict[str, Any]]:
    raw = getattr(get_settings(), "omnidimension_voice_catalog_json", "") or ""
    if not raw.strip(): return []
    try: values = json.loads(raw)
    except json.JSONDecodeError: return []
    if not isinstance(values, list): return []
    return [{"id": str(x["id"]), "name": str(x.get("name", x["id"])), "tier": str(x.get("tier", "basic")), "gender": str(x.get("gender", "unspecified")), "provider": str(x.get("provider", "google")), "provider_voice_id": str(x["provider_voice_id"]), "supports_cloning": bool(x.get("supports_cloning", False))} for x in values if isinstance(x, dict) and x.get("id") and x.get("provider_voice_id")]


def public_voice_catalog() -> list[dict[str, Any]]:
    return [{key: value for key, value in item.items() if key != "provider_voice_id"} for item in voice_catalog()]


def provider_voice_id(voice_id: str) -> str | None:
    return next((item["provider_voice_id"] for item in voice_catalog() if item["id"] == voice_id), None)
