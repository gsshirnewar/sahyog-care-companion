"""Rules-first W1 workflow orchestration. Demo repositories are synthetic-only."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID, uuid4

from services.api.app.auth.context import RequestContext
from services.domain.access_policy.policy import AccessPolicy
from services.domain.safety.red_flag.gate import RedFlagGate
from services.workflows.triage.graph import TriageDecisionGraph


@dataclass(frozen=True, slots=True)
class TriageRequest:
    intake_text: str
    language: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class SlotOption:
    slot_id: UUID
    doctor_id: UUID
    doctor_name: str
    clinic_id: UUID
    clinic_name: str
    specialty_code: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class TriageResult:
    run_id: UUID | None
    route: str
    reason: str
    specialty_code: str | None = None
    options: tuple[SlotOption, ...] = ()
    rule_set_version: str | None = None
    matched_rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BookingResult:
    appointment_id: UUID
    slot_id: UUID
    status: str
    created: bool


@dataclass(slots=True)
class TriageRun:
    run_id: UUID
    actor_id: UUID
    patient_id: UUID
    purpose: str
    intake_text: str
    specialty_code: str
    options: tuple[SlotOption, ...]
    expires_at: datetime
    status: str = "paused"


class TriageRepository(Protocol):
    async def find_active_slots(self, specialty_code: str, *, limit: int) -> list[SlotOption]: ...
    async def find_run(self, run_id: UUID) -> TriageRun | None: ...
    async def save_run(self, run: TriageRun) -> None: ...
    async def reserve(self, *, actor_id: UUID, patient_id: UUID, slot_id: UUID, idempotency_key: str) -> BookingResult: ...
    async def save_triage_idempotency(
        self, actor_id: UUID, key: str, fingerprint: str, result: TriageResult
    ) -> None: ...
    async def find_triage_idempotency(
        self, actor_id: UUID, key: str
    ) -> tuple[str, TriageResult] | None: ...


class TriageIdempotencyConflictError(ValueError):
    """An idempotency key was reused with a different triage request."""


class TriageService:
    def __init__(
        self,
        *,
        repository: TriageRepository,
        access_policy: AccessPolicy,
        red_flag_gate: RedFlagGate,
        specialty_mapper,
        run_ttl_seconds: int = 900,
    ) -> None:
        self._repository = repository
        self._access_policy = access_policy
        self._decision_graph = TriageDecisionGraph(
            gate=red_flag_gate,
            repository=repository,
            specialty_mapper=specialty_mapper,
        )
        self._run_ttl_seconds = run_ttl_seconds

    async def start(self, context: RequestContext, request: TriageRequest) -> TriageResult:
        if context.purpose not in {"triage", "booking"}:
            raise PermissionError("Request purpose does not permit triage")
        if context.subject_id is None:
            raise PermissionError("A patient subject must be established by trusted authentication")
        await self._access_policy.require(
            context,
            subject_id=context.subject_id,
            required_scope="triage:use",
        )
        if not request.intake_text.strip() or len(request.intake_text) > 4000:
            raise ValueError("Intake must contain 1-4000 characters")
        if not request.idempotency_key.strip():
            raise ValueError("Idempotency key is required")

        fingerprint = hashlib.sha256(
            json.dumps(
                {"intake_text": request.intake_text, "language": request.language},
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        prior = await self._repository.find_triage_idempotency(context.actor_id, request.idempotency_key)
        if prior is not None:
            prior_fingerprint, prior_result = prior
            if prior_fingerprint != fingerprint:
                raise TriageIdempotencyConflictError("Idempotency key was reused with different triage input")
            return prior_result

        decision = await self._decision_graph.run(
            intake_text=request.intake_text,
            language=request.language,
        )
        gate = decision["gate"]
        route = decision["route"]
        if route == "emergency_handoff":
            result = TriageResult(
                run_id=None,
                route="emergency_handoff",
                reason=decision["reason"],
                rule_set_version=gate.rule_set_version,
                matched_rule_ids=gate.matched_rule_ids,
            )
            await self._repository.save_triage_idempotency(context.actor_id, request.idempotency_key, fingerprint, result)
            return result
        if route != "continue":
            result = TriageResult(
                run_id=None,
                route="human_review",
                reason=decision.get("reason", gate.reason),
                rule_set_version=gate.rule_set_version,
                matched_rule_ids=gate.matched_rule_ids,
            )
            await self._repository.save_triage_idempotency(context.actor_id, request.idempotency_key, fingerprint, result)
            return result

        specialty_code = decision.get("specialty_code")
        if not specialty_code:
            result = TriageResult(None, "human_review", "specialty_unmapped", gate.rule_set_version)
            await self._repository.save_triage_idempotency(context.actor_id, request.idempotency_key, fingerprint, result)
            return result

        options = tuple(decision.get("options", []))
        if len(options) < 3:
            result = TriageResult(
                None,
                "human_review",
                "insufficient_eligible_options",
                specialty_code,
                options,
                gate.rule_set_version,
            )
            await self._repository.save_triage_idempotency(context.actor_id, request.idempotency_key, fingerprint, result)
            return result

        run = TriageRun(
            run_id=uuid4(),
            actor_id=context.actor_id,
            patient_id=context.subject_id,
            purpose=context.purpose,
            intake_text=request.intake_text,
            specialty_code=specialty_code,
            options=options,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self._run_ttl_seconds),
        )
        # Avoid retaining raw intake in workflow state; only the scoped references
        # and validated choices survive the interruption.
        run.intake_text = "[redacted from workflow state]"
        await self._repository.save_run(run)
        result = TriageResult(
            run_id=run.run_id,
            route="options",
            reason="patient_choice_required",
            specialty_code=specialty_code,
            options=options,
            rule_set_version=gate.rule_set_version,
        )
        await self._repository.save_triage_idempotency(context.actor_id, request.idempotency_key, fingerprint, result)
        return result

    async def resume(
        self,
        context: RequestContext,
        *,
        run_id: UUID,
        selected_slot_id: UUID,
        idempotency_key: str,
    ) -> BookingResult:
        if context.purpose not in {"triage", "booking"} or context.subject_id is None:
            raise PermissionError("Request context cannot resume booking")
        await self._access_policy.require(
            context,
            subject_id=context.subject_id,
            required_scope="appointments:write",
        )
        run = await self._repository.find_run(run_id)
        if run is None or run.status != "paused" or run.expires_at <= datetime.now(timezone.utc):
            raise ValueError("Workflow run is missing, expired, or no longer resumable")
        if run.actor_id != context.actor_id or run.patient_id != context.subject_id:
            raise PermissionError("Workflow run does not belong to this actor and patient")
        if run.purpose not in {"triage", "booking"}:
            raise PermissionError("Workflow run was not created for triage or booking")
        if selected_slot_id not in {option.slot_id for option in run.options}:
            raise ValueError("Selected slot was not one of the displayed options")

        # The repository atomically rechecks live slot capacity and idempotency.
        booking = await self._repository.reserve(
            actor_id=context.actor_id,
            patient_id=context.subject_id,
            slot_id=selected_slot_id,
            idempotency_key=idempotency_key,
        )
        run.status = "completed"
        await self._repository.save_run(run)
        return booking
