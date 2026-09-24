from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.employee_prompt import SCRIPT_SECTION_NAMES


CUSTOMER_FACING_SECTIONS = SCRIPT_SECTION_NAMES
TELUGU_RANGE = (0x0C00, 0x0C7F)
DEVANAGARI_RANGE = (0x0900, 0x097F)

# Generated scripts are stored as readable section cards.  Those cards contain
# English-only Purpose / Instructions / Handling metadata as well as the words
# an agent actually says.  Language validation must inspect the latter only.
_SPOKEN_LABEL_RE = re.compile(
    r"(?ims)^\s*(?:spoken(?:\s+example)?|question)\s*:\s*(.+?)"
    r"(?=^\s*(?:purpose|objective|instructions|handling|spoken(?:\s+example)?|question|reason|support\s+type|requirement\s+basis)\s*:|\Z)"
)

BUSINESS_ENGLISH_WORDS = {
    "insurance", "policy", "renewal", "product", "service", "offer", "discount",
    "location", "budget", "site", "visit", "appointment", "booking", "details",
    "available", "option", "confirm", "schedule", "demo", "price", "quotation",
    "call", "interest", "customer", "support", "website", "whatsapp", "email",
    "company", "manager", "premium", "claim", "payment", "documents", "advisor",
    "team", "follow", "up", "callback", "model", "code", "otp", "id", "url",
    "okay", "ok", "sure", "sorry", "thanks", "thank", "you", "right", "actually",
}

TELUGU_ROMAN_MARKERS = {
    "nenu", "mee", "meeru", "meeku", "maatladutunnanu", "maatladataniki",
    "gurinchi", "chesanu", "chesaru", "chesi", "cheyali", "kavala", "unda",
    "andi", "namaskaram", "sare", "avunu", "ledu", "inka", "mariyu",
    "gari", "lo", "kosam", "ki", "ga", "unnanu", "chestunnanu", "cheptanu", "avvandi",
}
HINDI_ROMAN_MARKERS = {
    "namaste", "main", "mein", "aap", "aapki", "aapka", "se", "bol", "raha",
    "rahi", "hoon", "hun", "hai", "hain", "baat", "karne", "liye", "kiya",
    "kya", "chahe", "chahiye", "ji", "haan", "achha", "theek", "zaroor",
}


@dataclass(frozen=True)
class ScriptLanguageIssue:
    section: str
    type: str
    message: str
    excerpt: str | None = None

    def as_dict(self) -> dict[str, str]:
        result = {"section": self.section, "type": self.type, "message": self.message}
        if self.excerpt:
            result["excerpt"] = self.excerpt
        return result


@dataclass(frozen=True)
class ScriptLanguageValidation:
    valid: bool
    language: str
    issues: list[ScriptLanguageIssue]

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "language": self.language,
            "issues": [issue.as_dict() for issue in self.issues],
        }


def validate_customer_facing_script(script: dict[str, Any], language: str, *, require_six: bool = True) -> ScriptLanguageValidation:
    normalized = _language_code(language)
    issues: list[ScriptLanguageIssue] = []
    if normalized not in {"te", "hi", "en"}:
        return ScriptLanguageValidation(True, normalized, [])
    if require_six and len(script) != 6:
        issues.append(ScriptLanguageIssue("Sections", "missing_section", "Exactly six populated sections are required."))
    for section in script:
        entries = _customer_facing_entries(script.get(section))
        if not entries:
            issues.append(ScriptLanguageIssue(section, "missing_section", f"{section} must contain customer-facing script content."))
            continue
        for index, text in enumerate(entries, 1):
            entry_name = section if len(entries) == 1 else f"{section} / {index}"
            if normalized == "te":
                issues.extend(_validate_native_section(entry_name, text, TELUGU_RANGE, TELUGU_ROMAN_MARKERS, "Telugu", "Telugu Unicode", "Roman/Tinglish Telugu"))
            elif normalized == "hi":
                issues.extend(_validate_native_section(entry_name, text, DEVANAGARI_RANGE, HINDI_ROMAN_MARKERS, "Hindi", "Devanagari", "Roman Hindi"))
    return ScriptLanguageValidation(not issues, normalized, issues)


def assert_customer_facing_script_language(script: dict[str, Any], language: str) -> None:
    validation = validate_customer_facing_script(script, language)
    if not validation.valid:
        from fastapi import HTTPException

        label = {"te": "Telugu", "hi": "Hindi", "en": "English"}.get(validation.language, validation.language)
        raise HTTPException(
            status_code=422,
            detail={
                "message": f"{label} script validation failed",
                "validation": validation.as_dict(),
            },
        )


