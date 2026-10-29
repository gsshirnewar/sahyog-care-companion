"""Synthetic-safe PDF ingestion and extractive, patient-scoped records Q&A."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Protocol
from uuid import UUID, uuid4

from pypdf import PdfReader

from services.api.app.auth.context import RequestContext
from services.domain.access_policy.policy import AccessPolicy
from services.domain.records.citation_verifier import Citation, RetrievedEvidence, verify_citations

MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_PAGES = 100
CHUNK_CHARACTERS = 1200
_MEDICAL_ADVICE_TERMS = re.compile(
    r"\b(diagnos(?:e|is|ed)|dosage|dose|how much (?:medicine|medication)|"
    r"should i take|prescri(?:be|ption)|treatment recommendation)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i in is it me my of on or please tell that the this to was what when where which who why with".split()
)


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    page_number: int
    text: str


@dataclass(frozen=True, slots=True)
class StoredChunk:
    chunk_id: UUID
    document_id: UUID
    patient_id: UUID
    page_number: int
    text: str
    start_offset: int
    end_offset: int


@dataclass(frozen=True, slots=True)
class StoredDocument:
    document_id: UUID
    patient_id: UUID
    document_type: str
    content_hash: str
    page_count: int
    uploaded_at: datetime
    chunks: tuple[StoredChunk, ...]


@dataclass(frozen=True, slots=True)
class DocumentIngestResult:
    document_id: UUID
    status: str
    page_count: int
    chunk_count: int


@dataclass(frozen=True, slots=True)
class RecordsAnswer:
    status: str
    answer: str | None
    citations: tuple[Citation, ...]
    reason: str


class RecordsRepository(Protocol):
    async def save(self, document: StoredDocument) -> None: ...
    async def retrieve(self, *, patient_id: UUID, query: str, limit: int) -> list[StoredChunk]: ...


class PageExtractor(Protocol):
    def extract(self, pdf_bytes: bytes) -> list[ExtractedPage]: ...


class PdfTextExtractor:
    """Extract embedded PDF text; scanned/image PDFs require a separate approved OCR worker."""

    def extract(self, pdf_bytes: bytes) -> list[ExtractedPage]:
        reader = PdfReader(BytesIO(pdf_bytes), strict=True)
        if reader.is_encrypted:
            raise ValueError("Encrypted PDFs are not supported")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("PDF exceeds the page limit")
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(ExtractedPage(page_number, text))
        return pages


class RecordsService:
    def __init__(
        self,
        *,
        repository: RecordsRepository,
        access_policy: AccessPolicy,
        extractor: PageExtractor | None = None,
    ) -> None:
        self._repository = repository
        self._access_policy = access_policy
        self._extractor = extractor or PdfTextExtractor()

    async def ingest_pdf(
        self,
        context: RequestContext,
        *,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> DocumentIngestResult:
        patient_id = self._require_records_context(context)
        await self._access_policy.require(
            context,
            subject_id=patient_id,
            required_scope="records:write",
        )
        if content_type.lower() != "application/pdf" or not filename.lower().endswith(".pdf"):
            raise ValueError("Only PDF documents are accepted")
        if not content or len(content) > MAX_PDF_BYTES:
            raise ValueError("PDF is empty or exceeds the 8 MiB limit")
        if not content.startswith(b"%PDF-"):
            raise ValueError("File content is not a PDF")

        pages = self._extractor.extract(content)
        if not pages or len(pages) > MAX_PAGES:
            raise ValueError("PDF contains no pages or exceeds the page limit")
        if not any(page.text.strip() for page in pages):
            raise ValueError("PDF has no extractable text; OCR is not enabled in this demonstrator")

        document_id = uuid4()
        chunks = tuple(
            StoredChunk(
                chunk_id=uuid4(),
                document_id=document_id,
                patient_id=patient_id,
                page_number=page.page_number,
                text=chunk_text,
                start_offset=start,
                end_offset=end,
            )
            for page in pages
            for chunk_text, start, end in _chunk_page(page.text)
        )
        document = StoredDocument(
            document_id=document_id,
            patient_id=patient_id,
            document_type="patient_report",
            content_hash=hashlib.sha256(content).hexdigest(),
            page_count=len(pages),
            uploaded_at=datetime.now(timezone.utc),
            chunks=chunks,
        )
        await self._repository.save(document)
        return DocumentIngestResult(document_id, "ready", len(pages), len(chunks))

    async def ask(self, context: RequestContext, *, question: str) -> RecordsAnswer:
        patient_id = self._require_records_context(context)
        await self._access_policy.require(
            context,
            subject_id=patient_id,
            required_scope="records:read",
        )
        if not question or not question.strip() or len(question) > 1000:
            raise ValueError("Question must contain 1-1000 characters")
        if _MEDICAL_ADVICE_TERMS.search(question):
            return RecordsAnswer("refused", None, (), "clinician_review_required")

        query_terms = _terms(question)
        if not query_terms:
            return RecordsAnswer("not_found", None, (), "no_searchable_terms")
        chunks = await self._repository.retrieve(patient_id=patient_id, query=question, limit=5)
        ranked = sorted(
            ((len(query_terms & _terms(chunk.text)), chunk) for chunk in chunks),
            key=lambda item: (item[0], -item[1].page_number),
            reverse=True,
        )
        if not ranked or ranked[0][0] == 0:
            return RecordsAnswer("not_found", None, (), "no_matching_evidence")

        top_chunks = tuple(chunk for score, chunk in ranked if score > 0)[:3]
        citations = tuple(
            Citation(
                doc_id=chunk.document_id,
                page_number=chunk.page_number,
                chunk_id=chunk.chunk_id,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
            for chunk in top_chunks
        )
        evidence = tuple(
            RetrievedEvidence(
                doc_id=chunk.document_id,
                page_number=chunk.page_number,
                chunk_id=chunk.chunk_id,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
            for chunk in top_chunks
        )
        checked = verify_citations(citations, evidence)
        if not checked.valid:
            return RecordsAnswer("refused", None, (), "citation_validation_failed")
        # Extractive only: return the evidence text directly, not a medical inference.
        answer = "\n\n".join(chunk.text for chunk in top_chunks)
        return RecordsAnswer("answered", answer, citations, "grounded_extractive_match")

    @staticmethod
    def _require_records_context(context: RequestContext) -> UUID:
        if context.purpose != "records" or context.subject_id is None:
            raise PermissionError("A records-purpose request scoped to an authenticated patient is required")
        return context.subject_id


def _chunk_page(text: str):
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return
    for start in range(0, len(normalized), CHUNK_CHARACTERS):
        end = min(start + CHUNK_CHARACTERS, len(normalized))
        fragment = normalized[start:end].strip()
        if fragment:
            yield fragment, start, end


def _terms(text: str) -> set[str]:
    return {
        term.casefold()
        for term in re.findall(r"[\w-]{2,}", text, flags=re.UNICODE)
        if term.casefold() not in _STOPWORDS
    }
