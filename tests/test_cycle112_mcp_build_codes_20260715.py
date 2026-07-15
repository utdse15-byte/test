"""Cycle-112: MCP build surfaces waiting_user and canceled outcomes.

Rewritten (P1 item 2/5): behavioral. Drive ``_h_build`` and assert the
``ToolError`` codes it actually raises, instead of ``inspect.getsource``
scanning the handler for string literals. The exception→code mapping now lives
in ``core.outcomes.classify_exception`` (used by ``_h_build``), so the old
literals ("WaitingUser", "BuildCanceled", …) no longer appear in the handler's
source — but the observable behavior is unchanged and is what we pin here.
"""

import pytest

from manju.build.graph import BuildCanceled, WaitingUser
from manju.mcp import tools as t


def _raiser(exc):
    def _f(*a, **k):
        raise exc
    return _f


def test_mcp_build_maps_waiting_user_to_waiting_user_code(tmp_project, monkeypatch):
    monkeypatch.setattr(t, "run_build",
                        _raiser(WaitingUser("确认后重试", 5.0, "CNY")))
    with pytest.raises(t.ToolError) as ei:
        t._h_build(tmp_project, {"target": "final"})
    assert ei.value.code == "waiting_user"


def test_mcp_build_maps_build_canceled_to_canceled_code(tmp_project, monkeypatch):
    monkeypatch.setattr(t, "run_build", _raiser(
        BuildCanceled("stopped mid-build", generated=0, spent=0.0, currency=None)))
    with pytest.raises(t.ToolError) as ei:
        t._h_build(tmp_project, {"target": "final"})
    assert ei.value.code == "canceled"


def test_mcp_build_lets_real_errors_propagate_unchanged(tmp_project, monkeypatch):
    # A genuine failure must NOT be reshaped into a soft ToolError code.
    monkeypatch.setattr(t, "run_build", _raiser(ValueError("disk on fire")))
    with pytest.raises(ValueError):
        t._h_build(tmp_project, {"target": "final"})
