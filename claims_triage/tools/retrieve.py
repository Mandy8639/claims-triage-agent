"""Tool: retrieve_policy — RAG over the synthetic corpus with citations."""

from __future__ import annotations

from typing import Any

from claims_triage.corpus.store import ClaimStore
from claims_triage.tools.allowlist import assert_allowed


def retrieve_policy(store: ClaimStore, query: str, limit: int = 8) -> dict[str, Any]:
    assert_allowed("retrieve_policy")
    hits = store.search_corpus(query, limit=limit)
    if not hits:
        return {
            "tool": "retrieve_policy",
            "found": False,
            "passages": [],
            "message": "No relevant corpus passages found for this query.",
        }
    return {
        "tool": "retrieve_policy",
        "found": True,
        "passages": hits,
        "message": f"Returned {len(hits)} cited passage(s).",
    }
