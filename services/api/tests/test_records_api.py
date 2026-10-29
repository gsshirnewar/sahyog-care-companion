from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.api.app.auth.context import RequestContext, get_request_context
from services.api.app.auth.roles import AppRole
from services.api.app.middleware.request_context import RequestContextMiddleware
from services.api.app.routers.records import get_records_service, router
from services.domain.access_policy.policy import AccessPolicy
from services.workflows.records_qa.service import ExtractedPage, RecordsService, StoredDocument

ACTOR = UUID("10000000-0000-4000-8000-000000000001")
PATIENT = UUID("20000000-0000-4000-8000-000000000001")


class OwnerReader:
    async def is_patient_owner(self, **kwargs):
        return kwargs["actor_id"] == ACTOR and kwargs["subject_id"] == PATIENT

    async def find_active_grant(self, **kwargs):
        return None


class Repo:
    def __init__(self):
        self.documents = {}

    async def save(self, document: StoredDocument):
        self.documents[document.document_id] = document

    async def retrieve(self, *, patient_id, query, limit):
        return [chunk for document in self.documents.values() if document.patient_id == patient_id for chunk in document.chunks][:limit]


class Extractor:
    def extract(self, content):
        return [ExtractedPage(1, "Synthetic measurement SAMPLE-A equals 42 units.")]


def make_client():
    service = RecordsService(repository=Repo(), access_policy=AccessPolicy(OwnerReader()), extractor=Extractor())
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)

    async def override_context():
        return RequestContext(ACTOR, AppRole.PATIENT, PATIENT, "records", (), "", None, {})

    app.dependency_overrides[get_request_context] = override_context
    app.dependency_overrides[get_records_service] = lambda: service
    return TestClient(app)


def test_upload_and_ask_api_end_to_end():
    client = make_client()
    uploaded = client.post(
        "/v1/documents",
        files={"file": ("synthetic.pdf", b"%PDF-synthetic-demo", "application/pdf")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["status"] == "ready"

    answer = client.post("/v1/records/ask", json={"question": "What is SAMPLE-A?"})
    assert answer.status_code == 200
    payload = answer.json()
    assert payload["status"] == "answered"
    assert "42 units" in payload["answer"]
    assert payload["citations"][0]["doc_id"] == uploaded.json()["doc_id"]
    assert payload["citations"][0]["page_number"] == 1


def test_records_api_refuses_diagnosis_and_invalid_upload():
    client = make_client()
    response = client.post("/v1/records/ask", json={"question": "Can you diagnose this?"})
    assert response.status_code == 200
    assert response.json()["status"] == "refused"
    assert response.json()["citations"] == []

    bad_file = client.post(
        "/v1/documents",
        files={"file": ("notes.txt", b"not a PDF", "text/plain")},
    )
    assert bad_file.status_code == 422
