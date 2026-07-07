"""The creation funnel (round V, goal item 2): build/funnel.py + CLI + director + MCP.

The staged creative workflow AS DATA — 立意→梗概→节拍→剧本→分镜→生成计划→生成.
These tests pin: the stage-detection walk (empty→brief current, each written
artifact advancing `current`, through produce), the done-predicates (a trivial
template is NOT done; 节拍 needs ≥3 beat lines AND real content; 分镜 reuses
run_check), the scaffolds (write/refuse/force + event), the `manju create`
checklist CLI + --json, the director suggest_next funnel-first-pre-storyboard
rule, and the MCP funnel_status tool. No ffmpeg — every artifact is text/a fake
final byte-file; the walk never probes media.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.build import director as d
from manju.build import funnel
from manju.cli import app
from manju.core.events import tail_events

runner = CliRunner()


# --------------------------------------------------------------- helpers

# All comfortably clear the >80-char content gate; headings/comments don't count.
BRIEF_BODY = (
    "# 立意\n\n"
    "一句话立意:雨夜便利店里,一个陌生人递来一把伞,改变了收银员整晚的心情与选择。\n"
    "给谁看:喜欢都市温情短片、常刷抖音与小红书的年轻观众,痛点是孤独与被看见的渴望。\n"
    "为什么现在:雨季与深夜话题正热,情绪向内容更容易被算法推荐进流量池。\n"
    "平台与时长:竖屏,目标三十秒,发抖音与小红书。\n"
)
SYNOPSIS_BODY = (
    "# 梗概\n\n"
    "深夜的便利店只剩收银员小夏一人,窗外暴雨如注。一个浑身湿透的陌生人进门,买了瓶热饮,"
    "却在离开时把自己的伞留在柜台,只说了句你下班也会淋雨吧。小夏打烊后追出门,雨里两个人的"
    "距离,从一把伞开始慢慢拉近。\n"
)
BEATS_BODY = (
    "# 节拍\n\n"
    "- Hook: 暴雨夜空荡的便利店,陌生人把伞留在柜台的特写,只有三秒。\n"
    "- Value: 小夏的犹豫与回忆,交代她一个人撑过的无数个深夜。\n"
    "- Payoff: 她冲进雨里追上陌生人,伞下两个人第一次对视。\n"
    "- CTA: 你也遇到过那把突然出现的伞吗?点赞关注看下一集。\n"
)
SCRIPT_BODY = (
    "# 剧本\n\n"
    "场景一 便利店 内景 夜。小夏靠在收银台后打哈欠,雨声盖过了空调的嗡鸣声响。\n"
    "小夏(自语):这种天,应该不会再有人来了吧。\n"
    "陌生人推门而入,风把门帘吹得乱响,他浑身湿透,买了瓶热饮又默默离开。\n"
)


def _write(project, relpath: str, text: str) -> None:
    p = project.root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _current(project) -> str | None:
    return funnel.funnel_status(project)["current"]


def _fake_final(project) -> Path:
    project.final_dir.mkdir(parents=True, exist_ok=True)
    final = project.final_dir / "final_v1.mp4"
    final.write_bytes(b"fake-final")
    (project.final_dir / "final_v1.key.json").write_text(
        json.dumps({"final_key": "sha256:deadbeef"}), encoding="utf-8")
    return final


# --------------------------------------------------------- stage-detection walk


def test_walk_empty_to_produce(tmp_project, add_shot):
    """Each written artifact advances `current` by exactly one stage."""
    p = tmp_project
    # fresh project: manju new scaffolds a TRIVIAL brief.md → not done → current
    assert _current(p) == "brief"

    _write(p, "story/brief.md", BRIEF_BODY)
    assert _current(p) == "synopsis"

    _write(p, "story/synopsis.md", SYNOPSIS_BODY)
    assert _current(p) == "beats"

    _write(p, "story/beats.md", BEATS_BODY)
    assert _current(p) == "script"

    _write(p, "story/script.md", SCRIPT_BODY)
    assert _current(p) == "storyboard"

    # a valid shot (references the fixture bible) + no check errors → storyboard done
    add_shot(p, "S001")
    assert _current(p) == "plan"

    # the approve-before-spend gate: a confirmed director proposal advances plan
    prop = d.propose(p, [{"type": "snapshot", "label": "cp"}], actor="human")
    d.confirm(p, prop.id, actor="human")
    assert _current(p) == "produce"

    # newest final exists → produce done → funnel complete
    _fake_final(p)
    status = funnel.funnel_status(p)
    assert status["current"] is None
    assert status["complete"] is True
    assert status["done"] == status["total"] == len(funnel.STAGES)
    assert all(s["state"] == "done" for s in status["stages"])


def test_status_shape_is_json_serializable(tmp_project):
    status = funnel.funnel_status(tmp_project)
    # round-trips through JSON (the API/MCP contract)
    reparsed = json.loads(json.dumps(status, ensure_ascii=False))
    assert [s["id"] for s in reparsed["stages"]] == list(funnel.STAGE_IDS)
    for s in reparsed["stages"]:
        assert set(s) == {"id", "cn", "state", "artifact", "evidence",
                          "skill", "next_action"}
        assert s["state"] in ("done", "current", "todo")
    # exactly one current on a fresh project
    assert sum(1 for s in reparsed["stages"] if s["state"] == "current") == 1


# ------------------------------------------------------------ done-predicates


def test_trivial_template_is_not_done(tmp_project):
    """The scaffold `manju new` writes (and `manju create` writes) is comments +
    headings only — writing it must NOT complete the stage."""
    p = tmp_project
    # manju new's trivial brief.md
    assert funnel.funnel_status(p)["current"] == "brief"
    # scaffolding the richer synopsis template still leaves synopsis not-done
    funnel.scaffold_stage(p, "synopsis", actor="human")
    _write(p, "story/brief.md", BRIEF_BODY)  # advance past brief
    assert funnel.funnel_status(p)["current"] == "synopsis"


def test_beats_needs_three_beat_lines_and_content(tmp_project):
    p = tmp_project
    _write(p, "story/brief.md", BRIEF_BODY)
    _write(p, "story/synopsis.md", SYNOPSIS_BODY)

    # only two beat lines (even if long) → not done
    _write(p, "story/beats.md", "# 节拍\n\n"
           "- Hook: 暴雨夜空荡的便利店,陌生人把伞留在柜台的特写,只有短短三秒钟。\n"
           "- Value: 小夏的犹豫与回忆,交代她一个人撑过的无数个孤独深夜时光。\n")
    assert _current(p) == "beats"

    # three beat lines but trivial content (<80 chars) → still not done
    _write(p, "story/beats.md", "# 节拍\n\n- a\n- b\n- c\n")
    assert _current(p) == "beats"

    # three real beats with real content → done, advances to script
    _write(p, "story/beats.md", BEATS_BODY)
    assert _current(p) == "script"


def test_storyboard_reuses_check_validity(tmp_project, add_shot):
    """分镜 done needs shots (index non-empty) AND a passing manju check."""
    p = tmp_project
    for rel, body in (("story/brief.md", BRIEF_BODY), ("story/synopsis.md", SYNOPSIS_BODY),
                      ("story/beats.md", BEATS_BODY), ("story/script.md", SCRIPT_BODY)):
        _write(p, rel, body)
    assert _current(p) == "storyboard"  # no shots yet

    # a shot referencing a character NOT in the bible → check fails → still todo
    add_shot(p, "S001", characters=["ghost"])
    assert _current(p) == "storyboard"

    # fix the reference → check passes → storyboard done
    add_shot(p, "S001", characters=["linxia"])
    assert _current(p) == "plan"


def test_plan_gate_confirmed_proposal_or_final(tmp_project, add_shot):
    p = tmp_project
    for rel, body in (("story/brief.md", BRIEF_BODY), ("story/synopsis.md", SYNOPSIS_BODY),
                      ("story/beats.md", BEATS_BODY), ("story/script.md", SCRIPT_BODY)):
        _write(p, rel, body)
    add_shot(p, "S001")
    assert _current(p) == "plan"  # nothing approved yet

    # a merely-proposed (unconfirmed) proposal does NOT open the gate
    prop = d.propose(p, [{"type": "snapshot"}], actor="human")
    assert _current(p) == "plan"

    d.confirm(p, prop.id, actor="human")
    assert _current(p) == "produce"


# ------------------------------------------------------------------ scaffolds


def test_scaffold_write_refuse_force_and_event(tmp_project):
    p = tmp_project
    # synopsis.md does not exist after `manju new` → clean write
    res = funnel.scaffold_stage(p, "synopsis", actor="human")
    assert res["stage"] == "synopsis" and res["path"] == "story/synopsis.md"
    assert (p.root / "story" / "synopsis.md").exists()
    # event recorded
    actions = [e.get("action") for e in tail_events(p.root, 20)]
    assert "funnel_scaffold" in actions

    # refuse to overwrite without --force
    with pytest.raises(funnel.FunnelError):
        funnel.scaffold_stage(p, "synopsis", actor="human")

    # --force overwrites
    forced = funnel.scaffold_stage(p, "synopsis", force=True, actor="human")
    assert forced["created"] is True

    # unknown / non-scaffoldable stage (script/storyboard) refused with guidance
    with pytest.raises(funnel.FunnelError):
        funnel.scaffold_stage(p, "script", actor="human")


def test_scaffold_does_not_complete_stage(tmp_project):
    """Writing a template is scaffolding, not authoring (§2)."""
    p = tmp_project
    funnel.scaffold_stage(p, "beats", force=True, actor="human")
    text = (p.root / "story" / "beats.md").read_text(encoding="utf-8")
    assert funnel._content_len(text) <= 80   # collapses to ~zero real content
    assert funnel._count_beats(text) == 0    # the example bullets sit in a comment


# --------------------------------------------------------------- create CLI


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


def test_create_checklist_json(in_project):
    result = runner.invoke(app, ["create", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["current"] == "brief"
    assert data["total"] == len(funnel.STAGES)
    assert data["stages"][0]["cn"] == "立意"


def test_create_checklist_human(in_project):
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, result.output
    assert "创作漏斗" in result.output
    assert "立意" in result.output and "下一步" in result.output


def test_create_stage_scaffold_json(in_project):
    result = runner.invoke(app, ["create", "synopsis", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["path"] == "story/synopsis.md" and data["created"] is True
    assert (in_project.root / "story" / "synopsis.md").exists()


def test_create_stage_refuses_existing_then_force(in_project):
    # brief.md already exists (manju new scaffold) → refuse
    refused = runner.invoke(app, ["create", "brief"])
    assert refused.exit_code == 1
    # --force succeeds
    forced = runner.invoke(app, ["create", "brief", "--force", "--json"])
    assert forced.exit_code == 0, forced.output
    assert json.loads(forced.output)["created"] is True


# --------------------------------------------------- director integration


def test_suggest_next_funnel_first_pre_storyboard(tmp_project):
    """Pre-storyboard, the FIRST suggestion is the funnel next_action (advisory)."""
    sugg = d.suggest_next(tmp_project)
    assert sugg and sugg[0].kind == "funnel"
    assert sugg[0].action is None            # a story edit is not a director action
    assert "立意" in sugg[0].text            # the current stage's 中文名
    assert "creation-funnel" in sugg[0].text  # carries the skill pointer


def test_suggest_next_no_funnel_after_storyboard(tmp_project, add_shot):
    """Once the storyboard is done, NO funnel suggestion — the shot signals lead."""
    p = tmp_project
    for rel, body in (("story/brief.md", BRIEF_BODY), ("story/synopsis.md", SYNOPSIS_BODY),
                      ("story/beats.md", BEATS_BODY), ("story/script.md", SCRIPT_BODY)):
        _write(p, rel, body)
    add_shot(p, "S001")
    assert _current(p) == "plan"  # past storyboard

    sugg = d.suggest_next(p)
    assert not any(s.kind == "funnel" for s in sugg)
    # the ordinary signals still lead (a missing shot → generate)
    assert any(s.kind == "generate" for s in sugg)


# ----------------------------------------------------------------- MCP


def test_mcp_funnel_status_tool(tmp_project):
    from manju.mcp.tools import TOOLS, call_tool, list_tools

    assert "funnel_status" in {t["name"] for t in list_tools()}
    assert TOOLS["funnel_status"]["inputSchema"]["type"] == "object"

    out = call_tool(tmp_project, "funnel_status", {})
    assert out["current"] == "brief"
    assert [s["id"] for s in out["stages"]] == list(funnel.STAGE_IDS)
