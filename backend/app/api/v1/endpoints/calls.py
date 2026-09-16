from fastapi import APIRouter, Depends, HTTPException, status
from uuid import UUID
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.integrations.omnidimension import OmniDimensionCallProvider, OmniDimensionClient
from app.integrations.omnidimension.exceptions import OmniDimensionError
from app.models.call import Call
from app.schemas.call import CallRead, InstantCallRead, InstantCallRequest
from app.services.auth import AuthenticatedUser
from app.services.instant_calls import (
    DuplicateInstantCallError,
    InstantCallNotFoundError,
    InstantCallService,
    InstantCallValidationError,
)
from app.services.call_results import CallResultService


router = APIRouter(prefix="/calls", tags=["calls"])
logger = logging.getLogger(__name__)
instant_call_service: InstantCallService | None = None


def get_instant_call_service() -> InstantCallService:
    global instant_call_service
    if instant_call_service is None:
        instant_call_service = InstantCallService(OmniDimensionCallProvider(OmniDimensionClient(get_settings())))
    return instant_call_service


@router.post("/instant", response_model=InstantCallRead, status_code=status.HTTP_201_CREATED)
def dispatch_instant_call(
    payload: InstantCallRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InstantCallRead:
    try:
        call = get_instant_call_service().dispatch(db, current_user.tenant.id, payload)
    except InstantCallNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateInstantCallError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InstantCallValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OmniDimensionError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unable to dispatch the call through the provider. Please retry.",
        ) from exc
    return call


@router.get("", response_model=list[CallRead])
def list_calls(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[Call]:
    return list(
        db.scalars(
            select(Call)
            .where(Call.tenant_id == current_user.tenant.id)
            .order_by(Call.created_at.desc())
            .limit(100)
        ).all()
    )


@router.get("/{call_id}", response_model=CallRead)
def get_call(
    call_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Call:
    try:
        parsed_call_id = UUID(call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Call not found.") from exc
    call = db.scalar(select(Call).where(Call.id == parsed_call_id, Call.tenant_id == current_user.tenant.id))
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found.")
    return call


@router.post("/{call_id}/refresh", response_model=CallRead)
def refresh_call(
    call_id: str,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Call:
    """Pull the provider call log when a post-call webhook was delayed or missed."""
    try:
        parsed_call_id = UUID(call_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Call not found") from exc
    call = db.scalar(select(Call).where(Call.id == parsed_call_id, Call.tenant_id == current_user.tenant.id))
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    if not call.provider_call_id or call.status not in {"queued", "ringing", "in_progress"}:
        return call
    try:
        provider = get_instant_call_service().provider
        payload = provider.get_call_log(call.provider_call_id)
        logger.info(
            "Provider call-log refresh local_call_id=%s provider_call_id=%s top_level_keys=%s "
            "status=%s end_reason=%s termination_source=%s",
            call.id, call.provider_call_id, list(payload) if isinstance(payload, dict) else type(payload).__name__,
            payload.get("call_status") or payload.get("status") if isinstance(payload, dict) else None,
            payload.get("end_reason") or payload.get("termination_reason") or payload.get("hangup_reason") if isinstance(payload, dict) else None,
            payload.get("termination_source") or payload.get("end_source") if isinstance(payload, dict) else None,
        )
        rows = payload.get("call_log_data") if isinstance(payload, dict) else None
        event = rows[0] if isinstance(rows, list) and rows else payload
        if isinstance(event, dict):
            event = {**event, "metadata": {"local_call_id": str(call.id), "tenant_id": str(call.tenant_id)}}
            refreshed = CallResultService().process_post_call(db, event)
            if refreshed is not None:
                return refreshed
    except Exception as exc:
        # A transient provider read failure must not break the Calls page.
        logger.exception("Provider call-log refresh failed local_call_id=%s provider_call_id=%s error=%s", call.id, call.provider_call_id, exc)
        db.refresh(call)
    return call
