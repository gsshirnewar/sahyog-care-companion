"""Append-only audit event writer with a PHI-minimized event contract."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class AuditRecord:
    event_id: UUID
    actor_id: UUID | None
    actor_role: str
    purpose: str
    action: str
    resource_type: str
    resource_id: UUID | None
    decision: str
    occurred_at: datetime
    trace_id: str
    consent_id: UUID | None = None


class AuditRepository(Protocol):
    async def append(self, record: AuditRecord) -> None: ...


class AuditWriter:
    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def record(
        self,
        *,
        actor_id: UUID | None,
        actor_role: str,
        purpose: str,
        action: str,
        resource_type: str,
        resource_id: UUID | None,
        decision: str,
        trace_id: str,
        consent_id: UUID | None = None,
    ) -> AuditRecord:
        values = (actor_role, purpose, action, resource_type, decision, trace_id)
        if any(not value or not value.strip() for value in values):
            raise ValueError("Audit context fields must be non-empty")
        if len(trace_id) > 128:
            raise ValueError("Trace ID exceeds the audit schema limit")

        record = AuditRecord(
            event_id=uuid4(),
            actor_id=actor_id,
            actor_role=actor_role,
            purpose=purpose,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            decision=decision,
            occurred_at=datetime.now(timezone.utc),
            trace_id=trace_id,
            consent_id=consent_id,
        )
        await self._repository.append(record)
        return record
