"""PII redaction for logs and traces (names, DOB, policy numbers)."""

from __future__ import annotations

import re
from typing import Any

POLICY_RE = re.compile(r"\bPOL-\d{4}\b")
DOB_RE = re.compile(r"\b(?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])\b")
# Synthetic holder names look like "Alex Rivera" — redact known field values when provided.


def redact_text(text: str, *, extra_names: list[str] | None = None) -> str:
    out = POLICY_RE.sub("POL-REDACTED", text)
    out = DOB_RE.sub("DATE-REDACTED", out)
    for name in extra_names or []:
        if name and len(name) >= 3:
            out = re.sub(re.escape(name), "NAME-REDACTED", out, flags=re.IGNORECASE)
    return out


def redact_obj(obj: Any, *, extra_names: list[str] | None = None) -> Any:
    if isinstance(obj, str):
        return redact_text(obj, extra_names=extra_names)
    if isinstance(obj, dict):
        return {k: redact_obj(v, extra_names=extra_names) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, extra_names=extra_names) for v in obj]
    return obj
