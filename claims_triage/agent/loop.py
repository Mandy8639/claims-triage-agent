"""LangGraph-style orchestration: validate → Gate 1 → retrieve+draft → Gate 2 → log.

The agent NEVER decides a claim outcome. Gates are human checkpoints.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from claims_triage.corpus.store import ClaimStore
from claims_triage.guardrail.pause import check_pause
from claims_triage.guardrail.redact import redact_obj
from claims_triage.guardrail.scanner import Verdict, scan_draft, scan_packet_fields
from claims_triage.tools.draft import draft_letter
from claims_triage.tools.retrieve import retrieve_policy
from claims_triage.tools.validate import validate_packet


class AgentState(TypedDict, total=False):
    packet_id: str
    run_id: str
    trace_id: str
    auto_gates: bool
    gate1_decision: str | None
    gate2_decision: str | None
    actor: str
    input_scan: dict[str, Any]
    validation: dict[str, Any]
    retrieval: dict[str, Any]
    draft: dict[str, Any]
    output_scan: dict[str, Any]
    status: str
    guardrail_verdict: str
    reason_code: str
    paused: bool
    pause_reason: str | None
    abstained: bool
    needs_human_review: bool
    cost_usd: float
    log_redacted: dict[str, Any]


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


def build_graph(store: ClaimStore):
    def start(state: AgentState) -> AgentState:
        run_id = state.get("run_id") or _new_run_id()
        trace_id = state.get("trace_id") or store.new_trace_id()
        return {
            **state,
            "run_id": run_id,
            "trace_id": trace_id,
            "actor": state.get("actor") or "adjuster",
            "cost_usd": 0.0,
            "paused": False,
            "abstained": False,
            "needs_human_review": False,
        }

    def pause_check(state: AgentState) -> AgentState:
        if store.is_paused():
            return {
                **state,
                "paused": True,
                "pause_reason": store.meta_control("pause_reason") or "agent_paused",
                "status": "paused",
                "guardrail_verdict": Verdict.HOLD.value,
                "reason_code": "agent_paused",
            }
        result = check_pause(
            corpus_hash=store.corpus_hash(),
            recent_runs=store.recent_run_summaries(100),
        )
        if result.should_pause:
            store.set_paused(True, reason=result.reason_code or "pause")
            store.append_exception(
                result.reason_code or "pause", result.detail, run_id=state.get("run_id")
            )
            return {
                **state,
                "paused": True,
                "pause_reason": result.reason_code,
                "status": "paused",
                "guardrail_verdict": Verdict.HOLD.value,
                "reason_code": result.reason_code or "pause",
            }
        return state

    def scan_input(state: AgentState) -> AgentState:
        packet = store.get_packet(state["packet_id"])
        if packet is None:
            return {
                **state,
                "status": "blocked",
                "guardrail_verdict": Verdict.BLOCK.value,
                "reason_code": "unknown_packet",
                "input_scan": {"verdict": "BLOCK", "detail": "unknown packet"},
            }
        scan = scan_packet_fields(packet, known_policy_ids=store.list_policy_ids())
        out: AgentState = {**state, "input_scan": scan.to_dict()}
        if scan.verdict is Verdict.BLOCK:
            out.update(
                {
                    "status": "blocked",
                    "guardrail_verdict": Verdict.BLOCK.value,
                    "reason_code": ",".join(scan.reason_codes) or "input_block",
                }
            )
            store.append_exception(out["reason_code"], scan.detail, run_id=state.get("run_id"))
        return out

    def validate(state: AgentState) -> AgentState:
        report = validate_packet(store, state["packet_id"])
        out: AgentState = {**state, "validation": report}
        if report.get("abstain"):
            out.update(
                {
                    "status": "needs_human_review",
                    "abstained": True,
                    "needs_human_review": True,
                    "guardrail_verdict": Verdict.HOLD.value,
                    "reason_code": report.get("abstain_reason") or "abstain",
                }
            )
        return out

    def gate1(state: AgentState) -> AgentState:
        """Human Gate 1: review validation before drafting."""
        decision = state.get("gate1_decision")
        if state.get("auto_gates") and not decision:
            # Auto-approve gate1 only when validation passed and not abstaining.
            if state.get("abstained") or (state.get("validation") or {}).get("overall") != "pass":
                decision = "reject"
            else:
                decision = "approve"
        if not decision:
            return {
                **state,
                "status": "awaiting_gate1",
                "gate1_decision": None,
                "needs_human_review": True,
            }
        store.append_review(
            run_id=state["run_id"],
            gate="gate1",
            decision=decision,
            actor=state.get("actor") or "adjuster",
            reason=f"Gate 1 {decision}",
        )
        if decision != "approve":
            return {
                **state,
                "gate1_decision": decision,
                "status": "gate1_rejected",
                "guardrail_verdict": Verdict.HOLD.value,
                "reason_code": "gate1_rejected",
                "needs_human_review": True,
            }
        return {**state, "gate1_decision": decision}

    def retrieve_and_draft(state: AgentState) -> AgentState:
        policy_id = (state.get("validation") or {}).get("policy_id", "")
        retrieval = retrieve_policy(
            store,
            f"contestability beneficiary required_documents {policy_id}",
        )
        draft = draft_letter(
            store,
            state["packet_id"],
            validation=state.get("validation"),
        )
        out: AgentState = {
            **state,
            "retrieval": retrieval,
            "draft": draft,
            "cost_usd": float(draft.get("cost_usd") or 0.0),
        }
        if draft.get("abstain"):
            out.update(
                {
                    "status": "needs_human_review",
                    "abstained": True,
                    "needs_human_review": True,
                    "guardrail_verdict": Verdict.HOLD.value,
                    "reason_code": draft.get("abstain_reason") or "abstain",
                }
            )
            return out

        # Low retrieval confidence → HOLD
        if draft.get("low_retrieval_confidence"):
            out.update(
                {
                    "status": "held",
                    "guardrail_verdict": Verdict.HOLD.value,
                    "reason_code": "low_retrieval_confidence",
                    "needs_human_review": True,
                }
            )
            return out

        # Citation post-check already stripped uncited claims; HOLD if any were stripped
        if draft.get("stripped_uncited"):
            out.update(
                {
                    "status": "held",
                    "guardrail_verdict": Verdict.HOLD.value,
                    "reason_code": "uncited_claims_stripped",
                    "needs_human_review": True,
                }
            )
            return out

        scan = scan_draft(
            draft.get("draft_text") or "",
            known_policy_ids=store.list_policy_ids(),
        )
        # The boilerplate says it does not approve/deny/pay — that phrase can trip CL-001.
        # Re-scan only the factual body without the disclaimer negation sentence.
        body = "\n".join(
            s
            for s in (draft.get("draft_text") or "").split(". ")
            if "does not approve, deny, or pay" not in s.lower()
        )
        scan = scan_draft(body, known_policy_ids=store.list_policy_ids())
        out["output_scan"] = scan.to_dict()
        if scan.verdict is Verdict.BLOCK:
            out.update(
                {
                    "status": "blocked",
                    "guardrail_verdict": Verdict.BLOCK.value,
                    "reason_code": ",".join(scan.reason_codes) or "output_block",
                }
            )
            store.append_exception(out["reason_code"], scan.detail, run_id=state.get("run_id"))
        elif scan.verdict is Verdict.HOLD:
            out.update(
                {
                    "status": "held",
                    "guardrail_verdict": Verdict.HOLD.value,
                    "reason_code": ",".join(scan.reason_codes) or "output_hold",
                    "needs_human_review": True,
                }
            )
        else:
            out.update(
                {
                    "status": "draft_ready",
                    "guardrail_verdict": Verdict.ALLOW.value,
                    "reason_code": "draft_ready_for_gate2",
                }
            )
        return out

    def gate2(state: AgentState) -> AgentState:
        decision = state.get("gate2_decision")
        if state.get("auto_gates") and not decision:
            decision = "approve"
        if not decision:
            return {
                **state,
                "status": "awaiting_gate2",
                "needs_human_review": True,
            }
        edited = None
        if decision == "edit":
            edited = (state.get("draft") or {}).get("draft_text")
        store.append_review(
            run_id=state["run_id"],
            gate="gate2",
            decision=decision,
            actor=state.get("actor") or "adjuster",
            reason=f"Gate 2 {decision}",
            edited_draft=edited,
        )
        if decision == "reject":
            return {
                **state,
                "gate2_decision": decision,
                "status": "gate2_rejected",
                "guardrail_verdict": Verdict.HOLD.value,
                "reason_code": "gate2_rejected",
            }
        # approve / edit → stub send (log only)
        store.log_send(state["run_id"])
        return {
            **state,
            "gate2_decision": decision,
            "status": "logged",
            "guardrail_verdict": state.get("guardrail_verdict") or Verdict.ALLOW.value,
            "reason_code": "logged_stub_send",
        }

    def persist(state: AgentState) -> AgentState:
        draft = state.get("draft") or {}
        coverage = float(draft.get("citation_coverage") or 0)
        ungrounded_rate = max(0.0, 1.0 - coverage) if draft.get("ok") else 0.0
        packet = store.get_packet(state["packet_id"]) or {}
        names = [packet.get("beneficiary_name") or "", packet.get("holder_name") or ""]
        redacted = redact_obj(
            {
                "packet_id": state["packet_id"],
                "status": state.get("status"),
                "guardrail_verdict": state.get("guardrail_verdict"),
                "reason_code": state.get("reason_code"),
                "validation": state.get("validation"),
                "draft_text": draft.get("draft_text"),
            },
            extra_names=[n for n in names if n],
        )
        store.append_run(
            {
                "id": state["run_id"],
                "packet_id": state["packet_id"],
                "trace_id": state["trace_id"],
                "status": state.get("status") or "unknown",
                "guardrail_verdict": state.get("guardrail_verdict"),
                "reason_code": state.get("reason_code"),
                "validation": state.get("validation"),
                "draft": draft,
                "gate1": state.get("gate1_decision"),
                "gate2": state.get("gate2_decision"),
                "had_draft": bool(draft.get("ok")),
                "ungrounded_rate": ungrounded_rate,
                "created_at": datetime.now(UTC).isoformat(),
            }
        )
        return {**state, "log_redacted": redacted}

    def route_after_pause(state: AgentState) -> Literal["scan_input", "persist"]:
        return "persist" if state.get("paused") else "scan_input"

    def route_after_scan(state: AgentState) -> Literal["validate", "persist"]:
        if state.get("status") == "blocked":
            return "persist"
        return "validate"

    def route_after_validate(state: AgentState) -> Literal["gate1", "persist"]:
        if state.get("abstained"):
            return "persist"
        return "gate1"

    def route_after_gate1(state: AgentState) -> Literal["retrieve_and_draft", "persist"]:
        if state.get("status") in {"awaiting_gate1", "gate1_rejected"}:
            return "persist"
        return "retrieve_and_draft"

    def route_after_draft(state: AgentState) -> Literal["gate2", "persist"]:
        if state.get("status") in {"blocked", "held", "needs_human_review"}:
            return "persist"
        if state.get("status") == "draft_ready":
            return "gate2"
        return "persist"

    g = StateGraph(AgentState)
    g.add_node("start", start)
    g.add_node("pause_check", pause_check)
    g.add_node("scan_input", scan_input)
    g.add_node("validate", validate)
    g.add_node("gate1", gate1)
    g.add_node("retrieve_and_draft", retrieve_and_draft)
    g.add_node("gate2", gate2)
    g.add_node("persist", persist)

    g.set_entry_point("start")
    g.add_edge("start", "pause_check")
    g.add_conditional_edges("pause_check", route_after_pause)
    g.add_conditional_edges("scan_input", route_after_scan)
    g.add_conditional_edges("validate", route_after_validate)
    g.add_conditional_edges("gate1", route_after_gate1)
    g.add_conditional_edges("retrieve_and_draft", route_after_draft)
    g.add_edge("gate2", "persist")
    g.add_edge("persist", END)
    return g.compile()


def run_packet(
    store: ClaimStore,
    packet_id: str,
    *,
    auto_gates: bool = False,
    gate1_decision: str | None = None,
    gate2_decision: str | None = None,
    actor: str = "adjuster",
    force_scenario: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the bounded agent on one packet.

    ``force_scenario`` is used by the eval harness for fabrication traps / pause presets
    without mutating the frozen golden packet corpus.
    """
    graph = build_graph(store)
    state: AgentState = {
        "packet_id": packet_id,
        "auto_gates": auto_gates,
        "gate1_decision": gate1_decision,
        "gate2_decision": gate2_decision,
        "actor": actor,
    }
    # Pause / fabrication presets applied by caller via store before invoke.
    result = graph.invoke(state)
    if force_scenario:
        result["force_scenario"] = force_scenario
    return dict(result)
