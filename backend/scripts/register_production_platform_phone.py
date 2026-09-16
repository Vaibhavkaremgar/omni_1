"""One-time guarded registration of the existing platform-owned Omni number.

Run from the backend directory with Railway's DATABASE_URL in the environment.
This script intentionally refuses SQLite/local databases.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.enums import NumberStatus, PhoneOwnership, TenantStatus
from app.models.phone_number import PhoneNumber
from app.models.tenant import Tenant
from app.services.phone_numbers import PhoneNumberService


PHONE = "+919429397429"
PROVIDER = "omnidimension"
PROVIDER_ID = "7933"


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("ERROR: DATABASE_URL is not set; refusing to run.")
        return 2
    if database_url.startswith(("sqlite://", "sqlite+")):
        print("ERROR: DATABASE_URL points to SQLite; refusing to run.")
        return 2
    if not database_url.startswith(("postgres://", "postgresql://", "postgresql+")):
        print("ERROR: DATABASE_URL is not a PostgreSQL URL; refusing to run.")
        return 2

    engine_url = database_url
    if engine_url.startswith("postgres://"):
        engine_url = "postgresql+psycopg://" + engine_url[len("postgres://"):]
    elif engine_url.startswith("postgresql://"):
        engine_url = "postgresql+psycopg://" + engine_url[len("postgresql://"):]

    engine = create_engine(engine_url, future=True, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        matches = db.scalars(select(PhoneNumber).where(or_(
            (PhoneNumber.provider_name == PROVIDER) & (PhoneNumber.provider_phone_number_id == PROVIDER_ID),
            PhoneNumber.e164_number == PHONE,
        )).with_for_update()).all()
        if len({item.id for item in matches}) > 1:
            raise RuntimeError("Multiple conflicting PhoneNumber records match the provider ID/phone number.")

        number = matches[0] if matches else PhoneNumber()
        created = number.id is None
        if created:
            db.add(number)
        number.e164_number = PHONE
        number.provider_name = PROVIDER
        number.provider_phone_number_id = PROVIDER_ID
        number.tenant_id = None
        number.ownership = PhoneOwnership.platform_demo.value
        number.status = NumberStatus.active.value
        number.label = "Platform demo phone"
        db.flush()

        exact_count = db.scalar(select(func.count()).select_from(PhoneNumber).where(
            PhoneNumber.provider_name == PROVIDER,
            PhoneNumber.provider_phone_number_id == PROVIDER_ID,
            PhoneNumber.e164_number == PHONE,
        ))
        if exact_count != 1:
            raise RuntimeError(f"Expected exactly one matching PhoneNumber before commit; found {exact_count}.")
        db.commit()
        db.refresh(number)

        verified = db.scalar(select(PhoneNumber).where(PhoneNumber.id == number.id))
        if verified is None or verified.tenant_id is not None or verified.ownership != PhoneOwnership.platform_demo.value or verified.status != NumberStatus.active.value or verified.provider_phone_number_id != PROVIDER_ID:
            raise RuntimeError("Post-commit PhoneNumber verification failed.")

        tenant_ids = list(db.scalars(select(Tenant.id).where(Tenant.status == TenantStatus.active.value).limit(2)).all())
        visibility = [str(verified.id) in {str(item.id) for item in PhoneNumberService.list_for_tenant(db, tenant_id)} for tenant_id in tenant_ids]
        if tenant_ids and not all(visibility):
            raise RuntimeError("Platform phone was not visible to every sampled active tenant.")

        duplicate_count = db.scalar(select(func.count()).select_from(PhoneNumber).where(
            PhoneNumber.provider_name == PROVIDER,
            PhoneNumber.provider_phone_number_id == PROVIDER_ID,
        ))
        print("updated" if not created else "created")
        print(f"Pontis phone record ID: {number.id}")
        print(f"phone number: {number.e164_number}")
        print(f"provider: {number.provider_name}")
        print(f"provider phone ID: {number.provider_phone_number_id}")
        print(f"ownership: {number.ownership}")
        print(f"status: {number.status}")
        print(f"duplicate count after operation: {duplicate_count}")
        print(f"active tenant visibility checks passed: {len(tenant_ids)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: registration was not completed: {exc}")
        raise SystemExit(1) from exc
