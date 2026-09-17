from uuid import UUID

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class EmployeeKnowledgeFile(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "employee_knowledge_files"

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("ai_employees.id"), index=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    knowledge_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_file_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="uploading")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
