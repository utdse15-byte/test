"""TRISURFACE F-01 — the ``final_export`` ask_before gate must bind the MCP
surface, not only the CLI.

Red-first evidence (TRISURFACE_TEST_2026-07-29.md §F-01): with
``final_export`` in ``project.yaml: ask_before`` the CLI refused
``manju export`` without ``--yes`` (waiting_user), while the SAME export
through the MCP ``export`` tool wrote outward artifacts with no gate at all —
and ``skills/manju/SKILL.md`` §5 explicitly tells agents this token means
"stop and ask". Enforcement lived only in ``cli.py``; CLAUDE.md's invariant
says checks live in service/core, not just the CLI.

The fix gives the check ONE owner (``build.graph.final_export_gate``, beside
``spend_gate``) consumed by every CLI site and by MCP ``_h_export``; MCP
answers with the same structured ``waiting_user`` code build/redo already use
(C61), confirmed by the same fail-closed ``assume_yes is True`` idiom (C59/
C60). The GUI export panel is deliberately NOT gated: there a HUMAN clicks
each deliverable's button, which is exactly the confirmation the token asks
for — recorded in the owner's docstring.
"""

from __future__ import annotations

import pytest

from manju.build.graph import WaitingUser, final_export_gate
from manju.mcp.tools import ToolError, call_tool


def _set_ask_before(project, tokens):
    config = project.load_config()
    config.ask_before = tokens
    project.save_config(config)


# ------------------------------------------------------------- the one owner


def test_gate_raises_waiting_user_when_token_present(tmp_project):
    with pytest.raises(WaitingUser) as exc:
        final_export_gate(tmp_project, confirmed=False,
                          noun="导出", retry="manju export --yes …")
    msg = str(exc.value)
    assert "final_export" in msg and "waiting_user" in msg
    assert "manju export --yes" in msg


def test_gate_passes_when_confirmed_or_token_absent(tmp_project):
    final_export_gate(tmp_project, confirmed=True,
                      noun="导出", retry="manju export --yes …")
    _set_ask_before(tmp_project, ["expensive_generation"])
    final_export_gate(tmp_project, confirmed=False,
                      noun="导出", retry="manju export --yes …")


# ------------------------------------------------------------------ MCP


def test_mcp_export_hits_the_gate_with_structured_waiting_user(tmp_project):
    """Default config carries final_export → the tool must answer the same
    structured waiting_user build/redo use, BEFORE any other precondition
    (the CLI checks this gate first too)."""
    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "export", {"formats": ["srt"]})
    assert exc.value.code == "waiting_user"
    assert "assume_yes" in str(exc.value)


def test_mcp_export_assume_yes_is_fail_closed(tmp_project):
    """C59/C60 idiom: only a real JSON true confirms — a truthy string must
    NOT approve an outward-artifact confirmation the caller never made."""
    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "export", {"formats": ["srt"], "assume_yes": "true"})
    assert exc.value.code == "waiting_user"


def test_mcp_export_confirmed_or_ungated_reaches_the_next_precondition(tmp_project):
    """With the gate satisfied (assume_yes) or removed (config), the tool
    proceeds to its next real precondition — the missing timeline — proving
    the gate cleared rather than short-circuiting everything."""
    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "export", {"formats": ["srt"], "assume_yes": True})
    assert "timeline" in str(exc.value)

    _set_ask_before(tmp_project, ["expensive_generation"])
    with pytest.raises(ToolError) as exc:
        call_tool(tmp_project, "export", {"formats": ["srt"]})
    assert "timeline" in str(exc.value)


def test_mcp_export_schema_advertises_assume_yes(tmp_project):
    """Agents discover the confirm arg from tools/list — the schema must
    carry it (ADD-only per the DR05 characterization pins)."""
    from manju.mcp.tools import list_tools

    export = next(t for t in list_tools() if t["name"] == "export")
    props = export["inputSchema"]["properties"]
    assert props.get("assume_yes") == {"type": "boolean", "default": False}
    # formats stays required exactly as before (must-preserve).
    assert export["inputSchema"]["required"] == ["formats"]
