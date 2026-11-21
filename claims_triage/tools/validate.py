"""Tool: validate_packet — deterministic FNOL checks (no LLM)."""

from __future__ import annotations

from datetime import date
from typing import Any

from claims_triage.corpus.store import ClaimStore
from claims_triage.tools.allowlist import assert_allowed


def _months_between(start: date, end: date) -> int:
    return (end.year - start.year) * 12 + (end.month - start.month)


def validate_packet(store: ClaimStore, packet_id: str) -> dict[str, Any]:
    assert_allowed("validate_packet")
    packet = store.get_packet(packet_id)
    if packet is None:
        return {
            "tool": "validate_packet",
            "packet_id": packet_id,
            "overall": "fail",
            "checks": [
                {
                    "name": "packet_exists",
                    "pass": False,
                    "reason": f"Unknown packet {packet_id}",
                }
            ],
            "missing_fields": ["packet"],
            "abstain": True,
            "abstain_reason": "unknown_packet",
        }

    policy = store.get_policy(packet["policy_id"])
    checks: list[dict[str, Any]] = []
    missing: list[str] = []

    death_ok = bool(packet.get("death_certificate_present"))
    checks.append(
        {
            "name": "death_certificate_present",
            "pass": death_ok,
            "reason": "Death certificate on file" if death_ok else "Death certificate missing",
        }
    )
    if not death_ok:
        missing.append("death_certificate")

    id_ok = bool(packet.get("beneficiary_id_complete")) and bool(packet.get("beneficiary_name"))
    dob = packet.get("beneficiary_dob") or ""
    if not dob:
        id_ok = False
    checks.append(
        {
            "name": "beneficiary_identity_complete",
            "pass": id_ok,
            "reason": (
                "Beneficiary identity fields complete"
                if id_ok
                else "Beneficiary identity fields incomplete"
            ),
        }
    )
    if not id_ok:
        missing.extend(
            [
                f
                for f in ("beneficiary_name", "beneficiary_dob", "beneficiary_id_complete")
                if not packet.get(f)
            ]
        )

    contest_edge = False
    contest_in_window = False
    if policy is None:
        checks.append(
            {
                "name": "policy_found",
                "pass": False,
                "reason": f"Policy {packet.get('policy_id')} not in corpus",
            }
        )
        missing.append("policy")
    else:
        checks.append(
            {
                "name": "policy_found",
                "pass": True,
                "reason": f"Policy {policy['id']} located",
            }
        )
        effective = date.fromisoformat(policy["effective_date"])
        dod = date.fromisoformat(packet["date_of_death"])
        months = _months_between(effective, dod)
        window = int(policy.get("contestability_months") or 24)
        contest_in_window = months < window
        contest_edge = abs(months - window) <= 1
        checks.append(
            {
                "name": "contestability_window",
                "pass": True,  # computed, not a hard fail
                "reason": (
                    f"{months} months since effective date; "
                    f"{'IN' if contest_in_window else 'OUT of'} "
                    f"{window}-month contestability window" + ("; EDGE" if contest_edge else "")
                ),
                "months_in_force": months,
                "in_window": contest_in_window,
                "edge": contest_edge,
            }
        )

    docs_ok = death_ok and id_ok and policy is not None
    checks.append(
        {
            "name": "required_documents_checklist",
            "pass": docs_ok,
            "reason": "Required documents complete" if docs_ok else "Required documents incomplete",
        }
    )

    conflict = bool(packet.get("conflicting_beneficiary_claims"))
    checks.append(
        {
            "name": "beneficiary_conflict",
            "pass": not conflict,
            "reason": (
                "No conflicting beneficiary claims"
                if not conflict
                else "Conflicting beneficiary claims present"
            ),
        }
    )

    hard_fails = [
        c
        for c in checks
        if c["name"]
        in {
            "death_certificate_present",
            "beneficiary_identity_complete",
            "required_documents_checklist",
            "policy_found",
            "packet_exists",
        }
        and not c["pass"]
    ]
    overall = "pass" if not hard_fails and not conflict else "fail"

    abstain = False
    abstain_reason = None
    if not death_ok:
        abstain, abstain_reason = True, "missing_death_certificate"
    elif not id_ok:
        abstain, abstain_reason = True, "incomplete_beneficiary_identity"
    elif conflict:
        abstain, abstain_reason = True, "conflicting_beneficiary_claims"
    elif contest_edge:
        abstain, abstain_reason = True, "contestability_edge"
        overall = "fail"

    return {
        "tool": "validate_packet",
        "packet_id": packet_id,
        "policy_id": packet.get("policy_id"),
        "overall": overall,
        "checks": checks,
        "missing_fields": sorted(set(missing)),
        "abstain": abstain,
        "abstain_reason": abstain_reason,
        "contestability_edge": contest_edge,
        "in_contestability_window": contest_in_window,
    }
