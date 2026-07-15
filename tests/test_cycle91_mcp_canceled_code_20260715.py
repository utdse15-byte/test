"""Cycle-91: MCP build/redo map cancellation to ToolError canceled.

The build path is asserted BEHAVIORALLY (P1 item 2/5): drive ``_h_build`` with
a BuildCanceled and check the ToolError code, rather than scanning its source
(the mapping moved into ``core.outcomes.classify_exception``). The redo path
keeps its source pin below; ``_h_redo`` was not refactored.
"""

import inspect

import pytest

from manju.build.graph import BuildCanceled
from manju.mcp import tools as t


def test_mcp_build_maps_canceled(tmp_project, monkeypatch):
    def _raise(*a, **k):
        raise BuildCanceled("stopped mid-build", generated=0, spent=0.0, currency=None)

    monkeypatch.setattr(t, "run_build", _raise)
    with pytest.raises(t.ToolError) as ei:
        t._h_build(tmp_project, {"target": "final"})
    assert ei.value.code == "canceled"


def test_mcp_redo_maps_canceled():
    src = inspect.getsource(t._h_redo)
    assert "ProviderCanceled" in src
    assert 'code="canceled"' in src or "code='canceled'" in src
