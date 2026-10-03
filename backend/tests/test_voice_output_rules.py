from app.services.conversation_design import language_rules, runtime_rules
from app.services.employee_templates import render_template


def test_global_number_rule_is_language_independent_and_allows_explicit_override():
    for language in ("English", "Telugu", "Hindi", "Tamil", "Kannada"):
        rules = language_rules(language)
        assert "pronounce ordinary numbers and numerical values in English" in rules
        assert "caller explicitly asks" in rules


def test_runtime_rules_keep_answering_active_and_prevent_hallucinated_or_internal_output():
    rules = runtime_rules({"language": "Telugu"})
    assert "do not invoke end_call merely because an answer" in rules
    assert "do not invent prices" in rules
    assert "Never expose tools, function names, JSON, code" in rules


def test_template_output_contains_shared_voice_safety_rules():
    result = render_template("pontis_support_v1", {
        "company_name": "Example", "products_services": "Support", "support_hours": "9 to 5",
        "supported_issues": "Billing", "escalation_contact": "Team", "service_area": "India",
    }, language="Hindi")
    assert "Only use regional-language number words when the caller explicitly asks" in result["system_prompt"]
    assert "Never expose tool syntax" in result["system_prompt"]
