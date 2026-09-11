from __future__ import annotations

from decimal import Decimal

from fastapi import HTTPException, status

from app.integrations.razorpay import RazorpayClient, RazorpayError
from app.models.billing_transaction import BillingTransaction


def paise(amount: Decimal) -> int:
    return int((amount * Decimal("100")).to_integral_exact())


def confirm_provider_payment(client: RazorpayClient, top_up: BillingTransaction, payment_id: str) -> None:
    """Require a captured payment and a paid order that exactly match our record."""
    try:
        payment = client.fetch_payment(payment_id)
        order = client.fetch_order(top_up.provider_reference or "")
    except RazorpayError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to verify payment with provider.") from exc
    expected = paise(Decimal(top_up.amount))
    if (
        payment.get("id") != payment_id
        or payment.get("order_id") != top_up.provider_reference
        or payment.get("amount") != expected
        or payment.get("currency") != "INR"
        or payment.get("status") != "captured"
        or order.get("id") != top_up.provider_reference
        or order.get("amount") != expected
        or order.get("currency") != "INR"
        or order.get("status") != "paid"
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payment does not match a captured top-up order.")
