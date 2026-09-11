from app.schemas.employee_options import EmployeeOptionsRead, LanguageOption, LLMOption


class LLMRegistry:
    """Provider-neutral registry; replace its source with config or provider APIs later."""

    def options(self) -> EmployeeOptionsRead:
        return EmployeeOptionsRead(
            llm_options=[
                LLMOption(
                    provider="OpenAI",
                    model="gpt-4o-mini",
                    speed="Fast",
                    intelligence="High",
                    cost="Low",
                    recommended=True,
                    availability="development",
                ),
                LLMOption(
                    provider="Anthropic",
                    model="claude-3-5-haiku",
                    speed="Fast",
                    intelligence="High",
                    cost="Medium",
                    recommended=False,
                    availability="development",
                ),
            ],
            languages=[
                LanguageOption(code="en-US", name="English (US)"),
                LanguageOption(code="en-GB", name="English (UK)"),
                LanguageOption(code="es-ES", name="Spanish"),
                LanguageOption(code="fr-FR", name="French"),
                LanguageOption(code="de-DE", name="German"),
                LanguageOption(code="hi-IN", name="Hindi"),
            ],
        )


llm_registry = LLMRegistry()
