"""Fail-closed deterministic red-flag gate; clinical rules require owner approval."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, Iterable


class GateRoute(StrEnum):
    EMERGENCY = "emergency"
    HUMAN_REVIEW = "human_review"
    ROUTINE = "routine"


@dataclass(frozen=True, slots=True)
class RedFlagRule:
    rule_id: str
    version: str
    matches: Callable[[str], bool]


@dataclass(frozen=True, slots=True)
class RedFlagRuleSet:
    version: str
    approved_by: str | None
    rules: tuple[RedFlagRule, ...]

    @property
    def approved(self) -> bool:
        return bool(self.version.strip() and self.approved_by and self.approved_by.strip())


@dataclass(frozen=True, slots=True)
class RedFlagResult:
    route: GateRoute
    matched_rule_ids: tuple[str, ...]
    rule_set_version: str | None
    reason: str


class RedFlagGate:
    """Evaluate only approved clinician-owned rules; never invent clinical rules."""

    def __init__(self, rule_set: RedFlagRuleSet | None) -> None:
        self._rule_set = rule_set

    def evaluate(self, text: str) -> RedFlagResult:
        if not text or not text.strip():
            return RedFlagResult(GateRoute.HUMAN_REVIEW, (), None, "empty_intake")
        rule_set = self._rule_set
        if rule_set is None or not rule_set.approved:
            return RedFlagResult(GateRoute.HUMAN_REVIEW, (), None, "rules_not_clinically_approved")

        try:
            matched = tuple(rule.rule_id for rule in rule_set.rules if rule.matches(text))
        except Exception:
            return RedFlagResult(
                GateRoute.HUMAN_REVIEW,
                (),
                rule_set.version,
                "rule_evaluation_failed",
            )
        return RedFlagResult(
            route=GateRoute.EMERGENCY if matched else GateRoute.ROUTINE,
            matched_rule_ids=matched,
            rule_set_version=rule_set.version,
            reason="approved_red_flag_match" if matched else "no_approved_rule_match",
        )


def approved_rule_set(
    *,
    version: str,
    approved_by: str,
    rules: Iterable[RedFlagRule],
) -> RedFlagRuleSet:
    """Construct an explicitly approved set; callers supply rules from clinical owner."""
    return RedFlagRuleSet(version=version, approved_by=approved_by, rules=tuple(rules))
