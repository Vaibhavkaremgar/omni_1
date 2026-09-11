from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IntegrationConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tenant-scoped integration connection.

    provider_credentials is Fernet-encrypted JSON stored as text.
    It is NEVER returned through any API response.
    """

    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "integration_key", name="uq_integration_connections_tenant_key"),
    )

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"), index=True, nullable=False)
    integration_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_connected")
    # Opaque reference visible to the tenant (e.g. HubSpot portal ID, Slack workspace name).
    # Never contains provider secrets.
    external_account_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Fernet-encrypted JSON blob — backend only, never serialised to API responses.
    provider_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Non-secret configuration (webhook URLs, account IDs, etc.)
    configuration: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(512), nullable=True)

    tenant = relationship("Tenant", back_populates="integration_connections")
