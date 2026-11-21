"""Deterministic scanner rules.

Prompt-injection detectors (PI-001..PI-008) are reused from SkillGate's
``skillgate/scanner/rules_text.py`` (proven on SkillGate's frozen scanner eval).
Claims-domain rules (CL-*) are additional and are *not* claimed as SkillGate results.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass

# SkillGate uses `I = re.IGNORECASE`; keep the same flag value.
_IC = re.IGNORECASE

# --- SkillGate PI patterns (reused from rules_text.py) --------------------------
OVERRIDE_RES = [
    re.compile(
        r"\b(ignore|disregard|forget|override)\b[^.\n]{0,20}?\b(all\s+)?"
        r"(previous|prior|above|earlier|preceding|your|system)\b[^.\n]{0,20}?"
        r"\b(instructions?|prompts?|rules|guidelines|directions|restrictions)\b",
        _IC,
    ),
    re.compile(
        r"\byou are now\b[^.\n]{0,40}?\b(mode|DAN|unrestricted|jailbroken|developer)\b",
        _IC,
    ),
    re.compile(r"\bnew (system )?instructions\s*:", _IC),
    re.compile(r"\bfrom now on,?\s+(you|ignore)\b", _IC),
]
SYSTEM_IMPERSONATION_RE = re.compile(
    r"^\s*(\[?system\]?\s*:|<\s*/?\s*system\s*>|<\|im_start\|>\s*system|"
    r"#{1,6}\s*system\s*(prompt|message)?\s*$|system override\b)",
    _IC,
)
B64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])")
B64_OPAQUE_MIN = 120
IMAGE_MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
DECODED_KEYWORDS = (
    "ignore",
    "instruction",
    "send",
    "exfil",
    "password",
    "api key",
    "api_key",
    "secret",
    "token",
    "ssh",
    "do not tell",
    "curl",
    "http://",
    "https://",
    "credential",
    "post ",
)
INVISIBLE_CPS = (
    {0x200B, 0x200C, 0x200D, 0x2060, 0x180E, 0xFEFF}
    | set(range(0x202A, 0x202F))
    | set(range(0x2066, 0x206A))
)
TOKEN_SPLIT_RE = re.compile(r"[^\s`'\"()<>\[\]{},;|*]+")
HTML_COMMENT_RE = re.compile(r"<!--(.*?)-->", re.DOTALL)
HIDDEN_KEYWORDS_RE = re.compile(
    r"\b(ignore|disregard|override|instead|secretly|do not (tell|mention|show)|"
    r"don't (tell|mention)|send|forward|upload|exfiltrat\w*|api[ _-]?keys?|"
    r"passwords?|tokens?|credentials?|system prompt|you must|always)\b",
    _IC,
)
CONCEALMENT_RES = [
    re.compile(
        r"\b(do not|don't|never)\s+(tell|mention|inform|show|reveal|disclose)\b"
        r"[^.\n]{0,30}?\b(the user|users?|anyone|them)\b",
        _IC,
    ),
    re.compile(r"\bwithout (telling|informing|notifying) the user\b", _IC),
    re.compile(r"\bsecretly\b", _IC),
    re.compile(r"\bkeep this (hidden|secret)\b", _IC),
]

# --- Claims-domain patterns (not SkillGate; do not claim as SkillGate-proven) ---
CLAIM_OUTCOME_RES = [
    re.compile(
        r"\b(approve|deny|reject|pay|authorize|settle)\b[^.\n]{0,40}?\b"
        r"(this\s+)?(claim|benefit|proceeds|death\s+benefit)\b",
        _IC,
    ),
    re.compile(
        r"\b(claim|benefit)\b[^.\n]{0,40}?\b(is\s+)?(approved|denied|paid|rejected)\b",
        _IC,
    ),
    re.compile(r"\bpay the beneficiary\b", _IC),
    re.compile(r"\bapprove this claim immediately\b", _IC),
]
FABRICATED_POLICY_RE = re.compile(r"\bPOL-(?:9\d{3}|[A-Z]{2}\d+)\b")
FABRICATED_STAT_RE = re.compile(
    r"\b\d{2,3}%\b[^.\n]{0,40}?\b(approval|denial|payout|contestability)\b",
    _IC,
)

# Six SkillGate-proven injection pattern IDs reused in the frozen golden set.
SKILLGATE_INJECTION_PATTERN_IDS = (
    "PI-001",  # instruction-override phrasing
    "PI-002",  # system-message impersonation
    "PI-004",  # base64 decodes to injection keywords
    "PI-005",  # invisible / bidi-override unicode
    "PI-007",  # hidden instruction in HTML comment
    "PI-008",  # concealment instruction
)


@dataclass(frozen=True)
class RuleHit:
    rule_id: str
    severity: str  # critical | high | medium | low
    category: str
    evidence: str
    source: str  # "skillgate" | "claims"


def strip_invisible(s: str) -> str:
    return "".join(
        ch for ch in s if ord(ch) not in INVISIBLE_CPS and not 0xE0000 <= ord(ch) <= 0xE007F
    )


def _first(res: list[re.Pattern[str]], text: str) -> re.Match[str] | None:
    for rx in res:
        m = rx.search(text)
        if m:
            return m
    return None


def _decode_b64(blob: str) -> bytes | None:
    try:
        return base64.b64decode(blob + "=" * (-len(blob) % 4), validate=True)
    except (binascii.Error, ValueError):
        return None


def _decoded_is_injection(decoded: bytes) -> bool:
    text = decoded.decode("utf-8", errors="ignore").lower()
    if any(rx.search(text) for rx in OVERRIDE_RES):
        return True
    return sum(1 for k in DECODED_KEYWORDS if k in text) >= 2


def scan_skillgate_injection(text: str) -> list[RuleHit]:
    """Apply SkillGate PI-001..PI-008 detectors to a free-text blob."""
    hits: list[RuleHit] = []
    lines = text.splitlines() or [text]
    for i, raw in enumerate(lines):
        norm = unicodedata.normalize("NFKC", strip_invisible(raw))
        if _first(OVERRIDE_RES, norm):
            hits.append(
                RuleHit("PI-001", "critical", "prompt-injection", raw.strip()[:160], "skillgate")
            )
        if SYSTEM_IMPERSONATION_RE.search(norm):
            hits.append(
                RuleHit("PI-002", "high", "prompt-injection", raw.strip()[:160], "skillgate")
            )
        for m in B64_RE.finditer(raw):
            blob = m.group(0)
            decoded = _decode_b64(blob)
            if decoded is None:
                continue
            if _decoded_is_injection(decoded):
                preview = decoded.decode("utf-8", errors="replace")[:80]
                hits.append(
                    RuleHit(
                        "PI-004",
                        "critical",
                        "prompt-injection",
                        f"base64({len(blob)} chars) -> {preview!r}",
                        "skillgate",
                    )
                )
            if len(blob) >= B64_OPAQUE_MIN and not decoded.startswith(IMAGE_MAGIC):
                hits.append(
                    RuleHit(
                        "PI-003",
                        "medium",
                        "prompt-injection",
                        f"{blob[:48]}... ({len(blob)} chars)",
                        "skillgate",
                    )
                )
        start = 1 if (i == 0 and raw.startswith("\ufeff")) else 0
        if any(ord(ch) in INVISIBLE_CPS or 0xE0000 <= ord(ch) <= 0xE007F for ch in raw[start:]):
            hits.append(
                RuleHit("PI-005", "high", "prompt-injection", raw.strip()[:160], "skillgate")
            )
        for tok in TOKEN_SPLIT_RE.findall(raw):
            latin = any("a" <= c.lower() <= "z" for c in tok)
            other = any("\u0400" <= c <= "\u04ff" or "\u0370" <= c <= "\u03ff" for c in tok)
            if latin and other:
                hits.append(RuleHit("PI-006", "high", "prompt-injection", tok, "skillgate"))
                break
        if _first(CONCEALMENT_RES, norm):
            hits.append(
                RuleHit("PI-008", "high", "prompt-injection", raw.strip()[:160], "skillgate")
            )
    for m in HTML_COMMENT_RE.finditer(text):
        if HIDDEN_KEYWORDS_RE.search(m.group(1)):
            hits.append(
                RuleHit(
                    "PI-007",
                    "high",
                    "prompt-injection",
                    m.group(0)[:160],
                    "skillgate",
                )
            )
    return hits


def scan_claims_domain(text: str, *, known_policy_ids: set[str] | None = None) -> list[RuleHit]:
    """Claims-specific BLOCK patterns (outcome language, fabricated IDs)."""
    hits: list[RuleHit] = []
    if _first(CLAIM_OUTCOME_RES, text):
        hits.append(
            RuleHit(
                "CL-001",
                "critical",
                "claim-outcome",
                "Draft or input states a claim outcome (approve/deny/pay).",
                "claims",
            )
        )
    for m in FABRICATED_POLICY_RE.finditer(text):
        pid = m.group(0)
        if known_policy_ids is not None and pid in known_policy_ids:
            continue
        hits.append(
            RuleHit(
                "CL-002",
                "critical",
                "fabrication",
                f"Fabricated or unknown policy id {pid}",
                "claims",
            )
        )
    if FABRICATED_STAT_RE.search(text):
        hits.append(
            RuleHit(
                "CL-003",
                "high",
                "fabrication",
                "Fabricated approval/denial statistic in draft.",
                "claims",
            )
        )
    return hits


SEVERITY_POINTS = {"critical": 40, "high": 24, "medium": 12, "low": 4}
BLOCK_THRESHOLD = 40
HOLD_THRESHOLD = 20
