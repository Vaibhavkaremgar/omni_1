from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from uuid import UUID, uuid4

from app.api.deps import get_current_user, get_db
from app.models.phone_number import PhoneNumber
from app.schemas.phone_number import (KycInitializeRequest, KycStepRequest, MarketplaceSearchRead, PhoneNumberRead, PlatformDemoPhoneCreate,
    PhonePurchaseOrderRead, PhonePurchaseOrderRequest, PhonePurchaseStatusRead, PhonePurchaseVerify)
from app.services.auth import AuthenticatedUser
from app.services.phone_numbers import (PhoneNumberMarketplaceService, PhoneNumberService, get_phone_number_marketplace_service,
                                        get_reseller_kyc_service, get_phone_lifecycle_service)
from app.services.reseller_kyc import ResellerKycService, map_kyc_error
from app.integrations.omnidimension import OmniDimensionClientError, OmniDimensionError
from app.integrations.razorpay import RazorpayClient, RazorpayError
from app.models.phone_purchase import PhonePurchase
from app.services.phone_purchases import PhonePurchaseService, message_for
from app.services.phone_lifecycle import PhoneLifecycleService
from app.services.top_ups import paise
from app.core.config import get_settings
from app.services.auth import require_admin
from app.models.platform_demo_phone_access import PlatformDemoPhoneAccess
from app.models.tenant import Tenant


router = APIRouter(prefix="/phone-numbers", tags=["phone-numbers"])
razorpay_client = RazorpayClient()


