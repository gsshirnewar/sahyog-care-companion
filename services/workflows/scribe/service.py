"""Synthetic-transcript scribe draft/review/sign workflow with immutable versions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.api.app.auth.context import RequestContext
from services.api.app.auth.roles import AppRole
from services.domain.access_policy.policy import AccessPolicy

SOAP_SECTIONS = ("subjective", "objective", "assessment", "plan")
UNKNOWN_TEXT = "Unknown; clinician review required."


class NoteStatus(StrEnum):
    DRAFT = "draft"
    REJECTED = "rejected"
    SIGNED = "signed"


class NoteStatement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=4000)
    source_start: int = Field(ge=0)
    source_end: int = Field(gt=0)


class SOAPContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subjective: tuple[NoteStatement, ...] = ()
    objective: tuple[NoteStatement, ...] = ()
    assessment: tuple[NoteStatement, ...] = ()
    plan: tuple[NoteStatement, ...] = ()
    unknown_sections: frozenset[str] = frozenset(SOAP_SECTIONS)

    @model_validator(mode="after")
    def validate_section_state(self):
        unknown = set(self.unknown_sections)
        if unknown - set(SOAP_SECTIONS):
            raise ValueError("Unknown SOAP section name")
        for section in SOAP_SECTIONS:
            if getattr(self, section) and section in unknown:
                raise ValueError(f"{section} cannot contain statements and be marked unknown")
            if not getattr(self, section) and section not in unknown:
                raise ValueError(f"Empty {section} section must be marked unknown")
        return self


@dataclass(frozen=True, slots=True)
class EncounterRecord:
    encounter_id: UUID
    patient_id: UUID
    assigned_clinician_id: UUID
    status: str = "open"


@dataclass(frozen=True, slots=True)
class ClinicalNoteVersion:
    note_id: UUID
    encounter_id: UUID
    version: int
    content: SOAPContent
    status: NoteStatus
    created_at: datetime
    signed_by: UUID | None = None
    signed_at: datetime | None = None
    content_hash: str | None = None
    prior_version: int | None = None
    rejection_reason: str | None = None


class ScribeRepository(Protocol):
    async def get_encounter(self, encounter_id: UUID) -> EncounterRecord | None: ...
    async def list_note_versions(self, encounter_id: UUID) -> list[ClinicalNoteVersion]: ...
    async def append_note_version(self, note: ClinicalNoteVersion) -> None: ...


class ScribeService:
    def __init__(self, *, repository: ScribeRepository, access_policy: AccessPolicy) -> None:
        self._repository = repository
        self._access_policy = access_policy

    async def create_draft(
        self,
        context: RequestContext,
        *,
        encounter_id: UUID,
        transcript: str,
    ) -> ClinicalNoteVersion:
        encounter = await self._authorized_encounter(
            context,
            encounter_id=encounter_id,
            scope="scribe:transcribe",
        )
        if not transcript or not transcript.strip() or len(transcript) > 20_000:
            raise ValueError("Synthetic transcript must contain 1-20000 characters")

        statement = NoteStatement(
            text=transcript,
            source_start=0,
            source_end=len(transcript),
        )
        content = SOAPContent(
            subjective=(statement,),
            unknown_sections=frozenset({"objective", "assessment", "plan"}),
        )
        existing = await self._repository.list_note_versions(encounter_id)
        if existing and existing[-1].status == NoteStatus.DRAFT:
            raise ValueError("A draft already exists; review or reject it before creating another")
        note_id = existing[0].note_id if existing else uuid4()
        version = existing[-1].version + 1 if existing else 1
        note = ClinicalNoteVersion(
            note_id=note_id,
            encounter_id=encounter.encounter_id,
            version=version,
            content=content,
            status=NoteStatus.DRAFT,
            created_at=datetime.now(timezone.utc),
            prior_version=existing[-1].version if existing else None,
        )
        await self._repository.append_note_version(note)
        return note

    async def latest(
        self,
        context: RequestContext,
        *,
        encounter_id: UUID,
    ) -> ClinicalNoteVersion:
        await self._authorized_encounter(
            context,
            encounter_id=encounter_id,
            scope="scribe:read",
        )
        versions = await self._repository.list_note_versions(encounter_id)
        if not versions:
            raise ValueError("No note exists for this encounter")
        return versions[-1]

    async def review(
        self,
        context: RequestContext,
        *,
        encounter_id: UUID,
        expected_version: int,
        action: str,
        edited_content: SOAPContent | None = None,
        rejection_reason: str | None = None,
    ) -> ClinicalNoteVersion:
        encounter = await self._authorized_encounter(
            context,
            encounter_id=encounter_id,
            scope="scribe:approve",
        )
        versions = await self._repository.list_note_versions(encounter_id)
        if not versions:
            raise ValueError("No note draft exists for this encounter")
        current = versions[-1]
        if current.version != expected_version:
            raise ValueError("Note version is stale; reload before reviewing")
        if current.status != NoteStatus.DRAFT:
            raise ValueError("Only a draft version can be reviewed")

        if action == "reject":
            if not rejection_reason or not rejection_reason.strip():
                raise ValueError("A rejection reason is required")
            result = ClinicalNoteVersion(
                note_id=current.note_id,
                encounter_id=current.encounter_id,
                version=current.version + 1,
                content=current.content,
                status=NoteStatus.REJECTED,
                created_at=datetime.now(timezone.utc),
                prior_version=current.version,
                rejection_reason=rejection_reason.strip()[:500],
            )
            await self._repository.append_note_version(result)
            return result

        if action not in {"edit", "sign"}:
            raise ValueError("Review action must be edit, reject, or sign")
        content = edited_content if edited_content is not None else current.content
        original_transcript = versions[0].content.subjective[0].text
        self._validate_evidence(content, transcript=original_transcript)

        if action == "edit":
            edited = ClinicalNoteVersion(
                note_id=current.note_id,
                encounter_id=current.encounter_id,
                version=current.version + 1,
                content=content,
                status=NoteStatus.DRAFT,
                created_at=datetime.now(timezone.utc),
                prior_version=current.version,
            )
            await self._repository.append_note_version(edited)
            return edited

        if content.unknown_sections:
            raise ValueError("All SOAP sections must contain reviewed evidence or be explicitly entered")
        canonical_payload = {
            section: [statement.model_dump(mode="json") for statement in getattr(content, section)]
            for section in SOAP_SECTIONS
        }
        canonical_payload["unknown_sections"] = sorted(content.unknown_sections)
        canonical = json.dumps(canonical_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        signed_at = datetime.now(timezone.utc)
        signed = ClinicalNoteVersion(
            note_id=current.note_id,
            encounter_id=encounter.encounter_id,
            version=current.version + 1,
            content=content,
            status=NoteStatus.SIGNED,
            created_at=signed_at,
            signed_by=context.actor_id,
            signed_at=signed_at,
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            prior_version=current.version,
        )
        await self._repository.append_note_version(signed)
        return signed

    async def _authorized_encounter(
        self,
        context: RequestContext,
        *,
        encounter_id: UUID,
        scope: str,
    ) -> EncounterRecord:
        if context.role != AppRole.CLINICIAN or context.purpose != "scribe":
            raise PermissionError("An authenticated clinician with scribe purpose is required")
        encounter = await self._repository.get_encounter(encounter_id)
        if encounter is None:
            raise ValueError("Encounter not found")
        if encounter.assigned_clinician_id != context.actor_id:
            raise PermissionError("Clinician is not assigned to this encounter")
        await self._access_policy.require(
            context,
            subject_id=encounter.patient_id,
            required_scope=scope,
        )
        return encounter

    @staticmethod
    def _validate_evidence(content: SOAPContent, *, transcript: str) -> None:
        for section in SOAP_SECTIONS:
            for statement in getattr(content, section):
                if statement.source_end > len(transcript):
                    raise ValueError("Note evidence span exceeds transcript length")
                source_text = transcript[statement.source_start : statement.source_end]
                if source_text != statement.text:
                    raise ValueError("Note statement must exactly match its cited transcript span")
