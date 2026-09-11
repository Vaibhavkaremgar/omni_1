from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.v1.endpoints import calls as calls_endpoint
from app.main import app
from app.models import Call, CreditTransaction, CreditWallet, Tenant, UsageRecord
from app.models.enums import CallDirection, CallStatus
from app.services.call_results import CallResultService
from app.services.instant_calls import InstantCallService
from app.services.pricing import calculate_call_charge
from app.services.wallets import InsufficientBalanceError, ensure_wallet, internal_top_up, require_minimum_balance

from test_instant_calls import authenticated_client, call_database, create_employee, create_number, request


def local_call(db, tenant, employee, phone, provider_id="bill-call"):
    call = Call(
        tenant_id=tenant.id, employee_id=employee.id, phone_number_id=phone.id,
        direction=CallDirection.outbound.value, status=CallStatus.queued.value,
        customer_phone_number="+15551234567", provider_call_id=provider_id,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def completed_payload(call, duration="4:30"):
    return {
        "id": call.provider_call_id,
        "call_status": "completed",
        "call_duration": duration,
        "summary": "Completed",
        "metadata": {"local_call_id": str(call.id), "tenant_id": str(call.tenant_id)},
    }


def test_pricing_uses_decimal_and_rounds_half_up():
    assert calculate_call_charge(270) == (Decimal("4.5000"), Decimal("36.00"))
    assert calculate_call_charge(272) == (Decimal("4.5333"), Decimal("36.27"))


def test_completed_call_creates_one_usage_record_and_debit(call_database):
    db, tenant_a, _ = call_database
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    call = local_call(db, tenant_a, employee, phone)
    service = CallResultService()
    service.process_post_call(db, completed_payload(call))
    service.process_post_call(db, completed_payload(call))
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_a.id))
    usage = db.scalar(select(UsageRecord).where(UsageRecord.call_id == call.id))
    transaction = db.scalar(select(CreditTransaction).where(CreditTransaction.call_id == call.id))
    assert wallet.balance_credits == Decimal("64.0000")
    assert usage.duration_seconds == 270
    assert usage.unit_price == Decimal("8.0000")
    assert usage.customer_charge_amount == Decimal("36.0000")
    assert transaction.amount_credits == Decimal("-36.0000")
    assert transaction.balance_before == Decimal("100.0000")
    assert transaction.balance_after == Decimal("64.0000")
    assert db.scalar(select(func.count()).select_from(UsageRecord).where(UsageRecord.call_id == call.id)) == 1


def test_failed_and_missing_duration_do_not_charge(call_database):
    db, tenant_a, _ = call_database
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    failed = local_call(db, tenant_a, employee, phone, "failed-call")
    missing = local_call(db, tenant_a, employee, phone, "missing-call")
    service = CallResultService()
    service.process_post_call(db, {"id": "failed-call", "call_status": "no-answer", "metadata": {"local_call_id": str(failed.id)}})
    service.process_post_call(db, {"id": "missing-call", "call_status": "completed", "metadata": {"local_call_id": str(missing.id)}})
    assert db.scalar(select(UsageRecord).where(UsageRecord.tenant_id == tenant_a.id)) is None
    assert db.scalar(select(CreditTransaction).where(CreditTransaction.tenant_id == tenant_a.id)) is None


def test_billing_failure_preserves_completed_call_without_debit(call_database):
    db, tenant_a, _ = call_database
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_a.id))
    wallet.balance_credits = Decimal("1.00")
    db.commit()
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    call = local_call(db, tenant_a, employee, phone, "low-balance-call")
    CallResultService().process_post_call(db, completed_payload(call))
    db.refresh(call)
    assert call.status == CallStatus.completed.value
    assert call.dispatch_metadata["billing_error"]
    assert db.scalar(select(UsageRecord).where(UsageRecord.call_id == call.id)) is None
    assert db.scalar(select(CreditTransaction).where(CreditTransaction.call_id == call.id)) is None


def test_wallet_creation_is_zero_balance_and_singleton(call_database):
    db, tenant_a, _ = call_database
    new_tenant = Tenant(name="New Tenant", slug="new-billing-tenant")
    db.add(new_tenant)
    db.commit()
    wallet = ensure_wallet(db, new_tenant.id)
    db.commit()
    assert wallet.currency == "INR"
    assert wallet.balance_credits == Decimal("0.0000")
    assert db.scalar(select(func.count()).select_from(CreditWallet).where(CreditWallet.tenant_id == new_tenant.id)) == 1


def test_empty_wallet_cannot_dispatch(call_database, monkeypatch):
    db, tenant_a, _ = call_database
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_a.id))
    wallet.balance_credits = Decimal("0")
    employee = create_employee(db, tenant_a)
    phone = create_number(db, tenant_a)
    db.commit()
    try:
        require_minimum_balance(db, tenant_a.id, Decimal("8.00"))
    except InsufficientBalanceError:
        pass
    else:
        raise AssertionError("empty wallet passed minimum balance check")
    api = authenticated_client(db, "call-user-a", monkeypatch)
    monkeypatch.setattr(calls_endpoint, "instant_call_service", InstantCallService(None))
    try:
        with api:
            response = api.post("/api/v1/calls/instant", json=request(employee.id, phone.id), headers={"Authorization": "Bearer a"})
        assert response.status_code == 422
        assert "balance" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_billing_endpoints_are_authenticated_and_tenant_scoped(call_database, monkeypatch):
    db, tenant_a, tenant_b = call_database
    employee = create_employee(db, tenant_b)
    phone = create_number(db, tenant_b, provider_id="billing-b-number")
    call = local_call(db, tenant_b, employee, phone, "billing-b-call")
    CallResultService().process_post_call(db, completed_payload(call))
    api = authenticated_client(db, "call-user-a", monkeypatch)
    try:
        with api:
            assert api.get("/api/v1/billing/wallet", headers={"Authorization": "Bearer a"}).json()["balance"] == "100.00"
            assert api.get("/api/v1/billing/transactions", headers={"Authorization": "Bearer a"}).json() == []
            assert api.get("/api/v1/billing/usage", headers={"Authorization": "Bearer a"}).json() == []
    finally:
        app.dependency_overrides.clear()
    with TestClient(app) as unauthenticated:
        assert unauthenticated.get("/api/v1/billing/wallet").status_code == 401


def test_internal_top_up_is_not_a_tenant_api_operation(call_database):
    db, tenant_a, _ = call_database
    internal_top_up(db, tenant_a.id, Decimal("10.00"))
    db.commit()
    wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_a.id))
    assert wallet.balance_credits == Decimal("110.0000")
    assert db.scalar(select(CreditTransaction).where(CreditTransaction.reference_type == "internal_top_up")) is not None
