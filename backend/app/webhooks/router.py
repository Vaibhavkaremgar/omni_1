from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.call_results import CallResultService
from app.integrations.razorpay import RazorpayClient
from app.models.billing_transaction import BillingTransaction
from app.models.enums import BillingTransactionStatus, BillingTransactionType
from app.services.top_ups import confirm_provider_payment
from app.services.wallets import credit_verified_top_up

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
call_result_service = CallResultService()
razorpay_client = RazorpayClient()


@router.post("/omnidimension/post-call")
async def receive_omnidimension_post_call(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Webhook payload must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")
    call = call_result_service.process_post_call(db, payload)
    if call is None:
        return {"status": "ignored"}
    return {"status": "processed", "call_id": str(call.id)}


@router.post("/razorpay")
async def receive_razorpay_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not signature or not razorpay_client.verify_webhook_signature(raw_body=raw_body, signature=signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")
    try:
        event = await request.json()
        payment = event["payload"]["payment"]["entity"]
        order = event["payload"]["order"]["entity"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Webhook payload must contain an order and payment.")
    if event.get("event") != "order.paid":
        return {"status": "ignored"}
    order_id, payment_id = order.get("id"), payment.get("id")
    if not isinstance(order_id, str) or not isinstance(payment_id, str):
        raise HTTPException(status_code=400, detail="Webhook contains invalid identifiers.")
    top_up = db.scalar(select(BillingTransaction).where(
        BillingTransaction.provider_reference == order_id,
        BillingTransaction.transaction_type == BillingTransactionType.payment.value,
    ).with_for_update())
    if top_up is None:
        return {"status": "ignored"}
    if top_up.status == BillingTransactionStatus.paid.value:
        return {"status": "paid"}
    # The webhook is authenticated, and the provider API check additionally verifies captured status and money values.
    confirm_provider_payment(razorpay_client, top_up, payment_id)
    try:
        credit_verified_top_up(db, top_up, payment_id)
        db.commit()
    except IntegrityError:
        db.rollback()
        return {"status": "paid"}
    return {"status": "paid"}
