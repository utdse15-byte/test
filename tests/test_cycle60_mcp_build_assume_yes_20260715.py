"""Cycle-60: MCP build accepts assume_yes for spend gate."""

import inspect

from manju.mcp import tools as mcp_tools


def test_mcp_build_schema_has_assume_yes() -> None:
    props = mcp_tools.TOOLS["build"]["inputSchema"]["properties"]
    assert "assume_yes" in props


def test_mcp_build_handler_passes_assume_yes() -> None:
    src = inspect.getsource(mcp_tools._h_build)
    assert "assume_yes" in src
