"""Product Polish R1 final wave — global Help & Support and browser privacy.

The help center is deliberately read-only.  These tests pin its user-facing
contract without turning diagnostics into an implicit background action: the
payload is bounded and secret-free, every primary shell exposes one accessible
trigger, first-run/unbound servers can answer it, browser privacy headers are
central, and the real client state machine opens through both F1 and Quick Open
without invoking any mutation endpoint.
"""

from __future__ import annotations

from http.client import HTTPConnection
import json
from pathlib import Path
import subprocess
import threading

import pytest

from manju.gui import page, pages, workspace
from manju.gui.command_palette import (
    render_command_palette_css,
    render_command_palette_js,
)
from manju.gui.help_center import (
    about_payload,
    render_help_center_css,
    render_help_center_js,
)
from manju.gui.pages import GLOSSARY_HEAD, nav_html
from manju.gui.server import create_server


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _get(server, path: str) -> tuple[int, dict[str, str], bytes]:
    host, port = server.server_address[:2]
    conn = HTTPConnection(host, port, timeout=10)
    conn.request("GET", path, headers={"Accept": "text/html,application/json"})
    response = conn.getresponse()
    body = response.read()
    headers = {key.lower(): value for key, value in response.getheaders()}
    status = response.status
    conn.close()
    return status, headers, body


def _browser_document(project_name: str, payload: dict, palette: dict) -> str:
    css = "\n".join([
        page.render_css(),
        pages.render_pages_css(),
        render_command_palette_css(),
        render_help_center_css(),
    ])
    bootstrap = json.dumps({"about": payload, "palette": palette}, ensure_ascii=False)
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="manju-token" content="token">'
        f'<style>{css}</style></head>'
        '<body data-page="/review" class="mj-mode-beginner">'
        + nav_html("/review", mode="beginner", project_name=project_name)
        + '<main id="main-content" tabindex="-1"><h1>审片</h1></main>'
        + '<script>window.__MJ_TEST=' + bootstrap + ';window.__MJ_CALLS=[];'
          'window.manjuApiOptions=function(){return {}};'
          'window.requestJson=function(method,url){window.__MJ_CALLS.push([method,url]);'
          'if(url==="/api/app/about")return Promise.resolve(window.__MJ_TEST.about);'
          'if(url==="/api/command-palette")return Promise.resolve(window.__MJ_TEST.palette);'
          'return Promise.reject(new Error("unexpected "+method+" "+url));};</script>'
        + f'<script>{render_command_palette_js()}</script>'
        + f'<script>{render_help_center_js()}</script>'
        + '</body></html>'
    )


def test_about_payload_is_bounded_secret_free_and_useful(monkeypatch, tmp_project):
    monkeypatch.setenv("MANJU_EXECUTION_MODE", "strict_zero_cost")
    secret = "DO-NOT-LEAK-wave15-super-secret"
    monkeypatch.setenv("DASHSCOPE_API_KEY", secret)
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setenv("SERPER_API_KEY", secret)

    payload = about_payload(tmp_project, readonly=True, app_mode=True)
    raw = json.dumps(payload, ensure_ascii=False)

    assert payload["schema"] == "manju.help-center/v1"
    assert payload["product"]["name"] == "Manju One"
    assert payload["product"]["version"]
    assert payload["execution"]["status"] == "strict"
    assert payload["execution"]["label"] == "本地安全"
    assert payload["project"] == {
        "bound": True,
        "name": tmp_project.load_config().name,
        "readonly": True,
    }
    assert payload["application"] == {
        "app_mode": True,
        "local_server": True,
        "telemetry": "none",
    }
    assert payload["links"]["doctor"] == "/doctor"
    assert payload["commands"]["support_bundle"] == "manju support-bundle"
    assert str(tmp_project.root) not in raw
    assert secret not in raw
    assert "DASHSCOPE_API_KEY" not in raw
    assert "OPENAI_API_KEY" not in raw
    assert "SERPER_API_KEY" not in raw

    unbound = about_payload(None)
    assert unbound["project"] == {"bound": False, "name": None, "readonly": False}
    assert unbound["links"]["doctor"] is None


