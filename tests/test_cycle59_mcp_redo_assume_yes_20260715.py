"""Cycle-59: MCP redo accepts and threads assume_yes."""

import inspect
from pathlib import Path

from manju.mcp import tools as mcp_tools


def test_mcp_redo_schema_has_assume_yes() -> None:
    props = mcp_tools.TOOLS["redo"]["inputSchema"]["properties"]
    assert "assume_yes" in props


def test_mcp_redo_handler_passes_assume_yes() -> None:
    src = inspect.getsource(mcp_tools._h_redo)
    assert "assume_yes" in src
