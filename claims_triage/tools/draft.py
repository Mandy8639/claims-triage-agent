"""Tool: draft_letter — draft ONLY from validated, cited facts; strip uncited claims."""

from __future__ import annotations

import re
from typing import Any

from claims_triage.agent.llm import LiveModeError, SpendCapExceededError, live_draft_text
from claims_triage.config import mode
from claims_triage.corpus.store import ClaimStore
from claims_triage.tools.allowlist import assert_allowed
from claims_triage.tools.retrieve import retrieve_policy
from claims_triage.tools.validate import validate_packet

CITE_RE = re.compile(r"\[([A-Z]+-\d{4}|packet:[a-z_]+)\]")


def _strip_uncited_sentences(text: str, allowed: set[str]) -> tuple[str, list[str], float]:
    """Drop any factual sentence whose citations are missing or unverifiable."""
    kept: list[str] = []
    stripped: list[str] = []
    factual = 0
    cited_ok = 0
    for raw in re.split(r"(?<=[.!?])\s+", text.strip()):
        sentence = raw.strip()
        if not sentence:
            continue
        cites = CITE_RE.findall(sentence)
        # Non-factual boilerplate may omit citations if it contains no digits/policy verbs.
        looks_factual = bool(
            re.search(
                r"\b(policy|beneficiary|certificate|contestability|document|amount|date|packet)\b",
                sentence,
                re.I,
            )
            or re.search(r"\bPOL-\d{4}\b", sentence)
            or re.search(r"\d", sentence)
        )
        if not looks_factual:
            kept.append(sentence)
            continue
        factual += 1
        if cites and all(c in allowed for c in cites):
            kept.append(sentence)
            cited_ok += 1
        else:
            stripped.append(sentence)
    coverage = (cited_ok / factual) if factual else 1.0
    return " ".join(kept), stripped, coverage


def _allowed_citation_set(
    *,
    policy_id: str,
    template_id: str,
    cite_clause: str,
    passages: list[dict[str, Any]],
) -> set[str]:
    allowed = {
        policy_id,
        "packet:packet_id",
        "packet:death_certificate_present",
        "packet:beneficiary_name",
        "packet:beneficiary_dob",
        "packet:date_of_death",
        template_id,
        cite_clause,
        *[p["id"] for p in passages],
    }
    return allowed


def _stub_raw_draft(
    *,
    packet_id: str,
    packet: dict[str, Any],
    policy: dict[str, Any],
    track: str,
    template_id: str,
    cite_clause: str,
    force_uncited: bool,
    force_outcome: bool,
    force_fabricated_policy: bool,
) -> str:
    cite_policy = policy["id"]
    cite_packet_cert = "packet:death_certificate_present"
    cite_packet_ben = "packet:beneficiary_name"
    sentences = [
        (
            f"This draft concerns First Notice of Loss packet {packet_id} "
            f"for policy {cite_policy} [{cite_policy}][packet:packet_id]."
        ),
        (f"A death certificate is recorded as present on the packet [{cite_packet_cert}]."),
        (f"Beneficiary identity on file names {packet['beneficiary_name']} [{cite_packet_ben}]."),
        (f"Policy face amount on the synthetic record is {policy['face_amount']} [{cite_policy}]."),
        (f"Effective date on the synthetic record is {policy['effective_date']} [{cite_policy}]."),
        (
            f"Correspondence track selected for adjuster review is {track} "
            f"[{template_id}][{cite_clause}]."
        ),
        (
            "This is a draft for human adjuster review only; "
            "it does not approve, deny, or pay any claim."
        ),
    ]
    if force_uncited:
        sentences.insert(
            3,
            "The claimant is entitled to an immediate settlement of 1000000 dollars "
            "under this policy.",
        )
    if force_outcome:
        sentences.insert(2, "We hereby approve this claim and will pay the beneficiary now.")
    if force_fabricated_policy:
        sentences.insert(1, "Coverage also exists under POL-9999 at triple the face amount.")
    return " ".join(sentences)


