from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.api.app.auth.context import RequestContext, get_request_context
from services.api.app.auth.roles import AppRole
from services.api.app.middleware.request_context import RequestContextMiddleware
from services.api.app.routers.scribe import get_scribe_service, router
from services.domain.access_policy.policy import AccessPolicy
from services.workflows.scribe.service import EncounterRecord, ScribeService

ACTOR = UUID("10000000-0000-4000-8000-000000000001")
PATIENT = UUID("20000000-0000-4000-8000-000000000001")
ENCOUNTER = UUID("30000000-0000-4000-8000-000000000001")


class Repo:
    def __init__(self):
        self.encounter = EncounterRecord(ENCOUNTER, PATIENT, ACTOR)
        self.versions = []

    async def get_encounter(self, encounter_id):
        return self.encounter if encounter_id == ENCOUNTER else None

    async def list_note_versions(self, encounter_id):
        return list(self.versions)

    async def append_note_version(self, note):
        self.versions.append(note)


class OwnerReader:
    async def is_patient_owner(self, **kwargs):
        return kwargs["subject_id"] == PATIENT and kwargs["actor_id"] == ACTOR

    async def find_active_grant(self, **kwargs):
        return None


def make_client():
    repository = Repo()
    service = ScribeService(repository=repository, access_policy=AccessPolicy(OwnerReader()))
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)

    async def context():
        return RequestContext(ACTOR, AppRole.CLINICIAN, PATIENT, "scribe", (), "", None, {})

    app.dependency_overrides[get_request_context] = context
    app.dependency_overrides[get_scribe_service] = lambda: service
    return TestClient(app), repository


def test_scribe_draft_review_and_sign_api():
    client, repository = make_client()
    transcript = "Synthetic transcript: phrase alpha and phrase beta."
    created = client.post(
        f"/v1/encounters/{ENCOUNTER}/draft",
        json={"transcript": transcript},
    )
    assert created.status_code == 201
    assert created.json()["status"] == "draft"
    assert set(created.json()["content"]["unknown_sections"]) == {"assessment", "objective", "plan"}

    fetched = client.get(f"/v1/encounters/{ENCOUNTER}/draft")
    assert fetched.status_code == 200
    assert fetched.json()["version"] == 1

    refused_sign = client.post(
        f"/v1/encounters/{ENCOUNTER}/review",
        json={"expected_version": 1, "action": "sign"},
    )
    assert refused_sign.status_code == 409

    statement_a = {"text": "phrase alpha", "source_start": transcript.index("phrase alpha"), "source_end": transcript.index("phrase alpha") + len("phrase alpha")}
    statement_b = {"text": "phrase beta", "source_start": transcript.index("phrase beta"), "source_end": transcript.index("phrase beta") + len("phrase beta")}
    complete = {
        "subjective": [statement_a],
        "objective": [statement_b],
        "assessment": [statement_a],
        "plan": [statement_b],
        "unknown_sections": [],
    }
    edited = client.post(
        f"/v1/encounters/{ENCOUNTER}/review",
        json={"expected_version": 1, "action": "edit", "edited_content": complete},
    )
    assert edited.status_code == 200
    assert edited.json()["version"] == 2

    signed = client.post(
        f"/v1/encounters/{ENCOUNTER}/review",
        json={"expected_version": 2, "action": "sign"},
    )
    assert signed.status_code == 200
    assert signed.json()["status"] == "signed"
    assert signed.json()["signed_by"] == str(ACTOR)
    assert len(signed.json()["content_hash"]) == 64
    assert [version.version for version in repository.versions] == [1, 2, 3]


def test_scribe_api_rejects_bad_transcript_and_stale_version():
    client, _ = make_client()
    empty = client.post(f"/v1/encounters/{ENCOUNTER}/draft", json={"transcript": ""})
    assert empty.status_code == 422

    missing_note = client.post(
        f"/v1/encounters/{ENCOUNTER}/review",
        json={"expected_version": 99, "action": "reject", "rejection_reason": "synthetic"},
    )
    assert missing_note.status_code == 409