def test_primary_chrome_and_workspace_expose_one_help_trigger(tmp_project):
    bound = nav_html("/review", mode="beginner", project_name="测试项目")
    picker = workspace.render_picker_page("token", bound=tmp_project, presets=[])

    for document in (bound, picker):
        assert document.count('id="mj-help-center-btn"') == 1
        assert 'aria-controls="mj-help-center"' in document
        assert 'aria-expanded="false"' in document
        assert 'aria-keyshortcuts="F1"' in document
        assert "帮助与支持（F1）" in document
    assert GLOSSARY_HEAD.count('/help-center.css') == 1
    assert GLOSSARY_HEAD.count('/help-center.js') == 1
    assert picker.count('/help-center.css') == 1
    assert picker.count('/help-center.js') == 1


def test_help_assets_about_endpoint_and_html_privacy_headers_work_bound_and_unbound(
    monkeypatch, tmp_project, tmp_path
):
    monkeypatch.setenv("MANJU_EXECUTION_MODE", "strict_zero_cost")
    for project in (tmp_project, None):
        server = create_server(project, host="127.0.0.1", port=0, app_mode=True)
        thread = _serve(server)
        try:
            status, headers, body = _get(server, "/api/app/about")
            assert status == 200
            payload = json.loads(body.decode("utf-8"))
            assert payload["project"]["bound"] is (project is not None)
            assert payload["application"]["app_mode"] is True

            status, _headers, js = _get(server, "/help-center.js")
            assert status == 200
            js_path = tmp_path / ("help-bound.js" if project is not None else "help-unbound.js")
            js_path.write_bytes(js)
            subprocess.run(["node", "--check", str(js_path)], check=True, capture_output=True)

            status, _headers, css = _get(server, "/help-center.css")
            assert status == 200
            assert b".mj-help-center-dialog" in css
            assert b"prefers-reduced-motion" in css

            status, html_headers, document = _get(server, "/")
            assert status == 200
            assert b'id="mj-help-center-btn"' in document
            assert html_headers["cross-origin-resource-policy"] == "same-origin"
            assert html_headers["x-permitted-cross-domain-policies"] == "none"
            permissions = html_headers["permissions-policy"]
            for denied in ("camera=()", "microphone=()", "geolocation=()", "payment=()", "usb=()"):
                assert denied in permissions
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


@pytest.mark.skipif(not Path("/usr/bin/chromium").exists(), reason="Chromium unavailable")
def test_help_center_real_client_opens_with_f1_and_quick_open_without_mutations(
    monkeypatch, tmp_project
):
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("MANJU_EXECUTION_MODE", "strict_zero_cost")
    payload = about_payload(tmp_project, readonly=False, app_mode=True)
    palette = {
        "version": 2,
        "project": {"bound": True, "name": tmp_project.load_config().name, "token": "p", "shot_count": 0},
        "navigation": [],
        "shots": [],
        "recents": [],
    }
    document = _browser_document(tmp_project.load_config().name, payload, palette)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=["--no-sandbox", "--disable-gpu"],
        )
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page_obj = context.new_page()
        page_obj.set_content(document, wait_until="domcontentloaded")

        page_obj.keyboard.press("F1")
        dialog = page_obj.locator("#mj-help-center")
        assert dialog.get_attribute("open") is not None
        assert page_obj.locator("#mj-help-center-title").inner_text() == "帮助与支持"
        assert "本地安全" in dialog.inner_text()
        assert page_obj.evaluate("document.activeElement.classList.contains('mj-help-center-close')")
        assert page_obj.evaluate("document.documentElement.scrollWidth <= innerWidth")

        page_obj.keyboard.press("Escape")
        assert dialog.get_attribute("open") is None
        assert page_obj.evaluate("document.activeElement.id") == "mj-help-center-btn"

        page_obj.keyboard.press("Control+k")
        page_obj.locator("#mj-command-input").fill("帮助")
        page_obj.keyboard.press("Enter")
        assert dialog.get_attribute("open") is not None
        assert "产品遥测：无" not in dialog.inner_text()  # copy summary, not visible chrome
        calls = page_obj.evaluate("window.__MJ_CALLS")
        assert calls.count(["GET", "/api/app/about"]) == 1
        assert calls.count(["GET", "/api/command-palette"]) == 1
        assert all(method == "GET" for method, _url in calls)
        assert not any(any(token in url for token in ("build", "generate", "select", "lock", "provider")) for _method, url in calls)

        context.close()
        browser.close()
