from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import VersionStatus


class AIEmployeeVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ai_employee_versions"
    __table_args__ = (
        UniqueConstraint("employee_id", "version_number", name="uq_employee_versions_number"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    employee_id: Mapped[UUID] = mapped_column(ForeignKey("ai_employees.id"), index=True, nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[VersionStatus] = mapped_column(
        String(32), nullable=False, default=VersionStatus.draft.value
    )
    configuration: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    provider_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_agent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    employee = relationship("AIEmployee", back_populates="versions", foreign_keys=[employee_id])
    created_by_user = relationship("User", back_populates="created_employee_versions")