def extract_call_script_from_prompt(prompt: str) -> dict[str, str]:
    """Numbered dynamic headings; unnumbered legacy headings remain readable."""
    text = _text(prompt)
    matches = list(re.finditer(r"(?m)^\s*([1-6])\. ([^\n]+)\s*$", text))
    if len(matches) == 6 and [m.group(1) for m in matches] == list("123456"):
        titles = [m.group(2).strip() for m in matches]
    else:
        matches = list(re.finditer(r"(?m)^\s*(Identity & Purpose|Greeting & Intro|Qualification|Handling Objections|Call to Action|Closing)\s*$", text))
        titles = [m.group(1) for m in matches]
    if len(matches) != 6 or len(set(t.casefold() for t in titles)) != 6:
        return {}
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index < 5 else len(text)
        body = text[match.end():end].strip()
        if not body:
            return {}
        result[titles[index]] = body
    return result


def validation_summary(configuration: dict[str, Any]) -> dict[str, Any]:
    from app.services.conversation_design import spoken_script
    script = spoken_script(configuration)
    validation = validate_customer_facing_script(script, _text(configuration.get("language")) or "English", require_six=not bool(configuration.get("conversation_design")))
    return validation.as_dict()


def _customer_facing_entries(value: Any) -> list[str]:
    """Extract labelled agent speech from a rendered section card.

    Older manually-authored scripts have no labels, so their complete value is
    still treated as customer-facing text.  A generated card that contains
    labels but no actual speech is intentionally rejected as missing content.
    """
    text = _text(value)
    if not text:
        return []
    matches = list(_SPOKEN_LABEL_RE.finditer(text))
    if not matches:
        return [text]
    entries: list[str] = []
    for match in matches:
        candidate = match.group(1).strip()
        # Conversation-design cards store quoted JSON strings.  Decode those
        # when possible so surrounding quotes are never part of validation.
        if candidate.startswith('"') and candidate.endswith('"'):
            try:
                import json
                decoded = json.loads(candidate)
                if isinstance(decoded, str):
                    candidate = decoded.strip()
            except (ValueError, TypeError):
                pass
        if candidate:
            entries.append(candidate)
    return entries


def _validate_native_section(
    section: str,
    text: str,
    native_range: tuple[int, int],
    roman_markers: set[str],
    language_name: str,
    script_name: str,
    roman_label: str,
) -> list[ScriptLanguageIssue]:
    issues: list[ScriptLanguageIssue] = []
    native_count = _script_count(text, native_range)
    # English gratitude is explicitly permitted even in a regional-language call.
    english_closing = bool(re.fullmatch(r"(?:thanks|thank you)[,.! ]*(?:have a nice day[.! ]*)?", text.strip(), re.I))
    latin_words = _latin_words(text)
    meaningful_latin = [word for word in latin_words if not _is_allowed_latin(word)]
    roman_hits = [word for word in meaningful_latin if word in roman_markers]
    alpha_count = sum(1 for char in text if char.isalpha())
    native_ratio = native_count / max(alpha_count, 1)
    if native_count < 4 and latin_words and not english_closing:
        issues.append(ScriptLanguageIssue(
            section,
            "english_only",
            f"Customer-facing {language_name} needs visible {script_name} conversational content. Natural English business terms are allowed, but the section cannot be English-only.",
            _excerpt(text),
        ))
    if roman_hits and (len(roman_hits) >= 2 or native_count < 12):
        issues.append(ScriptLanguageIssue(
            section,
            "romanized_language",
            f"Customer-facing {language_name} contains {roman_label}. Use {script_name} for conversational words while keeping natural English business terms in Latin script.",
            _excerpt(text),
        ))
    if native_count >= 4 and native_ratio < 0.08 and len(meaningful_latin) >= 12:
        issues.append(ScriptLanguageIssue(
            section,
            "insufficient_native_script",
            f"Customer-facing {language_name} has too little {script_name} conversational content for a substantial spoken section.",
            _excerpt(text),
        ))
    return issues


def _language_code(language: str) -> str:
    value = _text(language).casefold()
    if value in {"telugu", "te", "te-in", "telugu (india)"}:
        return "te"
    if value in {"hindi", "hi", "hi-in", "hindi (india)"}:
        return "hi"
    return "en" if value in {"english", "en", "en-us", "en-in", "english (india)", ""} else value


def _script_count(text: str, bounds: tuple[int, int]) -> int:
    start, end = bounds
    return sum(1 for char in text if start <= ord(char) <= end)


def _latin_words(text: str) -> list[str]:
    return [word.casefold() for word in re.findall(r"[A-Za-z][A-Za-z'-]*", text)]


def _is_allowed_latin(word: str) -> bool:
    clean = word.strip("'-").casefold()
    if not clean or len(clean) <= 1:
        return True
    if clean in BUSINESS_ENGLISH_WORDS:
        return True
    if re.fullmatch(r"[a-z]{1,3}\d+[a-z0-9]*|\d+[a-z]+", clean):
        return True
    if clean in {"http", "https", "www", "com", "in", "co"}:
        return True
    return False


def _excerpt(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned[:160]


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""
