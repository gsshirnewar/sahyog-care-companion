"""LangGraph for rules-first triage and specialty/slot shortlist preparation."""

from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from services.domain.safety.red_flag.gate import GateRoute, RedFlagGate, RedFlagResult
class TriageGraphState(TypedDict, total=False):
    intake_text: str
    language: str
    gate: RedFlagResult
    route: str
    specialty_code: str | None
    options: list[object]
    reason: str


class TriageDecisionGraph:
    """A per-request graph with a mandatory red-flag gate before any slot lookup."""

    def __init__(self, *, gate: RedFlagGate, repository, specialty_mapper) -> None:
        self._gate = gate
        self._repository = repository
        self._specialty_mapper = specialty_mapper
        builder = StateGraph(TriageGraphState)
        builder.add_node("red_flag_gate", self._red_flag_node)
        builder.add_node("specialty_match", self._specialty_node)
        builder.add_node("slot_search", self._slot_search_node)
        builder.add_edge(START, "red_flag_gate")
        builder.add_conditional_edges(
            "red_flag_gate",
            self._after_gate,
            {"specialty": "specialty_match", "stop": END},
        )
        builder.add_conditional_edges(
            "specialty_match",
            self._after_specialty,
            {"search": "slot_search", "stop": END},
        )
        builder.add_edge("slot_search", END)
        self._compiled = builder.compile()

    async def run(self, *, intake_text: str, language: str) -> TriageGraphState:
        return await self._compiled.ainvoke({"intake_text": intake_text, "language": language})

    async def _red_flag_node(self, state: TriageGraphState) -> dict:
        result = self._gate.evaluate(state["intake_text"])
        if result.route == GateRoute.EMERGENCY:
            return {"gate": result, "route": "emergency_handoff", "reason": result.reason}
        if result.route != GateRoute.ROUTINE:
            return {"gate": result, "route": "human_review", "reason": result.reason}
        return {"gate": result, "route": "continue"}

    async def _specialty_node(self, state: TriageGraphState) -> dict:
        specialty_code = self._specialty_mapper(state["intake_text"], state["language"])
        if not specialty_code:
            return {"route": "human_review", "reason": "specialty_unmapped"}
        return {"specialty_code": specialty_code, "route": "continue"}

    async def _slot_search_node(self, state: TriageGraphState) -> dict:
        options = await self._repository.find_active_slots(state["specialty_code"], limit=3)
        return {"options": options}

    @staticmethod
    def _after_gate(state: TriageGraphState) -> str:
        return "specialty" if state.get("route") == "continue" else "stop"

    @staticmethod
    def _after_specialty(state: TriageGraphState) -> str:
        return "search" if state.get("route") == "continue" else "stop"
