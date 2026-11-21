"""Five pause conditions from the build prompt — deterministic."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claims_triage.config import (
    AUTO_ALLOW_RATE_THRESHOLD,
    AUTO_ALLOW_WINDOW,
    BLOCK_STREAK_PAUSE,
    FABRICATION_STREAK_PAUSE,
    UNGROUNDED_RATE_THRESHOLD,
    UNGROUNDED_WINDOW,
    pinned_corpus_hash,
)
from claims_triage.guardrail.scanner import Verdict


@dataclass
class PauseResult:
    should_pause: bool
    reason_code: str | None
    detail: str


def check_pause(
    *,
    corpus_hash: str,
    recent_runs: list[dict[str, Any]],
) -> PauseResult:
    """Evaluate pause conditions against recent run history + corpus hash."""
    if corpus_hash != pinned_corpus_hash():
        return PauseResult(
            True,
            "corpus_stale",
            f"Corpus hash {corpus_hash!r} does not match pinned {pinned_corpus_hash()!r}.",
        )

    # Two fabrication BLOCKs in a row
    fab = 0
    for run in recent_runs[:FABRICATION_STREAK_PAUSE]:
        verdict = str(run.get("guardrail_verdict", ""))
        reason = str(run.get("reason_code", ""))
        if verdict == Verdict.BLOCK.value and (
            "CL-002" in reason
            or "CL-003" in reason
            or "fabrication" in reason
            or "invented" in reason
            or "uncited" in reason
        ):
            fab += 1
        else:
            break
    if fab >= FABRICATION_STREAK_PAUSE:
        return PauseResult(
            True,
            "fabrication_streak",
            f"{fab} consecutive fabrication BLOCKs.",
        )

    # Three consecutive BLOCKs
    blocks = 0
    for run in recent_runs[:BLOCK_STREAK_PAUSE]:
        if run.get("guardrail_verdict") == Verdict.BLOCK.value:
            blocks += 1
        else:
            break
    if blocks >= BLOCK_STREAK_PAUSE:
        return PauseResult(
            True,
            "block_streak",
            f"{blocks} consecutive guardrail BLOCKs.",
        )

    # Ungrounded-claim rate in last 10 drafts
    draft_runs = [r for r in recent_runs if r.get("had_draft")][:UNGROUNDED_WINDOW]
    if len(draft_runs) >= UNGROUNDED_WINDOW:
        ungrounded = sum(1 for r in draft_runs if float(r.get("ungrounded_rate", 0)) > 0)
        rate = ungrounded / len(draft_runs)
        if rate > UNGROUNDED_RATE_THRESHOLD:
            return PauseResult(
                True,
                "ungrounded_rate_breach",
                f"Ungrounded-claim rate {rate:.0%} over last {len(draft_runs)} drafts exceeds 5%.",
            )

    # Auto-ALLOW rate over last 20 packets
    window = recent_runs[:AUTO_ALLOW_WINDOW]
    if len(window) >= AUTO_ALLOW_WINDOW:
        allows = sum(1 for r in window if r.get("guardrail_verdict") == Verdict.ALLOW.value)
        auto_rate = allows / len(window)
        if auto_rate > AUTO_ALLOW_RATE_THRESHOLD:
            return PauseResult(
                True,
                "auto_allow_rate_breach",
                f"Auto-ALLOW rate {auto_rate:.0%} over last {len(window)} packets exceeds 80%.",
            )

    return PauseResult(False, None, "ok")
