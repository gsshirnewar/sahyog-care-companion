"""Authenticated patient-scoped document ingestion and records Q&A endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from services.api.app.auth.context import RequestContext, get_request_context
from services.workflows.records_qa.service import MAX_PDF_BYTES, RecordsService

router = APIRouter(prefix="/v1", tags=["records"])


class DocumentUploadOutput(BaseModel):
    doc_id: UUID
    status: str
    page_count: int
    chunk_count: int


class RecordsQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class CitationOutput(BaseModel):
    doc_id: UUID
    page_number: int
    chunk_id: UUID | None = None
    start_offset: int | None = None
    end_offset: int | None = None


class RecordsAnswerOutput(BaseModel):
    status: str
    answer: str | None
    citations: list[CitationOutput] = Field(default_factory=list)
    reason: str


async def get_records_service() -> RecordsService:
    """Must be configured with patient-scoped persistent storage by deployment wiring."""
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Records workflow is not configured",
    )


@router.post("/documents", response_model=DocumentUploadOutput, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    context: RequestContext = Depends(get_request_context),
    service: RecordsService = Depends(get_records_service),
) -> DocumentUploadOutput:
    content = await file.read(MAX_PDF_BYTES + 1)
    if len(content) > MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="PDF exceeds the 8 MiB limit")
    try:
        result = await service.ingest_pdf(
            context,
            filename=file.filename or "",
            content_type=file.content_type or "",
            content=content,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return DocumentUploadOutput(
        doc_id=result.document_id,
        status=result.status,
        page_count=result.page_count,
        chunk_count=result.chunk_count,
    )


@router.post("/records/ask", response_model=RecordsAnswerOutput)
async def ask_records(
    payload: RecordsQuestion,
    context: RequestContext = Depends(get_request_context),
    service: RecordsService = Depends(get_records_service),
) -> RecordsAnswerOutput:
    try:
        result = await service.ask(context, question=payload.question)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return RecordsAnswerOutput(
        status=result.status,
        answer=result.answer,
        citations=[
            CitationOutput(
                doc_id=item.doc_id,
                page_number=item.page_number,
                chunk_id=item.chunk_id,
                start_offset=item.start_offset,
                end_offset=item.end_offset,
            )
            for item in result.citations
        ],
        reason=result.reason,
    )
