from uuid import UUID
from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, UUIDPrimaryKeyMixin


class PlatformDemoPhoneAccess(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "platform_demo_phone_access"
    __table_args__ = (UniqueConstraint("phone_number_id", "tenant_id", name="uq_demo_phone_tenant"),)
    phone_number_id: Mapped[UUID] = mapped_column(ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    phone = relationship("PhoneNumber", back_populates="demo_access")
