"""Don't tell a newcomer to run a command that will refuse.

Found by walking the funnel as a new user rather than by reading it. On a
brand-new project the very first instruction was:

    【立意】编辑 story/brief.md 写清一句话立意/…(manju create brief 生成模板)

and running the suggested command answers:

    story/brief.md 已存在 — 不覆盖人写的内容;确要重置用 --force

The refusal is correct — `manju new` scaffolds brief.md and script.md, and the
engine must never overwrite the owner's text. The ADVICE was wrong, and it was
the first thing a new project said. The script stage never carried the clause,
so the two siblings also disagreed about the same situation.

The clause is now dropped exactly when the artifact already exists, and kept
when it does not (synopsis and beats are genuinely absent on a new project, so
generating their template IS the next move).
"""

from __future__ import annotations

from typer.testing import CliRunner

from manju.build.funnel import funnel_status
from manju.cli import app

runner = CliRunner()

LONG_BRIEF = (
    "末班公交上,一个女孩发现司机三年前就死了,而车上其他乘客似乎都知道。"
    "给谁看:年轻观众。时长:60 秒。平台:竖屏。"
    "为什么抓人:回家路变成回不去的路,日常里的裂缝最吓人。"
)


def _stage(project, sid: str) -> dict:
    rep = funnel_status(project)
    return next(s for s in rep["stages"] if s["id"] == sid)


def test_a_scaffolded_file_is_not_told_to_regenerate(tmp_project) -> None:
    """brief.md exists from `manju new`, so the template clause must be gone."""
    (tmp_project.root / "story").mkdir(exist_ok=True)
    (tmp_project.root / "story" / "brief.md").write_text("# x\n", encoding="utf-8")
    action = _stage(tmp_project, "brief")["next_action"]
    assert "生成模板" not in action, action
    assert "编辑 story/brief.md" in action, action


def test_an_absent_file_still_offers_the_template(tmp_project) -> None:
    """synopsis.md does NOT exist on a new project — generating it is the move."""
    (tmp_project.root / "story").mkdir(exist_ok=True)
    syn = tmp_project.root / "story" / "synopsis.md"
    if syn.exists():
        syn.unlink()
    action = _stage(tmp_project, "synopsis")["next_action"]
    assert "manju create synopsis 生成模板" in action, action


def test_the_rest_of_the_sentence_survives(tmp_project) -> None:
    """Only the clause goes — the instruction and the skill pointer stay, with
    no doubled spaces or orphaned punctuation left behind."""
    (tmp_project.root / "story").mkdir(exist_ok=True)
    (tmp_project.root / "story" / "brief.md").write_text("# x\n", encoding="utf-8")
    action = _stage(tmp_project, "brief")["next_action"]
    assert "manju skills show creation-funnel" in action, action
    assert "  " not in action and "()" not in action and "()" not in action, action


def test_the_advice_and_the_command_agree_end_to_end(
        tmp_project, monkeypatch) -> None:
    """The real defect, stated as behaviour: whatever `manju create` tells the
    owner to run must not immediately refuse."""
    (tmp_project.root / "story").mkdir(exist_ok=True)
    (tmp_project.root / "story" / "brief.md").write_text("# x\n", encoding="utf-8")
    monkeypatch.chdir(tmp_project.root)

    out = runner.invoke(app, ["create"]).output
    assert "manju create brief" not in out, (
        "still recommending a command that will answer 已存在")

    res = runner.invoke(app, ["create", "brief"])
    assert "已存在" in res.output, (
        "premise gone: `manju create brief` no longer refuses, so this test "
        "would pass without proving anything")


def test_brief_and_script_now_agree(tmp_project) -> None:
    """Both are scaffolded by `manju new`; they used to advise differently."""
    (tmp_project.root / "story").mkdir(exist_ok=True)
    for name in ("brief.md", "script.md"):
        (tmp_project.root / "story" / name).write_text("# x\n", encoding="utf-8")
    for sid in ("brief", "script"):
        assert "生成模板" not in _stage(tmp_project, sid)["next_action"], sid


def test_once_the_brief_is_written_the_funnel_moves_on(tmp_project) -> None:
    """Guard the guard: the brief stage must be reachable and completable, or
    the assertions above are about a stage nobody sees."""
    (tmp_project.root / "story").mkdir(exist_ok=True)
    (tmp_project.root / "story" / "brief.md").write_text(LONG_BRIEF, encoding="utf-8")
    assert _stage(tmp_project, "brief")["state"] == "done"


# ------------------------------------- the same defect at the two later stages


def test_the_plan_stage_gives_a_runnable_command(tmp_project) -> None:
    """`manju director propose` alone answers "pass exactly one of
    --from-file / --actions-json" and exits 1 — the third stage in a row whose
    advice refused when followed literally. And the action shape is not
    guessable: {"op": "build"} is rejected as "unknown action type None"."""
    action = _stage(tmp_project, "plan")["next_action"]
    assert "--actions-json" in action or "--from-file" in action, action
    assert '"type"' in action, f"the action key is not shown: {action}"
    assert '"op"' not in action, "shows the key that gets rejected"


def test_the_plan_advice_matches_the_real_whitelist(tmp_project) -> None:
    """Tie the printed example to the engine: the action type it demonstrates
    must be one the director actually accepts, or the advice rots silently."""
    import json
    import re as _re

    action = _stage(tmp_project, "plan")["next_action"]
    m = _re.search(r"(\[\{.*?\}\])", action)
    assert m, f"no JSON example in the advice: {action}"
    parsed = json.loads(m.group(1))
    assert parsed and isinstance(parsed, list)
    kind = parsed[0].get("type")

    from manju.build.director import ACTION_TYPES  # noqa: PLC0415

    assert kind in set(ACTION_TYPES), (
        f"the advice demonstrates {kind!r}, not in {sorted(ACTION_TYPES)}")


def test_the_storyboard_stage_no_longer_leans_on_board(tmp_project) -> None:
    """`manju board scene` with no shots answers "has no shots — nothing to
    board" and exits 1, so offering it as THE helper at the step that creates
    shots was backwards. It may still be mentioned as an after-step."""
    action = _stage(tmp_project, "storyboard")["next_action"]
    assert "manju gui" in action, action
    assert "shots/index.yaml" in action, "the by-hand route omits the index"
    if "manju board" in action:
        assert "有镜头之后" in action or "之后" in action, (
            "board is still presented as the way to CREATE shots")


def test_the_finish_line_does_not_recommend_what_was_just_done(
        tmp_project, add_shot, monkeypatch) -> None:
    """The funnel reaches 7/7 only once a final exists, so "可 manju build
    出片" told the owner to redo the step they had just completed."""
    monkeypatch.chdir(tmp_project.root)
    out = runner.invoke(app, ["create"]).output
    if "全部完成" in out:
        tail = out[out.index("全部完成"):]
        assert "manju build" not in tail, tail
        for nxt in ("manju qc", "manju package", "manju export"):
            assert nxt in tail, f"{nxt} missing from the post-funnel pointer"
