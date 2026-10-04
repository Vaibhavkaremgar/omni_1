"""Verification tests for the first-client boundary.

The PostgreSQL concurrency test is opt-in via POSTGRES_TEST_DATABASE_URL because
the repository does not provision a database service for tests.
"""
from decimal import Decimal
import os
from threading import Barrier, Thread

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models import Call, CreditTransaction, CreditWallet, PhoneNumber, UsageRecord, User
from app.models.enums import CallDirection, CallStatus
from app.services.auth import hash_password
from app.services.call_results import CallResultService
from app.services.usage_billing import charge_completed_call

from test_instant_calls import call_database, create_employee, create_number
from test_billing import completed_payload, local_call


def test_first_client_23_second_settlement_is_exactly_once(call_database):
    db, tenant, _ = call_database
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant.id))
    wallet.balance_credits = Decimal("100.0000")
    employee = create_employee(db, tenant)
    phone = create_number(db, tenant)
    call = local_call(db, tenant, employee, phone, "first-client-23")

    payload = completed_payload(call, "0:23")
    service = CallResultService()
    service.process_post_call(db, payload)
    service.process_post_call(db, payload)
    db.refresh(call); db.refresh(wallet)

    assert call.billing_status == "settled"
    assert call.billed_duration_seconds == 23
    assert wallet.balance_credits == Decimal("96.9333")
    assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.call_id == call.id)) == 1
    assert db.scalar(select(func.count()).select_from(CreditTransaction).where(CreditTransaction.call_id == call.id)) == 1


def test_transient_billing_failure_is_retryable_then_settles(call_database, monkeypatch):
    db, tenant, _ = call_database
    employee = create_employee(db, tenant)
    phone = create_number(db, tenant)
    call = local_call(db, tenant, employee, phone, "retryable-billing")

    original = __import__("app.services.call_results", fromlist=["charge_completed_call"]).charge_completed_call
    attempts = {"count": 0}

    def flaky(db_session, local_call):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("transient")
        return original(db_session, local_call)

    monkeypatch.setattr("app.services.call_results.charge_completed_call", flaky)
    service = CallResultService()
    service.process_post_call(db, completed_payload(call, "0:23"))
    db.refresh(call)
    assert call.billing_status == "retryable"

    service.process_post_call(db, completed_payload(call, "0:23"))
    db.refresh(call)
    assert call.billing_status == "settled"
    assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.call_id == call.id)) == 1


def test_customer_cannot_access_other_tenant_call_or_billing(call_database, monkeypatch):
    from fastapi.testclient import TestClient
    from app.api.deps import get_db
    from app.main import app
    from app.services import auth as auth_service
    from app.models import User

    db, tenant_a, tenant_b = call_database
    employee = create_employee(db, tenant_b)
    phone = create_number(db, tenant_b, provider_id="tenant-b-verification")
    call = local_call(db, tenant_b, employee, phone, "tenant-b-call")
    call.transcript = "private transcript"
    call.recording_url = "https://provider.invalid/private-recording"
    db.commit()
    user_a = db.scalar(select(User).where(User.tenant_id == tenant_a.id))
    monkeypatch.setattr(auth_service, "decode_access_token", lambda _: user_a.id)
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as api:
            headers = {"Authorization": "Bearer tenant-a"}
            assert api.get(f"/api/v1/calls/{call.id}", headers=headers).status_code == 404
            assert api.get("/api/v1/billing/transactions", headers=headers).json() == []
            assert api.get("/api/v1/billing/usage", headers=headers).json() == []
    finally:
        app.dependency_overrides.clear()


def test_postgres_duplicate_settlement_is_exactly_once():
    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    from app.models import Tenant, AIEmployee, AIEmployeeVersion
    from app.models.enums import CallDirection

    setup = Session()
    suffix = os.urandom(6).hex()
    tenant = Tenant(name=f"PG concurrency {suffix}", slug=f"pg-concurrency-{suffix}")
    setup.add(tenant); setup.flush()
    employee = AIEmployee(tenant_id=tenant.id, name="PG employee", purpose="Test", call_type="outbound", llm_provider="test", llm_model="test", language="en", creation_mode="chat", status="published")
    setup.add(employee); setup.flush()
    version = AIEmployeeVersion(tenant_id=tenant.id, employee_id=employee.id, version_number=1, status="published", provider_agent_id="pg-agent")
    setup.add(version); setup.flush(); employee.published_version = version
    phone = PhoneNumber(tenant_id=tenant.id, e164_number=f"+1555{suffix[:7]}", provider_name="test", provider_phone_number_id=f"pg-{suffix}", status="active")
    wallet = CreditWallet(tenant_id=tenant.id, balance_credits=Decimal("100.0000"), currency="INR", status="active")
    call = Call(tenant_id=tenant.id, employee_id=employee.id, employee_version_id=version.id, phone_number_id=phone.id, direction=CallDirection.outbound.value, status=CallStatus.completed.value, duration_seconds=60, provider_call_id=f"pg-call-{suffix}")
    setup.add_all([phone, wallet, call]); setup.commit(); call_id = call.id; tenant_id = tenant.id; setup.close()

    barrier = Barrier(2)
    outcomes = []
    def settle():
        session = Session()
        try:
            local_call = session.get(Call, call_id)
            barrier.wait(timeout=10)
            charge_completed_call(session, local_call)
            session.commit(); outcomes.append("settled")
        except Exception as exc:
            session.rollback(); outcomes.append(type(exc).__name__)
        finally:
            session.close()
    threads = [Thread(target=settle) for _ in range(2)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=20)
    check = Session()
    try:
        final_wallet = check.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_id))
        assert final_wallet.balance_credits == Decimal("92.0000")
        assert check.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.call_id == call_id)) == 1
        assert check.scalar(select(func.count()).select_from(CreditTransaction).where(CreditTransaction.call_id == call_id)) == 1
        assert outcomes.count("settled") == 1
    finally:
        check.close()


def test_postgres_call_billing_migration_is_additive():
    url = os.getenv("POSTGRES_TEST_DATABASE_URL")
    if not url:
        pytest.skip("POSTGRES_TEST_DATABASE_URL is not configured")
    engine = create_engine(url, pool_pre_ping=True)
    from app.db import init_db as init_module
    columns = {column["name"] for column in __import__("sqlalchemy").inspect(engine).get_columns("calls")}
    with engine.begin() as connection:
        if "billing_status" in columns:
            connection.execute(text("ALTER TABLE calls DROP COLUMN billing_status"))
        if "billed_duration_seconds" in columns:
            connection.execute(text("ALTER TABLE calls DROP COLUMN billed_duration_seconds"))
    init_module.engine = engine
    init_module._ensure_call_billing_columns()
    init_module._ensure_call_billing_columns()
    columns = {column["name"] for column in __import__("sqlalchemy").inspect(engine).get_columns("calls")}
    assert {"billing_status", "billed_duration_seconds"} <= columns
