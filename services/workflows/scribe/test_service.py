from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from services.api.app.auth.context import RequestContext
from services.api.app.auth.roles import AppRole
from services.domain.access_policy.policy import AccessPolicy
from services.workflows.scribe.service import (
    ClinicalNoteVersion,
    EncounterRecord,
    NoteStatus,
    NoteStatement,
    SOAPContent,
    ScribeService,
)

CLINICIAN = UUID("10000000-0000-4000-8000-000000000001")
PATIENT = UUID("20000000-0000-4000-8000-000000000001")
ENCOUNTER = UUID("30000000-0000-4000-8000-000000000001")


def context(*, role=AppRole.CLINICIAN, purpose="scribe", actor=CLINICIAN, consents=()):
    return RequestContext(actor, role, PATIENT, purpose, tuple(consents), "scribe-trace", None, {})


class AccessReader:
    def __init__(self, *, owner=True, active_grant=None):
        self.owner = owner
        self.active_grant = active_grant
        self.required_scope = None

    async def is_patient_owner(self, **kwargs):
        return self.owner

    async def find_active_grant(self, **kwargs):
        self.required_scope = kwargs["scope"]
        return self.active_grant if self.active_grant in kwargs["consent_ids"] else None


class Repository:
    def __init__(self):
        self.encounters = {
            ENCOUNTER: EncounterRecord(ENCOUNTER, PATIENT, CLINICIAN),
        }
        self.versions = []

    async def get_encounter(self, encounter_id):
        return self.encounters.get(encounter_id)

    async def list_note_versions(self, encounter_id):
        return [note for note in self.versions if note.encounter_id == encounter_id]

    async def append_note_version(self, note: ClinicalNoteVersion):
        if any(existing.note_id == note.note_id and existing.version == note.version for existing in self.versions):
            raise AssertionError("note versions must be append-only and unique")
        if self.versions and self.versions[-1].status == NoteStatus.SIGNED and note.version <= self.versions[-1].version:
            raise AssertionError("signed versions cannot be overwritten")
        self.versions.append(note)


def service(*, access_reader=None):
    repository = Repository()
    reader = access_reader or AccessReader()
    return ScribeService(repository=repository, access_policy=AccessPolicy(reader)), repository, reader


@pytest.mark.asyncio
async def test_draft_keeps_only_transcript_evidence_and_unknown_sections():
    workflow, repo, _ = service()
    transcript = "Synthetic transcript: patient reports sample marker alpha."
    draft = await workflow.create_draft(context(), encounter_id=ENCOUNTER, transcript=transcript)
    assert draft.status == NoteStatus.DRAFT
    assert draft.content.subjective[0].text == transcript
    assert draft.content.subjective[0].source_start == 0
    assert draft.content.unknown_sections == frozenset({"objective", "assessment", "plan"})
    assert len(repo.versions) == 1
    assert draft.signed_at is None


@pytest.mark.asyncio
async def test_sign_requires_authenticated_assigned_clinician_and_scoped_access():
    workflow, _, _ = service()
    with pytest.raises(PermissionError):
        await workflow.create_draft(
            context(role=AppRole.PATIENT), encounter_id=ENCOUNTER, transcript="synthetic"
        )
    with pytest.raises(PermissionError):
        await workflow.create_draft(
            context(actor=uuid4()), encounter_id=ENCOUNTER, transcript="synthetic"
        )
    with pytest.raises(PermissionError):
        await workflow.create_draft(
            context(purpose="records"), encounter_id=ENCOUNTER, transcript="synthetic"
        )


