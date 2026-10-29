from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from services.api.app.auth.context import RequestContext
from services.api.app.auth.roles import AppRole
from services.domain.access_policy.policy import AccessPolicy
from services.domain.safety.red_flag.gate import RedFlagGate, RedFlagRule, approved_rule_set
from services.workflows.triage.service import (
    BookingResult,
    SlotOption,
    TriageIdempotencyConflictError,
    TriageRequest,
    TriageRun,
    TriageService,
)

ACTOR = UUID("10000000-0000-4000-8000-000000000001")
PATIENT = UUID("20000000-0000-4000-8000-000000000001")
NOW = datetime.now(timezone.utc)
OPTIONS = tuple(
    SlotOption(
        slot_id=UUID(f"30000000-0000-4000-8000-{index:012d}"),
        doctor_id=UUID(f"40000000-0000-4000-8000-{index:012d}"),
        doctor_name=f"Synthetic Clinician {index}",
        clinic_id=UUID("50000000-0000-4000-8000-000000000001"),
        clinic_name="Synthetic Clinic",
        specialty_code="DEMO-GENERAL",
        starts_at=NOW + timedelta(days=index),
        ends_at=NOW + timedelta(days=index, minutes=15),
    )
    for index in range(1, 4)
)


def make_context(*, purpose="triage", actor=ACTOR, patient=PATIENT):
    return RequestContext(actor, AppRole.PATIENT, patient, purpose, (), "trace", None, {})


class FakeRepository:
    def __init__(self, options=OPTIONS):
        self.options = list(options)
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
        key = (actor_id, idempotency_key)
        if key in self.bookings:
            existing = self.bookings[key]
            if existing.slot_id != slot_id:
                raise ValueError("idempotency conflict")
            return BookingResult(existing.appointment_id, slot_id, existing.status, False)
        if slot_id not in {option.slot_id for option in self.options}:
            raise ValueError("slot unavailable")
        booking = BookingResult(uuid4(), slot_id, "reserved", True)
        self.bookings[key] = booking
        return booking

    async def save_triage_idempotency(self, actor_id, key, fingerprint, result):
        self.triage_keys[(actor_id, key)] = (fingerprint, result)

    async def find_triage_idempotency(self, actor_id, key):
        return self.triage_keys.get((actor_id, key))


def service(repository=None, *, approved=True):
    repo = repository or FakeRepository()
    rules = approved_rule_set(
        version="synthetic-test-v1",
        approved_by="synthetic-test-only",
        rules=[RedFlagRule("SYNTHETIC-STOP", "v1", lambda text: "synthetic-red-flag" in text)],
    ) if approved else None
    return TriageService(
        repository=repo,
        access_policy=AccessPolicy(repo),
        red_flag_gate=RedFlagGate(rules),
        specialty_mapper=lambda _text, _language: "DEMO-GENERAL",
    ), repo


@pytest.mark.asyncio
async def test_rules_run_before_slot_search_and_red_flag_stops_booking() -> None:
    workflow, repo = service()
    result = await workflow.start(
        make_context(),
        TriageRequest("synthetic-red-flag scenario", "en", "triage-1"),
    )
    assert result.route == "emergency_handoff"
    assert result.options == ()
    assert repo.runs == {}


@pytest.mark.asyncio
async def test_unapproved_red_flag_set_fails_closed_to_human() -> None:
    workflow, repo = service(approved=False)
    result = await workflow.start(make_context(), TriageRequest("synthetic text", "en", "triage-2"))
    assert result.route == "human_review"
    assert result.reason == "rules_not_clinically_approved"
    assert repo.runs == {}


@pytest.mark.asyncio
async def test_routine_flow_pauses_then_resumes_only_displayed_slot() -> None:
    workflow, repo = service()
    result = await workflow.start(make_context(), TriageRequest("routine synthetic text", "en", "triage-3"))
    assert result.route == "options"
    assert len(result.options) == 3
    run = await repo.find_run(result.run_id)
    assert run.status == "paused"
    assert "routine synthetic text" not in run.intake_text

    with pytest.raises(ValueError, match="not one of the displayed"):
        await workflow.resume(
            make_context(purpose="booking"),
            run_id=result.run_id,
            selected_slot_id=uuid4(),
            idempotency_key="booking-1",
        )

    with pytest.raises(PermissionError):
        await workflow.resume(
            make_context(actor=uuid4()),
            run_id=result.run_id,
            selected_slot_id=result.options[0].slot_id,
            idempotency_key="booking-1",
        )

    booking = await workflow.resume(
        make_context(),
        run_id=result.run_id,
        selected_slot_id=result.options[0].slot_id,
        idempotency_key="booking-1",
    )
    assert booking.status == "reserved"
    assert (await repo.find_run(result.run_id)).status == "completed"


@pytest.mark.asyncio
async def test_triage_requires_scoped_patient_purpose_and_three_slots() -> None:
    workflow, _ = service(FakeRepository(options=OPTIONS[:2]))
    result = await workflow.start(make_context(), TriageRequest("routine text", "en", "triage-4"))
    assert result.route == "human_review"
    assert result.reason == "insufficient_eligible_options"

    with pytest.raises(PermissionError):
        await workflow.start(
            make_context(purpose="records"),
            TriageRequest("routine text", "en", "triage-5"),
        )


@pytest.mark.asyncio
async def test_triage_idempotency_key_cannot_be_reused_for_changed_input() -> None:
    workflow, _ = service()
    await workflow.start(
        make_context(),
        TriageRequest("routine synthetic text", "en", "same-key"),
    )
    with pytest.raises(TriageIdempotencyConflictError):
        await workflow.start(
            make_context(),
            TriageRequest("different synthetic text", "en", "same-key"),
        )
