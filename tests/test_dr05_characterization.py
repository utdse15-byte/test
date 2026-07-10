"""DR05 §6.4 characterization — 15 pins of the CURRENT (collaborative) MCP
surface, captured BEFORE any ToolPolicy / profile change so every DR05 delta is
a provable one.

Discipline (mirrors the AI_IDE_06 characterization set): these pins run against
the UNTOUCHED tree and are GREEN there. They pin the load-bearing default
behavior the DR05 work must not change — the tool registry, the result/error
shapes, the get_shot ``rev`` CAS reality that the unattended ALLOW_WITH_CAS rule
leans on, the free/read-only dry-run path the DRY_RUN_ONLY rule leans on, and
the director human-only confirm gate the profile complements (never replaces).

All pins call the tool HANDLERS directly (the same objects the server dispatches)
so no ffmpeg / no subprocess is needed; the one build pin uses dry_run, which is
read-only and returns before any generation.
"""

from __future__ import annotations

import pytest

from manju.core.hashing import hash_text
from manju.core.locks import seal_lock
from manju.core.writes import shot_text_hash
from manju.mcp import tools as mcp_tools
from manju.mcp.tools import TOOL_DEFS, TOOLS, ToolError, call_tool, list_tools

# The 25 tools the registry exposed at the DR05 base commit (DR01–DR06 grown).
# Pinned as a MUST-PRESERVE set: DR05 may ADD (agent_surface) but must never
# drop, rename, or change any of these.
BASELINE_TOOL_NAMES = [
    "status", "explain", "impact", "check", "list_shots", "get_shot",
    "update_shot", "select_take", "build", "redo", "qc", "qc_brief",
    "qc_coverage", "qc_verdict", "export", "events", "board", "propose",
    "director_propose", "director_confirm", "director_execute",
    "director_suggest", "funnel_status", "skill_list", "skill_show",
]


def _handler(name: str):
    return next(t["handler"] for t in TOOL_DEFS if t["name"] == name)


# ---------------------------------------------------------------- 1-3 registry


def test_char_01_all_baseline_tools_present():
    names = [t["name"] for t in list_tools()]
    for expected in BASELINE_TOOL_NAMES:
        assert expected in names, f"{expected} disappeared from the registry"
    # dangerous / off-surface commands stay ABSENT (§5, §11)
    for forbidden in ("unlock", "gc", "pack", "import"):
        assert forbidden not in names


def test_char_02_list_payload_shape_is_name_desc_schema_only():
    # tools/list carries ONLY {name, description, inputSchema} — no policy/handler
    # leaks into the wire payload (byte-identity anchor for collaborative).
    for entry in list_tools():
        assert set(entry.keys()) == {"name", "description", "inputSchema"}
        assert entry["description"]
        assert entry["inputSchema"]["type"] == "object"


def test_char_03_load_bearing_schemas_byte_identical():
    by_name = {t["name"]: t for t in list_tools()}
    # build's flag enums (dry_run/gen/target) — the DRY_RUN_ONLY rule reads these
    props = by_name["build"]["inputSchema"]["properties"]
    assert props["dry_run"] == {"type": "boolean", "default": False}
    assert props["gen"]["enum"] == ["missing", "auto", "off"]
    assert props["target"]["enum"] == ["proxy", "final", "exports", "qc"]
    # update_shot required + expected_rev (CAS) present
    us = by_name["update_shot"]["inputSchema"]
    assert us["required"] == ["shot_id", "yaml_content"]
    assert "expected_rev" in us["properties"]
    # explain graph flag (DR03B) present, get_shot requires shot_id
    assert "graph" in by_name["explain"]["inputSchema"]["properties"]
    assert by_name["get_shot"]["inputSchema"]["required"] == ["shot_id"]


# ------------------------------------------------------ 4-6 get_shot rev / CAS


