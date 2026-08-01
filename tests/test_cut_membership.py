"""「移出本刀」——把镜头移出成片,并随时放回(2026-08-01)。

返工自由度审计留了一条待店主拍板:**GUI 无法把镜头移出成片**。CLI 能(手改
`shots/index.yaml` 的 order),GUI 不能,因为 `/api/index` 的写入器
`permute_index` 只接受**排列**——而那条不变量正是两个标签页同时改序时的安全
守卫(GUI-INDEX-P1-001:没有它,第二个标签页的 ↑/↓ 会静默回滚第一个的改序)。

店主授权后定的语义(按"最不容易后悔"选):

- **移出 ≠ 删除。** 镜头文件永远留在盘上,移出只改 index order —— 与 CLI
  既有语义完全一致(`check` 早就会说"它被 EXCLUDE 了,这样加回来")。
- **随时放回。** 单向门在返工里是不可接受的。
- **旧的排列守卫一动不动。** 新写入器 `set_cut_order` 自带守卫;
  `permute_index` 仍然只收排列,所以没有 CAS 令牌的老页面**依然只能改序、
  不可能丢镜头**;改变成员(移出/放回)必须带令牌。
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

runner = CliRunner()


def _three(project, add_shot):
    for sid in ("S001", "S002", "S003"):
        add_shot(project, sid)
    return ["S001", "S002", "S003"]


# ------------------------------------------------------------- core writer


def test_set_cut_order_drops_a_shot_without_touching_its_file(tmp_project, add_shot):
    from manju.core.writes import set_cut_order

    _three(tmp_project, add_shot)
    path = tmp_project.shots_dir / "S002.yaml"
    before = path.read_bytes()

    applied = set_cut_order(tmp_project, ["S001", "S003"], actor="human", via="test")

    assert applied == ["S001", "S003"]
    assert tmp_project.load_index().order == ["S001", "S003"]
    assert path.exists() and path.read_bytes() == before, "移出绝不能碰镜头文件"


def test_a_dropped_shot_goes_back(tmp_project, add_shot):
    from manju.core.writes import set_cut_order

    _three(tmp_project, add_shot)
    set_cut_order(tmp_project, ["S001", "S003"])
    set_cut_order(tmp_project, ["S001", "S002", "S003"])
    assert tmp_project.load_index().order == ["S001", "S002", "S003"]


def test_set_cut_order_refuses_ids_that_are_not_shots(tmp_project, add_shot):
    from manju.core.writes import WriteRejected, set_cut_order

    _three(tmp_project, add_shot)
    with pytest.raises(WriteRejected) as exc:
        set_cut_order(tmp_project, ["S001", "S999"])
    assert "S999" in str(exc.value)


def test_set_cut_order_refuses_duplicates(tmp_project, add_shot):
    from manju.core.writes import WriteRejected, set_cut_order

    _three(tmp_project, add_shot)
    with pytest.raises(WriteRejected):
        set_cut_order(tmp_project, ["S001", "S001"])


def test_set_cut_order_records_what_moved(tmp_project, add_shot):
    from manju.core.events import tail_events
    from manju.core.writes import set_cut_order

    _three(tmp_project, add_shot)
    set_cut_order(tmp_project, ["S001", "S003"], actor="human", via="test")
    rows = tail_events(tmp_project.root, 20)
    cut = [r for r in rows if r.get("action") == "cut"]
    assert cut, "移出必须留痕"
    detail = cut[-1].get("detail") or {}
    assert detail.get("dropped") == ["S002"]
    assert detail.get("order") == ["S001", "S003"]


def test_permute_index_still_refuses_a_subset(tmp_project, add_shot):
    """老的排列守卫一动不动 —— 没有令牌的老页面依然丢不了镜头。"""
    from manju.core.writes import WriteRejected, permute_index

    _three(tmp_project, add_shot)
    with pytest.raises(WriteRejected):
        permute_index(tmp_project, ["S001", "S003"])


# --------------------------------------------------------------------- CLI


def test_cut_lists_the_cut_and_what_is_out(tmp_project, add_shot, monkeypatch):
    from manju.cli import app

    _three(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)
    runner.invoke(app, ["cut", "drop", "S002"])
    result = runner.invoke(app, ["cut"])
    assert result.exit_code == 0, result.output
    assert "S001" in result.output and "S003" in result.output
    assert "S002" in result.output          # the dropped one is still NAMED…
    assert "放回" in result.output           # …and the way back is on screen


def test_cut_drop_and_restore_round_trip(tmp_project, add_shot, monkeypatch):
    from manju.cli import app

    _three(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)

    dropped = runner.invoke(app, ["cut", "drop", "S002"])
    assert dropped.exit_code == 0, dropped.output
    assert tmp_project.load_index().order == ["S001", "S003"]
    assert (tmp_project.shots_dir / "S002.yaml").exists()

    back = runner.invoke(app, ["cut", "restore", "S002"])
    assert back.exit_code == 0, back.output
    assert "S002" in tmp_project.load_index().order


def test_cut_drop_is_forgiving_about_a_shot_already_out(tmp_project, add_shot, monkeypatch):
    """返工里最不该被骂的就是重复操作 —— 已经移出的再移一次是 no-op,不是错误。"""
    from manju.cli import app

    _three(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)
    runner.invoke(app, ["cut", "drop", "S002"])
    again = runner.invoke(app, ["cut", "drop", "S002"])
    assert again.exit_code == 0, again.output
    assert "本来就不在" in again.output or "已不在" in again.output


def test_cut_drop_rejects_an_unknown_shot(tmp_project, add_shot, monkeypatch):
    from manju.cli import app

    _three(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["cut", "drop", "S999"])
    assert result.exit_code != 0
    assert "S999" in result.output


def test_cut_json_is_machine_readable(tmp_project, add_shot, monkeypatch):
    from manju.cli import app

    _three(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["cut", "drop", "S002", "--json"])
    payload = json.loads(result.output)
    assert payload["order"] == ["S001", "S003"]
    assert payload["dropped"] == ["S002"]
    assert payload["out_of_cut"] == ["S002"]


def test_cut_drop_accepts_the_shorthand(tmp_project, add_shot, monkeypatch):
    """s2 / 2 → S002,与 select/redo 同一套解析(DECISIONS #47)。"""
    from manju.cli import app

    _three(tmp_project, add_shot)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["cut", "drop", "s2"])
    assert result.exit_code == 0, result.output
    assert tmp_project.load_index().order == ["S001", "S003"]


