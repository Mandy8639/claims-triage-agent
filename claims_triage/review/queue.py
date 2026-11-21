"""Human review queue for Gate 1 / Gate 2 decisions."""

from __future__ import annotations

from typing import Any

from claims_triage.corpus.store import ClaimStore
from claims_triage.guardrail.scanner import Verdict


def pending_reviews(store: ClaimStore) -> list[dict[str, Any]]:
    """Runs waiting on a human gate or held for review."""
    out = []
    for run in store.list_runs(limit=200):
        if run.get("status") in {
            "awaiting_gate1",
            "awaiting_gate2",
            "needs_human_review",
            "held",
            "gate1_rejected",
        }:
            out.append(run)
    return out


def apply_gate_decision(
    store: ClaimStore,
    run_id: str,
    *,
    gate: str,
    decision: str,
    actor: str,
    reason: str,
    edited_draft: str | None = None,
) -> dict[str, Any]:
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(f"unknown run {run_id}")
    if decision not in {"approve", "reject", "edit"}:
        raise ValueError("decision must be approve|reject|edit")
    if gate not in {"gate1", "gate2"}:
        raise ValueError("gate must be gate1|gate2")

    store.append_review(
        run_id=run_id,
        gate=gate,
        decision=decision,
        actor=actor,
        reason=reason,
        edited_draft=edited_draft,
    )

    if gate == "gate1":
        if decision != "approve":
            store.update_run(
                run_id,
                status="gate1_rejected",
                gate1=decision,
                guardrail_verdict=Verdict.HOLD.value,
                reason_code="gate1_rejected",
            )
        else:
            store.update_run(run_id, status="gate1_approved", gate1=decision)
    else:
        if decision == "reject":
            store.update_run(
                run_id,
                status="gate2_rejected",
                gate2=decision,
                guardrail_verdict=Verdict.HOLD.value,
                reason_code="gate2_rejected",
            )
        else:
            if edited_draft and run.get("draft"):
                draft = dict(run["draft"])
                draft["draft_text"] = edited_draft
                store.update_run(run_id, draft=draft)
            store.log_send(run_id, detail=f"stub send after gate2 {decision}")
            store.update_run(
                run_id,
                status="logged",
                gate2=decision,
                reason_code="logged_stub_send",
            )
    updated = store.get_run(run_id)
    assert updated is not None
    return updated
