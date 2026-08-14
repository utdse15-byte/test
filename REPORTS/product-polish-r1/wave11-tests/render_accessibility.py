from __future__ import annotations

import json
from http.client import HTTPConnection
import os
from pathlib import Path
import re
import tempfile
import threading

from playwright.sync_api import sync_playwright

from manju.core.container import Project
from manju.core.recents import touch_recent
from manju.gui.common_js import render_common_js
from manju.gui.glossary import render_glossary_css, render_glossary_js, tooltip_html
from manju.gui.page import render_css
from manju.gui.pages import _shell, render_pages_css
from manju.gui.server import create_server
from manju.gui.workspace import render_picker_page, render_workspace_css, render_workspace_js

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "REPORTS/product-polish-r1/screenshots/wave11"
OUT.mkdir(parents=True, exist_ok=True)
RESULT = ROOT / "REPORTS/product-polish-r1/wave11-tests/browser-accessibility.json"


def offline_document(document: str, css: str) -> str:
    document = re.sub(r'<link[^>]+rel="stylesheet"[^>]*>\n?', '', document)
    document = re.sub(r'<script[^>]+src="[^"]+"[^>]*></script>\n?', '', document)
    return document.replace('</head>', f'<style>{css}</style></head>')


def no_overflow(page) -> dict[str, int | bool]:
    return page.evaluate("""() => ({
      viewport: document.documentElement.clientWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
    })""")


