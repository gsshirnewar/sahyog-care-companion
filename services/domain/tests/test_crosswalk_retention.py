from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from services.domain.access_policy.specialty_crosswalk import CrosswalkEntry, SpecialtyCrosswalk
from services.domain.retention.audio_deletion import (
    AudioDeletionCandidate,
    AudioDeletionWorker,
)


def test_specialty_crosswalk_requires_valid_active_clinically_approved_entry():
    entries = [
        CrosswalkEntry("demo category alpha", "DEMO-GENERAL", "en", clinically_approved=True),
        CrosswalkEntry("demo category beta", "DEMO-SKIN", "en", clinically_approved=False),
    ]
    crosswalk = SpecialtyCrosswalk(entries, {"DEMO-GENERAL", "DEMO-SKIN"})
    assert crosswalk.validate().valid
    assert crosswalk.resolve("  DEMO CATEGORY ALPHA ", "en") == "DEMO-GENERAL"
    assert crosswalk.resolve("demo category beta", "en") is None
    assert crosswalk.resolve("demo category alpha", "hi") is None

    invalid = SpecialtyCrosswalk(
        [
            CrosswalkEntry("same phrase", "DEMO-GENERAL", "en"),
            CrosswalkEntry("same phrase", "UNKNOWN", "en"),
        ],
        {"DEMO-GENERAL"},
    )
    assert not invalid.validate().valid
    assert invalid.resolve("same phrase", "en") is None


class FakeDeletionRepository:
    def __init__(self, candidates):
        self.candidates = candidates
        self.states = {}

    async def due_audio(self, *, now, limit):
        return self.candidates[:limit]

    async def mark_deleting(self, encounter_id):
        self.states[encounter_id] = "deleting"

    async def mark_deleted(self, encounter_id, *, deleted_at):
        self.states[encounter_id] = ("deleted", deleted_at)

    async def mark_deletion_failed(self, encounter_id, *, reason_code):
        self.states[encounter_id] = ("failed", reason_code)


class FakeStorage:
    def __init__(self, objects, *, verify_failure=False):
        self.objects = set(objects)
        self.verify_failure = verify_failure
        self.deleted = []

    async def delete(self, object_key):
        self.deleted.append(object_key)
        self.objects.discard(object_key)

    async def exists(self, object_key):
        return self.verify_failure or object_key in self.objects


def candidate(*, hours=24, retention_hours=24, key="synthetic/audio-one.wav"):
    uploaded_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return AudioDeletionCandidate(
        encounter_id=uuid4(),
        actor_id=None,
        object_key=key,
        uploaded_at=uploaded_at,
        retention_until=uploaded_at + timedelta(hours=retention_hours),
        trace_id="synthetic-trace",
    )


@pytest.mark.asyncio
async def test_audio_deletion_is_verified_and_persisted():
    item = candidate()
    repository = FakeDeletionRepository([item])
    storage = FakeStorage([item.object_key])
    now = item.retention_until
    result = await AudioDeletionWorker(repository=repository, storage=storage).run_due(now=now)
    assert result[0].status == "deleted"
    assert result[0].reason == "verified_absent"
    assert item.object_key in storage.deleted
    assert repository.states[item.encounter_id][0] == "deleted"


@pytest.mark.asyncio
async def test_audio_deletion_marks_provider_verification_failure():
    item = candidate(key="synthetic/audio-verify-fail.wav")
    repository = FakeDeletionRepository([item])
    storage = FakeStorage([item.object_key], verify_failure=True)
    result = await AudioDeletionWorker(repository=repository, storage=storage).run_due(now=item.retention_until)
    assert result[0].status == "failed"
    assert repository.states[item.encounter_id] == ("failed", "delete_or_verification_failed")


@pytest.mark.asyncio
async def test_audio_deletion_rejects_retention_over_24_hours_and_future_items():
    too_long = candidate(retention_hours=25, key="synthetic/audio-too-long.wav")
    future = candidate(retention_hours=12, key="synthetic/audio-future.wav")
    repository = FakeDeletionRepository([too_long, future])
    storage = FakeStorage([too_long.object_key, future.object_key])
    early = too_long.uploaded_at + timedelta(hours=6)
    result = await AudioDeletionWorker(repository=repository, storage=storage).run_due(now=early)
    assert result[0].reason == "retention_exceeds_policy"
    assert len(result) == 1
    assert future.object_key not in storage.deleted
