"""Verified, retryable encounter-audio deletion worker."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from uuid import UUID

MAX_AUDIO_RETENTION = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class AudioDeletionCandidate:
    encounter_id: UUID
    actor_id: UUID | None
    object_key: str
    uploaded_at: datetime
    retention_until: datetime
    trace_id: str


@dataclass(frozen=True, slots=True)
class DeletionResult:
    encounter_id: UUID
    status: str
    reason: str


class AudioDeletionRepository(Protocol):
    async def due_audio(self, *, now: datetime, limit: int) -> list[AudioDeletionCandidate]: ...
    async def mark_deleting(self, encounter_id: UUID) -> None: ...
    async def mark_deleted(self, encounter_id: UUID, *, deleted_at: datetime) -> None: ...
    async def mark_deletion_failed(self, encounter_id: UUID, *, reason_code: str) -> None: ...


class ObjectStorage(Protocol):
    async def delete(self, object_key: str) -> None: ...
    async def exists(self, object_key: str) -> bool: ...


class AudioDeletionWorker:
    def __init__(self, *, repository: AudioDeletionRepository, storage: ObjectStorage) -> None:
        self._repository = repository
        self._storage = storage

    async def run_due(self, *, now: datetime | None = None, limit: int = 100) -> list[DeletionResult]:
        current_time = now or datetime.now(timezone.utc)
        if current_time.tzinfo is None:
            raise ValueError("Deletion clock must be timezone-aware")
        if not 1 <= limit <= 1000:
            raise ValueError("Batch size must be between 1 and 1000")
        candidates = await self._repository.due_audio(now=current_time, limit=limit)
        results: list[DeletionResult] = []
        for candidate in candidates:
            if candidate.uploaded_at.tzinfo is None or candidate.retention_until.tzinfo is None:
                await self._repository.mark_deletion_failed(candidate.encounter_id, reason_code="invalid_retention_timestamp")
                results.append(DeletionResult(candidate.encounter_id, "failed", "invalid_retention_timestamp"))
                continue
            if candidate.retention_until > candidate.uploaded_at + MAX_AUDIO_RETENTION:
                await self._repository.mark_deletion_failed(candidate.encounter_id, reason_code="retention_exceeds_policy" )
                results.append(DeletionResult(candidate.encounter_id, "failed", "retention_exceeds_policy"))
                continue
            if candidate.retention_until > current_time:
                continue

            await self._repository.mark_deleting(candidate.encounter_id)
            try:
                await self._storage.delete(candidate.object_key)
                if await self._storage.exists(candidate.object_key):
                    raise RuntimeError("object_still_exists")
            except Exception:
                # Avoid logging exception messages or object keys: provider errors
                # may contain credentials, paths, or personal identifiers.
                await self._repository.mark_deletion_failed(candidate.encounter_id, reason_code="delete_or_verification_failed")
                results.append(DeletionResult(candidate.encounter_id, "failed", "delete_or_verification_failed"))
                continue

            await self._repository.mark_deleted(candidate.encounter_id, deleted_at=current_time)
            results.append(DeletionResult(candidate.encounter_id, "deleted", "verified_absent"))
        return results
