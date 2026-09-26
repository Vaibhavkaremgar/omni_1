"""Build-time, grounded company research for an employee version."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import logging
import random
import time
import re
from typing import Any
from urllib.parse import urlparse
import ipaddress

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


RESEARCH_CONTEXT_KEYS = ("business_name", "business_description", "purpose", "original_requirement", "website_url", "products", "products_services")
PERSONAL_CONTEXT_MARKERS = (
    "wedding", "birthday", "anniversary", "family event", "family function",
    "community event", "personal event", "housewarming", "engagement",
)


def _gemini_text(body: dict[str, Any]) -> str:
    candidates = body.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()


def _log_gemini_call(research_id: str, phase: str, response: Any, body: dict[str, Any]) -> None:
    usage = body.get("usageMetadata") or {}
    logger.info(
        "Business research Gemini call research_id=%s phase=%s status=%s input_tokens=%s output_tokens=%s thinking_tokens=%s total_tokens=%s",
        research_id, phase, getattr(response, "status_code", 200),
        usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
        usage.get("thoughtsTokenCount"), usage.get("totalTokenCount"),
    )


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
    personal_text = " ".join(str(config.get(key) or "") for key in ("purpose", "original_requirement", "business_description")).casefold()
    if any(marker in personal_text for marker in PERSONAL_CONTEXT_MARKERS):
        config["business_research"] = {"status": "unavailable", "reason": "personal_context", "fingerprint": fingerprint}
        logger.info("Business research skipped reason=personal_context")
        return config
    if not business_name:
        config["business_research"] = {"status": "unavailable", "reason": "business_name_missing", "fingerprint": fingerprint}
        return config

    settings = get_settings()
    if not settings.gemini_api_key:
        logger.warning("Business research unavailable reason=gemini_api_key_missing business_name=%s", business_name[:120])
        config["business_research"] = {"status": "unavailable", "reason": "gemini_api_key_missing", "fingerprint": fingerprint}
        return config

    research_id = fingerprint[:12]
    prompt = (
        "Research this business using Google Search grounding. Prefer the official company website and authoritative sources. "
        "Return concise plain-text findings. Do not guess. If a fact is not verified, omit it.\n\n"
        f"Company name: {business_name}\nBusiness description: {description}\n"
        f"Original requirement: {config.get('original_requirement') or ''}\n"
        f"Employee role/purpose: {config.get('role') or config.get('job_role') or config.get('purpose') or ''}\n"
        f"Official website to prioritize when relevant: {website_url or 'None provided'}\n"
        f"Language: {config.get('language') or ''}\nCall type: {config.get('call_type') or ''}"
    )
    grounding_payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
    }
    http = client or httpx.Client(timeout=settings.gemini_research_timeout_seconds)
    endpoint = f"{settings.gemini_base_url.rstrip('/')}/models/{settings.gemini_model}:generateContent"
    headers = {"x-goog-api-key": settings.gemini_api_key, "content-type": "application/json"}
    grounded_text = ""
    grounding_sources: list[dict[str, str]] = []
    try:
        try:
            grounding_response = http.post(endpoint, headers=headers, json=grounding_payload)
            grounding_response.raise_for_status()
            grounding_raw = grounding_response.json()
            _log_gemini_call(research_id, "grounding", grounding_response, grounding_raw)
            grounded_text = _gemini_text(grounding_raw)
            metadata = (grounding_raw.get("candidates") or [{}])[0].get("groundingMetadata", {})
            for chunk in metadata.get("groundingChunks", []):
                web = chunk.get("web", {}) if isinstance(chunk, dict) else {}
                if web.get("uri"):
                    grounding_sources.append({"title": str(web.get("title") or web["uri"]), "uri": str(web["uri"])})
        except Exception as exc:
            status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            logger.warning(
                "Business research Gemini call failed research_id=%s phase=grounding status=%s exception_class=%s input_tokens=%s output_tokens=%s thinking_tokens=%s total_tokens=%s continuing_with_context=true",
                research_id, status_code, type(exc).__name__, None, None, None, None,
            )

        formatting_prompt = (
            "Format business research into JSON with exactly these keys: summary (string), facts (array of strings), "
            "and sources (array of objects with title and uri). Use only the supplied business context, grounded findings, "
            "and grounded sources. Do not invent facts or sources. If information is missing, use an empty string or empty array.\n\n"
            f"BUSINESS CONTEXT:\n{prompt}\n\n"
            f"GROUNDED FINDINGS:\n{grounded_text or 'No grounded findings were available.'}\n\n"
            f"GROUNDED SOURCES:\n{json.dumps(grounding_sources, ensure_ascii=False)}"
        )
        formatting_payload = {
            "contents": [{"role": "user", "parts": [{"text": formatting_prompt}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        # Retry formatting only; grounded search results above are retained.
        formatting_models = [settings.gemini_model]
        fallback_model = getattr(settings, "gemini_fallback_model", None)
        if fallback_model and fallback_model not in formatting_models:
            formatting_models.append(fallback_model)
        response = None
        last_exc = None
        for model_index, formatting_model in enumerate(formatting_models):
            formatting_endpoint = f"{settings.gemini_base_url.rstrip('/')}/models/{formatting_model}:generateContent"
            for retry_index in range(3):
                try:
                    response = http.post(formatting_endpoint, headers=headers, json=formatting_payload)
                    if response.status_code == 503:
                        raise httpx.HTTPStatusError("Gemini formatting service unavailable", request=response.request, response=response)
                    response.raise_for_status()
                    break
                except Exception as exc:
                    last_exc = exc
                    status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                    if status_code != 503 or retry_index == 2:
                        break
                    delay = min(8.0, 1.5 * (2 ** retry_index)) * random.uniform(0.8, 1.2)
                    logger.warning("Business research formatting retry research_id=%s model=%s retry=%d backoff_seconds=%.2f", research_id, formatting_model, retry_index + 1, delay)
                    time.sleep(delay)
            if response is not None and response.status_code < 400:
                break
            response = None
        if response is None:
            raise last_exc or RuntimeError("Gemini formatting failed")
        raw = response.json()
        _log_gemini_call(research_id, "formatting", response, raw)
        text = _gemini_text(raw)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
        result = json.loads(text)
        sources = result.get("sources") if isinstance(result, dict) else []
        if not isinstance(sources, list):
            sources = []
        if not sources:
            sources = grounding_sources
        facts = result.get("facts", []) if isinstance(result, dict) else []
        summary = str(result.get("summary", "")).strip() if isinstance(result, dict) else ""
        snapshot = {
            "status": "success", "summary": summary, "facts": [str(item) for item in facts if item],
            "sources": [{"title": str(item.get("title") or ""), "uri": str(item.get("uri") or "")} for item in sources if isinstance(item, dict) and item.get("uri")],
            "researched_at": datetime.now(timezone.utc).isoformat(), "fingerprint": fingerprint,
            "research_query": prompt,
            "context": {key: config.get(key) for key in RESEARCH_CONTEXT_KEYS if config.get(key)},
        }
        if not summary and not facts:
            snapshot["status"] = "unavailable"
            snapshot["reason"] = "no_verified_facts"
        elif not snapshot["sources"]:
            snapshot["status"] = "unavailable"
            snapshot["reason"] = "no_grounding_sources"
        return {**config, "business_research": snapshot}
    except Exception as exc:
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        provider_body = ""
        if isinstance(exc, httpx.HTTPStatusError):
            provider_body = re.sub(r"\s+", " ", exc.response.text or "")[:500]
        logger.warning(
            "Business research Gemini call failed research_id=%s phase=formatting status=%s exception_class=%s input_tokens=%s output_tokens=%s thinking_tokens=%s total_tokens=%s body=%s",
            research_id, status_code, type(exc).__name__, None, None, None, None, provider_body,
        )
        return {**config, "business_research": {"status": "failed", "reason": "provider_error", "fingerprint": fingerprint, **({"status_code": status_code} if status_code else {})}}
    finally:
        if client is None:
            http.close()
