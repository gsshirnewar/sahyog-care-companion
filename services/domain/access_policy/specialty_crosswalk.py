"""Deterministic, owner-approved specialty crosswalk lookup and validation."""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CrosswalkEntry:
    source_key: str
    specialty_code: str
    language_tag: str
    active: bool = True
    clinically_approved: bool = False


@dataclass(frozen=True, slots=True)
class CrosswalkIssue:
    source_key: str
    reason: str


@dataclass(frozen=True, slots=True)
class CrosswalkValidation:
    valid: bool
    issues: tuple[CrosswalkIssue, ...]


class SpecialtyCrosswalk:
    def __init__(self, entries: Iterable[CrosswalkEntry], active_specialty_codes: set[str]) -> None:
        self._entries = tuple(entries)
        self._active_codes = frozenset(active_specialty_codes)

    def validate(self) -> CrosswalkValidation:
        issues: list[CrosswalkIssue] = []
        seen: set[tuple[str, str]] = set()
        for entry in self._entries:
            normalized_key = (entry.language_tag.casefold(), _normalize(entry.source_key))
            if not normalized_key[1]:
                issues.append(CrosswalkIssue(entry.source_key, "empty_source_key"))
            if normalized_key in seen:
                issues.append(CrosswalkIssue(entry.source_key, "duplicate_source_mapping"))
            seen.add(normalized_key)
            if entry.specialty_code not in self._active_codes:
                issues.append(CrosswalkIssue(entry.source_key, "unknown_or_inactive_specialty"))
            if not entry.language_tag.strip():
                issues.append(CrosswalkIssue(entry.source_key, "missing_language_tag"))
        return CrosswalkValidation(not issues, tuple(issues))

    def resolve(self, text: str, language_tag: str) -> str | None:
        if not text.strip() or not language_tag.strip() or not self.validate().valid:
            return None
        normalized_text = _normalize(text)
        matches = {
            entry.specialty_code
            for entry in self._entries
            if entry.active
            and entry.clinically_approved
            and entry.language_tag.casefold() == language_tag.casefold()
            and _normalize(entry.source_key) == normalized_text
            and entry.specialty_code in self._active_codes
        }
        return next(iter(matches)) if len(matches) == 1 else None


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())
