#!/usr/bin/env python3
"""Zero-cost browser acceptance for Manju's global quick-open palette.

The tool renders the real server HTML/CSS and the shipped command-palette
JavaScript in an offline Playwright document.  It proves keyboard discovery,
shot-content search, beginner-mode filtering, honest index failure, workspace
focus actions, recent-project switching, focus restoration and responsive
layout.  It never contacts localhost or the network, reads credentials, starts
FFmpeg, calls a Provider or changes canonical project truth.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git_sha(root: Path) -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True,
            stderr=subprocess.DEVNULL, timeout=5,
        ).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _chromium(raw: str | None) -> str:
    candidates = [raw, os.environ.get("MANJU_DEV_CHROMIUM"), shutil.which("chromium"),
                  shutil.which("chromium-browser"), shutil.which("google-chrome")]
    for value in candidates:
        if value and Path(value).exists():
            return str(Path(value))
    raise RuntimeError("Chromium is required for command-palette acceptance")


def _clean_html(raw: str, css: str) -> str:
    import re

    clean = re.sub(r'<link[^>]+rel=["\']stylesheet["\'][^>]*>\s*', "", raw)
    clean = re.sub(r'<script[^>]+src=["\'][^"\']+["\'][^>]*></script>\s*', "", clean)
    return clean.replace("</head>", f"<style>{css}</style></head>")


def _make_project(work: Path):
    from tests.fixtures.make_gui_scale_project import build
    from manju.core.container import Project

    root = build(work, shots=12, takes_per_shot=1, name="quick_open_fixture")
    project = Project(root)
    project.update_shot_raw(
        "S003",
        lambda raw: raw.update({
            "scene": "雨夜便利店",
            "action": {"main": "她推开卷帘门", "emotion": "犹豫"},
            "dialogue": {"speaker": "linxia", "text": "还有人在吗？"},
        }),
    )
    return project


def _browser_facts(page) -> dict[str, Any]:
    return page.evaluate(
        """() => ({
          viewport: {width: innerWidth, height: innerHeight},
          documentWidth: document.documentElement.scrollWidth,
          overflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
          dialogOpen: !!document.querySelector('#mj-command-palette[open]'),
          focus: document.activeElement && document.activeElement.id,
          triggerExpanded: document.querySelector('#mj-command-btn')?.getAttribute('aria-expanded'),
          inputRole: document.querySelector('#mj-command-input')?.getAttribute('role'),
          inputExpanded: document.querySelector('#mj-command-input')?.getAttribute('aria-expanded'),
          options: document.querySelectorAll('#mj-command-results [role="option"]').length,
          selected: document.querySelector('#mj-command-results [aria-selected="true"] .mj-command-option-title')?.textContent || '',
          status: document.querySelector('#mj-command-description')?.textContent || '',
        })"""
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = _root()
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root))
    os.environ["MANJU_EXECUTION_MODE"] = "strict_zero_cost"

    from playwright.sync_api import sync_playwright
    from manju.gui import page as app_page
    from manju.gui.command_palette import (
        palette_payload, render_command_palette_css, render_command_palette_js,
    )
    from manju.gui.pages import render_pages_css, render_review
    from manju.gui.state import project_identity
    from manju.gui.userstate import set_mode, set_show_pro_terms
    from manju.gui.workspace import render_picker_page, render_workspace_css

    owned = args.work_dir is None
    work = Path(args.work_dir).resolve() if args.work_dir else Path(
        tempfile.mkdtemp(prefix="manju-command-palette-")
    )
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["MANJU_GUI_STATE"] = str(work / "gui-state.json")
    os.environ["MANJU_RECENTS"] = str(work / "recents.json")
    set_mode("beginner")
    set_show_pro_terms(False)

    project = _make_project(work / "fixture")
    token = project_identity(project)
    payload = palette_payload(project, project_token=token, mode="beginner")
    payload["recents"] = [
        {"name": "山海之间", "path": r"D:\Films\山海之间.manju", "current": False,
         "pinned": False, "last_opened": None},
        {"name": "雨夜便利店", "path": r"D:\Films\雨夜便利店.manju", "current": False,
         "pinned": True, "last_opened": None},
    ]

    base_css = "\n".join([
        app_page.render_css(), render_pages_css(), render_command_palette_css(),
    ])
    review_html = _clean_html(render_review(project, token), base_css)
    workspace_html = _clean_html(
        render_picker_page(token, bound=project, presets=[]),
        app_page.render_css() + "\n" + render_workspace_css() + "\n" + render_command_palette_css(),
    )
    js = render_command_palette_js()

    result: dict[str, Any] = {
        "schema": "manju.command-palette-acceptance/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(root),
        "zero_cost": True,
        "mode": "offline-real-renderer",
        "live_http_e2e": False,
        "cases": {},
        "errors": [],
    }

    def check(condition: bool, case: str, message: str) -> None:
        if not condition:
            result["errors"].append({"case": case, "message": message})

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=_chromium(args.chromium), headless=True,
                args=["--no-sandbox", "--disable-gpu"],
            )

            # Desktop: rich action/scene search and safe navigation.
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()
            page.set_content(review_html, wait_until="domcontentloaded")
            page.evaluate(
                """(payload) => {
                  window.__requests = []; window.__navigated = []; window.__openedProject = null;
                  window.__MJ_COMMAND_NAVIGATE = href => window.__navigated.push(href);
                  window.manjuApiOptions = opts => opts || {};
                  window.requestJson = (method, url, body) => {
                    window.__requests.push({method, url, body});
                    if (method === 'GET' && url === '/api/command-palette') return Promise.resolve(payload);
                    if (method === 'POST' && url === '/api/workspace/open') {
                      window.__openedProject = body.path;
                      return Promise.resolve({ok:true, next_action:{kind:'reload_current'}});
                    }
                    return Promise.reject(new Error('unexpected ' + method + ' ' + url));
                  };
                  window.handleProjectAction = () => {};
                  window.ManjuTaskCenter = {open: () => { window.__taskOpened = true; }};
                }""",
                payload,
            )
            page.add_script_tag(content=js)
            check(page.evaluate("window.__requests.length") == 0, "desktop", "index was not lazy")
            page.keyboard.press("Control+k")
            page.wait_for_function("window.__requests.length === 1")
            page.locator("#mj-command-input").fill("推开卷帘门")
            page.wait_for_timeout(20)
            facts = _browser_facts(page)
            check(facts["selected"] == "打开镜头 S003", "desktop", "action search missed S003")
            check(facts["overflow"] == 0, "desktop", "desktop overflow")
            check(facts["inputRole"] == "combobox", "desktop", "input is not a combobox")
            shot_text = page.locator("#mj-command-results").inner_text()
            check("雨夜便利店" in shot_text, "desktop", "scene context missing")
            page.screenshot(path=str(out / "command-palette-desktop.png"), full_page=False)
            page.keyboard.press("Enter")
            check(page.evaluate("window.__navigated.pop()") == "/lab?shot=S003", "desktop", "unsafe or wrong shot route")
            result["cases"]["desktop"] = facts
            context.close()

            # Narrow: review intent and no horizontal overflow.
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.set_content(review_html, wait_until="domcontentloaded")
            page.evaluate(
                """(payload) => {
                  window.__requests = []; window.__navigated = [];
                  window.__MJ_COMMAND_NAVIGATE = href => window.__navigated.push(href);
                  window.manjuApiOptions = opts => opts || {};
                  window.requestJson = () => Promise.resolve(payload);
                }""",
                payload,
            )
            page.add_script_tag(content=js)
            page.keyboard.press("Control+k")
            page.locator("#mj-command-input").fill("审片 推开卷帘门")
            page.wait_for_timeout(20)
            facts = _browser_facts(page)
            check(facts["selected"] == "审片 S003", "narrow", "review intent missed S003")
            check(facts["overflow"] == 0, "narrow", "narrow overflow")
            page.screenshot(path=str(out / "command-palette-narrow.png"), full_page=False)
            page.keyboard.press("Enter")
            check(page.evaluate("window.__navigated.pop()") == "/review?shot=S003", "narrow", "wrong review route")
            result["cases"]["narrow"] = facts
            context.close()

            # Workspace: safe focus actions and recent project discovery.
            context = browser.new_context(viewport={"width": 960, "height": 760})
            page = context.new_page()
            page.set_content(workspace_html, wait_until="domcontentloaded")
            page.evaluate(
                """(payload) => {
                  window.__requests = [];
                  window.manjuApiOptions = opts => opts || {};
                  window.requestJson = (method, url, body) => {
                    window.__requests.push({method, url, body});
                    if (method === 'GET') return Promise.resolve(payload);
                    return Promise.resolve({ok:true, next_action:{kind:'reload_current'}});
                  };
                  window.handleProjectAction = () => {};
                }""",
                payload,
            )
            page.add_script_tag(content=js)
            page.keyboard.press("Control+k")
            page.locator("#mj-command-input").fill("项目")
            page.wait_for_timeout(20)
            facts = _browser_facts(page)
            check(facts["overflow"] == 0, "workspace", "workspace overflow")
            check("打开现有项目" in page.locator("#mj-command-results").inner_text(), "workspace", "open action missing")
            page.screenshot(path=str(out / "command-palette-workspace.png"), full_page=False)
            result["cases"]["workspace"] = facts
            context.close()

            # Honest failure: pages remain available and the next open retries.
            context = browser.new_context(viewport={"width": 960, "height": 760})
            page = context.new_page()
            page.set_content(review_html, wait_until="domcontentloaded")
            page.evaluate(
                """(payload) => {
                  window.__reads = 0; window.__navigated = [];
                  window.__MJ_COMMAND_NAVIGATE = href => window.__navigated.push(href);
                  window.manjuApiOptions = opts => opts || {};
                  window.requestJson = () => {
                    window.__reads += 1;
                    return window.__reads === 1 ? Promise.reject(new Error('offline')) : Promise.resolve(payload);
                  };
                }""",
                payload,
            )
            page.add_script_tag(content=js)
            page.keyboard.press("Control+k")
            page.wait_for_function("window.ManjuCommandPalette.current().loadError === true")
            status = page.locator("#mj-command-description").inner_text()
            check("项目索引暂时无法更新" in status, "failure", "failure was silent")
            page.locator("#mj-command-input").fill("创作")
            page.keyboard.press("Enter")
            check(page.evaluate("window.__navigated.pop()") == "/create", "failure", "static navigation stopped working")
            page.keyboard.press("Control+k")
            page.wait_for_function("window.__reads === 2")
            check(not page.evaluate("window.ManjuCommandPalette.current().loadError"), "failure", "failed read was cached")
            result["cases"]["failure"] = {"status": status, "reads": page.evaluate("window.__reads")}
            context.close()

            browser.close()
    finally:
        if owned and not args.keep_work_dir:
            shutil.rmtree(work, ignore_errors=True)

    result["passed"] = not result["errors"]
    (out / "command-palette-acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-work-dir", action="store_true")
    parser.add_argument("--chromium")
    args = parser.parse_args(argv)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
