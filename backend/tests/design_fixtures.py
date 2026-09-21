def design_response(titles, speech="Hello, how can I help?", job="Resolve the requested issue"):
    return {
        "understanding": {
            "organization": "Test organization", "activity": job, "job": job,
            "audience": "The person with this request", "information_needed": [],
            "information_to_provide": [job], "irrelevant_questions": ["Purchase intent"],
            "concerns_and_refusal": "Respect refusal and answer only from supplied facts",
            "flow": "Understand request, provide the configured information, wait for confirmation",
            "completion": "The requested information has been provided",
            "unknowns_and_prohibited_claims": ["Do not invent missing facts"],
        },
        "opening": speech,
        "sections": [
            {"key": f"slot_{i}", "title": title, "purpose": f"Handle {title}",
             "objective": f"Complete {title}", "content": f"Follow the supplied facts for {title}.",
             "questions": [], "spoken_examples": [speech]}
            for i, title in enumerate(titles)
        ],
    }
