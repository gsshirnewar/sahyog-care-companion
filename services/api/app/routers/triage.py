"""Authenticated W1 triage and booking-resume API endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from services.api.app.auth.context import RequestContext, get_request_context
from services.workflows.triage.service import (
    TriageIdempotencyConflictError,
    TriageRequest,
    TriageService,
)

router = APIRouter(prefix="/v1", tags=["triage-booking"])


class TriageInput(BaseModel):
    intake_text: str = Field(min_length=1, max_length=4000)
    language: str = Field(min_length=2, max_length=35, default="en")


class SlotOptionOutput(BaseModel):
    slot_id: UUID
    doctor_id: UUID
    doctor_name: str
    clinic_id: UUID
    clinic_name: str
    specialty_code: str
    starts_at: datetime
    ends_at: datetime


class TriageOutput(BaseModel):
    run_id: UUID | None
    route: str
    reason: str
    specialty_code: str | None = None
    options: list[SlotOptionOutput] = Field(default_factory=list)
    rule_set_version: str | None = None
    matched_rule_ids: list[str] = []


class ResumeInput(BaseModel):
    selected_slot_id: UUID


class BookingOutput(BaseModel):
    appointment_id: UUID
    slot_id: UUID
    status: str
    created: bool


async def get_triage_service() -> TriageService:
    """Must be configured with persistent stores by deployment wiring."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Triage workflow is not configured",
    )


@router.post("/triage", response_model=TriageOutput)
async def start_triage(
    payload: TriageInput,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    context: RequestContext = Depends(get_request_context),
    service: TriageService = Depends(get_triage_service),
) -> TriageOutput:
    try:
        result = await service.start(
            context,
            TriageRequest(payload.intake_text, payload.language, idempotency_key),
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TriageIdempotencyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return TriageOutput(
        run_id=result.run_id,
        route=result.route,
        reason=result.reason,
        specialty_code=result.specialty_code,
        options=[
            SlotOptionOutput(
                slot_id=option.slot_id,
                doctor_id=option.doctor_id,
                doctor_name=option.doctor_name,
                clinic_id=option.clinic_id,
                clinic_name=option.clinic_name,
                specialty_code=option.specialty_code,
                starts_at=option.starts_at,
                ends_at=option.ends_at,
            )
            for option in result.options
        ],
        rule_set_version=result.rule_set_version,
        matched_rule_ids=list(result.matched_rule_ids),
    )


@router.post("/runs/{run_id}/resume", response_model=BookingOutput)
async def resume_booking(
    run_id: UUID,
    payload: ResumeInput,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255),
    context: RequestContext = Depends(get_request_context),
    service: TriageService = Depends(get_triage_service),
) -> BookingOutput:
    try:
        result = await service.resume(
            context,
            run_id=run_id,
            selected_slot_id=payload.selected_slot_id,
            idempotency_key=idempotency_key,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return BookingOutput(
        appointment_id=result.appointment_id,
        slot_id=result.slot_id,
        status=result.status,
        created=result.created,
    )