@pytest.mark.asyncio
async def test_reject_and_edit_append_new_versions_then_sign_is_immutable():
    workflow, repo, _ = service()
    transcript = "Synthetic transcript has phrase A and phrase B."
    draft = await workflow.create_draft(context(), encounter_id=ENCOUNTER, transcript=transcript)

    rejected = await workflow.review(
        context(),
        encounter_id=ENCOUNTER,
        expected_version=draft.version,
        action="reject",
        rejection_reason="Please review source text",
    )
    assert rejected.status == NoteStatus.REJECTED
    assert rejected.version == 2

    new_draft = await workflow.create_draft(context(), encounter_id=ENCOUNTER, transcript=transcript)
    assert new_draft.version == 3
    edited = SOAPContent(
        subjective=(NoteStatement(text="phrase A", source_start=25, source_end=33),),
        objective=(NoteStatement(text="phrase B", source_start=38, source_end=46),),
        unknown_sections=frozenset({"assessment", "plan"}),
    )
    version4 = await workflow.review(
        context(),
        encounter_id=ENCOUNTER,
        expected_version=new_draft.version,
        action="edit",
        edited_content=edited,
    )
    assert version4.version == 4 and version4.status == NoteStatus.DRAFT

    completed = SOAPContent(
        subjective=(NoteStatement(text="phrase A", source_start=25, source_end=33),),
        objective=(NoteStatement(text="phrase B", source_start=38, source_end=46),),
        assessment=(NoteStatement(text="phrase A", source_start=25, source_end=33),),
        plan=(NoteStatement(text="phrase B", source_start=38, source_end=46),),
        unknown_sections=frozenset(),
    )
    signed = await workflow.review(
        context(),
        encounter_id=ENCOUNTER,
        expected_version=version4.version,
        action="sign",
        edited_content=completed,
    )
    assert signed.status == NoteStatus.SIGNED
    assert signed.signed_by == CLINICIAN
    assert signed.signed_at is not None
    assert signed.content_hash and len(signed.content_hash) == 64
    assert signed.prior_version == version4.version
    assert [item.version for item in repo.versions] == [1, 2, 3, 4, 5]

    with pytest.raises(ValueError, match="Only a draft"):
        await workflow.review(
            context(),
            encounter_id=ENCOUNTER,
            expected_version=signed.version,
            action="edit",
            edited_content=completed,
        )

    # A corrected note is a new version, never a mutation of the signed record.
    follow_up = await workflow.create_draft(context(), encounter_id=ENCOUNTER, transcript=transcript)
    assert follow_up.version == 6
    assert repo.versions[4].status == NoteStatus.SIGNED


@pytest.mark.asyncio
async def test_unsupported_statement_and_stale_version_are_rejected():
    workflow, _, _ = service()
    transcript = "Synthetic transcript: sample quantity is stated as 42 units."
    draft = await workflow.create_draft(context(), encounter_id=ENCOUNTER, transcript=transcript)
    unsupported = SOAPContent(
        subjective=(NoteStatement(text="sample quantity is stated as 42 units", source_start=0, source_end=39),),
        unknown_sections=frozenset({"objective", "assessment", "plan"}),
    )
    with pytest.raises(ValueError, match="exactly match"):
        await workflow.review(
            context(), encounter_id=ENCOUNTER, expected_version=draft.version,
            action="edit", edited_content=unsupported,
        )
    with pytest.raises(ValueError, match="stale"):
        await workflow.review(
            context(), encounter_id=ENCOUNTER, expected_version=99, action="reject",
            rejection_reason="synthetic",
        )


@pytest.mark.asyncio
async def test_consent_scope_must_be_revalidated_and_unknown_sections_block_sign():
    grant = uuid4()
    reader = AccessReader(owner=False, active_grant=grant)
    workflow, _, _ = service(access_reader=reader)
    draft = await workflow.create_draft(
        context(consents=[grant]), encounter_id=ENCOUNTER, transcript="Synthetic source phrase."
    )
    assert reader.required_scope == "scribe:transcribe"

    with pytest.raises(ValueError, match="reviewed evidence"):
        await workflow.review(
            context(consents=[grant]), encounter_id=ENCOUNTER,
            expected_version=draft.version, action="sign",
        )
    assert reader.required_scope == "scribe:approve"

    expired_reader = AccessReader(owner=False, active_grant=None)
    denied_workflow, _, _ = service(access_reader=expired_reader)
    with pytest.raises(PermissionError):
        await denied_workflow.create_draft(
            context(consents=[grant]), encounter_id=ENCOUNTER, transcript="Synthetic source."
        )


def test_soap_contract_rejects_empty_sections_not_marked_unknown():
    with pytest.raises(ValidationError):
        SOAPContent(
            subjective=(NoteStatement(text="x", source_start=0, source_end=1),),
            unknown_sections=frozenset(),
        )
