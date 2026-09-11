from uuid import UUID

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import EmployeeCallType, EmployeeCreationMode, EmployeeStatus


class AIEmployee(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_employees"

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    call_type: Mapped[EmployeeCallType] = mapped_column(
        String(32), nullable=False, default=EmployeeCallType.inbound.value
    )
    llm_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(150), nullable=False)
    language: Mapped[str] = mapped_column(String(100), nullable=False)
    creation_mode: Mapped[EmployeeCreationMode] = mapped_column(
        String(32), nullable=False, default=EmployeeCreationMode.chat.value
    )
    status: Mapped[EmployeeStatus] = mapped_column(
        String(32), nullable=False, default=EmployeeStatus.draft.value
    )
    published_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ai_employee_versions.id"), nullable=True
    )

    tenant = relationship("Tenant", back_populates="ai_employees")
    versions = relationship(
        "AIEmployeeVersion",
        back_populates="employee",
        cascade="all, delete-orphan",
        foreign_keys="AIEmployeeVersion.employee_id",
    )
    published_version = relationship(
        "AIEmployeeVersion",
        foreign_keys=[published_version_id],
        post_update=True,
        uselist=False,
    )
    campaigns = relationship("Campaign", back_populates="employee")
    calls = relationship("Call", back_populates="employee")
    interview_sessions = relationship(
        "EmployeeInterviewSession",
        back_populates="employee",
        cascade="all, delete-orphan",
    )
