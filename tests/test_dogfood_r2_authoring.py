"""Story buffer safety through real HTTP and the real create-page JavaScript."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from manju.core.events import tail_events
from manju.core.hashing import hash_text
from manju.gui import create_page
from manju.gui.server import create_server
from tests.test_dogfood_r2_finishing import browser_page, mount  # noqa: F401


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()
    thread.join(timeout=5)


def post(gui, body):
    req = urllib.request.Request(f"http://127.0.0.1:{gui.port}/api/create/save",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-Manju-Token": gui.token})
    try:
        response = urllib.request.urlopen(req, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        return response.status, json.loads(response.read())


def test_story_stale_buffer_never_overwrites_external_edit(gui):
    path = gui.project.root / "story/brief.md"
    original = path.read_text(encoding="utf-8")
    path.write_text("外部助手已写入的新版本\n", encoding="utf-8")
    before_events = tail_events(gui.project.root, 100)
    status, data = post(gui, {"stage": "brief", "text": "旧窗口内容", "expected_rev": hash_text(original)})
    assert status == 409, data
    assert data["current"] == "外部助手已写入的新版本\n"
    assert path.read_text(encoding="utf-8") == data["current"]
    assert tail_events(gui.project.root, 100) == before_events


def test_story_missing_snapshot_cannot_replace_new_file(gui):
    path = gui.project.root / "story/synopsis.md"
    assert not path.exists()
    path.write_text("另一个窗口先创建了此文件\n", encoding="utf-8")
    status, data = post(gui, {"stage": "synopsis", "text": "迟到的草稿", "expected_rev": ""})
    assert status == 409, data
    assert path.read_text(encoding="utf-8") == "另一个窗口先创建了此文件\n"


@pytest.mark.parametrize("rev", [0, False, [], {}])
def test_story_malformed_revision_fails_closed(gui, rev):
    path = gui.project.root / "story/brief.md"
    before = path.read_bytes()
    status, data = post(gui, {"stage": "brief", "text": "must not write", "expected_rev": rev})
    assert status == 400, data
    assert path.read_bytes() == before


def test_story_success_returns_next_revision_and_drafts_remain_editable(gui):
    # Incomplete story material is a legitimate draft, not a schema failure.
    status, first = post(gui, {"stage": "synopsis", "text": "未完成草稿", "expected_rev": ""})
    assert status == 200, first
    assert first["created"] is True
    assert first["rev"] == hash_text("未完成草稿\n")
    status, second = post(gui, {"stage": "synopsis", "text": "继续写", "expected_rev": first["rev"]})
    assert status == 200, second
    assert second["rev"] == hash_text("继续写\n")


def test_typing_while_save_is_in_flight_is_not_marked_saved(tmp_project, browser_page):
    page = browser_page
    mount(page, create_page.render_create(tmp_project, "test-token"), "")
    page.evaluate("""() => {
        window.__posted = null;
        window.post = (url, body) => { window.__posted = body; return new Promise(r => window.__complete = r); };
        window.__timers = [];
        window.setTimeout = fn => {window.__timers.push(fn); return window.__timers.length;};
    }""")
    page.add_script_tag(content=create_page.render_create_js())
    ta = page.locator('.cw-text[data-stage="brief"]')
    ta.fill("这部分已提交")
    page.locator('[data-act=save][data-stage=brief]').click()
    assert page.evaluate("window.__posted.text") == "这部分已提交"
    assert page.evaluate("window.__posted.expected_rev") == hash_text(
        (tmp_project.root / "story/brief.md").read_text(encoding="utf-8"))
    ta.fill("这部分已提交，还在继续写，不可丢失")
    page.evaluate("window.__complete({status:200,data:{ok:true,rev:'next-revision'}})")
    page.wait_for_timeout(50)
    assert not page.locator('[data-act=save][data-stage=brief]').is_disabled()
    assert ta.input_value() == "这部分已提交，还在继续写，不可丢失"
    assert ta.evaluate("el => el.defaultValue") == "这部分已提交"
    assert ta.get_attribute("data-rev") == "next-revision"
    assert page.locator('.cw-editor[data-stage=brief] .cw-save-state').inner_text() == "未保存"


def test_story_save_respects_cross_process_build_lock(gui):
    from manju.runtime.buildlock import build_lock
    path = gui.project.root / "story/brief.md"
    before = path.read_text(encoding="utf-8")
    with build_lock(gui.project.root, actor="test-other-process"):
        status, data = post(gui, {"stage": "brief", "text": "blocked", "expected_rev": hash_text(before)})
    assert status == 409, data
    assert path.read_text(encoding="utf-8") == before


def test_story_unreadable_file_is_never_overwritten(gui):
    path = gui.project.root / "story/brief.md"
    before = "不可丢失的旧编码".encode("gbk")
    path.write_bytes(before)
    status, data = post(gui, {"stage": "brief", "text": "replacement", "expected_rev": ""})
    assert status == 409, data
    assert path.read_bytes() == before


def test_two_concurrent_story_saves_only_one_snapshot_wins(gui):
    from concurrent.futures import ThreadPoolExecutor
    path = gui.project.root / "story/brief.md"
    rev = hash_text(path.read_text(encoding="utf-8"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda text: post(gui, {
            "stage": "brief", "text": text, "expected_rev": rev,
        }), ["窗口甲的新稿", "窗口乙的新稿"]))
    assert sorted(status for status, _ in results) == [200, 409]
    conflict = next(data for status, data in results if status == 409)
    assert path.read_text(encoding="utf-8") == conflict["current"]


def test_crlf_and_chinese_snapshot_has_no_false_conflict(gui):
    path = gui.project.root / "story/brief.md"
    path.write_bytes("第一行\r\n第二行\r\n".encode())
    revision = hash_text(path.read_text(encoding="utf-8"))
    status, data = post(gui, {"stage": "brief", "text": "新一行", "expected_rev": revision})
    assert status == 200, data
    assert data["rev"] == hash_text("新一行\n")


@pytest.mark.parametrize("scenario", ["after_response", "hidden_stage", "conflict", "network_error"])
def test_story_save_never_discards_other_or_later_drafts(tmp_project, browser_page, scenario):
    page = browser_page
    if scenario == "hidden_stage":
        # A completed brief unlocks the next editor, exactly as a real project
        # does. Pending stages are deliberately not rendered by the product.
        (tmp_project.root / "story/brief.md").write_text(
            "# 立意\n\n" + "雨夜末班车离开，检票员发现等候父亲的孩子。她重新打开门，决定送他回家。" * 4,
            encoding="utf-8")
    mount(page, create_page.render_create(tmp_project, "test-token"), "")
    page.evaluate("""() => {
      window.__timers = [];
      window.setTimeout = fn => {window.__timers.push(fn); return window.__timers.length;};
      window.post = () => new Promise((resolve, reject) => {window.__resolve=resolve;window.__reject=reject;});
    }""")
    page.add_script_tag(content=create_page.render_create_js())
    ta = page.locator('.cw-text[data-stage=brief]')
    original = ta.evaluate("e => e.defaultValue")
    original_rev = ta.get_attribute("data-rev")
    ta.fill("提交的草稿")
    if scenario == "hidden_stage":
        other = page.locator('.cw-text[data-stage=synopsis]')
        other.fill("另一阶段还没保存")
        # The draft persists when the user explicitly chooses to switch.
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator('[data-act=edit][data-stage=brief]').first.click()
    page.locator('[data-act=save][data-stage=brief]').click()
    if scenario == "conflict":
        page.evaluate("window.__resolve({status:409,data:{error:'stale',current:'another writer'}})")
    elif scenario == "network_error":
        page.evaluate("window.__reject(new Error('offline'))")
    else:
        page.evaluate("window.__resolve({status:200,data:{ok:true,rev:'next'}})")
    page.wait_for_timeout(40)
    if scenario == "after_response":
        ta.fill("响应后继续输入的文字")
    page.evaluate("window.__timers.splice(0).forEach(fn => fn())")
    page.wait_for_timeout(80)
    assert ta.count() == 1, "Automatic reload must not discard any newer or other-stage draft"
    assert not page.locator('[data-act=save][data-stage=brief]').is_disabled()
    if scenario == "after_response":
        assert ta.input_value() == "响应后继续输入的文字"
        assert ta.evaluate("e => e.defaultValue") == "提交的草稿"
    elif scenario == "hidden_stage":
        assert other.input_value() == "另一阶段还没保存"
    else:
        assert ta.input_value() == "提交的草稿"
        assert ta.evaluate("e => e.defaultValue") == original
        assert ta.get_attribute("data-rev") == original_rev
