"""Cycle-61: MCP build/redo map WaitingUser to ToolError waiting_user.

The build path is now asserted BEHAVIORALLY (P1 item 2/5): drive ``_h_build``
and check the ToolError code, rather than scanning its source — the mapping
moved into ``core.outcomes.classify_exception``. (The redo path still keeps its
source pin below; ``_h_redo`` was not refactored.)
"""

import inspect

import pytest

from manju.build.graph import WaitingUser
from manju.mcp import tools as mcp_tools


def test_mcp_build_catches_waiting_user(tmp_project, monkeypatch) -> None:
    def _raise(*a, **k):
        raise WaitingUser("确认后重试", 5.0, "CNY")

    monkeypatch.setattr(mcp_tools, "run_build", _raise)
    with pytest.raises(mcp_tools.ToolError) as ei:
        mcp_tools._h_build(tmp_project, {"target": "final"})
    assert ei.value.code == "waiting_user"


def test_mcp_redo_catches_waiting_user() -> None:
    src = inspect.getsource(mcp_tools._h_redo)
    assert "WaitingUser" in src
    assert 'code="waiting_user"' in src or "code='waiting_user'" in src
