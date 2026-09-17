"""Backend-only defaults for an employee's internal deployment configuration."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from app.core.config import Settings, get_settings
from app.models.ai_employee import AIEmployee
from app.models.ai_employee_version import AIEmployeeVersion


INTERNAL_CONFIGURATION_KEYS = frozenset({"llm_provider", "llm_model"})


def public_employee_configuration(configuration: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep backend LLM routing details out of customer-facing responses."""
    if configuration is None:
        return None
    result = {key: value for key, value in configuration.items() if key not in INTERNAL_CONFIGURATION_KEYS}
    research = result.get("business_research")
    if isinstance(research, dict):
        # Grounding URLs, query text, and fingerprints are internal build
        # metadata; verified facts/status are safe product configuration.
        result["business_research"] = {
            key: value for key, value in research.items()
            if key not in {"sources", "research_query", "fingerprint"}
        }
    return result


def normalize_employee_llm_configuration(
    employee: AIEmployee,
    version: AIEmployeeVersion,
    settings: Settings | None = None,
) -> None:
    """Fill missing internal LLM fields without replacing valid persisted values."""
    settings = settings or get_settings()
    configuration = dict(version.configuration or {})

    provider = _first_value(configuration.get("llm_provider"), employee.llm_provider, settings.effective_llm_provider)
    model = _first_value(configuration.get("llm_model"), employee.llm_model, settings.effective_llm_model)
    if not provider or not model:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI employee deployment is not configured. Please contact support.",
        )

    employee.llm_provider = provider
    employee.llm_model = model
    configuration["llm_provider"] = provider
    configuration["llm_model"] = model
    version.configuration = configuration


def strip_customer_internal_configuration(configuration: dict[str, Any]) -> dict[str, Any]:
    """Ignore browser-supplied routing details; they are always server-owned."""
    return {key: value for key, value in configuration.items() if key not in INTERNAL_CONFIGURATION_KEYS}


def _first_value(*values: Any) -> str | None:
    for value in values:
        normalized = str(value).strip() if value is not None else ""
        # These were historical placeholders, not deployable configuration.
        if normalized and normalized.lower() not in {"internal", "default"}:
            return normalized
    return None