def draft_letter(
    store: ClaimStore,
    packet_id: str,
    *,
    validation: dict[str, Any] | None = None,
    track: str | None = None,
    force_uncited: bool = False,
    force_outcome: bool = False,
    force_fabricated_policy: bool = False,
    force_mode: str | None = None,
) -> dict[str, Any]:
    assert_allowed("draft_letter")

    packet = store.get_packet(packet_id)
    if packet is None:
        return {
            "tool": "draft_letter",
            "ok": False,
            "error": f"Unknown packet {packet_id}",
            "abstain": True,
            "abstain_reason": "unknown_packet",
        }

    report = validation or validate_packet(store, packet_id)
    if report.get("abstain"):
        return {
            "tool": "draft_letter",
            "ok": False,
            "abstain": True,
            "abstain_reason": report.get("abstain_reason"),
            "missing_fields": report.get("missing_fields") or [],
            "needs_human_review": True,
            "message": (
                f"Refusing to draft: packet requires human review ({report.get('abstain_reason')})."
            ),
            "validation": report,
        }

    if report.get("overall") != "pass":
        return {
            "tool": "draft_letter",
            "ok": False,
            "abstain": True,
            "abstain_reason": "validation_failed",
            "missing_fields": report.get("missing_fields") or [],
            "needs_human_review": True,
            "message": "Refusing to draft: validation failed.",
            "validation": report,
        }

    policy = store.get_policy(packet["policy_id"])
    assert policy is not None

    if track is None:
        track = "information_request"
        if report.get("overall") == "pass" and not report.get("in_contestability_window"):
            track = "approval_track"
        elif report.get("in_contestability_window"):
            track = "information_request"

    retrieval = retrieve_policy(
        store,
        f"{track} contestability beneficiary required_documents {packet['policy_id']}",
        limit=6,
    )
    passages = retrieval.get("passages") or []
    low_confidence = (not passages) or all(float(p.get("confidence") or 0) < 0.45 for p in passages)
    if low_confidence and not passages:
        return {
            "tool": "draft_letter",
            "ok": False,
            "abstain": True,
            "abstain_reason": "low_retrieval_confidence",
            "needs_human_review": True,
            "message": "Refusing to draft: retrieval confidence too low.",
            "retrieval": retrieval,
            "validation": report,
        }

    templates = [t for t in store.list_templates() if t["track"] == track]
    template = templates[0] if templates else None
    template_id = template["id"] if template else "TPL-0001"
    cite_clause = next((p["id"] for p in passages if p["kind"] == "clause"), "CLA-0005")
    allowed = _allowed_citation_set(
        policy_id=policy["id"],
        template_id=template_id,
        cite_clause=cite_clause,
        passages=passages,
    )

    # Fabrication traps and the frozen stub eval always use the deterministic stub path.
    trap = force_uncited or force_outcome or force_fabricated_policy
    active_mode = (force_mode or mode()).lower()
    if trap:
        active_mode = "stub"

    cost_usd = 0.0
    draft_mode = "stub"
    if active_mode == "live":
        draft_mode = "live"
        try:
            raw_draft, cost_usd = live_draft_text(
                packet=packet,
                policy=policy,
                validation=report,
                retrieval=retrieval,
                track=track,
                template_id=template_id,
                allowed_citations=sorted(allowed),
            )
        except LiveModeError as exc:
            return {
                "tool": "draft_letter",
                "ok": False,
                "abstain": True,
                "abstain_reason": "live_mode_unavailable",
                "needs_human_review": True,
                "error": str(exc),
                "message": str(exc),
                "mode": "live",
                "cost_usd": 0.0,
                "validation": report,
                "retrieval": retrieval,
            }
        except SpendCapExceededError as exc:
            return {
                "tool": "draft_letter",
                "ok": False,
                "abstain": True,
                "abstain_reason": "spend_cap_exceeded",
                "needs_human_review": True,
                "error": str(exc),
                "message": str(exc),
                "mode": "live",
                "cost_usd": 0.0,
                "validation": report,
                "retrieval": retrieval,
            }
    else:
        raw_draft = _stub_raw_draft(
            packet_id=packet_id,
            packet=packet,
            policy=policy,
            track=track,
            template_id=template_id,
            cite_clause=cite_clause,
            force_uncited=force_uncited,
            force_outcome=force_outcome,
            force_fabricated_policy=force_fabricated_policy,
        )

    cleaned, stripped, coverage = _strip_uncited_sentences(raw_draft, allowed)

    return {
        "tool": "draft_letter",
        "ok": True,
        "abstain": False,
        "packet_id": packet_id,
        "policy_id": policy["id"],
        "track": track,
        "template_id": template_id,
        "draft_text": cleaned,
        "raw_draft_text": raw_draft,
        "stripped_uncited": stripped,
        "citation_coverage": coverage,
        "citations": sorted(CITE_RE.findall(cleaned)),
        "retrieval": retrieval,
        "validation": report,
        "low_retrieval_confidence": low_confidence,
        "mode": draft_mode,
        "cost_usd": float(cost_usd),
    }
