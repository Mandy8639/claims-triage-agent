"""Hard allowlist: the agent may call exactly these three tools."""

from __future__ import annotations

ALLOWED_TOOLS = frozenset({"retrieve_policy", "validate_packet", "draft_letter"})


class ToolAllowlistError(PermissionError):
    """Raised when code attempts a non-allowlisted tool."""


def assert_allowed(tool_name: str) -> None:
    if tool_name not in ALLOWED_TOOLS:
        raise ToolAllowlistError(
            f"Tool {tool_name!r} is not on the allowlist {sorted(ALLOWED_TOOLS)}"
        )
