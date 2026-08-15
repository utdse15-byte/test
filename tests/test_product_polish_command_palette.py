"""Wave 13 — global quick-open is discoverable, lazy and navigation-only."""

from __future__ import annotations

import json
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.recents import touch_recent
from manju.gui.command_palette import (
    palette_payload,
    render_command_palette_css,
    render_command_palette_js,
)
from manju.gui.pages import GLOSSARY_HEAD, nav_html, navigation_items
from manju.gui.server import create_server
from manju.gui.workspace import render_picker_page


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _get_json(server, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _get_text(server, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=10) as resp:
        return resp.status, resp.read().decode("utf-8")


def _browser_document(tmp_project, *, page: str = "/review", mode: str = "beginner") -> str:
    from manju.gui import page as app_page
    from manju.gui.pages import render_pages_css

    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="manju-token" content="token">'
        f'<style>{app_page.render_css()}\n{render_pages_css()}\n'
        f'{render_command_palette_css()}</style>'
        f'</head><body data-page="{page}" class="mj-mode-{mode}">'
        + nav_html(page, mode=mode, project_name=tmp_project.load_config().name)
        + '<main id="main-content" tabindex="-1"><h1>审片</h1></main></body></html>'
    )


def test_palette_navigation_reuses_mode_aware_page_owner():
    beginner = navigation_items("beginner")
    pro = navigation_items("pro")

    assert [row["href"] for row in beginner] == [
        "/", "/create", "/storyboard", "/director", "/lab", "/ingest",
        "/edit", "/review", "/subtitles", "/mixer", "/packaging",
        "/exports", "/library",
    ]
    assert {"/compare", "/providers", "/routing", "/doctor"}.isdisjoint(
        row["href"] for row in beginner
    )
    assert {"/compare", "/providers", "/routing", "/doctor"}.issubset(
        row["href"] for row in pro
    )
    review = next(row for row in pro if row["href"] == "/review")
    assert review["group"] == "审片"
    assert "选片" in review["keywords"]


def test_palette_payload_indexes_human_shot_text_without_writes(
    monkeypatch, tmp_project, add_shot, tmp_path
):
    add_shot(
        tmp_project,
        "S003",
        scene="雨夜便利店",
        action={"main": "她推开卷帘门", "emotion": "犹豫"},
        dialogue={"speaker": "linxia", "text": "还有人在吗？"},
        characters=["linxia", "dianzhang"],
    )
    add_shot(tmp_project, "S010", action={"main": "收音机突然停止"})
    # A hand-edited broken shot remains discoverable by id instead of taking
    # the entire quick-open index down.
    index = tmp_project.load_index()
    index.order.append("S099")
    tmp_project.save_index(index)
    tmp_project.shot_path("S099").write_text("- not-a-mapping\n", encoding="utf-8")

    other = Project.create(tmp_path / "另一部片.manju", name="另一部片", git_init=False)
    touch_recent(other)
    touch_recent(tmp_project)
    before = _tree_bytes(tmp_project.root)

    def forbidden(*args, **kwargs):
        raise AssertionError(
            "palette crossed a media, timeline, credential or typed-shot boundary"
        )

    import importlib

    monkeypatch.setattr(tmp_project, "load_shot", forbidden)
    monkeypatch.setattr(importlib.import_module("manju.media.probe"), "probe", forbidden)
    monkeypatch.setattr(
        importlib.import_module("manju.timeline.compiler"), "compile_timeline", forbidden
    )
    monkeypatch.setattr(
        importlib.import_module("manju.providers.zero_cost"),
        "credential_presence",
        forbidden,
    )
    payload = palette_payload(tmp_project, project_token="project-token", mode="beginner")

    assert payload["version"] == 2
    assert payload["project"] == {
        "bound": True,
        "name": tmp_project.load_config().name,
        "token": "project-token",
        "shot_count": 3,
    }
    by_id = {row["id"]: row for row in payload["shots"]}
    assert by_id["S003"] == {
        "id": "S003",
        "scene": "雨夜便利店",
        "action": "她推开卷帘门",
        "dialogue": "还有人在吗？",
        "characters": ["linxia", "dianzhang"],
        "available": True,
    }
    assert by_id["S099"]["available"] is False
    assert payload["recents"][0]["current"] is True
    assert any(row["name"] == "另一部片" for row in payload["recents"])
    assert all(row["href"] != "/providers" for row in payload["navigation"])
    assert _tree_bytes(tmp_project.root) == before


def test_palette_payload_unbound_is_still_useful_and_secret_free(monkeypatch):
    payload = palette_payload(None, project_token="", mode="beginner")

    assert payload["project"] == {
        "bound": False, "name": "", "token": "", "shot_count": 0
    }
    assert payload["shots"] == []
    assert payload["navigation"]
    raw = json.dumps(payload, ensure_ascii=False)
    assert "DASHSCOPE_API_KEY" not in raw
    assert "SERPER_API_KEY" not in raw


