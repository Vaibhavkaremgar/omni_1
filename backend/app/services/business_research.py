"""Build-time, grounded company research for an employee version."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import re
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def research_fingerprint(configuration: dict[str, Any]) -> str:
    source = "\n".join(str(configuration.get(key) or "").strip() for key in ("business_name", "business_description"))
    return hashlib.sha256(source.casefold().encode("utf-8")).hexdigest()


def ensure_business_research(configuration: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    """Reuse a matching snapshot or research once during publish/build."""
    config = dict(configuration)
    fingerprint = research_fingerprint(config)
    existing = config.get("business_research")
    if isinstance(existing, dict) and existing.get("fingerprint") == fingerprint:
        return config

    business_name = str(config.get("business_name") or "").strip()
    description = str(config.get("business_description") or "").strip()
    if not business_name:
        config["business_research"] = {"status": "unavailable", "reason": "business_name_missing", "fingerprint": fingerprint}
        return config

    settings = get_settings()
    if not settings.gemini_api_key:
        logger.warning("Business research unavailable reason=gemini_api_key_missing business_name=%s", business_name[:120])
        config["business_research"] = {"status": "unavailable", "reason": "gemini_api_key_missing", "fingerprint": fingerprint}
        return config

    prompt = (
        "Research this business using Google Search grounding. Prefer the official company website and authoritative sources. "
        "Return JSON only with keys summary (string), facts (array of strings), and sources (array of objects with title and uri). "
        "Do not guess. If a fact is not verified, omit it.\n\n"
        f"Company name: {business_name}\nBusiness description: {description}\n"
        f"Original requirement: {config.get('original_requirement') or ''}\n"
        f"Employee role/purpose: {config.get('role') or config.get('job_role') or config.get('purpose') or ''}\n"
        f"Language: {config.get('language') or ''}\nCall type: {config.get('call_type') or ''}"
    )
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    http = client or httpx.Client(timeout=settings.gemini_research_timeout_seconds)
    try:
        response = http.post(
            f"{settings.gemini_base_url.rstrip('/')}/models/{settings.gemini_model}:generateContent",
            headers={"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        raw = response.json()
        text = "".join(part.get("text", "") for part in raw.get("candidates", [{}])[0].get("content", {}).get("parts", []) if isinstance(part, dict))
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        result = json.loads(text)
        metadata = raw.get("candidates", [{}])[0].get("groundingMetadata", {})
        sources = result.get("sources") if isinstance(result, dict) else []
        if not isinstance(sources, list):
            sources = []
        if not sources:
            for chunk in metadata.get("groundingChunks", []):
                web = chunk.get("web", {}) if isinstance(chunk, dict) else {}
                if web.get("uri"):
                    sources.append({"title": web.get("title") or web["uri"], "uri": web["uri"]})
        facts = result.get("facts", []) if isinstance(result, dict) else []
        summary = str(result.get("summary", "")).strip() if isinstance(result, dict) else ""
        if not summary and not facts:
            raise ValueError("grounded response contained no usable facts")
        snapshot = {
            "status": "success", "summary": summary, "facts": [str(item) for item in facts if item],
            "sources": [{"title": str(item.get("title") or ""), "uri": str(item.get("uri") or "")} for item in sources if isinstance(item, dict) and item.get("uri")],
            "researched_at": datetime.now(timezone.utc).isoformat(), "fingerprint": fingerprint,
            "research_query": prompt,
        }
        if not snapshot["sources"]:
            snapshot["status"] = "unavailable"
            snapshot["reason"] = "no_grounding_sources"
        return {**config, "business_research": snapshot}
    except Exception as exc:
        logger.warning("Business research failed business_name=%s exception_class=%s", business_name[:120], type(exc).__name__)
        return {**config, "business_research": {"status": "failed", "reason": "provider_error", "fingerprint": fingerprint}}
    finally:
        if client is None:
            http.close()
