"""Deterministic ALLOW / HOLD / BLOCK scanner (SkillGate pattern).

Same inputs → same verdict. No LLM calls. Risk score 0–100 from rule severities.
SkillGate uses ALLOW/REVIEW/BLOCK; this demo maps REVIEW → HOLD for the claims mandate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from claims_triage.guardrail.rules import (
    BLOCK_THRESHOLD,
    HOLD_THRESHOLD,
    SEVERITY_POINTS,
    RuleHit,
    scan_claims_domain,
    scan_skillgate_injection,
)


class Verdict(StrEnum):
    ALLOW = "ALLOW"
    HOLD = "HOLD"
    BLOCK = "BLOCK"


@dataclass(frozen=True)
class ScanResult:
    verdict: Verdict
    score: int
    hits: list[RuleHit] = field(default_factory=list)
    detail: str = ""
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "score": self.score,
            "detail": self.detail,
            "reason_codes": list(self.reason_codes),
            "hits": [
                {
                    "rule_id": h.rule_id,
                    "severity": h.severity,
                    "category": h.category,
                    "evidence": h.evidence,
                    "source": h.source,
                }
                for h in self.hits
            ],
        }


def _score(hits: list[RuleHit]) -> int:
    by_rule: dict[str, list[RuleHit]] = {}
    for h in hits:
        by_rule.setdefault(h.rule_id, []).append(h)
    total = 0.0
    for rule_hits in by_rule.values():
        base = SEVERITY_POINTS[rule_hits[0].severity]
        raw = base + base * 0.25 * (len(rule_hits) - 1)
        total += min(raw, base * 2.0)
    return int(min(100, round(total)))


def _verdict_from_score(score: int, hits: list[RuleHit]) -> Verdict:
    # Any critical hit → BLOCK. Any SkillGate prompt-injection hit on input → BLOCK
    # (injected instructions must never reach the LLM — build prompt §3).
    if any(h.severity == "critical" for h in hits):
        return Verdict.BLOCK
    if any(h.source == "skillgate" and h.category == "prompt-injection" for h in hits):
        return Verdict.BLOCK
    if score >= BLOCK_THRESHOLD:
        return Verdict.BLOCK
    if score >= HOLD_THRESHOLD:
        return Verdict.HOLD
    return Verdict.ALLOW


def scan_text(
    text: str,
    *,
    known_policy_ids: set[str] | None = None,
    include_claims_domain: bool = True,
) -> ScanResult:
    hits = scan_skillgate_injection(text)
    if include_claims_domain:
        hits.extend(scan_claims_domain(text, known_policy_ids=known_policy_ids))
    score = _score(hits)
    verdict = _verdict_from_score(score, hits) if hits else Verdict.ALLOW
    codes = sorted({h.rule_id for h in hits})
    detail = (
        f"{verdict.value}: score={score}; rules={','.join(codes) or 'none'}"
        if hits
        else "ALLOW: no rule hits"
    )
    return ScanResult(verdict=verdict, score=score, hits=hits, detail=detail, reason_codes=codes)


def scan_packet_fields(
    fields: dict[str, Any], *, known_policy_ids: set[str] | None = None
) -> ScanResult:
    """Scan every string field of an FNOL packet before the LLM ever sees it.

    Field *values* are scanned on their own lines (no key prefix) so SkillGate
    start-anchored rules like PI-002 still match.
    """
    blobs: list[str] = []
    for _key, value in fields.items():
        if isinstance(value, str):
            blobs.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    blobs.append(item)
                elif isinstance(item, dict):
                    blobs.append(str(item))
        elif isinstance(value, dict):
            blobs.append(str(value))
    return scan_text("\n".join(blobs), known_policy_ids=known_policy_ids)


def scan_draft(
    draft_text: str,
    *,
    known_policy_ids: set[str] | None = None,
) -> ScanResult:
    """Scan a draft for outcome language / fabrication / injection leakage."""
    return scan_text(draft_text, known_policy_ids=known_policy_ids, include_claims_domain=True)
