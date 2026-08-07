"""The creation funnel (round V, goal item 2): build/funnel.py + CLI + director + GUI.

The staged creative workflow AS DATA — 立意→梗概→节拍→剧本→分镜→生成计划→生成.
These tests pin: the stage-detection walk (empty→brief current, each written
artifact advancing `current`, through produce), the done-predicates (a trivial
template is NOT done; 节拍 needs ≥3 beat lines AND real content; 分镜 reuses
run_check), the scaffolds (write/refuse/force + event), the `manju create`
checklist CLI + --json, the director suggest_next funnel-first-pre-storyboard
rule, and the shared funnel status service. No ffmpeg — every artifact is text/a fake
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
    """A fake final byte-file whose ``.key.json`` carries the REAL content key
    for the project's current (compilable) specs — round-W #66 made `produce`
    done require the export center's genuine UP_TO_DATE freshness verdict, so
    a final with an arbitrary/mismatched key sidecar no longer counts (that
    was the very bug being fixed). Requires the shot(s) to already have a
    selected, probeable take (see ``_ready_for_final``)."""
    from manju.media.probe import probe_duration_ms
    from manju.media.render import final_content_key
    from manju.timeline.compiler import compile_timeline, gather_compile_input

    tl = compile_timeline(gather_compile_input(project, probe_duration_ms))
    key = final_content_key(project, tl, ass_file=None, target="final")
    project.final_dir.mkdir(parents=True, exist_ok=True)
    final = project.final_dir / "final_v1.mp4"
    final.write_bytes(b"fake-final")
    (project.final_dir / "final_v1.key.json").write_text(
        json.dumps({"final_key": key, "target": "final"}), encoding="utf-8")
    return final


def _ready_for_final(project, shot_id: str) -> None:
    """Register + select a real, probeable take for ``shot_id`` so the
    timeline actually compiles (needed for :func:`_fake_final`'s real content
    key, and for a realistic "produce" stage — a fake final over an
    unresolved/no-take shot could never come from a real `manju build`)."""
    import subprocess
    import shutil

    from manju.core.models import TakeSidecar

    tmp = project.root / f"_{shot_id}_src.mp4"
    if shutil.which("ffmpeg"):
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=blue:s=160x120:d=1:r=24", "-pix_fmt", "yuv420p", str(tmp)],
            check=True, capture_output=True,
        )
    else:  # no ffmpeg on this box — a manual take with an explicit duration
        tmp.write_bytes(b"fake-take-bytes")
    take = project.register_take(shot_id, tmp, TakeSidecar(provider="manual_import",
                                                            spec_hash="manual"))
    tmp.unlink(missing_ok=True)
    project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    if not shutil.which("ffmpeg"):
        # duration="auto" needs a probe; without ffmpeg give it a fixed one.
        project.update_shot_raw(shot_id, lambda d: d.__setitem__("duration", 1.5))


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

    # newest final exists AND is up to date per the export-center freshness
    # verdict (round-W #66) → produce done → funnel complete
    _ready_for_final(p, "S001")
    _fake_final(p)
    status = funnel.funnel_status(p)
    assert status["current"] is None
    assert status["complete"] is True
    assert status["done"] == status["total"] == len(funnel.STAGES)
    assert all(s["state"] == "done" for s in status["stages"])


def test_status_shape_is_json_serializable(tmp_project):
    status = funnel.funnel_status(tmp_project)
    # round-trips through JSON (the serialized service contract)
    reparsed = json.loads(json.dumps(status, ensure_ascii=False))
    assert [s["id"] for s in reparsed["stages"]] == list(funnel.STAGE_IDS)
    for s in reparsed["stages"]:
        # `satisfied` joined the shape deliberately: `state` is POSITIONAL, so a
        # later stage whose own predicate already holds still reads "todo", and
        # the renderer needs the predicate itself to stop printing ○ next to
        # evidence saying 已落地. Kept as exact equality — the point of this pin
        # is that the serialized service shape never grows a field by ACCIDENT.
        assert set(s) == {"id", "cn", "state", "satisfied", "artifact",
                          "evidence", "skill", "next_action"}
        assert s["state"] in ("done", "current", "todo")
        assert isinstance(s["satisfied"], bool)
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


def test_plan_not_done_when_confirmed_proposal_has_expired(tmp_project, add_shot):
    """round-W #66: a `state="confirmed"` proposal on disk is only HONESTLY
    confirmed while it is still CURRENT — the funnel reuses the director's own
    fingerprint check (never a parallel one) so a project that moved
    underneath a confirmed-but-now-stale proposal does not keep reporting
    "过审" (approved) forever."""
    p = tmp_project
    for rel, body in (("story/brief.md", BRIEF_BODY), ("story/synopsis.md", SYNOPSIS_BODY),
                      ("story/beats.md", BEATS_BODY), ("story/script.md", SCRIPT_BODY)):
        _write(p, rel, body)
    add_shot(p, "S001")

    prop = d.propose(p, [{"type": "snapshot"}], actor="human")
    d.confirm(p, prop.id, actor="human")
    assert _current(p) == "produce"  # plan done: freshly confirmed, fingerprint matches

    # the project moves underneath the confirmed proposal: a NEW shot changes
    # state_fingerprint's per-shot spec-hash input, without anyone re-proposing.
    add_shot(p, "S002")
    status = funnel.funnel_status(p)
    plan_stage = next(s for s in status["stages"] if s["id"] == "plan")
    assert plan_stage["state"] == "current"  # no longer "done"
    assert "待更新" in plan_stage["evidence"]
    assert _current(p) == "plan"


def test_plan_stays_done_for_executing_or_done_proposal_even_if_stale_now(
    tmp_project, add_shot
):
    """round-W #66: only a merely-`confirmed` (not yet executed) proposal is
    re-checked for currency — `executing`/`done` are past tense and already
    passed this exact check at the moment they transitioned, so a project
    change AFTER a plan executed must not retroactively un-complete the plan
    stage."""
    p = tmp_project
    for rel, body in (("story/brief.md", BRIEF_BODY), ("story/synopsis.md", SYNOPSIS_BODY),
                      ("story/beats.md", BEATS_BODY), ("story/script.md", SCRIPT_BODY)):
        _write(p, rel, body)
    add_shot(p, "S001")

    # a git-independent action (this fixture's project has no .git) so
    # execute() can actually reach the "done" state.
    action = {"type": "packaging", "op": "apply", "patch": {"cover": {"frame_ms": 500}}}
    prop = d.propose(p, [action], actor="human")
    d.confirm(p, prop.id, actor="human")
    d.execute(p, prop.id, actor="human")
    from manju.build.director import load_proposal
    assert load_proposal(p, prop.id).state == "done"

    add_shot(p, "S002")  # the project moves on AFTER execution
    status = funnel.funnel_status(p)
    plan_stage = next(s for s in status["stages"] if s["id"] == "plan")
    assert plan_stage["state"] == "done"


def test_produce_not_done_when_final_is_stale(tmp_project, add_shot):
    """round-W #66: produce done requires the newest final to be up_to_date
    per the export-center freshness verdict, not merely "a final file
    exists" — the exact bug being fixed."""
    p = tmp_project
    for rel, body in (("story/brief.md", BRIEF_BODY), ("story/synopsis.md", SYNOPSIS_BODY),
                      ("story/beats.md", BEATS_BODY), ("story/script.md", SCRIPT_BODY)):
        _write(p, rel, body)
    add_shot(p, "S001")
    prop = d.propose(p, [{"type": "snapshot", "label": "cp"}], actor="human")
    d.confirm(p, prop.id, actor="human")
    assert _current(p) == "produce"

    _ready_for_final(p, "S001")
    _fake_final(p)  # a REAL, matching content key
    assert funnel.funnel_status(p)["complete"] is True

    # the spec moves on WITHOUT a rebuild: the final now predates current specs.
    p.update_shot_raw(
        "S001", lambda d: d.setdefault("dialogue", {}).__setitem__(
            "text", "完全不同的一句新台词,足以改变编译结果与内容键。"
        ),
    )
    status = funnel.funnel_status(p)
    produce_stage = next(s for s in status["stages"] if s["id"] == "produce")
    assert produce_stage["state"] == "current"  # no longer "done"
    assert "构建产物已过期" in produce_stage["evidence"]
    assert status["complete"] is False


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
    assert data["stages"][0]["cn"] == "故事与结尾"


def test_create_checklist_human(in_project):
    result = runner.invoke(app, ["create"])
    assert result.exit_code == 0, result.output
    assert "创作漏斗" in result.output
    assert "故事与结尾" in result.output and "下一步" in result.output


def test_funnel_uses_narrative_neutral_contract_and_proof_terms(tmp_project):
    labels = [stage.cn for stage in funnel.STAGES]
    joined = " ".join(labels + [stage.next_action for stage in funnel.STAGES])
    assert "SceneContract" in joined
    assert "ShotContract" in joined
    assert "Animatic" in joined
    assert "Proof Shot" in joined
    assert "Proof Scene" in joined
    assert "candidate" in joined
    assert "Hook→Value→Payoff→CTA" not in joined
    assert "Hook(0-3s" not in funnel.SCAFFOLDS["beats"][1]


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
