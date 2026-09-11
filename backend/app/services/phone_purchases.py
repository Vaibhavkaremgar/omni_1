from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4
from collections.abc import Callable

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.omnidimension import OmniDimensionError, OmniDimensionPhoneNumberProvider
from app.integrations.razorpay import RazorpayClient
from app.models.phone_number import PhoneNumber
from app.models.phone_purchase import PhonePurchase
from app.models.tenant import Tenant
from app.services.top_ups import confirm_provider_payment, paise


class PhonePurchaseService:
    def __init__(self, provider: OmniDimensionPhoneNumberProvider, razorpay: RazorpayClient, price: Decimal):
        self.provider, self.razorpay, self.price = provider, razorpay, price

    def available(self, *, phone_number: str, region: str, carrier: str) -> bool:
        numbers, _, _, _, _ = self.provider.search_available_numbers(region=region, carrier=carrier, page=1, limit=150)
        return any(
            item.e164_number == phone_number and item.region == region and item.carrier == carrier
            for item in numbers
        )

    def fulfill(self, db: Session, purchase: PhonePurchase, tenant: Tenant,
                kyc_status: Callable[[], dict] | None = None) -> PhonePurchase:
        if purchase.fulfillment_status == "fulfilled":
            return purchase
        if kyc_status is not None:
            status = kyc_status()
            if status.get("can_purchase") is not True:
                purchase.fulfillment_status, purchase.failure_reason = "retryable", "kyc_incomplete"
                db.commit()
                return purchase
        if not self.available(phone_number=purchase.phone_number, region=purchase.region, carrier=purchase.carrier):
            purchase.fulfillment_status, purchase.failure_reason = "refund_pending", "unavailable"
            db.commit()
            return purchase
        try:
            result = self.provider.purchase_number(region=purchase.region, carrier=purchase.carrier,
                phone_number=purchase.phone_number, user_id=tenant.omni_reseller_user_id or "", idempotency_key=purchase.omni_idempotency_key)
        except OmniDimensionError:
            purchase.fulfillment_status, purchase.failure_reason = "retryable", "provider_error"
            db.commit()
            return purchase
        provider_id = result.get("order_id")
        local = db.scalar(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant.id, PhoneNumber.e164_number == purchase.phone_number))
        if local is None:
            local = PhoneNumber(tenant_id=tenant.id, e164_number=purchase.phone_number, provider_name="omnidimension",
                provider_phone_number_id=str(provider_id) if provider_id is not None else None, status="active",
                capabilities={"region": purchase.region, "carrier": purchase.carrier})
            db.add(local)
        purchase.omni_order_id = str(provider_id) if provider_id is not None else None
        purchase.fulfillment_status, purchase.failure_reason, purchase.fulfilled_at = "fulfilled", None, datetime.now(timezone.utc)
        db.commit()
        return purchase

    def verify_and_fulfill(self, db: Session, *, purchase: PhonePurchase, tenant: Tenant, payment_id: str, signature: str,
                           kyc_status: Callable[[], dict] | None = None) -> PhonePurchase:
        if purchase.fulfillment_status == "fulfilled":
            return purchase
        if purchase.razorpay_order_id is None or not self.razorpay.verify_checkout_signature(order_id=purchase.razorpay_order_id, payment_id=payment_id, signature=signature):
            raise HTTPException(status_code=400, detail="Invalid payment signature.")
        if purchase.payment_status != "paid":
            # Reuses the same captured-payment and exact-money verification used for wallet top-ups.
            adapter = type("PaymentRecord", (), {"provider_reference": purchase.razorpay_order_id, "amount": purchase.amount})()
            confirm_provider_payment(self.razorpay, adapter, payment_id)
            purchase.payment_status, purchase.razorpay_payment_id = "paid", payment_id
            db.commit()
        return self.fulfill(db, purchase, tenant, kyc_status=kyc_status)


def message_for(purchase: PhonePurchase) -> str:
    if purchase.fulfillment_status == "fulfilled": return "Number activated"
    if purchase.fulfillment_status == "refund_pending": return "That number is no longer available. Your payment is being handled for recovery/refund."
    if purchase.fulfillment_status == "retryable": return "Payment received. Activating your number is being retried safely."
    return "Payment received. Activating your number..."
