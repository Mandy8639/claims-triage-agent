"""Agent flow: packet → gates → cited draft (or block/abstain)."""

from __future__ import annotations

from pathlib import Path

import pytest
from claims_triage.agent.loop import run_packet
from claims_triage.corpus.seed import refresh_updated_at, seed_corpus
from claims_triage.corpus.store import ClaimStore
from claims_triage.tools.allowlist import ALLOWED_TOOLS, ToolAllowlistError, assert_allowed


@pytest.fixture()
def store(tmp_path: Path) -> ClaimStore:
    db = tmp_path / "claims.db"
    seed_corpus(db, force=True)
    refresh_updated_at(db)
    return ClaimStore(db)


def test_allowlist_enforced() -> None:
    assert "retrieve_policy" in ALLOWED_TOOLS
    with pytest.raises(ToolAllowlistError):
        assert_allowed("send_email")


def test_clean_packet_through_gates(store: ClaimStore) -> None:
    result = run_packet(
        store,
        "FNOL-0001",
        gate1_decision="approve",
        gate2_decision="approve",
    )
    assert result["status"] == "logged"
    draft = result.get("draft") or {}
    assert draft.get("ok") is True
    assert draft.get("citations")
    assert float(draft.get("citation_coverage") or 0) >= 0.99


def test_injection_blocked_before_draft(store: ClaimStore) -> None:
    result = run_packet(store, "FNOL-0015", auto_gates=True)
    assert result["status"] == "blocked"
    assert result.get("draft") in (None, {}) or not (result.get("draft") or {}).get("ok")


def test_abstain_no_draft(store: ClaimStore) -> None:
    result = run_packet(store, "FNOL-0007", auto_gates=True)
    assert result["abstained"] is True
    assert result["status"] == "needs_human_review"
