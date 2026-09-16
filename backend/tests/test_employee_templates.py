import pytest

from app.services.employee_templates import SUPPORTED_LANGUAGES, render_template, template_library


def test_all_pontis_templates_have_contract():
    templates = template_library()
    assert len(templates) == 7
    for template in templates:
        assert template["id"].startswith("pontis_")
        assert template["template_version"] == 1
        assert template["default_language"] == "Telugu"
        assert template["supported_languages"] == SUPPORTED_LANGUAGES
        assert template["placeholders"]
        assert template["safety_guardrails"]


def test_rendering_fills_hospital_values_and_telugu_policy():
    template = template_library()[0]
    values = {placeholder["key"]: "configured" for placeholder in template["placeholders"]}
    values.update({"business_name": "Life Hospitals", "location": "Hyderabad", "departments": "Cardiology, General Medicine"})
    result = render_template(template["id"], values)
    assert "Life Hospitals" in result["system_prompt"]
    assert "Hyderabad" in result["system_prompt"]
    assert "Cardiology, General Medicine" in result["system_prompt"]
    assert "Occasional English words do not trigger language switching" in result["system_prompt"]
    assert "do not end after the first caller response" in result["system_prompt"]


def test_required_placeholder_is_enforced_and_optional_can_be_empty():
    template = template_library()[0]
    values = {placeholder["key"]: "ok" for placeholder in template["placeholders"]}
    required = next(p for p in template["placeholders"] if p["required"])
    optional = next(p for p in template["placeholders"] if not p["required"])
    values.pop(required["key"])
    values[optional["key"]] = ""
    with pytest.raises(ValueError, match=required["key"]):
        render_template(template["id"], values)
