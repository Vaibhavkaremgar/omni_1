from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.tenant import Tenant
from app.models.instant_lead_source import InstantLeadSource, InstantLeadRow
from app.services.instant_leads import InstantLeadService, row_fingerprint


@pytest.fixture()
def instant_db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    tenant = Tenant(name="Instant", slug="instant-test")
    db.add(tenant); db.commit(); db.refresh(tenant)
    yield db, tenant
    db.close()


def test_row_fingerprint_is_deterministic_and_changes_for_new_contact():
    row = {"phone_number": "+15551234567", "first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.test"}
    assert row_fingerprint(row) == row_fingerprint(dict(row))
    changed = {**row, "phone_number": "+15557654321"}
    assert row_fingerprint(row) != row_fingerprint(changed)


def test_monitoring_imports_only_new_rows_and_does_not_redial(instant_db):
    db, tenant_a = instant_db
    calls = []

    class FakeCalls:
        def dispatch(self, db, tenant_id, request):
            calls.append(request.destination_phone_number)
            return SimpleNamespace()

    source = InstantLeadSource(
        tenant_id=tenant_a.id, employee_id=tenant_a.id, phone_number_id=tenant_a.id,
        filename="leads.csv", content_type="text/csv",
        content=b"phone,first_name\n+15551234567,Ada\n+15557654321,Grace\n", enabled=True,
    )
    db.add(source); db.commit(); db.refresh(source)
    service = InstantLeadService(FakeCalls())
    first = service.check(db, source.id, tenant_a.id)
    second = service.check(db, source.id, tenant_a.id)
    assert first["new_rows"] == 2
    assert second["new_rows"] == 0
    assert calls == ["+15551234567", "+15557654321"]
    assert db.query(InstantLeadRow).count() == 2
