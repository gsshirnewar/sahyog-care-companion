from __future__ import annotations

from uuid import UUID

import pytest

from services.api.app.auth.context import RequestContext
from services.api.app.auth.roles import AppRole
from services.domain.access_policy.policy import AccessPolicy
from services.workflows.records_qa.service import (
    ExtractedPage,
    RecordsService,
    StoredDocument,
)

ACTOR = UUID("10000000-0000-4000-8000-000000000001")
PATIENT = UUID("20000000-0000-4000-8000-000000000001")
OTHER_PATIENT = UUID("20000000-0000-4000-8000-000000000002")


def context(*, subject=PATIENT, purpose="records", consents=()):
    return RequestContext(ACTOR, AppRole.PATIENT, subject, purpose, tuple(consents), "trace", None, {})


class FakeAccessReader:
    def __init__(self, *, owner=True, active_grant=None):
        self.owner = owner
        self.active_grant = active_grant

    async def is_patient_owner(self, **kwargs):
        return self.owner

    async def find_active_grant(self, **kwargs):
        if self.active_grant not in kwargs["consent_ids"]:
            return None
        if kwargs["purpose"] != "records" or kwargs["scope"] not in {"records:read", "records:write"}:
            return None
        return self.active_grant


class FakeRepo:
    def __init__(self):
        self.documents: dict[UUID, StoredDocument] = {}

    async def save(self, document):
        self.documents[document.document_id] = document

    async def retrieve(self, *, patient_id, query, limit):
        return [
            chunk
            for document in self.documents.values()
            if document.patient_id == patient_id
            for chunk in document.chunks
        ][:limit]


class FakeExtractor:
    def __init__(self, pages=None):
        self.pages = (
            pages
            if pages is not None
            else [ExtractedPage(1, "Synthetic measurement SAMPLE-A equals 42 units.")]
        )

    def extract(self, pdf_bytes):
        assert pdf_bytes.startswith(b"%PDF-")
        return self.pages


def make_service(*, access=None, repo=None, extractor=None):
    return RecordsService(
        repository=repo or FakeRepo(),
        access_policy=AccessPolicy(access or FakeAccessReader()),
        extractor=extractor or FakeExtractor(),
    ), repo


@pytest.mark.asyncio
async def test_ingest_and_extractive_answer_have_resolvable_patient_citation():
    repository = FakeRepo()
    service, _ = make_service(repo=repository)
    ingested = await service.ingest_pdf(
        context(),
        filename="synthetic.pdf",
        content_type="application/pdf",
        content=b"%PDF-synthetic-fixture",
    )
    assert ingested.status == "ready"
    assert ingested.page_count == 1
    assert ingested.chunk_count == 1

    answer = await service.ask(context(), question="What value is SAMPLE-A?")
    assert answer.status == "answered"
    assert "42 units" in answer.answer
    assert len(answer.citations) == 1
    assert answer.citations[0].doc_id == ingested.document_id
    assert answer.citations[0].page_number == 1


@pytest.mark.asyncio
async def test_upload_and_ask_deny_wrong_scope_or_wrong_patient():
    service, _ = make_service(access=FakeAccessReader(owner=False))
    with pytest.raises(PermissionError):
        await service.ingest_pdf(
            context(consents=[]),
            filename="synthetic.pdf",
            content_type="application/pdf",
            content=b"%PDF-synthetic",
        )
    with pytest.raises(PermissionError):
        await service.ask(context(purpose="booking"), question="What is recorded?")


@pytest.mark.asyncio
async def test_pdf_validation_and_scanned_pdf_are_explicit():
    service, _ = make_service()
    for filename, content_type, data in [
        ("report.txt", "text/plain", b"not a pdf"),
        ("report.pdf", "application/pdf", b"not a pdf"),
        ("empty.pdf", "application/pdf", b"%PDF-"),
    ]:
        extractor = FakeExtractor([] if data == b"%PDF-" else None)
        local_service, _ = make_service(extractor=extractor)
        with pytest.raises(ValueError):
            await local_service.ingest_pdf(
                context(), filename=filename, content_type=content_type, content=data
            )


@pytest.mark.asyncio
async def test_medical_advice_and_out_of_record_questions_do_not_generate_answers():
    service, _ = make_service()
    advice = await service.ask(context(), question="Please diagnose this result")
    assert advice.status == "refused"
    assert advice.answer is None
    assert advice.reason == "clinician_review_required"

    not_found = await service.ask(context(), question="Unrelated synthetic phrase")
    assert not_found.status == "not_found"
    assert not_found.citations == ()

    generic = await service.ask(context(), question="What is it?")
    assert generic.status == "not_found"
    assert generic.reason == "no_searchable_terms"


@pytest.mark.asyncio
async def test_owner_identity_is_resolved_by_repository_not_uuid_equality():
    service, _ = make_service(access=FakeAccessReader(owner=False))
    with pytest.raises(PermissionError):
        await service.ask(context(subject=ACTOR), question="What value is SAMPLE-A?")
