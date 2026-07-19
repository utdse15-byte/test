"""mcp/ integrity — regressions found by the hourly mcp/ scan (adversarially
reproduced before fixing). Behavioral pins, never source-text greps.

SPEND-SAFETY (high): _h_build / _h_redo coerced the assume_yes tool argument
with bool(), so a truthy NON-bool value the wire can carry (the JSON string
"false"/"0"/"no", or a number) became True and silently APPROVED the spend gate
the caller never confirmed — a paid provider firing with no human approval. The
CLI can't be fooled (--yes is an argparse store_true flag) and the codebase's
other gate already fails closed (policy.decide uses `args.get("dry_run") is
True`). Fixed to the same fail-closed `is True`.

arg-validation (low): _h_update_shot ran yaml.safe_load on a non-string
yaml_content (a JSON object/number for the declared "string" field), which
raises AttributeError — NOT caught by `except yaml.YAMLError` — so an internal
error leaked instead of the intended structured ToolError.
"""

from __future__ import annotations

import pytest

from manju.mcp import tools as mcp_tools


# --------------------------------------------------------------------------- #
# spend-safety: assume_yes is fail-closed (only a real bool True approves)       #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad_truthy", ["false", "0", "no", "true", 1])
def test_mcp_redo_assume_yes_non_bool_does_not_approve(
        monkeypatch, tmp_project, add_shot, bad_truthy):
    add_shot(tmp_project, "S001")
    captured: dict = {}

    def fake_redo_shot(project, shot_id, *, candidates=None, provider=None,
                       seed=None, actor="engine", assume_yes=False):
        captured["assume_yes"] = assume_yes
        return []

    monkeypatch.setattr(mcp_tools, "redo_shot", fake_redo_shot)
    mcp_tools._h_redo(tmp_project, {"shot_id": "S001", "assume_yes": bad_truthy})
    # a truthy NON-bool must NOT approve the paid spend gate
    assert captured["assume_yes"] is False


def test_mcp_redo_real_bool_true_still_approves(monkeypatch, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    captured: dict = {}

    def fake_redo_shot(project, shot_id, *, candidates=None, provider=None,
                       seed=None, actor="engine", assume_yes=False):
        captured["assume_yes"] = assume_yes
        return []

    monkeypatch.setattr(mcp_tools, "redo_shot", fake_redo_shot)
    mcp_tools._h_redo(tmp_project, {"shot_id": "S001", "assume_yes": True})
    assert captured["assume_yes"] is True


@pytest.mark.parametrize("assume_yes,expected", [("false", False), (True, True)])
def test_mcp_build_assume_yes_fail_closed(
        monkeypatch, tmp_project, add_shot, assume_yes, expected):
    add_shot(tmp_project, "S001")
    captured: dict = {}

    class _Result:
        def to_dict(self):
            return {"ok": True}

    def fake_run_build(project, **kw):
        captured["assume_yes"] = kw.get("assume_yes")
        return _Result()

    monkeypatch.setattr(mcp_tools, "run_build", fake_run_build)
    mcp_tools._h_build(tmp_project, {"target": "final", "assume_yes": assume_yes})
    assert captured["assume_yes"] is expected


# --------------------------------------------------------------------------- #
# arg-validation: a non-string yaml_content is a structured ToolError            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", [{"id": "S001"}, 42, ["a"]])
def test_mcp_update_shot_non_string_yaml_is_tool_error(tmp_project, add_shot, bad):
    add_shot(tmp_project, "S001")
    with pytest.raises(mcp_tools.ToolError) as exc:
        mcp_tools._h_update_shot(
            tmp_project, {"shot_id": "S001", "yaml_content": bad})
    assert exc.value.code == "invalid_argument"