def test_shared_chrome_and_workspace_expose_one_accessible_palette_trigger(tmp_project):
    nav = nav_html("/review", mode="beginner", project_name="长片")
    workspace = render_picker_page("token", bound=tmp_project, presets=[])

    for html in (nav, workspace):
        assert html.count('id="mj-command-btn"') == 1
        assert 'aria-controls="mj-command-palette"' in html
        assert 'aria-expanded="false"' in html
        assert 'aria-keyshortcuts="Control+K Meta+K"' in html
        assert "Ctrl K" in html
    assert GLOSSARY_HEAD.count('/command-palette.css') == 1
    assert GLOSSARY_HEAD.count('/command-palette.js') == 1
    assert workspace.count('/command-palette.css') == 1
    assert workspace.count('/command-palette.js') == 1


def test_palette_assets_and_endpoint_work_bound_and_unbound(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001", action={"main": "抬头看向门外"})
    for project in (tmp_project, None):
        server = create_server(project, host="127.0.0.1", port=0)
        thread = _serve(server)
        try:
            status, payload = _get_json(server, "/api/command-palette")
            assert status == 200
            assert payload["version"] == 2
            assert payload["project"]["bound"] is (project is not None)
            if project is not None:
                assert payload["shots"][0]["action"] == "抬头看向门外"
            else:
                assert payload["shots"] == []

            status, js = _get_text(server, "/command-palette.js")
            assert status == 200
            js_path = tmp_path / "command-palette.js"
            js_path.write_text(js, encoding="utf-8")
            subprocess.run(["node", "--check", str(js_path)], check=True, capture_output=True)

            status, css = _get_text(server, "/command-palette.css")
            assert status == 200
            assert ".mj-command-dialog" in css
            assert "prefers-reduced-motion" in css
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@pytest.mark.skipif(not Path("/usr/bin/chromium").exists(), reason="Chromium unavailable")
def test_palette_real_browser_searches_shot_content_and_never_executes_dangerous_actions(
    tmp_project, add_shot
):
    from playwright.sync_api import sync_playwright

    add_shot(tmp_project, "S001", action={"main": "收音机突然停止"})
    add_shot(
        tmp_project,
        "S003",
        scene="雨夜便利店",
        action={"main": "她推开卷帘门"},
        dialogue={"speaker": "linxia", "text": "还有人在吗？"},
    )
    add_shot(tmp_project, "S010", action={"main": "灯光熄灭"})
    payload = palette_payload(tmp_project, project_token="project-token", mode="beginner")
    payload["recents"] = [
        {"name": "另一部片", "path": r"D:\\Films\\另一部片.manju", "current": False,
         "pinned": False, "last_opened": None}
    ]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/chromium", headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.set_content(_browser_document(tmp_project), wait_until="domcontentloaded")
        page.evaluate(
            """(payload) => {
              window.__requests = [];
              window.__navigated = [];
              window.__openedProject = null;
              window.__MJ_COMMAND_NAVIGATE = href => window.__navigated.push(href);
              window.manjuApiOptions = opts => opts || {};
              window.requestJson = (method, url, body) => {
                window.__requests.push({method, url, body});
                if (method === 'GET' && url === '/api/command-palette') return Promise.resolve(payload);
                if (method === 'POST' && url === '/api/workspace/open') {
                  window.__openedProject = body.path;
                  return Promise.resolve({ok:true, next_action:{kind:'reload_current'}});
                }
                return Promise.reject(new Error('unexpected request ' + method + ' ' + url));
              };
              window.handleProjectAction = () => {};
              window.ManjuTaskCenter = {open: () => { window.__taskOpened = true; }};
            }""",
            payload,
        )
        page.add_script_tag(content=render_command_palette_js())

        # The discovery index is lazy: rendering the application shell makes no read.
        assert page.evaluate("window.__requests.length") == 0
        trigger = page.locator("#mj-command-btn")
        assert trigger.get_attribute("aria-expanded") == "false"

        page.keyboard.press("Control+k")
        page.wait_for_function("window.__requests.length === 1")
        assert trigger.get_attribute("aria-expanded") == "true"
        assert page.locator("#mj-command-input").get_attribute("role") == "combobox"
        assert page.locator("#mj-command-input").get_attribute("aria-expanded") == "true"
        assert page.evaluate("document.activeElement.id") == "mj-command-input"

        # A person can remember the action rather than the shot id.
        page.locator("#mj-command-input").fill("推开卷帘门")
        assert "打开镜头 S003" in page.locator("#mj-command-results").inner_text()
        assert "雨夜便利店" in page.locator("#mj-command-results").inner_text()
        page.keyboard.press("Enter")
        assert page.evaluate("window.__navigated.pop()") == "/lab?shot=S003"

        page.keyboard.press("Control+k")
        page.locator("#mj-command-input").fill("审片 推开卷帘门")
        page.keyboard.press("Enter")
        assert page.evaluate("window.__navigated.pop()") == "/review?shot=S003"

        page.keyboard.press("Control+k")
        page.locator("#mj-command-input").fill("另一部片")
        page.keyboard.press("Enter")
        page.wait_for_timeout(20)
        assert page.evaluate("window.__openedProject") == r"D:\\Films\\另一部片.manju"

        page.keyboard.press("Control+k")
        page.locator("#mj-command-input").fill("服务商")
        assert "没有找到匹配项" in page.locator("#mj-command-results").inner_text()
        page.keyboard.press("Escape")
        assert page.locator("#mj-command-palette").get_attribute("open") is None
        assert trigger.get_attribute("aria-expanded") == "false"
        assert page.evaluate("document.activeElement.id") == "mj-command-btn"

        requests = page.evaluate("window.__requests")
        assert any(row["url"] == "/api/command-palette" for row in requests)
        assert sum(row["url"] == "/api/workspace/open" for row in requests) == 1
        assert not any(row["url"] in {
            "/api/select", "/api/build", "/api/redo", "/api/director/run"
        } for row in requests)
        assert page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - innerWidth)"
        ) == 0

        context.close()
        browser.close()