@router.post("/platform-demo", response_model=PhoneNumberRead, status_code=201)
def register_platform_demo_phone(payload: PlatformDemoPhoneCreate, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db)) -> PhoneNumber:
    require_admin(current_user)
    try:
        target_tenant = current_user.tenant.id if payload.authorized_tenant_id is None else UUID(payload.authorized_tenant_id)
    except (ValueError, TypeError):
        raise HTTPException(status_code=422, detail="authorized_tenant_id must be a valid UUID.") from None
    if db.get(Tenant, target_tenant) is None:
        raise HTTPException(status_code=404, detail="Authorized tenant not found.")
    existing = db.scalar(select(PhoneNumber).where(PhoneNumber.provider_name == payload.provider, PhoneNumber.provider_phone_number_id == payload.provider_phone_number_id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="That provider phone ID is already registered.")
    number = PhoneNumber(e164_number=payload.phone_number, provider_name=payload.provider, provider_phone_number_id=payload.provider_phone_number_id, ownership="platform_demo", status="active", label="Platform demo phone")
    db.add(number); db.flush()
    db.add(PlatformDemoPhoneAccess(phone_number_id=number.id, tenant_id=target_tenant))
    db.commit(); db.refresh(number)
    return number


@router.get("", response_model=list[PhoneNumberRead])
def list_phone_numbers(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PhoneNumber]:
    return PhoneNumberService.list_for_tenant(db, current_user.tenant.id)


@router.get("/marketplace", response_model=MarketplaceSearchRead)
def search_marketplace(
    region: str = Query(..., pattern="^(IN|US)$"),
    carrier: str = Query(..., min_length=1, max_length=80, pattern="^[A-Za-z0-9-]+$"),
    pattern: str | None = Query(None, min_length=1, max_length=30, pattern="^[0-9]+$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=150),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: PhoneNumberMarketplaceService = Depends(get_phone_number_marketplace_service),
) -> MarketplaceSearchRead:
    del current_user  # Authentication establishes the caller; no tenant id is accepted or used.
    try:
        return service.search(region=region, carrier=carrier, pattern=pattern, page=page, limit=limit)
    except OmniDimensionError:
        raise HTTPException(status_code=502, detail="Live number availability is temporarily unavailable.") from None


def _kyc_error(error: OmniDimensionError) -> HTTPException:
    status_code, detail = map_kyc_error(error)
    return HTTPException(status_code=status_code, detail=detail)


@router.post("/kyc/initialize")
def initialize_kyc(
    request: KycInitializeRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: ResellerKycService = Depends(get_reseller_kyc_service),
) -> dict:
    try:
        return service.initialize(db, tenant=current_user.tenant, user=current_user.user, phone=request.phone)
    except OmniDimensionError as error:
        raise _kyc_error(error) from None


@router.get("/kyc/status")
def kyc_status(
    carrier: str | None = Query(None, min_length=1, max_length=80, pattern="^[A-Za-z0-9-]+$"),
    region: str = Query("IN", pattern="^[A-Z]{2}$"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    service: ResellerKycService = Depends(get_reseller_kyc_service),
) -> dict:
    try:
        if carrier is None:
            return service.status(db, tenant=current_user.tenant)
        return service.status(db, tenant=current_user.tenant, region=region, carrier=carrier)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except OmniDimensionError as error:
        raise _kyc_error(error) from None


@router.get("/kyc/requirements")
def kyc_requirements(
    region: str = Query("IN", pattern="^[A-Z]{2}$"),
    carrier: str = Query(..., min_length=1, max_length=80, pattern="^[A-Za-z0-9-]+$"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    service: ResellerKycService = Depends(get_reseller_kyc_service),
) -> dict:
    del current_user
    try:
        return service.requirements(region=region, carrier=carrier)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except OmniDimensionError as error:
        raise _kyc_error(error) from None


@router.post("/kyc/step")
def submit_kyc_step(
    request: KycStepRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    marketplace: PhoneNumberMarketplaceService = Depends(get_phone_number_marketplace_service),
    service: ResellerKycService = Depends(get_reseller_kyc_service),
) -> dict:
    try:
        if not marketplace.validate_selected_number(
            phone_number=request.phone_number, region=request.region, carrier=request.carrier
        ):
            raise HTTPException(status_code=409, detail="That number is not available for the selected carrier.")
        return service.submit(
            db, tenant=current_user.tenant,
            step=request.step, region=request.region,
            carrier=request.carrier, values=request.values,
        )
    except LookupError:
        raise HTTPException(status_code=409, detail="Start verification before submitting a step.") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except OmniDimensionError as error:
        if isinstance(error, OmniDimensionClientError) and error.status_code == 409:
            try:
                current = service.status(db, tenant=current_user.tenant, region=request.region, carrier=request.carrier)
            except (OmniDimensionError, ValueError):
                current = {"region": request.region, "carrier": request.carrier, "next_step": None, "can_purchase": False}
            current["message"] = "That verification step is not the current required step. Refreshing your verification status."
            raise HTTPException(status_code=409, detail=current) from None
        raise _kyc_error(error) from None


def _purchase_read(purchase: PhonePurchase) -> PhonePurchaseStatusRead:
    return PhonePurchaseStatusRead(purchase_id=str(purchase.id), payment_status=purchase.payment_status,
        fulfillment_status=purchase.fulfillment_status, phone_number=purchase.phone_number, message=message_for(purchase))


@router.post("/purchase/order", response_model=PhonePurchaseOrderRead, status_code=201)
def create_purchase_order(request: PhonePurchaseOrderRequest, current_user: AuthenticatedUser = Depends(get_current_user),
                          db: Session = Depends(get_db), marketplace: PhoneNumberMarketplaceService = Depends(get_phone_number_marketplace_service),
                          kyc: ResellerKycService = Depends(get_reseller_kyc_service)) -> PhonePurchaseOrderRead:
    settings = get_settings()
    if not settings.razorpay_key_id or not settings.razorpay_key_secret:
        raise HTTPException(status_code=503, detail="Payments are not configured.")
    tenant = current_user.tenant
    if not tenant.omni_reseller_user_id:
        raise HTTPException(status_code=409, detail="Complete identity verification before purchasing a number.")
    try:
        status = kyc.status(db, tenant=tenant, region=request.region, carrier=request.carrier)
    except OmniDimensionError as error:
        raise _kyc_error(error) from None
    if status.get("can_purchase") is not True:
        raise HTTPException(status_code=409, detail="Complete identity verification before purchasing a number.")
    existing = db.scalar(select(PhonePurchase).where(PhonePurchase.tenant_id == tenant.id, PhonePurchase.phone_number == request.phone_number,
        PhonePurchase.payment_status.in_(("pending", "paid")), PhonePurchase.fulfillment_status.in_(("pending", "retryable"))))
    if existing is not None:
        raise HTTPException(status_code=409, detail="A purchase attempt for this number is already in progress.")
    try:
        if not PhonePurchaseService(marketplace.provider, razorpay_client, settings.phone_number_monthly_price_inr).available(phone_number=request.phone_number, region=request.region, carrier=request.carrier):
            raise HTTPException(status_code=409, detail="That number is no longer available.")
    except OmniDimensionError:
        raise HTTPException(status_code=502, detail="Live number availability is temporarily unavailable.") from None
    purchase = PhonePurchase(tenant_id=tenant.id, created_by_user_id=current_user.user.id, phone_number=request.phone_number,
        region=request.region, carrier=request.carrier, amount=settings.phone_number_monthly_price_inr, currency="INR",
        omni_idempotency_key=str(uuid4()), payment_status="pending", fulfillment_status="pending")
    db.add(purchase); db.flush()
    receipt = f"phone_{purchase.id.hex[:28]}"
    try:
        order = razorpay_client.create_order(amount_paise=paise(purchase.amount), receipt=receipt, notes={"phone_purchase_id": str(purchase.id)})
        if not isinstance(order.get("id"), str) or order.get("amount") != paise(purchase.amount) or order.get("currency") != "INR":
            raise RazorpayError("Invalid order response")
    except RazorpayError:
        purchase.payment_status, purchase.fulfillment_status = "failed", "failed"; db.commit()
        raise HTTPException(status_code=502, detail="Unable to create payment order.") from None
    purchase.razorpay_order_id = order["id"]; db.commit()
    return PhonePurchaseOrderRead(purchase_id=str(purchase.id), razorpay_order_id=order["id"], amount=purchase.amount, currency="INR", key_id=settings.razorpay_key_id)


@router.post("/purchase/verify", response_model=PhonePurchaseStatusRead)
def verify_purchase(request: PhonePurchaseVerify, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db),
                    marketplace: PhoneNumberMarketplaceService = Depends(get_phone_number_marketplace_service),
                    kyc: ResellerKycService = Depends(get_reseller_kyc_service)) -> PhonePurchaseStatusRead:
    purchase = db.scalar(select(PhonePurchase).where(PhonePurchase.tenant_id == current_user.tenant.id,
        PhonePurchase.razorpay_order_id == request.razorpay_order_id).with_for_update())
    if purchase is None:
        raise HTTPException(status_code=404, detail="Phone purchase order not found.")
    service = PhonePurchaseService(marketplace.provider, razorpay_client, get_settings().phone_number_monthly_price_inr)
    try:
        return _purchase_read(service.verify_and_fulfill(db, purchase=purchase, tenant=current_user.tenant,
            payment_id=request.razorpay_payment_id, signature=request.razorpay_signature,
            kyc_status=lambda: kyc.status(db, tenant=current_user.tenant, region=purchase.region, carrier=purchase.carrier)))
    except RazorpayError:
        raise HTTPException(status_code=502, detail="Unable to verify payment with provider.") from None
    except OmniDimensionError as error:
        raise _kyc_error(error) from None


@router.post("/sync", response_model=list[PhoneNumberRead])
def sync_tenant_numbers(current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db),
                        service: PhoneLifecycleService = Depends(get_phone_lifecycle_service)) -> list[PhoneNumber]:
    try:
        return service.sync(db, current_user.tenant)
    except OmniDimensionError:
        raise HTTPException(status_code=502, detail="Unable to refresh phone numbers right now.") from None


@router.post("/{phone_number_id}/release", response_model=PhoneNumberRead)
def release_phone_number(phone_number_id: UUID, current_user: AuthenticatedUser = Depends(get_current_user), db: Session = Depends(get_db),
                         service: PhoneLifecycleService = Depends(get_phone_lifecycle_service)) -> PhoneNumber:
    number = db.scalar(select(PhoneNumber).where(PhoneNumber.id == phone_number_id, PhoneNumber.tenant_id == current_user.tenant.id).with_for_update())
    if number is None:
        raise HTTPException(status_code=404, detail="Phone number not found.")
    if number.status not in {"active", "release_failed", "release_pending", "released"}:
        raise HTTPException(status_code=409, detail="This phone number cannot be released.")
    try:
        return service.release(db, current_user.tenant, number)
    except LookupError:
        raise HTTPException(status_code=409, detail="Phone number cannot be released yet.") from None