# --------------------------------------------------------------------- GUI


def test_gui_index_accepts_a_subset_only_with_the_cas_token(tmp_project, add_shot):
    """成员变更必须带令牌;没有令牌的老页面只能改序 —— 守卫不退让。"""
    import inspect

    from manju.gui import server as gui_server

    src = inspect.getsource(gui_server._Handler._act_index)
    assert "set_cut_order" in src, "GUI 仍无法移出镜头"
    assert "expected_rev" in src
    # the subset path must be gated on the token, not silently allowed
    assert "is_subset" in src
    gate = src[src.index("is_subset"):]
    assert "expected_rev is None" in gate, "成员变更没有被令牌把住"


def test_the_edit_strip_offers_drop_and_a_way_back(tmp_project, add_shot):
    from manju.core.writes import set_cut_order
    from manju.gui.edit import render_edit

    _three(tmp_project, add_shot)
    html = render_edit(tmp_project, "tok", {})
    assert "ed-drop" in html, "剪辑台没有「移出本刀」按钮"
    assert "移出" in html

    set_cut_order(tmp_project, ["S001", "S003"])
    html = render_edit(tmp_project, "tok", {})
    assert "ed-restore" in html, "被移出的镜头没有「放回」入口 —— 那就是单向门"
    assert "不在本刀里" in html


def test_the_strip_renders_the_cut_not_every_file_on_disk(tmp_project, add_shot):
    """真 bug(本波连带修):剪辑台画的是 `shot_ids()`(含盘上多余镜头),
    于是被移出的镜头照样显示在主轨道上,而 currentOrder() 会把它一起回传 ——
    在 GUI 里点一下 ▲ 就把它**静默塞回成片**,把店主刚做的移出撤销掉。"""
    from manju.core.writes import set_cut_order
    from manju.gui.edit import render_edit

    _three(tmp_project, add_shot)
    set_cut_order(tmp_project, ["S001", "S003"])
    html = render_edit(tmp_project, "tok", {})
    strip = html[html.index('id="ed-strip"'):html.index("ed-out-panel")]
    assert 'data-shot="S002"' not in strip, "移出的镜头仍画在主轨道上"
    for sid in ("S001", "S003"):
        assert f'data-shot="{sid}"' in strip
