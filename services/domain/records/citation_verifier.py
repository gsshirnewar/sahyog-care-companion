"""Deterministic citation-to-retrieved-evidence verification."""

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID


@dataclass(frozen=True, slots=True)
class Citation:
    doc_id: UUID
    page_number: int
    chunk_id: UUID | None = None
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    doc_id: UUID
    page_number: int
    chunk_id: UUID
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True, slots=True)
class CitationCheck:
    valid: bool
    invalid_citations: tuple[Citation, ...]
    reason: str


def verify_citations(
    citations: Iterable[Citation],
    retrieved: Iterable[RetrievedEvidence],
) -> CitationCheck:
    evidence = tuple(retrieved)
    citation_list = tuple(citations)
    if not citation_list:
        return CitationCheck(False, (), "no_citations")

    invalid: list[Citation] = []
    for citation in citation_list:
        match = any(
            item.doc_id == citation.doc_id
            and item.page_number == citation.page_number
            and (citation.chunk_id is None or item.chunk_id == citation.chunk_id)
            and (
                citation.start_offset is None
                or item.start_offset == citation.start_offset
            )
            and (
                citation.end_offset is None
                or item.end_offset == citation.end_offset
            )
            for item in evidence
        )
        if citation.page_number < 1 or not match:
            invalid.append(citation)

    return CitationCheck(
        valid=not invalid,
        invalid_citations=tuple(invalid),
        reason="all_citations_resolve" if not invalid else "citation_not_in_retrieved_evidence",
    )
