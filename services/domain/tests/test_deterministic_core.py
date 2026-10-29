from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID, uuid4

import pytest

from services.api.app.auth.context import RequestContext
from services.api.app.auth.roles import AppRole
from services.domain.access_policy.policy import AccessPolicy
from services.domain.audit.writer import AuditWriter
from services.domain.records.citation_verifier import Citation, RetrievedEvidence, verify_citations
from services.domain.safety.red_flag.gate import (
    GateRoute,
    RedFlagGate,
    RedFlagRule,
    approved_rule_set,
)
from services.domain.scheduling.reservation import (
    IdempotencyConflictError,
    SchedulingService,
    SlotUnavailableError,
)

ACTOR_ID = UUID("10000000-0000-4000-8000-000000000001")
PATIENT_ID = UUID("20000000-0000-4000-8000-000000000001")
SLOT_ID = UUID("30000000-0000-4000-8000-000000000001")


def context(*, role: AppRole = AppRole.PATIENT, consents=()) -> RequestContext:
    return RequestContext(
        actor_id=ACTOR_ID,
        role=role,
        subject_id=PATIENT_ID,
        purpose="records",
        consent_ids=tuple(consents),
        trace_id="trace-test",
        idempotency_key="idem-test",
        claims={},
    )


class FakeConsentReader:
    def __init__(self, *, owner=False, active_grant=None):
        self.owner = owner
        self.active_grant = active_grant
        self.query = None

    async def is_patient_owner(self, **kwargs):
        self.query = kwargs
        return self.owner

    async def find_active_grant(self, **kwargs):
        self.query = kwargs
        if self.active_grant not in kwargs["consent_ids"]:
            return None
        return self.active_grant


@pytest.mark.asyncio
async def test_access_policy_owner_and_active_consent_and_revocation() -> None:
    owner_policy = AccessPolicy(FakeConsentReader(owner=True))
    assert (await owner_policy.decide(context(), subject_id=PATIENT_ID, required_scope="records:read")).reason == "owner"

    grant_id = uuid4()
    consent_policy = AccessPolicy(FakeConsentReader(active_grant=grant_id))
    delegated = await consent_policy.decide(
        context(consents=[grant_id]),
        subject_id=PATIENT_ID,
        required_scope="records:read",
    )
    assert delegated.allowed and delegated.consent_id == grant_id

    revoked_policy = AccessPolicy(FakeConsentReader(active_grant=None))
    denied = await revoked_policy.decide(
        context(consents=[grant_id]),
        subject_id=PATIENT_ID,
        required_scope="records:read",
    )
    assert not denied.allowed
    with pytest.raises(PermissionError):
        await revoked_policy.require(context(consents=[grant_id]), subject_id=PATIENT_ID, required_scope="records:read")


@pytest.mark.asyncio
async def test_audit_writer_persists_minimized_append_only_record() -> None:
    class Repository:
        record = None

        async def append(self, record):
            self.record = record

    repository = Repository()
    record = await AuditWriter(repository).record(
        actor_id=ACTOR_ID,
        actor_role="patient",
        purpose="records",
        action="read",
        resource_type="document",
        resource_id=uuid4(),
        decision="allow",
        trace_id="trace-123",
    )
    assert repository.record == record
    assert record.occurred_at.tzinfo is not None
    assert not hasattr(record, "clinical_text")
    with pytest.raises(ValueError):
        await AuditWriter(repository).record(
            actor_id=ACTOR_ID,
            actor_role="patient",
            purpose="records",
            action="read",
            resource_type="document",
            resource_id=None,
            decision="allow",
            trace_id=" ",
        )