def test_char_04_get_shot_rev_is_stable_content_hash(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    r1 = _handler("get_shot")(tmp_project, {"shot_id": "S001"})
    r2 = _handler("get_shot")(tmp_project, {"shot_id": "S001"})
    assert r1["rev"] == r2["rev"]  # stable across reads (no time/pid in it)
    assert r1["rev"].startswith("sha256:")
    assert r1["rev"] == shot_text_hash(tmp_project, "S001")
    assert r1["rev"] == hash_text(tmp_project.shot_path("S001").read_text("utf-8"))


def test_char_05_get_shot_rev_tracks_content(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    before = _handler("get_shot")(tmp_project, {"shot_id": "S001"})["rev"]
    # mutate via update_shot (unlocked dialogue.text)
    data = _handler("get_shot")(tmp_project, {"shot_id": "S001"})["data"]
    data["dialogue"]["text"] = "改写后的台词。"
    import yaml
    _handler("update_shot")(tmp_project, {
        "shot_id": "S001",
        "yaml_content": yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    })
    after = _handler("get_shot")(tmp_project, {"shot_id": "S001"})["rev"]
    assert after != before  # the CAS token moved with the content


def test_char_06_expected_rev_roundtrip_and_stale_refused(tmp_project, add_shot):
    import yaml
    add_shot(tmp_project, "S001")
    got = _handler("get_shot")(tmp_project, {"shot_id": "S001"})
    rev = got["rev"]
    data = got["data"]
    data["dialogue"]["text"] = "第一次改。"
    ok = _handler("update_shot")(tmp_project, {
        "shot_id": "S001",
        "yaml_content": yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        "expected_rev": rev,  # matches HEAD -> accepted
    })
    assert ok["ok"] is True
    # the same (now stale) rev must be refused, not silently last-writer-win
    data["dialogue"]["text"] = "第二次改(用旧 rev)。"
    with pytest.raises(ToolError):
        _handler("update_shot")(tmp_project, {
            "shot_id": "S001",
            "yaml_content": yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            "expected_rev": rev,  # stale
        })


# ------------------------------------------------- 7-10 write tool result shapes


def test_char_07_update_shot_happy_result_shape(tmp_project, add_shot):
    import yaml
    add_shot(tmp_project, "S001")
    data = _handler("get_shot")(tmp_project, {"shot_id": "S001"})["data"]
    data["dialogue"]["text"] = "新台词。"
    res = _handler("update_shot")(tmp_project, {
        "shot_id": "S001",
        "yaml_content": yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
    })
    assert res["ok"] is True
    assert "check_warnings" in res


def test_char_08_update_shot_locked_field_rejected(tmp_project, add_shot):
    import yaml
    add_shot(tmp_project, "S001")
    raw = tmp_project.load_shot_raw("S001")
    digest = seal_lock(raw, "dialogue.text")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("locked", {}).__setitem__("dialogue.text", digest)
    )
    before = tmp_project.shot_path("S001").read_text("utf-8")
    data = _handler("get_shot")(tmp_project, {"shot_id": "S001"})["data"]
    data["dialogue"]["text"] = "被篡改。"
    with pytest.raises(ToolError):
        _handler("update_shot")(tmp_project, {
            "shot_id": "S001",
            "yaml_content": yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        })
    assert tmp_project.shot_path("S001").read_text("utf-8") == before


def test_char_09_update_shot_adding_lock_rejected(tmp_project, add_shot):
    import yaml
    add_shot(tmp_project, "S001")
    before = tmp_project.shot_path("S001").read_text("utf-8")
    data = _handler("get_shot")(tmp_project, {"shot_id": "S001"})["data"]
    data["locked"] = {"camera.movement": "sha256:deadbeef"}
    with pytest.raises(ToolError):
        _handler("update_shot")(tmp_project, {
            "shot_id": "S001",
            "yaml_content": yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        })
    assert tmp_project.shot_path("S001").read_text("utf-8") == before


def test_char_10_select_take_shape_and_unknown_take(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    res = _handler("select_take")(tmp_project, {"shot_id": "S001", "take": take.name})
    assert res["ok"] is True and res["shot"] == "S001" and res["take"] == take.name
    with pytest.raises(ToolError):
        _handler("select_take")(tmp_project, {"shot_id": "S001", "take": "take_99"})


# ------------------------------------------- 11-12 read-only/free-path anchors


def test_char_11_build_dry_run_is_free_and_generates_nothing(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    res = _handler("build")(tmp_project, {"dry_run": True})
    assert res["ok"] is True
    assert isinstance(res["plan"], list)
    assert res["estimated_cost"] == 0  # caption_card fallback is free
    # dry-run generated nothing: the shot is still without takes
    listing = _handler("list_shots")(tmp_project, {})
    st = {s["id"]: s for s in listing["shots"]}["S001"]
    assert st["state"] == "missing"
    assert not res.get("generated")


def test_char_12_director_propose_free_returns_proposal(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # a text/local proposal (no priced action) — director_propose prices via the
    # shared dry-run estimators, no network
    prop = _handler("director_propose")(tmp_project, {
        "actions": [{"type": "snapshot"}], "why": "characterization",
    })
    assert prop["state"] == "proposed"
    assert prop["id"].startswith("prop_")


# ------------------------------- 13 director human-only confirm gate (engine)


def test_char_13_priced_proposal_ai_confirm_refused_engine_side(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    prop = _handler("director_propose")(tmp_project, {
        "actions": [{"type": "redo", "shot": "S001"}], "why": "priced",
    })
    # the MCP director_confirm handler runs actor="ai" — a PRICED proposal is
    # refused human-only ENGINE-side today (Round Y #15). DR05's unattended DENY
    # is belt-and-suspenders on top of this, never a replacement.
    with pytest.raises(ToolError):
        _handler("director_confirm")(tmp_project, {"id": prop["id"]})


# ----------------------------------------- 14-15 status shape + dispatch contract


def test_char_14_status_snapshot_field_inventory(tmp_project, add_shot):
    # the rich takeover snapshot the AgentWorkspaceSnapshot gate is measured
    # against — pin the load-bearing keys are all here in ONE call.
    add_shot(tmp_project, "S001")
    st = _handler("status")(tmp_project, {})
    for key in ("shots_total", "shots_by_state", "next_step", "total_cost",
                "budget_limit", "recent_events", "timeline", "qc", "mode"):
        assert key in st, f"status lost {key}"


def test_char_15_dispatch_unknown_tool_and_known_dispatch(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # unknown tool -> ToolError (the server renders it isError with {"error":...})
    with pytest.raises(ToolError):
        call_tool(tmp_project, "does_not_exist", {})
    # a known read dispatches through call_tool unchanged
    out = call_tool(tmp_project, "list_shots", {})
    assert "shots" in out
    # every registry entry is addressable by name
    assert set(TOOLS) >= set(BASELINE_TOOL_NAMES)
    assert mcp_tools.TOOLS["status"]["handler"] is _handler("status")