@pytest.mark.skipif(not Path("/usr/bin/chromium").exists(), reason="Chromium unavailable")
def test_palette_failed_index_is_honest_and_static_navigation_still_works(
    tmp_project
):
    from playwright.sync_api import sync_playwright

    payload = palette_payload(tmp_project, project_token="project-token", mode="beginner")
    payload["shots"] = [{
        "id": "S777", "scene": "旧项目场景", "action": "旧项目动作",
        "dialogue": "", "characters": [], "available": True,
    }]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/chromium", headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )
        page = browser.new_page(viewport={"width": 960, "height": 760})
        page.set_content(_browser_document(tmp_project), wait_until="domcontentloaded")
        page.evaluate(
            """(payload) => {
              window.__reads = 0;
              window.__navigated = [];
              window.__MJ_COMMAND_NAVIGATE = href => window.__navigated.push(href);
              window.manjuApiOptions = opts => opts || {};
              window.requestJson = (method, url) => {
                if (method === 'GET' && url === '/api/command-palette') {
                  window.__reads += 1;
                  if (window.__reads === 1 || window.__reads === 3) {
                    return Promise.reject(new Error('offline'));
                  }
                  return Promise.resolve(payload);
                }
                return Promise.reject(new Error('unexpected request'));
              };
            }""",
            payload,
        )
        page.add_script_tag(content=render_command_palette_js())

        page.keyboard.press("Control+k")
        page.wait_for_function("window.ManjuCommandPalette.current().loadError === true")
        assert "项目索引暂时无法更新" in page.locator(
            "#mj-command-description"
        ).inner_text()
        page.locator("#mj-command-input").fill("创作")
        assert "创作" in page.locator("#mj-command-results").inner_text()
        page.keyboard.press("Enter")
        assert page.evaluate("window.__navigated.pop()") == "/create"

        # A failed read is not cached as success: the next explicit open retries.
        page.keyboard.press("Control+k")
        page.wait_for_function("window.__reads === 2")
        assert page.evaluate("window.ManjuCommandPalette.current().loadError") is False
        page.locator("#mj-command-input").fill("S777")
        assert "打开镜头 S777" in page.locator("#mj-command-results").inner_text()
        page.keyboard.press("Escape")

        # A later failed refresh clears old dynamic results instead of offering
        # a shot from the previous project snapshot. Static pages remain usable.
        page.evaluate("window.dispatchEvent(new CustomEvent('manju:jobs-changed'))")
        page.keyboard.press("Control+k")
        page.wait_for_function("window.__reads === 3")
        page.wait_for_function("window.ManjuCommandPalette.current().loadError === true")
        page.locator("#mj-command-input").fill("S777")
        assert "打开镜头 S777" not in page.locator("#mj-command-results").inner_text()
        page.locator("#mj-command-input").fill("创作")
        assert "创作" in page.locator("#mj-command-results").inner_text()
        page.keyboard.press("Escape")
        browser.close()


@pytest.mark.skipif(not Path("/usr/bin/chromium").exists(), reason="Chromium unavailable")
def test_workspace_palette_focuses_existing_safe_actions(tmp_project):
    from playwright.sync_api import sync_playwright
    from manju.gui import page as app_page
    from manju.gui.workspace import render_workspace_css

    html_doc = render_picker_page("token", bound=tmp_project, presets=[])
    css = f"{app_page.render_css()}\n{render_workspace_css()}\n{render_command_palette_css()}"
    html_doc = html_doc.replace('</head>', f'<style>{css}</style></head>')
    payload = palette_payload(tmp_project, project_token="token", mode="beginner")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/chromium", headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )
        page = browser.new_page(viewport={"width": 390, "height": 844})
        page.set_content(html_doc, wait_until="domcontentloaded")
        page.evaluate(
            """(payload) => {
              window.manjuApiOptions = opts => opts || {};
              window.requestJson = () => Promise.resolve(payload);
            }""",
            payload,
        )
        page.add_script_tag(content=render_command_palette_js())
        page.keyboard.press("Control+k")
        page.locator("#mj-command-input").fill("打开现有项目")
        page.keyboard.press("Enter")
        page.wait_for_function("document.activeElement.id === 'ws-open-path'")
        assert page.evaluate("document.activeElement.id") == "ws-open-path"
        assert page.evaluate(
            "Math.max(0, document.documentElement.scrollWidth - innerWidth)"
        ) == 0
        browser.close()
