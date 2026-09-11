from decimal import Decimal

from sqlalchemy import select

from app.api.v1.endpoints import billing as billing_endpoint
from app.core.config import get_settings
from app.models import BillingTransaction, CreditTransaction, CreditWallet
from test_instant_calls import authenticated_client, call_database


class FakeRazorpay:
    def create_order(self, *, amount_paise, receipt, notes):
        return {"id": "order_topup_1", "amount": amount_paise, "currency": "INR"}

    def verify_checkout_signature(self, **kwargs):
        return kwargs["signature"] == "valid"

    def fetch_payment(self, payment_id):
        return {"id": payment_id, "order_id": "order_topup_1", "amount": 50000, "currency": "INR", "status": "captured"}

    def fetch_order(self, order_id):
        return {"id": order_id, "amount": 50000, "currency": "INR", "status": "paid"}


def configure(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "razorpay_key_id", "rzp_test_public")
    monkeypatch.setattr(settings, "razorpay_key_secret", "secret")
    monkeypatch.setattr(settings, "razorpay_minimum_top_up_inr", Decimal("10"))
    monkeypatch.setattr(settings, "razorpay_maximum_top_up_inr", Decimal("100000"))
    monkeypatch.setattr(billing_endpoint, "razorpay_client", FakeRazorpay())


def test_top_up_order_and_verified_credit_are_tenant_scoped_and_idempotent(call_database, monkeypatch):
    db, tenant_a, _ = call_database
    configure(monkeypatch)
    api = authenticated_client(db, "call-user-a", monkeypatch)
    try:
        with api:
            order = api.post("/api/v1/billing/top-ups/order", json={"amount": "500.00"}, headers={"Authorization": "Bearer a"})
            assert order.status_code == 201
            assert order.json()["key_id"] == "rzp_test_public"
            assert "secret" not in order.text
            assert api.post("/api/v1/billing/top-ups/verify", json={"razorpay_order_id": "order_topup_1", "razorpay_payment_id": "pay_1", "razorpay_signature": "valid"}, headers={"Authorization": "Bearer a"}).status_code == 200
            assert api.post("/api/v1/billing/top-ups/verify", json={"razorpay_order_id": "order_topup_1", "razorpay_payment_id": "pay_1", "razorpay_signature": "valid"}, headers={"Authorization": "Bearer a"}).status_code == 200
        wallet = db.scalar(select(CreditWallet).where(CreditWallet.tenant_id == tenant_a.id))
        assert wallet.balance_credits == Decimal("600.0000")
        assert len(db.scalars(select(CreditTransaction).where(CreditTransaction.tenant_id == tenant_a.id)).all()) == 1
        assert db.scalar(select(BillingTransaction).where(BillingTransaction.tenant_id == tenant_a.id)).status == "paid"
    finally:
        api.app.dependency_overrides.clear()


def test_top_up_rejects_bad_amount_and_signature(call_database, monkeypatch):
    db, _, _ = call_database
    configure(monkeypatch)
    api = authenticated_client(db, "call-user-a", monkeypatch)
    try:
        with api:
            assert api.post("/api/v1/billing/top-ups/order", json={"amount": "0"}, headers={"Authorization": "Bearer a"}).status_code == 422
            api.post("/api/v1/billing/top-ups/order", json={"amount": "500"}, headers={"Authorization": "Bearer a"})
            assert api.post("/api/v1/billing/top-ups/verify", json={"razorpay_order_id": "order_topup_1", "razorpay_payment_id": "pay_1", "razorpay_signature": "bad"}, headers={"Authorization": "Bearer a"}).status_code == 400
    finally:
        api.app.dependency_overrides.clear()
