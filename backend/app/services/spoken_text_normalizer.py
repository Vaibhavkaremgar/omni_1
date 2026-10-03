"""Normalize numeric spellings in caller-facing text before provider delivery."""
from __future__ import annotations

import re

_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_TEENS = ("ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")


def _integer_words(value: int) -> str:
    if value < 10:
        return _ONES[value]
    if value < 20:
        return _TEENS[value - 10]
    if value < 100:
        return _TENS[value // 10] + (f" {_ONES[value % 10]}" if value % 10 else "")
    if value < 1000:
        return f"{_ONES[value // 100]} hundred" + (f" {_integer_words(value % 100)}" if value % 100 else "")
    for divisor, name in ((1_000_000_000, "billion"), (1_000_000, "million"), (1000, "thousand")):
        if value >= divisor:
            return f"{_integer_words(value // divisor)} {name}" + (f" {_integer_words(value % divisor)}" if value % divisor else "")
    return str(value)


def _digits(value: str) -> str:
    return " ".join(_ONES[int(char)] for char in value)


def _alphanumeric(match: re.Match[str]) -> str:
    token = match.group(0)
    prefix, digits = re.match(r"([A-Za-z]+)(\d+(?:\.\d+)?)", token).groups()
    if "." in digits:
        whole, fraction = digits.split(".", 1)
        spoken = f"{_integer_words(int(whole))} point {_digits(fraction)}"
    else:
        spoken = _integer_words(int(digits))
    return f"{prefix}{' ' if prefix else ''}{spoken}"


def normalize_spoken_text(text: str) -> str:
    """Convert numeric text to English words without changing surrounding language.

    This operates only on text, never on structured values. Existing English
    number words are naturally untouched, making the operation idempotent.
    """
    if not isinstance(text, str) or not text:
        return text
    protected: list[str] = []

    def protect(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    # URLs, emails, and numeric path/query values are not spoken-number text.
    value = re.sub(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", protect, text)

    # Product/model names: V2, A2, X200, V2.0. Preserve letters.
    value = re.sub(r"(?<![A-Za-z0-9])([A-Za-z]+\d+(?:\.\d+)?)(?![A-Za-z0-9])", _alphanumeric, value)

    # Long digit runs and explicit OTP/code/reference labels are digit-by-digit.
    value = re.sub(r"(?i)(\b(?:otp|code|reference|ref|id|account)\s*[:#-]?\s*)\d{4,}", lambda m: m.group(1) + _digits(re.search(r"\d+", m.group(0)).group(0)), value)
    value = re.sub(r"(?<![A-Za-z0-9])\d{7,}(?![A-Za-z0-9])", lambda m: _digits(m.group(0)), value)

    # Remaining standalone quantities.
    value = re.sub(r"(?<![A-Za-z0-9])\d+(?![A-Za-z0-9])", lambda m: _integer_words(int(m.group(0))), value)

    def restore(match: re.Match[str]) -> str:
        return protected[int(match.group(1))]

    return re.sub(r"\x00(\d+)\x00", restore, value)
