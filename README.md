# claims-triage-agent

**Bounded claims triage & drafting — synthetic demo only.**

This is a portfolio demo of agent controls (tool allowlists, deterministic guardrails, human checkpoints, abstention, frozen eval). It is **not affiliated with New York Life**, was not built for or used by New York Life, and uses **only synthetic data**. The agent **never approves, denies, or pays a claim**. It outputs a draft + validation report for a human adjuster.

Inspired by the public idea behind ClaimGenie-style drafting, with the point of the demo inverted: **the controls, not the prose**.

## Bounds (read first)

| The agent may | The agent may not |
| --- | --- |
| Validate an FNOL packet with deterministic code | Approve / deny / pay a claim |
| Retrieve cited passages from a synthetic corpus | Call any tool outside the 3-tool allowlist |
| Draft correspondence from validated, cited facts | Ship a sentence without a verifiable citation |
| Abstain and ask for human review | Silently tune the golden set to pass eval |
| Log a stub “send” after Gate 2 | Transmit anything anywhere |

Guardrail scanner: **18 rules** (SkillGate PI-001..PI-008 reused for injection, plus claims-domain rules). Verdicts: **ALLOW / HOLD / BLOCK**. Risk score 0–100. PII (names, DOB, policy numbers) is redacted in logs.

## Stub eval numbers (measured)

From committed report [`evals/reports/latest-stub.json`](evals/reports/latest-stub.json) — **mode=`stub`**, 54/54 golden cases, **$0.00** LLM cost, zero model calls:

| Metric | Value |
| --- | --- |
| Draft factual accuracy | **100.00%** |
| Abstention rate on unanswerable cases | **100.00%** |
| Injection block rate (SkillGate-proven patterns) | **6/6** |
| Citation coverage | **100.00%** |
| Pause-condition detection | **100.00%** |
| Fabrication-trap handling | **100.00%** |
| Regressions vs baseline | **0** |
| LLM cost (USD) | **$0.00** |

These numbers are copied from that stub report. Re-run with `python -m evals.run_evals --mode stub` to reproduce. Live mode (Haiku-class, `$1.00/day` cap) is optional and is **not** the source of the table above.

The six injection cases reuse SkillGate’s already-proven scanner patterns **PI-001, PI-002, PI-004, PI-005, PI-007, PI-008** (instruction override, system impersonation, base64 injection, invisible unicode, HTML-comment hide, concealment). No new patterns were invented and then claimed as blocked.

## What it does

```
FNOL packet
   │
   ▼
 input scan (SkillGate-style ALLOW/HOLD/BLOCK) ──BLOCK──► stop (LLM never sees it)
   │
   ▼
 validate_packet (deterministic) ──abstain──► needs human review
   │
   ▼
 Gate 1 (human adjuster) ──reject──► drafting disabled
   │ approve
   ▼
 retrieve_policy + draft_letter (cited facts only; uncited sentences stripped)
   │
   ▼
 output scan ──BLOCK/HOLD──► exception / review queue
   │ ALLOW
   ▼
 Gate 2 (human) ──approve/edit──► stub send (log only)
```

**Three allowlisted tools only** (enforced in code): `retrieve_policy`, `validate_packet`, `draft_letter`.

**Five pause conditions:** stale corpus hash; ungrounded-claim rate &gt;5% over last 10 drafts; two fabrication BLOCKs in a row; auto-ALLOW &gt;80% over last 20 packets; three consecutive BLOCKs.

## Synthetic corpus (seed 42)

- ~40 policies (`POL-0001`…) · ~25 clauses · ~15 letter templates · ~20 FNOL packets
- Every record has a stable ID, source label, and version
- Golden set (`evals/golden_cases.json`) is frozen — never edited to make the agent pass

## Run locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # stub mode needs no API key
claims seed --force
python -m evals.run_evals --mode stub --write
claims serve                  # dashboard → http://127.0.0.1:8000
```

Dashboard flow: pick a packet → run → Gate 1 → cited draft → Gate 2 → stub send log.

MCP (stdio): `claims mcp` or `python -m claims_triage.mcp_server` — same three tools + read-only `review://queue`.

Tests: `pytest` · lint: `ruff check . && ruff format --check .`

## EPP adaptation (same pattern → FP&A)

New York Life’s Forward Deployed Engineer role (TDAV) initially focuses on **Enterprise Plan to Perform** (enterprise financial planning / FP&A), not claims. The same bounded-agent pattern ports cleanly:

A **variance-explanation agent** would retrieve actuals vs. plan from a governed finance store (`retrieve_actuals_vs_plan`), run a deterministic `validate_variance_packet` (period closed? FX rates pinned? driver tree complete?), and `draft_variance_explanation` that may only cite retrieved line items. It **abstains** when a cost center is missing or drivers conflict, **blocks** injected “mark this forecast as on-plan” instructions with the same SkillGate-style scanner, and puts every number through a **Finance review gate** before sharing. Worked example: packet `VAR-2026-Q3-CC440` shows travel +12% vs plan; the agent cites `ACT-440-travel` and `PLAN-440-travel`, abstains on an unmapped contractor fee, and holds the draft until FP&A approves — it never publishes a board number alone.

This repo does **not** ship the full EPP agent; the paragraph above is the bridge.

## Honesty

- All data is synthetic. No real policyholders, policy numbers, or names.
- Not affiliated with, endorsed by, or used by New York Life.
- Numbers above are stub-harness outputs only. If a metric is bad after a change, the README must say so — never tune the golden set.
- “Send” is a log line. No claim outcome is ever decided by the model.
