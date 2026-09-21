"""Build-time, grounded company research for an employee version."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import re
from typing import Any
from urllib.parse import urlparse
import ipaddress

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


RESEARCH_CONTEXT_KEYS = ("business_name", "business_description", "purpose", "original_requirement", "website_url", "products", "products_services")


def research_fingerprint(configuration: dict[str, Any]) -> str:
    context = {key: str(configuration.get(key) or "").strip() for key in RESEARCH_CONTEXT_KEYS}
    return hashlib.sha256(json.dumps(context, sort_keys=True, ensure_ascii=True).casefold().encode("utf-8")).hexdigest()


def validate_public_research_url(value: str | None) -> str | None:
    """Allow only public HTTP(S) origins; research must never target private services."""
    value = str(value or "").strip()
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Website URL must be a public HTTP or HTTPS URL.")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValueError("Private or local website URLs are not allowed.")
    except ValueError as exc:
        if str(exc).startswith("Private or local"):
            raise
    return value


def ensure_business_research(configuration: dict[str, Any], client: httpx.Client | None = None) -> dict[str, Any]:
    """Reuse a matching snapshot or research once during publish/build."""
    config = dict(configuration)
    fingerprint = research_fingerprint(config)
    existing = config.get("business_research")
    if isinstance(existing, dict) and existing.get("fingerprint") == fingerprint:
        return config

    business_name = str(config.get("business_name") or "").strip()
    description = str(config.get("business_description") or "").strip()
    website_url = validate_public_research_url(config.get("website_url"))
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
        f"Official website to prioritize when relevant: {website_url or 'None provided'}\n"
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
            "context": {key: config.get(key) for key in RESEARCH_CONTEXT_KEYS if config.get(key)},
        }
        if not snapshot["sources"]:
            snapshot["status"] = "unavailable"
            snapshot["reason"] = "no_grounding_sources"
        return {**config, "business_research": snapshot}
    except Exception as exc:
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.warning(
            "Business research failed business_name=%s exception_class=%s status_code=%s",
            business_name[:120], type(exc).__name__, status_code,
        )
        return {**config, "business_research": {"status": "failed", "reason": "provider_error", "fingerprint": fingerprint, **({"status_code": status_code} if status_code else {})}}
    finally:
        if client is None:
            http.close()
