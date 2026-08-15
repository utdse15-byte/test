#!/usr/bin/env python3
"""Offline browser acceptance for Manju's global Help & Support surface.

This tool uses the product's real server-side renderers, shipped CSS and shipped
JavaScript with Playwright ``page.set_content``.  It never starts a Provider,
reads credentials, opens the network, or mutates a project.  The result is a
repeatable release aid rather than a claim of live localhost HTTP E2E.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from playwright.sync_api import sync_playwright


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


def _clean(document: str, css: str, scripts: str) -> str:
    document = re.sub(r'<link rel="stylesheet" href="[^"]+">\n?', "", document)
    document = re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>\n?', "", document)
    return document.replace(
        "</head>", f"<style>{css}</style></head>",
    ).replace("</body>", scripts + "</body>")


def _fixture(work: Path):
    from tests.fixtures.make_gui_scale_project import build
    from manju.core.container import Project

    root = build(work, shots=3, takes_per_shot=1, name="帮助中心样片")
    return Project(root)


def _bootstrap(about: dict[str, Any], palette: dict[str, Any]) -> str:
    payload = json.dumps({"about": about, "palette": palette}, ensure_ascii=False)
    return (
        '<script>window.__MJ_FIXTURE=' + payload + ';window.__MJ_CALLS=[];'
        'window.__MJ_FAIL_ABOUT=false;window.manjuApiOptions=function(){return {}};'
        'window.requestJson=function(method,url){window.__MJ_CALLS.push([method,url]);'
        'if(url==="/api/app/about"){'
        'if(window.__MJ_FAIL_ABOUT)return Promise.reject(new Error("fixture failure"));'
        'return Promise.resolve(window.__MJ_FIXTURE.about);}'
        'if(url==="/api/command-palette")return Promise.resolve(window.__MJ_FIXTURE.palette);'
        'return Promise.reject(new Error("unexpected "+method+" "+url));};</script>'
    )


def _facts(page) -> dict[str, Any]:
    return page.evaluate(
        """() => ({
          viewport: {width: innerWidth, height: innerHeight},
          documentWidth: document.documentElement.scrollWidth,
          horizontalOverflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
          dialogOpen: !!document.querySelector('#mj-help-center[open]'),
          title: document.querySelector('#mj-help-center-title')?.textContent?.trim() || '',
          focused: document.activeElement?.id || document.activeElement?.className || '',
          helpButtons: document.querySelectorAll('#mj-help-center-btn').length,
          aboutCalls: (window.__MJ_CALLS || []).filter(x => x[1] === '/api/app/about').length,
          paletteCalls: (window.__MJ_CALLS || []).filter(x => x[1] === '/api/command-palette').length,
          calls: window.__MJ_CALLS || [],
          text: document.querySelector('#mj-help-center')?.innerText || ''
        })"""
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = _root()
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root))
    os.environ["MANJU_EXECUTION_MODE"] = "strict_zero_cost"

    owned = args.work_dir is None
    work = Path(args.work_dir).resolve() if args.work_dir else Path(
        tempfile.mkdtemp(prefix="manju-help-acceptance-")
    )
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["MANJU_GUI_STATE"] = str(work / "gui-state.json")
    os.environ["MANJU_RECENTS"] = str(work / "recents.json")

    from manju.gui import command_palette, help_center, page as app_page, pages, workspace
    from manju.gui.command_palette import palette_payload
    from manju.gui.state import project_identity

    project = _fixture(work / "fixture")
    token = project_identity(project)
    about = help_center.about_payload(project, app_mode=True)
    palette = palette_payload(project, project_token=token, mode="beginner")
    css = "\n".join([
        app_page.render_css(), pages.render_pages_css(),
        command_palette.render_command_palette_css(),
        help_center.render_help_center_css(), workspace.render_workspace_css(),
    ])
    scripts = (
        _bootstrap(about, palette)
        + f'<script>{command_palette.render_command_palette_js()}</script>'
        + f'<script>{help_center.render_help_center_js()}</script>'
    )
    bound = pages._shell(
        "帮助验收", token, "/review",
        '<section class="panel"><h1>审片</h1><p>离线帮助中心验收。</p></section>',
        project,
    )
    unbound_about = help_center.about_payload(None, app_mode=True)
    unbound_palette = palette_payload(None, project_token="", mode="beginner")
    unbound_scripts = (
        _bootstrap(unbound_about, unbound_palette)
        + f'<script>{command_palette.render_command_palette_js()}</script>'
        + f'<script>{help_center.render_help_center_js()}</script>'
    )
    picker = workspace.render_picker_page(token, bound=None, presets=[])

    result: dict[str, Any] = {
        "schema": "manju.product-help-center-acceptance/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": _git_sha(root),
        "zero_cost": True,
        "live_http_e2e": False,
        "cases": {},
        "errors": [],
    }

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=args.chromium,
                headless=True,
                args=["--no-sandbox", "--disable-gpu"],
            )
            for name, viewport, document in (
                ("bound-desktop", {"width": 1440, "height": 900}, _clean(bound, css, scripts)),
                ("bound-narrow", {"width": 390, "height": 844}, _clean(bound, css, scripts)),
                ("workspace", {"width": 960, "height": 760}, _clean(picker, css, unbound_scripts)),
            ):
                context = browser.new_context(viewport=viewport)
                page = context.new_page()
                page_errors: list[str] = []
                page.on("pageerror", lambda exc, rows=page_errors: rows.append(str(exc)))
                page.set_content(document, wait_until="domcontentloaded")
                page.keyboard.press("F1")
                page.wait_for_timeout(40)
                facts = _facts(page)
                facts["pageErrors"] = page_errors
                failures: list[str] = []
                if not facts["dialogOpen"] or facts["title"] != "帮助与支持":
                    failures.append("F1 did not open the help dialog")
                if facts["helpButtons"] != 1:
                    failures.append(f"expected one help trigger, got {facts['helpButtons']}")
                if facts["aboutCalls"] != 1:
                    failures.append(f"expected one about read, got {facts['aboutCalls']}")
                if facts["horizontalOverflow"]:
                    failures.append(f"horizontal overflow {facts['horizontalOverflow']}px")
                for phrase in ("本地安全", "遥测", "支持包", "F1"):
                    if phrase not in facts["text"]:
                        failures.append(f"missing product copy: {phrase}")
                if page_errors:
                    failures.append("page errors: " + " | ".join(page_errors[:3]))
                screenshot = out / f"help-center-{name}.png"
                page.screenshot(path=str(screenshot), full_page=True)
                facts["screenshot"] = screenshot.name

                # Focus restoration is part of the product contract.
                page.keyboard.press("Escape")
                page.wait_for_function(
                    "!document.querySelector('#mj-help-center')?.open && "
                    "document.activeElement?.id === 'mj-help-center-btn'"
                )
                if page.evaluate("document.activeElement.id") != "mj-help-center-btn":
                    failures.append("Escape did not restore focus to the help trigger")

                if name == "bound-desktop":
                    page.keyboard.press("Control+k")
                    page.locator("#mj-command-input").fill("帮助")
                    page.keyboard.press("Enter")
                    page.wait_for_function("document.querySelector('#mj-help-center')?.open === true")
                    if not page.evaluate("document.querySelector('#mj-help-center')?.open === true"):
                        failures.append("Quick Open could not open Help & Support")
                    calls = page.evaluate("window.__MJ_CALLS")
                    if any(method != "GET" for method, _url in calls):
                        failures.append("help acceptance issued a mutation request")
                    dangerous = ("build", "generate", "select", "lock", "provider")
                    if any(any(word in url for word in dangerous) for _method, url in calls):
                        failures.append("help acceptance touched a dangerous endpoint")

                    # A failed refresh must show a durable recovery surface and
                    # must not replace the project/page underneath it.
                    page.evaluate("window.__MJ_FAIL_ABOUT=true")
                    page.evaluate("window.ManjuHelpCenter.refresh().catch(()=>{})")
                    page.wait_for_timeout(30)
                    if "产品信息暂时无法读取" not in page.locator("#mj-help-center").inner_text():
                        failures.append("failed refresh did not expose recovery guidance")

                facts["failures"] = failures
                result["cases"][name] = facts
                if failures:
                    result["errors"].append({"case": name, "failures": failures})
                context.close()
            browser.close()
    finally:
        if owned and not args.keep_work_dir:
            shutil.rmtree(work, ignore_errors=True)

    result["passed"] = not result["errors"]
    result_path = out / "help-center-acceptance.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-work-dir", action="store_true")
    parser.add_argument(
        "--chromium",
        default=os.environ.get("MANJU_DEV_CHROMIUM", "/usr/bin/chromium"),
    )
    args = parser.parse_args(argv)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
