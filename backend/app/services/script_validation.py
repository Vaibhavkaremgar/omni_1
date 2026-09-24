from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

from app.services.script_validation_constants import (
    ALLOWED_COMMON_ENGLISH,
    HINDI_MONTH_STEMS,
    TELUGU_BANNED_STEMS,
    TELUGU_MONTH_STEMS,
)


def validate_script(
    sections: list[dict[str, Any]],
    language: str,
    call_direction: str,
    *,
    user_context: str = "",
    host_name: str = "",
    agent_name: str = "",
    business_name: str = "",
    enforce: bool = False,
) -> list[dict[str, str]]:
    """Return generated-script violations without mutating model output."""
    issues: list[dict[str, str]] = []
    normalized_language = str(language or "").casefold()
    spoken_lines: list[tuple[int, str, str]] = []
    for index, section in enumerate(sections):
        title = str(section.get("title") or f"section_{index + 1}")
        for field in ("questions", "examples"):
            for line in section.get(field) or []:
                spoken_lines.append((index, field, str(line)))
                _validate_line(issues, index, title, field, str(line), normalized_language, enforce=enforce)

    if normalized_language in {"telugu", "te", "te-in", "hindi", "hi", "hi-in"}:
        for index, title, field, line in [(i, str(sections[i].get("title") or ""), f, line) for i, f, line in spoken_lines]:
            if _contains_native_date(line, normalized_language):
                issues.append(_issue(index, title, field, line, "english_dates", "date is written with native-language month words"))

    if str(call_direction or "").casefold() == "outbound":
        main_index = 3
        question_count = sum(len(section.get("questions") or []) for section in sections)
        if question_count > (0 if enforce else 1):
            issues.append(_issue(main_index, str(sections[main_index].get("title") if len(sections) > main_index else ""), "questions", "", "outbound_questions", "outbound scripts must not ask customer questions"))
        for index, section in enumerate(sections):
            if index != main_index and section.get("questions"):
                issues.append(_issue(index, str(section.get("title") or ""), "questions", str(section.get("questions")), "outbound_questions", "outbound questions belong only in the main-request section"))
        opening = sections[0] if sections else {}
        for field in ("examples", "questions"):
            for line in opening.get(field) or []:
                if _permission_question(line):
                    issues.append(_issue(0, str(opening.get("title") or ""), field, str(line), "outbound_permission", "outbound opening asks permission or ends as a question"))

    for left_index, (_, _, left) in enumerate(spoken_lines):
        for right_index in range(left_index + 1, len(spoken_lines)):
            _, _, right = spoken_lines[right_index]
            if _similar(left, right):
                issues.append(_issue(spoken_lines[right_index][0], str(sections[spoken_lines[right_index][0]].get("title") or ""), spoken_lines[right_index][1], right, "duplicate_line", "line duplicates or nearly duplicates another spoken line"))
    for index, section in enumerate(sections):
        for field in ("examples", "questions"):
            for line in section.get(field) or []:
                if _looks_like_reply(str(line)):
                    issues.append(_issue(index, str(section.get("title") or ""), field, str(line), "person_reply", "line appears to be the other person's reply"))
                if re.search(r"\bif asked\b|\badigితే\b|అడిగితే", str(line), re.IGNORECASE):
                    issues.append(_issue(index, str(section.get("title") or ""), field, str(line), "meta_line", "spoken example contains meta instructions"))

    allowed = " ".join((user_context, host_name, agent_name, business_name)).casefold()
    context_dates = _date_keys(user_context)
    for index, section in enumerate(sections):
        for line in section.get("examples") or []:
            if enforce and context_dates:
                for date_key in _date_keys(str(line)):
                    if date_key not in context_dates:
                        issues.append(_issue(index, str(section.get("title") or ""), "examples", str(line), "invented_date", f"date {date_key} is not present in user context"))
            for candidate in re.findall(r"\b[A-Z][a-z]{2,}\b", str(line)):
                if candidate.casefold() not in allowed and candidate.casefold() not in ALLOWED_COMMON_ENGLISH:
                    issues.append(_issue(index, str(section.get("title") or ""), "examples", str(line), "invented_name", f"possible invented name: {candidate}", severity="error" if enforce else "warning"))
    return issues


def _validate_line(
    issues: list[dict[str, str]], index: int, title: str, field: str, line: str,
    language: str, *, enforce: bool,
) -> None:
    if language in {"telugu", "te", "te-in"} and any(line.find(stem) >= 0 for stem in TELUGU_BANNED_STEMS):
        issues.append(_issue(index, title, field, line, "telugu_banned_word", "formal or literary Telugu word stem is banned by the prompt"))


def _contains_native_date(line: str, language: str) -> bool:
    months = TELUGU_MONTH_STEMS if language in {"telugu", "te", "te-in"} else HINDI_MONTH_STEMS
    return any(re.search(rf"\d{{1,2}}\s*{re.escape(month)}\s*\d{{2,4}}", line, re.IGNORECASE) for month in months)


_ENGLISH_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def _date_keys(text: str) -> set[str]:
    keys: set[str] = set()
    month_pattern = "|".join(_ENGLISH_MONTHS)
    for match in re.finditer(
        rf"\b(?:(?P<month>{month_pattern})\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s*(?P<year>\d{{4}}))?|(?P<day2>\d{{1,2}})(?:st|nd|rd|th)?(?:\s+of)?\s+(?P<month2>{month_pattern})(?:\s+(?P<year2>\d{{4}}))?)\b",
        text.casefold(),
    ):
        month = _ENGLISH_MONTHS.get(match.group("month") or match.group("month2"))
        day = match.group("day") or match.group("day2")
        year = match.group("year") or match.group("year2") or ""
        if month and day:
            keys.add(f"{month:02d}-{int(day):02d}-{year}")
    return keys


def _permission_question(line: str) -> bool:
    text = line.casefold().strip()
    return text.endswith("?") or any(phrase in text for phrase in ("is this a good time", "do you have time", "can i ask", "may i", "are you available"))


def _looks_like_reply(line: str) -> bool:
    text = line.casefold().strip()
    return bool(re.match(r"^(yes|no|okay|sure|అవును|కాదు|हाँ|नहीं)[,.! ]", text)) or "నేను హాజరు" in text or "i will attend" in text


def _similar(left: str, right: str) -> bool:
    normalize = lambda value: re.sub(r"[^\w]+", " ", value.casefold()).strip()
    a, b = normalize(left), normalize(right)
    return bool(a and b and a == b or (len(a) > 24 and SequenceMatcher(None, a, b).ratio() >= 0.92))


def _issue(index: int, title: str, field: str, line: str, rule: str, reason: str, *, severity: str = "error") -> dict[str, str]:
    return {"section": title, "section_index": str(index), "field": field, "line": line, "rule": rule, "reason": reason, "severity": severity}