def test_red_flag_gate_fails_closed_until_rules_approved_and_handles_match() -> None:
    assert RedFlagGate(None).evaluate("synthetic intake").route == GateRoute.HUMAN_REVIEW
    rule = RedFlagRule("SYNTH-01", "v1", lambda text: "synthetic-red-flag" in text)
    unapproved = approved_rule_set(version="v1", approved_by="", rules=[rule])
    assert RedFlagGate(unapproved).evaluate("synthetic-red-flag").route == GateRoute.HUMAN_REVIEW

    approved = approved_rule_set(version="v1", approved_by="clinical-owner-demo", rules=[rule])
    result = RedFlagGate(approved).evaluate("synthetic-red-flag case")
    assert result.route == GateRoute.EMERGENCY
    assert result.matched_rule_ids == ("SYNTH-01",)
    assert RedFlagGate(approved).evaluate("routine synthetic text").route == GateRoute.ROUTINE

    failing = approved_rule_set(
        version="v2",
        approved_by="clinical-owner-demo",
        rules=[RedFlagRule("BROKEN", "v2", lambda _: 1 / 0)],
    )
    assert RedFlagGate(failing).evaluate("synthetic text").reason == "rule_evaluation_failed"


def test_citation_verifier_rejects_non_retrieved_or_wrong_page_citations() -> None:
    doc_id, chunk_id = uuid4(), uuid4()
    evidence = [RetrievedEvidence(doc_id, 2, chunk_id, 10, 30)]
    good = verify_citations([Citation(doc_id, 2, chunk_id, 10, 30)], evidence)
    assert good.valid
    bad = verify_citations([Citation(doc_id, 3, chunk_id)], evidence)
    assert not bad.valid
    assert bad.reason == "citation_not_in_retrieved_evidence"
    assert not verify_citations([], evidence).valid


class FakeConnection:
    def __init__(self, *, existing=None, available=True):
        self.existing = existing
        self.available = available
        self.inserted = None
        self.transaction_calls = 0
        self.fetch_calls = 0

    @asynccontextmanager
    async def transaction(self):
        self.transaction_calls += 1
        yield

    async def fetchrow(self, query, *args):
        self.fetch_calls += 1
        if "pg_advisory_xact_lock" in query:
            return {"locked": None}
        if "from public.appointment" in query:
            return self.existing
        if "update public.slot" in query:
            return {"slot_id": SLOT_ID} if self.available else None
        if "insert into public.appointment" in query:
            self.inserted = args
            return {
                "appointment_id": uuid4(),
                "patient_id": args[0],
                "slot_id": args[1],
                "status": "reserved",
            }
        raise AssertionError(f"Unexpected SQL: {query}")

    async def execute(self, query, *args):
        raise AssertionError("Unexpected execute")


@pytest.mark.asyncio
async def test_scheduling_transaction_is_atomic_idempotent_and_capacity_checked() -> None:
    connection = FakeConnection()
    service = SchedulingService(connection)
    result = await service.reserve(
        actor_id=ACTOR_ID,
        patient_id=PATIENT_ID,
        slot_id=SLOT_ID,
        idempotency_key="retry-1",
    )
    assert result.created and result.status == "reserved"
    assert connection.transaction_calls == 1
    assert connection.inserted == (PATIENT_ID, SLOT_ID, "w1_booking", "retry-1", ACTOR_ID)

    existing = {
        "appointment_id": uuid4(),
        "patient_id": PATIENT_ID,
        "slot_id": SLOT_ID,
        "status": "reserved",
    }
    retry_conn = FakeConnection(existing=existing)
    retry = await SchedulingService(retry_conn).reserve(
        actor_id=ACTOR_ID,
        patient_id=PATIENT_ID,
        slot_id=SLOT_ID,
        idempotency_key="retry-1",
    )
    assert retry.created is False
    assert retry.appointment_id == existing["appointment_id"]

    conflict_conn = FakeConnection(existing={**existing, "slot_id": uuid4()})
    with pytest.raises(IdempotencyConflictError):
        await SchedulingService(conflict_conn).reserve(
            actor_id=ACTOR_ID,
            patient_id=PATIENT_ID,
            slot_id=SLOT_ID,
            idempotency_key="retry-1",
        )

    full_conn = FakeConnection(available=False)
    with pytest.raises(SlotUnavailableError):
        await SchedulingService(full_conn).reserve(
            actor_id=ACTOR_ID,
            patient_id=PATIENT_ID,
            slot_id=SLOT_ID,
            idempotency_key="retry-2",
        )
    assert full_conn.inserted is None
