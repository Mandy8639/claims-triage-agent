"""MCP server exposing the same 3 allowlisted tools + read-only review queue.

Same guardrail and allowlist as the agent — not a bypass.
Pattern aligned with oncology-research-agent / bnpl-servicing-agent (MCP 2.x).
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from claims_triage.agent.loop import run_packet
from claims_triage.config import db_path
from claims_triage.corpus.seed import refresh_updated_at, seed_corpus
from claims_triage.corpus.store import ClaimStore
from claims_triage.guardrail.scanner import scan_packet_fields
from claims_triage.review.queue import pending_reviews
from claims_triage.tools.allowlist import ALLOWED_TOOLS, assert_allowed
from claims_triage.tools.draft import draft_letter
from claims_triage.tools.retrieve import retrieve_policy
from claims_triage.tools.validate import validate_packet

server = MCPServer(
    name="claims-triage-agent",
    instructions=(
        "Bounded claims triage tools over a SYNTHETIC life-insurance corpus. "
        "Not affiliated with New York Life. Never approves, denies, or pays a claim. "
        f"Allowlisted tools only: {sorted(ALLOWED_TOOLS)}. "
        "Every call is scanned by the deterministic guardrail."
    ),
)


def _store() -> ClaimStore:
    path = db_path()
    if not path.exists():
        seed_corpus(path, force=True)
        refresh_updated_at(path)
    return ClaimStore(path)


@server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def retrieve_policy_tool(query: str, limit: int = 8) -> dict[str, Any]:
    """RAG over the synthetic corpus; returns cited passages with source IDs."""
    assert_allowed("retrieve_policy")
    return retrieve_policy(_store(), query, limit=limit)


@server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def validate_packet_tool(packet_id: str) -> dict[str, Any]:
    """Deterministic FNOL validation report (no LLM)."""
    assert_allowed("validate_packet")
    store = _store()
    packet = store.get_packet(packet_id)
    if packet is not None:
        scan = scan_packet_fields(packet, known_policy_ids=store.list_policy_ids())
        if scan.verdict.value == "BLOCK":
            return {
                "blocked": True,
                "guardrail": scan.to_dict(),
                "message": "Packet blocked by input guardrail before validation.",
            }
    return validate_packet(store, packet_id)


@server.tool(annotations=ToolAnnotations(destructive_hint=False, open_world_hint=False))
def draft_letter_tool(packet_id: str) -> dict[str, Any]:
    """Draft correspondence ONLY from validated, cited facts (post-checked)."""
    assert_allowed("draft_letter")
    store = _store()
    packet = store.get_packet(packet_id)
    if packet is not None:
        scan = scan_packet_fields(packet, known_policy_ids=store.list_policy_ids())
        if scan.verdict.value == "BLOCK":
            return {
                "blocked": True,
                "guardrail": scan.to_dict(),
                "message": "Packet blocked by input guardrail; draft refused.",
            }
    return draft_letter(store, packet_id)


@server.resource("review://queue")
def review_queue_resource() -> str:
    """Read-only review queue (pending human gates / holds)."""
    rows = pending_reviews(_store())
    return json.dumps(rows, indent=2, default=str)


@server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
def run_bounded_packet(packet_id: str, auto_gates: bool = False) -> dict[str, Any]:
    """Run validate → gates → draft under the same guardrail (observability helper)."""
    result = run_packet(_store(), packet_id, auto_gates=auto_gates)
    return {
        "run_id": result.get("run_id"),
        "trace_id": result.get("trace_id"),
        "status": result.get("status"),
        "guardrail_verdict": result.get("guardrail_verdict"),
        "reason_code": result.get("reason_code"),
        "abstained": result.get("abstained"),
        "paused": result.get("paused"),
        "cost_usd": result.get("cost_usd"),
    }


def main() -> None:
    seed_corpus(force=False)
    refresh_updated_at()
    server.run("stdio")


if __name__ == "__main__":
    main()
