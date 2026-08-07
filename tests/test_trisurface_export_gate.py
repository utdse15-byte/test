"""The ``final_export`` confirmation gate is owned by the shared build service.\n\nCLI and GUI callers rely on this one fail-closed gate; these tests pin its\nconfirmation and configuration semantics without network or media generation.\n"""

from __future__ import annotations

import pytest

from manju.build.graph import WaitingUser, final_export_gate


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
