"""Live-mode drafting: spend cap, missing key, stub isolation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from claims_triage.agent.llm import (
    LiveModeError,
    SpendCapExceededError,
    SpendLedger,
    estimate_cost_usd,
    live_draft_text,
)
from claims_triage.corpus.seed import refresh_updated_at, seed_corpus
from claims_triage.corpus.store import ClaimStore
from claims_triage.tools.draft import draft_letter


@pytest.fixture()
def store(tmp_path: Path) -> ClaimStore:
    db = tmp_path / "claims.db"
    seed_corpus(db, force=True)
    refresh_updated_at(db)
    return ClaimStore(db)


def test_spend_ledger_blocks_over_cap(tmp_path: Path) -> None:
    path = tmp_path / "spend.json"
    clock = lambda: datetime(2026, 11, 21, tzinfo=UTC)  # noqa: E731
    ledger = SpendLedger(path=path, cap_usd=1.0, clock=clock)
    ledger.add(0.95)
    with pytest.raises(SpendCapExceededError):
        ledger.check(0.10)
    ledger.check(0.04)  # still under


def test_estimate_cost_rejects_unpriced_model() -> None:
    with pytest.raises(LiveModeError):
        estimate_cost_usd("claude-mystery-99", 100, 100)


def test_live_without_key_refuses(store: ClaimStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAIMS_MODE", "live")
    result = draft_letter(store, "FNOL-0001", force_mode="live")
    assert result["ok"] is False
    assert result["abstain_reason"] == "live_mode_unavailable"
    assert "ANTHROPIC_API_KEY" in (result.get("error") or "")
    assert result.get("mode") == "live"
    assert float(result.get("cost_usd") or 0) == 0.0


def test_stub_mode_ignores_missing_key(store: ClaimStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAIMS_MODE", "stub")
    result = draft_letter(store, "FNOL-0001", force_mode="stub")
    assert result["ok"] is True
    assert result.get("mode") == "stub"
    assert float(result.get("cost_usd") or 0) == 0.0
    assert result.get("citations")


def test_live_draft_text_charges_spend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    path = tmp_path / "spend.json"
    ledger = SpendLedger(path=path, cap_usd=1.0)

    class FakeMessages:
        def create(self, **_kwargs):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text='{"draft_text":"Packet FNOL-0001 [POL-0001]."}'
                    )
                ],
                usage=SimpleNamespace(input_tokens=100, output_tokens=50),
            )

    client = SimpleNamespace(messages=FakeMessages())
    text, cost = live_draft_text(
        packet={"id": "FNOL-0001", "policy_id": "POL-0001", "beneficiary_name": "Alex Rivera"},
        policy={"id": "POL-0001", "face_amount": 100000, "effective_date": "2020-01-01"},
        validation={"overall": "pass", "checks": []},
        retrieval={"passages": [{"id": "CLA-0001", "kind": "clause", "confidence": 1.0}]},
        track="information_request",
        template_id="TPL-0001",
        allowed_citations=["POL-0001", "CLA-0001"],
        spend=ledger,
        client=client,
    )
    assert "POL-0001" in text
    assert cost > 0
    assert ledger.spent_today() == pytest.approx(cost)
