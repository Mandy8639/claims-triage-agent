"""Stub eval reproduces committed README numbers."""

from __future__ import annotations

import json
from pathlib import Path

from evals.run_evals import run_suite

REPORT = Path(__file__).resolve().parents[1] / "evals" / "reports" / "latest-stub.json"


def test_stub_eval_green() -> None:
    report = run_suite(mode="stub")
    m = report["metrics"]
    assert m["passed"] == m["n_cases"]
    assert m["injection_block_rate"] == "6/6"
    assert m["abstention_rate_unanswerable"] == 1.0
    assert m["llm_cost_usd"] == 0.0
    assert m["citation_coverage"] == 1.0


def test_readme_numbers_match_committed_report() -> None:
    assert REPORT.exists(), "commit evals/reports/latest-stub.json"
    committed = json.loads(REPORT.read_text())["metrics"]
    # Spot-check the public numbers
    assert committed["injection_block_rate"] == "6/6"
    assert committed["llm_cost_usd"] == 0.0
    assert committed["abstention_rate_unanswerable"] == 1.0
