"""Deterministic patient access decisions; missing evidence always denies."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from services.api.app.auth.context import RequestContext


@dataclass(frozen=True, slots=True)
class AccessDecision:
    allowed: bool
    reason: str
    consent_id: UUID | None = None


class ConsentReader(Protocol):
    async def is_patient_owner(self, *, subject_id: UUID, actor_id: UUID) -> bool: ...

    async def find_active_grant(
        self,
        *,
        subject_id: UUID,
        grantee_id: UUID,
        purpose: str,
        scope: str,
        consent_ids: tuple[UUID, ...],
    ) -> UUID | None: ...


class AccessPolicy:
    """Authorize owner access or revalidated, purpose/scope-specific consent."""

    def __init__(self, consent_reader: ConsentReader) -> None:
        self._consent_reader = consent_reader

    async def decide(
        self,
        context: RequestContext,
        *,
        subject_id: UUID,
        required_scope: str,
    ) -> AccessDecision:
        if await self._consent_reader.is_patient_owner(
            subject_id=subject_id,
            actor_id=context.actor_id,
        ):
            return AccessDecision(True, "owner")
        if not context.consent_ids:
            return AccessDecision(False, "no_consent_claim")
        grant_id = await self._consent_reader.find_active_grant(
            subject_id=subject_id,
            grantee_id=context.actor_id,
            purpose=context.purpose,
            scope=required_scope,
            consent_ids=context.consent_ids,
        )
        if grant_id is None:
            return AccessDecision(False, "no_active_matching_consent")
        return AccessDecision(True, "active_consent", grant_id)

    async def require(
        self,
        context: RequestContext,
        *,
        subject_id: UUID,
        required_scope: str,
    ) -> AccessDecision:
        decision = await self.decide(
            context,
            subject_id=subject_id,
            required_scope=required_scope,
        )
        if not decision.allowed:
            raise PermissionError("Patient access denied")
        return decision
