"""Clinician-authenticated scribe draft, review, and signing endpoints."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from services.api.app.auth.context import RequestContext, get_request_context
from services.workflows.scribe.service import ClinicalNoteVersion, SOAPContent, ScribeService

router = APIRouter(prefix="/v1", tags=["scribe"])


class DraftInput(BaseModel):
    transcript: str = Field(min_length=1, max_length=20_000)


class ReviewInput(BaseModel):
    expected_version: int = Field(ge=1)
    action: Literal["edit", "reject", "sign"]
    edited_content: SOAPContent | None = None
    rejection_reason: str | None = Field(default=None, max_length=500)


class NoteVersionOutput(BaseModel):
    note_id: UUID
    encounter_id: UUID
    version: int
    content: SOAPContent
    status: str
    signed_by: UUID | None
    signed_at: datetime | None
    content_hash: str | None
    prior_version: int | None
    rejection_reason: str | None


async def get_scribe_service() -> ScribeService:
    """Must be configured with encounter, consent, audit, and note persistence."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Scribe workflow is not configured",
    )


def _output(note: ClinicalNoteVersion) -> NoteVersionOutput:
    return NoteVersionOutput(
        note_id=note.note_id,
        encounter_id=note.encounter_id,
        version=note.version,
        content=note.content,
        status=note.status.value,
        signed_by=note.signed_by,
        signed_at=note.signed_at,
        content_hash=note.content_hash,
        prior_version=note.prior_version,
        rejection_reason=note.rejection_reason,
    )


@router.post("/encounters/{encounter_id}/draft", response_model=NoteVersionOutput, status_code=201)
async def create_scribe_draft(
    encounter_id: UUID,
    payload: DraftInput,
    context: RequestContext = Depends(get_request_context),
    service: ScribeService = Depends(get_scribe_service),
) -> NoteVersionOutput:
    try:
        note = await service.create_draft(context, encounter_id=encounter_id, transcript=payload.transcript)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _output(note)


@router.get("/encounters/{encounter_id}/draft", response_model=NoteVersionOutput)
async def get_scribe_draft(
    encounter_id: UUID,
    context: RequestContext = Depends(get_request_context),
    service: ScribeService = Depends(get_scribe_service),
) -> NoteVersionOutput:
    try:
        note = await service.latest(context, encounter_id=encounter_id)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _output(note)


@router.post("/encounters/{encounter_id}/review", response_model=NoteVersionOutput)
async def review_scribe_note(
    encounter_id: UUID,
    payload: ReviewInput,
    context: RequestContext = Depends(get_request_context),
    service: ScribeService = Depends(get_scribe_service),
) -> NoteVersionOutput:
    try:
        note = await service.review(
            context,
            encounter_id=encounter_id,
            expected_version=payload.expected_version,
            action=payload.action,
            edited_content=payload.edited_content,
            rejection_reason=payload.rejection_reason,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _output(note)