def main() -> None:
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="manju-wave11-") as td:
        base = Path(td)
        os.environ["MANJU_RECENTS"] = str(base / "recents.json")
        current = Project.create(base / "current.manju", git_init=False)
        recent = Project.create(base / "recent.manju", git_init=False)
        touch_recent(recent)
        touch_recent(current)

        workspace_html = offline_document(
            render_picker_page("token", bound=current),
            render_css() + render_workspace_css(),
        )
        shell_html = offline_document(
            _shell(
                "无障碍验证",
                "token",
                "/providers",
                '<section class="panel"><h1>服务商</h1><p>当前使用'
                + tooltip_html("provider")
                + '。</p><button class="btn" type="button">继续</button></section>',
                current,
            ),
            render_css() + render_pages_css() + render_glossary_css(),
        )

        server = create_server(current, host="127.0.0.1", port=0)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            host, port = server.server_address[:2]
            connection = HTTPConnection(host, port, timeout=5)
            connection.request("GET", "/missing-page", headers={"Accept": "text/html"})
            response = connection.getresponse()
            assert response.status == 404
            not_found_html = offline_document(response.read().decode("utf-8"), render_css())
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=5)

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path="/usr/bin/chromium",
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )

            page = browser.new_page(viewport={"width": 900, "height": 620}, device_scale_factor=1)
            page.set_content(not_found_html, wait_until="domcontentloaded")
            not_found_state = no_overflow(page)
            not_found_state.update(page.evaluate("""() => ({
              title: document.querySelector('h1').textContent,
              protection: document.querySelector('.panel p:nth-of-type(2)').textContent,
              main: document.querySelector('main').id
            })"""))
            results["not_found"] = not_found_state
            assert not not_found_state["overflow"], not_found_state
            assert not_found_state["title"] == "这里没有这个页面"
            assert "项目文件没有被修改" in not_found_state["protection"]
            page.screenshot(path=str(OUT / "not-found.png"), full_page=True)
            page.close()

            # Workspace desktop + skip-link behavior.
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page.set_content(workspace_html, wait_until="domcontentloaded")
            page.keyboard.press("Tab")
            focused_before = page.evaluate("document.activeElement && document.activeElement.className")
            page.keyboard.press("Enter")
            focused_after = page.evaluate("document.activeElement && document.activeElement.id")
            results["skip_link"] = {"before": focused_before, "after": focused_after}
            assert "mj-skip-link" in str(focused_before)
            assert focused_after == "main-content"
            page.screenshot(path=str(OUT / "workspace-desktop.png"), full_page=True)

            # First-run form error: input stays and error remains until the user acts.
            page.add_init_script("""
              window.requestJson = function () {
                return Promise.reject({message: '找不到这个项目文件夹', data: {error: 'ENOENT'}});
              };
            """)
            # add_init_script applies on navigation, so reload the deterministic document.
            page.set_content(workspace_html, wait_until="domcontentloaded")
            page.evaluate("""() => {
              window.requestJson = function () {
                return Promise.reject({message: '找不到这个项目文件夹', data: {error: 'ENOENT'}});
              };
            }""")
            page.add_script_tag(content=render_workspace_js())
            page.fill("#ws-open-path", "D:\\不存在\\电影.manju")
            page.click("#ws-open-form button[type=submit]")
            page.wait_for_selector("#ws-feedback:not([hidden])")
            error_state = page.evaluate("""() => ({
              title: document.querySelector('#ws-feedback-title').textContent,
              copy: document.querySelector('#ws-feedback-copy').textContent,
              value: document.querySelector('#ws-open-path').value,
              live: document.querySelector('#ws-feedback').getAttribute('aria-live'),
              focused: document.activeElement && document.activeElement.id,
              skipFocused: document.querySelector('.mj-skip-link').matches(':focus'),
              skipTransform: getComputedStyle(document.querySelector('.mj-skip-link')).transform
            })""")
            results["workspace_error"] = error_state
            assert error_state["value"] == "D:\\不存在\\电影.manju"
            assert error_state["live"] == "assertive"
            assert error_state["focused"] == "ws-feedback"
            # Let the skip-link's short focus transition finish so the evidence
            # captures the durable error state rather than a departing focus cue.
            page.wait_for_timeout(180)
            page.locator("#ws-feedback").scroll_into_view_if_needed()
            page.screenshot(path=str(OUT / "workspace-error.png"))
            page.close()

            # Narrow / 400%-equivalent reflow: 1280px layout at 400% becomes 320 CSS px.
            page = browser.new_page(viewport={"width": 320, "height": 800}, device_scale_factor=1)
            page.set_content(workspace_html, wait_until="domcontentloaded")
            narrow = no_overflow(page)
            results["workspace_400_percent_equivalent"] = narrow
            assert not narrow["overflow"], narrow
            page.screenshot(path=str(OUT / "workspace-400-percent-equivalent.png"), full_page=True)
            page.close()

            # Shared application chrome must also reflow to the 320 CSS-pixel
            # equivalent of a 1280px desktop at 400% zoom.
            page = browser.new_page(viewport={"width": 320, "height": 800}, device_scale_factor=1)
            page.set_content(shell_html, wait_until="domcontentloaded")
            shell_narrow = no_overflow(page)
            results["shared_shell_400_percent_equivalent"] = shell_narrow
            assert not shell_narrow["overflow"], shell_narrow
            page.screenshot(path=str(OUT / "shared-shell-400-percent-equivalent.png"), full_page=True)
            page.close()

            # Text-only zoom: the shell must retain reflow at 200% and 400%.
            zoom_results = {}
            for zoom in (200, 400):
                page = browser.new_page(viewport={"width": 1280, "height": 800}, device_scale_factor=1)
                page.set_content(shell_html, wait_until="domcontentloaded")
                page.evaluate(f"document.documentElement.style.fontSize='{zoom}%'")
                metrics = no_overflow(page)
                zoom_results[str(zoom)] = metrics
                assert not metrics["overflow"], (zoom, metrics)
                if zoom == 200:
                    page.screenshot(path=str(OUT / "shared-shell-200-percent-text.png"), full_page=True)
                page.close()
            results["shared_shell_text_zoom"] = zoom_results

            # Glossary button opens by click and closes with Escape while restoring focus.
            page = browser.new_page(viewport={"width": 900, "height": 500}, device_scale_factor=1)
            page.set_content(shell_html, wait_until="domcontentloaded")
            page.evaluate("""() => {
              window.requestJson = function () { return Promise.resolve({recents: []}); };
              window.fetch = function () { return Promise.resolve({ok: true, json: () => Promise.resolve({})}); };
            }""")
            page.add_script_tag(content=render_glossary_js())
            page.click(".mj-help")
            glossary_open = page.evaluate("""() => ({
              expanded: document.querySelector('.mj-help').getAttribute('aria-expanded'),
              open: document.querySelector('.mj-help-wrap').classList.contains('is-open'),
              tooltipDisplay: getComputedStyle(document.querySelector('.mj-tip')).display
            })""")
            results["glossary_open"] = glossary_open
            assert glossary_open == {"expanded": "true", "open": True, "tooltipDisplay": "block"}
            page.screenshot(path=str(OUT / "glossary-help.png"), full_page=True)
            page.keyboard.press("Escape")
            glossary_closed = page.evaluate("""() => ({
              expanded: document.querySelector('.mj-help').getAttribute('aria-expanded'),
              open: document.querySelector('.mj-help-wrap').classList.contains('is-open'),
              focused: document.activeElement === document.querySelector('.mj-help')
            })""")
            results["glossary_closed"] = glossary_closed
            assert glossary_closed == {"expanded": "false", "open": False, "focused": True}
            page.close()

            # Shared toast semantics: duplicate chatter is coalesced, errors stay
            # until explicitly dismissed, and each message owns its correct live role.
            page = browser.new_page(viewport={"width": 1100, "height": 650}, device_scale_factor=1)
            page.set_content(shell_html, wait_until="domcontentloaded")
            page.evaluate("""() => {
              window.requestJson = function (method, path) {
                if (path === '/api/project-id') return Promise.resolve({token: ''});
                if (path === '/api/meta/job-kinds') return Promise.resolve({kinds: {}});
                if (path === '/api/jobs') return Promise.resolve({jobs: []});
                if (path === '/api/finishing/status') return Promise.resolve({});
                return Promise.resolve({});
              };
              window.fetch = function () {
                return Promise.resolve({ok: true, json: () => Promise.resolve({})});
              };
            }""")
            page.add_script_tag(content=render_common_js())
            page.evaluate("""() => {
              toast('项目已保存', true);
              toast('项目已保存', true);
              toast('保存没有完成。项目文件没有被修改；请检查冲突后重试。', false);
            }""")
            toast_state = page.evaluate("""() => ({
              count: document.querySelectorAll('.toast-item').length,
              roles: Array.from(document.querySelectorAll('.toast-item')).map(x => x.getAttribute('role')),
              closeButtons: document.querySelectorAll('.mj-toast-close').length,
              messages: Array.from(document.querySelectorAll('.mj-toast-copy')).map(x => x.textContent)
            })""")
            results["toast_initial"] = toast_state
            assert toast_state["count"] == 2, toast_state
            assert toast_state["roles"] == ["status", "alert"], toast_state
            assert toast_state["closeButtons"] == 2, toast_state
            page.wait_for_timeout(260)
            page.screenshot(path=str(OUT / "toast-feedback.png"), full_page=True)
            page.wait_for_timeout(4800)
            toast_after = page.evaluate("""() => ({
              count: document.querySelectorAll('.toast-item').length,
              role: document.querySelector('.toast-item') && document.querySelector('.toast-item').getAttribute('role'),
              message: document.querySelector('.mj-toast-copy') && document.querySelector('.mj-toast-copy').textContent
            })""")
            results["toast_after_success_timeout"] = toast_after
            assert toast_after["count"] == 1 and toast_after["role"] == "alert", toast_after
            page.click('.mj-toast-close')
            assert page.locator('.toast-item').count() == 0

            page.evaluate("projectSwitchedOverlay('新的剪辑项目')")
            overlay_state = page.evaluate("""() => ({
              role: document.querySelector('#mj-proj-switched').getAttribute('role'),
              modal: document.querySelector('#mj-proj-switched').getAttribute('aria-modal'),
              focused: document.activeElement && document.activeElement.textContent,
              protect: document.querySelector('#mj-proj-switched-copy').nextElementSibling.textContent
            })""")
            results["project_switch_overlay"] = overlay_state
            assert overlay_state["role"] == "alertdialog"
            assert overlay_state["modal"] == "true"
            assert overlay_state["focused"] == "刷新并跟随当前项目"
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement && document.activeElement.textContent") == "刷新并跟随当前项目"
            page.keyboard.press("Escape")
            assert page.locator('#mj-proj-switched').count() == 1
            page.screenshot(path=str(OUT / "project-switch-protection.png"), full_page=True)
            page.close()

            browser.close()

    RESULT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
