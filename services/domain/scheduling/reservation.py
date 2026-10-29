"""Atomic, idempotent slot reservation against the PostgreSQL source of truth."""

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


class AsyncTransaction(Protocol):
    async def __aenter__(self) -> Any: ...
    async def __aexit__(self, exc_type, exc, traceback) -> bool | None: ...


class SchedulingConnection(Protocol):
    def transaction(self) -> AsyncTransaction: ...
    async def execute(self, query: str, *args: Any) -> str: ...
    async def fetchrow(self, query: str, *args: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class Reservation:
    appointment_id: UUID
    patient_id: UUID
    slot_id: UUID
    status: str
    created: bool


class SlotUnavailableError(RuntimeError):
    """The selected slot is missing, inactive, or at capacity."""


class IdempotencyConflictError(RuntimeError):
    """The key was already used for a different patient or slot."""


class SchedulingService:
    def __init__(self, connection: SchedulingConnection) -> None:
        self._connection = connection

    async def reserve(
        self,
        *,
        actor_id: UUID,
        patient_id: UUID,
        slot_id: UUID,
        idempotency_key: str,
        workflow: str = "w1_booking",
    ) -> Reservation:
        if not 1 <= len(idempotency_key.strip()) <= 255:
            raise ValueError("Idempotency key must contain 1-255 non-space characters")
        if not workflow.strip():
            raise ValueError("Workflow is required")

        async with self._connection.transaction():
            # Serialize retries for the same actor/workflow/key before checking
            # existing state. The DB unique constraint is a final guardrail.
            lock_key = f"{actor_id}:{workflow}:{idempotency_key}"
            await self._connection.fetchrow(
                "select pg_advisory_xact_lock(hashtextextended($1, 0)) as locked",
                lock_key,
            )
            existing = await self._connection.fetchrow(
                """
                select appointment_id, patient_id, slot_id, status
                from public.appointment
                where created_by = $1 and workflow = $2 and idempotency_key = $3
                for update
                """,
                actor_id,
                workflow,
                idempotency_key,
            )
            if existing:
                if existing["patient_id"] != patient_id or existing["slot_id"] != slot_id:
                    raise IdempotencyConflictError("Idempotency key was reused with different booking input")
                return Reservation(
                    appointment_id=existing["appointment_id"],
                    patient_id=existing["patient_id"],
                    slot_id=existing["slot_id"],
                    status=existing["status"],
                    created=False,
                )

            reserved_slot = await self._connection.fetchrow(
                """
                update public.slot s
                set reserved_count = s.reserved_count + 1,
                    version = s.version + 1,
                    updated_at = statement_timestamp()
                from public.doctor d, public.clinic c
                where s.slot_id = $1
                  and s.reserved_count < s.capacity
                  and s.starts_at > statement_timestamp()
                  and d.doctor_id = s.doctor_id
                  and d.clinic_id = s.clinic_id
                  and d.is_active
                  and c.clinic_id = s.clinic_id
                  and c.is_active
                returning s.slot_id
                """,
                slot_id,
            )
            if reserved_slot is None:
                raise SlotUnavailableError("Selected slot is no longer available")

            appointment = await self._connection.fetchrow(
                """
                insert into public.appointment (
                    patient_id, slot_id, status, workflow, idempotency_key,
                    created_by, source, his_reconciliation_state
                ) values ($1, $2, 'reserved', $3, $4, $5, 'local_demo', 'not_applicable')
                returning appointment_id, patient_id, slot_id, status
                """,
                patient_id,
                slot_id,
                workflow,
                idempotency_key,
                actor_id,
            )
            return Reservation(
                appointment_id=appointment["appointment_id"],
                patient_id=appointment["patient_id"],
                slot_id=appointment["slot_id"],
                status=appointment["status"],
                created=True,
            )
