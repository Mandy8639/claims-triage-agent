"""MCP server exposes only allowlisted tools + review resource."""

from __future__ import annotations

from claims_triage.tools.allowlist import ALLOWED_TOOLS


def test_allowed_tools_exactly_three() -> None:
    assert ALLOWED_TOOLS == frozenset({"retrieve_policy", "validate_packet", "draft_letter"})
