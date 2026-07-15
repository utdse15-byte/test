"""Cycle-61: MCP build/redo map WaitingUser to ToolError waiting_user."""

import inspect

from manju.mcp import tools as mcp_tools


def test_mcp_build_catches_waiting_user() -> None:
    src = inspect.getsource(mcp_tools._h_build)
    assert "WaitingUser" in src
    assert 'code="waiting_user"' in src or "code='waiting_user'" in src


def test_mcp_redo_catches_waiting_user() -> None:
    src = inspect.getsource(mcp_tools._h_redo)
    assert "WaitingUser" in src
    assert 'code="waiting_user"' in src or "code='waiting_user'" in src
