from app.services.script_validation import validate_script


def _sections(*, questions=None, examples=None):
    return [
        {"title": title, "purpose": "Purpose", "instructions": "Instructions", "questions": questions if index == 3 and questions is not None else [], "examples": examples or ["Hello అండి, నేను {{HOST_NAME}} గారి తరపున call చేస్తున్నాను."], "handling": "Handling"}
        for index, title in enumerate(("Opening", "Details", "Listening", "Request", "Exceptions", "Closing"))
    ]


def test_bad_wedding_script_reports_prompt_conflicts():
    sections = _sections(
        questions=["Is this a good time?", "Can you confirm?"],
        examples=["అవును, నేను హాజరవుతాను.", "15 జనవరి 2027 న మా వివాహానికి హాజరు అవ్వండి.", "if asked, say yes"],
    )
    sections[0]["questions"] = ["Is this a good time?"]
    issues = validate_script(
        sections, "Telugu", "outbound",
        user_context="invite contacts to a wedding on January 15th, 2027",
    )
    rules = {item["rule"] for item in issues}
    assert {"telugu_banned_word", "english_dates", "outbound_permission", "outbound_questions", "person_reply", "meta_line"}.issubset(rules)


def test_good_wedding_script_has_no_blocking_violations():
    sections = [
        {"title": "Opening", "purpose": "Open", "instructions": "Speak briefly", "questions": [], "examples": ["Hello అండి, నేను {{HOST_NAME}} గారి తరపున call చేస్తున్నాను. వాళ్ల wedding కి మిమ్మల్ని invite చేయడానికి call చేశాను అండి."], "handling": "Handle refusal politely."},
        {"title": "Details", "purpose": "Details", "instructions": "Share known facts", "questions": [], "examples": ["January 15th, 2027 న wedding జరుగుతుంది అండి."], "handling": "Do not invent details."},
        {"title": "Listening", "purpose": "Listen", "instructions": "Acknowledge", "questions": [], "examples": ["I understand అండి, మీ మాట clear గా ఉంది, no problem."], "handling": "Answer from context."},
        {"title": "Request", "purpose": "Request", "instructions": "Make the request", "questions": [], "examples": ["మీరు తప్పకుండా attend అవ్వండి అండి."], "handling": "Do not pressure."},
        {"title": "Exceptions", "purpose": "Exceptions", "instructions": "Handle exceptions", "questions": [], "examples": ["మీకు details కావాలంటే, నాకు ఆ detail available లేదు అండి."], "handling": "Be honest."},
        {"title": "Closing", "purpose": "Close", "instructions": "Close politely", "questions": [], "examples": ["Thank you అండి, January 15th న wedding కి తప్పకుండా రండి."], "handling": "End politely."},
    ]
    assert [item for item in validate_script(sections, "Telugu", "outbound", user_context="January 15th, 2027 wedding") if item["severity"] == "error"] == []
