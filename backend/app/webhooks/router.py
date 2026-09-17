import json
import logging

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
logger = logging.getLogger(__name__)
call_result_service = CallResultService()
razorpay_client = RazorpayClient()


@router.post("/omnidimension/post-call")
async def receive_omnidimension_post_call(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        payload = await request.json()
    except ValueError as exc:
        logger.warning("[OMNI_WEBHOOK_REJECTED] method=%s path=%s reason=invalid_json", request.method, request.url.path)
        raise HTTPException(status_code=400, detail="Webhook payload must be valid JSON.") from exc
    if not isinstance(payload, dict):
        logger.warning("[OMNI_WEBHOOK_REJECTED] method=%s path=%s reason=payload_not_object", request.method, request.url.path)
        raise HTTPException(status_code=400, detail="Webhook payload must be a JSON object.")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    report = payload.get("call_report") if isinstance(payload.get("call_report"), dict) else {}
    logger.info(
        "[OMNI_WEBHOOK_RECEIVED] method=%s path=%s event_type=%s provider_call_id=%s provider_request_id=%s",
        request.method, request.url.path,
        payload.get("event_type") or payload.get("type") or payload.get("event") or report.get("event_type") or "unknown",
        payload.get("call_log_id") or payload.get("call_id") or payload.get("id") or "unknown",
        payload.get("requestId") or payload.get("request_id") or metadata.get("provider_request_id") or "unknown",
    )
    logger.info(
        "[OMNI_WEBHOOK_RAW_PAYLOAD] method=%s path=%s payload=%s",
        request.method, request.url.path,
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    logger.info(
        "[OMNI_EVENT_RECEIVED] local_call_id=%s employee_id=%s employee_version_id=%s provider_agent_id=%s "
        "provider_request_id=%s provider_call_id=%s event_type=%s provider_status=%s raw_reason=%s event_timestamp=%s",
        metadata.get("local_call_id"), metadata.get("employee_id"), metadata.get("employee_version_id"),
        metadata.get("provider_agent_id"), payload.get("requestId") or metadata.get("provider_request_id"),
        payload.get("call_id") or payload.get("call_log_id") or payload.get("id"),
        payload.get("event_type") or payload.get("type") or payload.get("event") or report.get("event_type"),
        payload.get("call_status") or payload.get("status") or report.get("status"),
        payload.get("end_reason") or payload.get("termination_reason") or payload.get("hangup_reason") or report.get("reason"),
        payload.get("event_timestamp") or payload.get("timestamp") or payload.get("created_at"),
    )
    try:
        call = call_result_service.process_post_call(db, payload)
    except Exception as exc:
        logger.exception(
            "[CALL_RUNTIME_EXCEPTION] local_call_id=%s provider_call_id=%s stage=webhook_processing exception_type=%s exception_message=%s",
            metadata.get("local_call_id"), payload.get("call_id") or payload.get("call_log_id") or payload.get("id"),
            type(exc).__name__, str(exc)[:300],
        )
        logger.warning("[OMNI_WEBHOOK_REJECTED] method=%s path=%s reason=processing_error exception_type=%s", request.method, request.url.path, type(exc).__name__)
        raise
    if call is None:
        logger.warning("[OMNI_WEBHOOK_REJECTED] method=%s path=%s reason=unmatched_call", request.method, request.url.path)
        return {"status": "ignored"}
    logger.info("[OMNI_WEBHOOK_PROCESSED] method=%s path=%s local_call_id=%s provider_call_id=%s provider_request_id=%s status=%s", request.method, request.url.path, call.id, call.provider_call_id or "unknown", (call.dispatch_metadata or {}).get("provider_request_id") or "unknown", call.status)
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
