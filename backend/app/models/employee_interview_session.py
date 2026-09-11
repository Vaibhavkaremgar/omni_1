from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EmployeeInterviewSession(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employee_interview_sessions"

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("ai_employees.id"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    messages: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    answers: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    current_question: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    suggested_next_question: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    suggested_questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    consumed_questions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    extracted_configuration: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    is_complete: Mapped[bool] = mapped_column(nullable=False, default=False)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    tenant = relationship("Tenant")
    employee = relationship("AIEmployee", back_populates="interview_sessions")
