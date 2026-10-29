from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.api.app.auth.context import RequestContext, get_request_context
from services.api.app.auth.roles import AppRole
from services.api.app.middleware.request_context import RequestContextMiddleware
from services.api.app.routers.triage import get_triage_service, router
from services.domain.access_policy.policy import AccessPolicy
from services.domain.safety.red_flag.gate import RedFlagGate, RedFlagRule, approved_rule_set
from services.workflows.triage.service import BookingResult, SlotOption, TriageRun, TriageService

ACTOR = UUID("10000000-0000-4000-8000-000000000001")
PATIENT = UUID("20000000-0000-4000-8000-000000000001")
SLOT_IDS = [UUID(f"30000000-0000-4000-8000-{index:012d}") for index in range(1, 4)]


class ApiRepository:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.options = [
            SlotOption(slot, UUID(int=index + 10), f"Synthetic Doctor {index}", UUID(int=99), "Synthetic Clinic", "DEMO-GENERAL", now + timedelta(days=index), now + timedelta(days=index, minutes=15))
            for index, slot in enumerate(SLOT_IDS, start=1)
        ]
        self.runs = {}
        self.triage_keys = {}
        self.bookings = {}

    async def is_patient_owner(self, *, subject_id, actor_id):
        return subject_id == PATIENT and actor_id == ACTOR

    async def find_active_grant(self, **kwargs):
        return None

    async def find_active_slots(self, specialty_code, *, limit):
        return [option for option in self.options if option.specialty_code == specialty_code][:limit]

    async def find_run(self, run_id):
        return self.runs.get(run_id)

    async def save_run(self, run):
        self.runs[run.run_id] = run

    async def reserve(self, *, actor_id, patient_id, slot_id, idempotency_key):
        booking = BookingResult(UUID(int=501), slot_id, "reserved", True)
        self.bookings[(actor_id, idempotency_key)] = booking
        return booking

    async def save_triage_idempotency(self, actor_id, key, fingerprint, result):
        self.triage_keys[(actor_id, key)] = (fingerprint, result)

    async def find_triage_idempotency(self, actor_id, key):
        return self.triage_keys.get((actor_id, key))


def create_client(purpose="triage"):
    repository = ApiRepository()
    rules = approved_rule_set(
        version="test-only-v1",
        approved_by="synthetic-test-owner",
        rules=[RedFlagRule("SYNTH-STOP", "test-v1", lambda text: "synthetic-red-flag" in text)],
    )
    service = TriageService(
        repository=repository,
        access_policy=AccessPolicy(repository),
        red_flag_gate=RedFlagGate(rules),
        specialty_mapper=lambda _text, _language: "DEMO-GENERAL",
    )
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)
    app.dependency_overrides[get_triage_service] = lambda: service

    async def context_override():
        return RequestContext(ACTOR, AppRole.PATIENT, PATIENT, purpose, (), "", None, {})

    app.dependency_overrides[get_request_context] = context_override
    return TestClient(app), repository


def test_triage_endpoint_returns_only_handoff_for_red_flag():
    client, repository = create_client()
    response = client.post(
        "/v1/triage",
        headers={"Idempotency-Key": "triage-1"},
        json={"intake_text": "synthetic-red-flag test", "language": "en"},
    )
    assert response.status_code == 200
    assert response.json()["route"] == "emergency_handoff"
    assert response.json()["options"] == []
    assert not repository.runs


def test_triage_and_resume_endpoint_integration():
    client, repository = create_client()
    response = client.post(
        "/v1/triage",
        headers={"Idempotency-Key": "triage-2", "X-Trace-ID": "w1-trace-2"},
        json={"intake_text": "routine synthetic case", "language": "en"},
    )
    assert response.status_code == 200
    assert response.headers["x-trace-id"] == "w1-trace-2"
    body = response.json()
    assert body["route"] == "options"
    assert len(body["options"]) == 3

    resumed = client.post(
        f"/v1/runs/{body['run_id']}/resume",
        headers={"Idempotency-Key": "booking-1"},
        json={"selected_slot_id": str(SLOT_IDS[0])},
    )
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "reserved"
    assert resumed.json()["created"] is True
    assert repository.runs[UUID(body["run_id"])].status == "completed"


def test_triage_rejects_missing_idempotency_key():
    client, _ = create_client()
    response = client.post("/v1/triage", json={"intake_text": "synthetic text"})
    assert response.status_code == 422


def test_triage_idempotency_key_payload_conflict_returns_409():
    client, _ = create_client()
    headers = {"Idempotency-Key": "same-triage-key"}
    first = client.post("/v1/triage", headers=headers, json={"intake_text": "routine synthetic case"})
    second = client.post("/v1/triage", headers=headers, json={"intake_text": "different synthetic case"})
    assert first.status_code == 200
    assert second.status_code == 409
