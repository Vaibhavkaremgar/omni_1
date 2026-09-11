from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.schemas.employee_interview import (
    InterviewAnswerRequest,
    InterviewConsumeRequest,
    InterviewStartRequest,
    InterviewStateRead,
)
from app.services.auth import AuthenticatedUser
from app.services.employee_interview import EmployeeInterviewService, RealLLMService

router = APIRouter(prefix="/employees/{employee_id}/interview", tags=["employee-interview"])
interview_service = EmployeeInterviewService()


def _state(session, service: EmployeeInterviewService) -> InterviewStateRead:
    data = InterviewStateRead.model_validate(session, from_attributes=True)
    data.llm_used = isinstance(service.llm_service, RealLLMService)
    return data


@router.post("/start", response_model=InterviewStateRead)
def start_interview(
    employee_id: UUID,
    payload: InterviewStartRequest | None = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterviewStateRead:
    session = interview_service.start(db, employee_id, current_user.tenant.id, payload.restart if payload else False)
    return _state(session, interview_service)


@router.post("/answer", response_model=InterviewStateRead)
def submit_answer(
    employee_id: UUID,
    payload: InterviewAnswerRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterviewStateRead:
    session = interview_service.submit_answer(
        db,
        employee_id,
        current_user.tenant.id,
        payload.question,
        payload.answer,
        payload.skip,
    )
    return _state(session, interview_service)


@router.post("/consume", response_model=InterviewStateRead)
def consume_suggestion(
    employee_id: UUID,
    payload: InterviewConsumeRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterviewStateRead:
    session = interview_service.consume_suggestion(
        db, employee_id, current_user.tenant.id, payload.question
    )
    return _state(session, interview_service)


@router.get("", response_model=InterviewStateRead)
def get_interview_state(
    employee_id: UUID,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InterviewStateRead:
    interview_service.get_employee(db, employee_id, current_user.tenant.id)
    session = interview_service.get_session(db, employee_id, current_user.tenant.id)
    return _state(session, interview_service)
