from pydantic import BaseModel


class LLMOption(BaseModel):
    provider: str
    model: str
    speed: str
    intelligence: str
    cost: str
    recommended: bool
    availability: str


class LanguageOption(BaseModel):
    code: str
    name: str


class EmployeeOptionsRead(BaseModel):
    llm_options: list[LLMOption]
    languages: list[LanguageOption]
