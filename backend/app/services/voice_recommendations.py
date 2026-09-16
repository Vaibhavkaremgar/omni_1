from __future__ import annotations

from typing import Any, Iterable


def normalize_language(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    return {"te": "telugu", "tel": "telugu"}.get(normalized, normalized)


def _requested_gender(gender: Any, requirement: Any) -> str | None:
    value = str(gender or "").strip().casefold()
    if value in {"female", "woman", "women"}:
        return "female"
    if value in {"male", "man", "men"}:
        return "male"
    text = str(requirement or "").casefold()
    if any(term in text for term in ("female voice", "woman voice", "women voice")):
        return "female"
    if any(term in text for term in ("male voice", "man voice", "men voice")):
        return "male"
    return None


def recommend_voices(
    voices: Iterable[dict[str, Any]],
    *,
    language: Any = None,
    gender: Any = None,
    requirement: Any = None,
) -> list[dict[str, Any]]:
    requested_language = normalize_language(language)
    requested_gender = _requested_gender(gender, requirement)
    cloned_preference = any(term in str(requirement or "").casefold() for term in ("my cloned voice", "cloned voice", "voice clone"))
    scored: list[tuple[int, dict[str, Any]]] = []
    for voice in voices:
        if not isinstance(voice, dict) or not voice.get("id") or voice.get("available") is False:
            continue
        if str(voice.get("status", "ready")).casefold() not in {"", "ready"}:
            continue
        languages = [normalize_language(item) for item in voice.get("languages", []) or []]
        language_match = not languages or not requested_language or requested_language in languages
        if not language_match:
            continue
        score = 2 if requested_language and languages and requested_language in languages else 1
        voice_gender = str(voice.get("gender", "")).casefold()
        if requested_gender:
            if voice_gender == requested_gender:
                score += 3
            elif voice_gender in {"male", "female"}:
                score -= 2
        if voice.get("is_cloned") is True or str(voice.get("tier", "")).casefold() in {"cloned", "custom"}:
            score += 3 if cloned_preference else 1
        scored.append((score, voice))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("name", ""))))
    return [
        {"id": voice["id"], "reason": _reason(voice, requested_language, requested_gender)}
        for _, voice in scored
    ]


def _reason(voice: dict[str, Any], language: str, gender: str | None) -> str:
    parts: list[str] = []
    if gender and str(voice.get("gender", "")).casefold() == gender:
        parts.append(f"{gender.title()} voice")
    if language and language in [normalize_language(item) for item in voice.get("languages", []) or []]:
        parts.append(language.title())
    if voice.get("is_cloned") is True or str(voice.get("tier", "")).casefold() in {"cloned", "custom"}:
        parts.append("cloned voice")
    return " ".join(parts) or "Available voice"
