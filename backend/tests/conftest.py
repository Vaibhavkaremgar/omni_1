import sys
from pathlib import Path
from types import SimpleNamespace
import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture(autouse=True)
def deterministic_employee_llm_settings(monkeypatch):
    """Keep employee API tests offline while preserving production validation."""
    from app.api.v1.endpoints import employees as employee_endpoint
    monkeypatch.setattr(
        employee_endpoint,
        "get_settings",
        lambda: SimpleNamespace(effective_llm_provider="test-provider", effective_llm_model="test-model"),
    )
