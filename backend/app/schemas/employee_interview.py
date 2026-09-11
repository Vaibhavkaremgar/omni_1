from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InterviewMessage(BaseModel):
    role: str
    content: str
    created_at: datetime


class InterviewStartRequest(BaseModel):
    restart: bool = False


class InterviewAnswerRequest(BaseModel):
    answer: str = Field(default="", max_length=10000)
    question: str | None = Field(default=None, max_length=2000)
    skip: bool = False


class InterviewConsumeRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class InterviewStateRead(BaseModel):
    id: UUID
    employee_id: UUID
    status: str
    messages: list[InterviewMessage]
    questions: list[str]
    answers: list[dict]
    current_question: str | None
    suggested_next_question: str | None
    suggested_questions: list[dict[str, str]] = []
    consumed_questions: list[str] = []
    progress: int
    extracted_configuration: dict
    is_complete: bool
    llm_used: bool = False
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
