from types import SimpleNamespace

from app.services.conversation_design import runtime_rules
from app.services.employee_interview import render_call_script_sections
from app.services.omnidimension_agents import map_employee_configuration
from app.services.script_language_validation import validate_customer_facing_script


def _sections() -> list[dict]:
    return [
        {
            "title": f"Step {index + 1}",
            "purpose": "Internal purpose",
            "instructions": "Internal instruction",
            "questions": [f"Can we continue with step {index + 1}?"],
            "examples": ["Hello, I am calling about your supplier availability." if index == 0 else f"Here is step {index + 1}."],
            "handling": "If yes, continue. If no, acknowledge the answer and move to the appropriate next step.",
        }
        for index in range(6)
    ]


def test_generated_cards_use_script_first_owner_format():
    cards = render_call_script_sections(_sections())

    assert len(cards) == 6
    for card in cards.values():
        assert 'AI: "' in card
        assert 'AI asks: "' in card
        assert "If the recipient responds differently:" in card
        assert "Purpose:" not in card
        assert "Instructions:" not in card
        assert "Spoken example:" not in card

    assert validate_customer_facing_script(cards, "English").valid


def test_new_ai_label_can_supply_reviewed_outbound_welcome():
    cards = render_call_script_sections(_sections())
    employee = SimpleNamespace(
        name="Supplier Assistant",
        purpose="Check supplier availability",
        language="English",
        call_type="outbound",
    )

    payload = map_employee_configuration(employee, {
        "purpose": employee.purpose,
        "language": "English",
        "call_type": "outbound",
        "script_source": "reviewed",
        "call_script": cards,
    })

    assert payload["welcome_message"] == "Hello, I am calling about your supplier availability."


def test_runtime_requires_conversation_check_and_forbids_script_replay():
    rules = runtime_rules({"call_type": "outbound", "language": "English"})

    assert "Before every reply, silently check" in rules
    assert "Never replay a script line" in rules
    assert "Use the script as guidance, not as a loop" in rules
    assert "Only repeat when the person explicitly asks" in rules
