#!/usr/bin/env python3
"""Render Manju's core product journey without network or paid services.

The sandbox and some enterprise Chromium policies block navigation to localhost,
so this release aid deliberately uses the same server-side renderers, real CSS,
and selected real page JavaScript with Playwright ``page.set_content``.  It is a
repeatable structural/visual acceptance pass, not a claim of live HTTP E2E.

The script creates a disposable local project, renders the workspace, creation,
storyboard, review, edit, and export surfaces at desktop and narrow viewports,
captures screenshots, and records facts that should stay invariant:
Chinese document language, one skip link, one main landmark, unique element IDs,
no literal ``undefined``, no horizontal page overflow, and no browser page
errors.  Nothing it writes is a project or build input.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


_PAGE_NAMES = ("workspace", "create", "storyboard", "review", "edit", "exports")
_VIEWPORTS = {
    "desktop": {"width": 1440, "height": 900},
    "narrow": {"width": 390, "height": 844},
    "zoom400-equivalent": {"width": 320, "height": 800},
}


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _selection(raw: str, *, allowed: tuple[str, ...], noun: str) -> list[str]:
    values = [token.strip() for token in raw.split(",") if token.strip()]
    if not values:
        raise argparse.ArgumentTypeError(f"at least one {noun} is required")
    unknown = [value for value in values if value not in allowed]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown {noun}: {', '.join(unknown)}; choose from {', '.join(allowed)}"
        )
    return list(dict.fromkeys(values))


def _parse_pages(raw: str) -> list[str]:
    return _selection(raw, allowed=_PAGE_NAMES, noun="page")


def _parse_viewports(raw: str) -> list[str]:
    return _selection(raw, allowed=tuple(_VIEWPORTS), noun="viewport")


def _clean_html(html_doc: str, css: str, project_token: str | None = None) -> str:
    html_doc = re.sub(r'<link rel="stylesheet" href="[^"]+">\n?', "", html_doc)
    html_doc = re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>\n?', "", html_doc)
    if project_token and 'name="manju-project"' not in html_doc:
        html_doc = html_doc.replace(
            '<meta name="manju-token"',
            f'<meta name="manju-project" content="{project_token}">\n'
            '<meta name="manju-token"',
            1,
        )
    return html_doc.replace("</head>", f"<style>{css}</style></head>")


class _EmptyRunner:
    def list(self) -> list[Any]:
        return []

    def interrupted(self) -> list[Any]:
        return []


def _make_project(work: Path):
    from tests.fixtures.make_gui_scale_project import build
    from manju.core.container import Project

    root = build(work, shots=12, takes_per_shot=2, name="产品打磨样片")
    project = Project(root)
    story = {
        "brief.md": (
            "# 故事与结尾\n\n雨夜末班车即将关闭，检票员发现一个等待失约父亲的孩子。"
            "她原本只想结束工作，却决定陪孩子回家，并第一次正视自己多年没有处理的离别。"
        ),
        "synopsis.md": (
            "# 场次概览\n\n第一场从空站和关门广播开始；第二场两人在雨中寻找线索；"
            "第三场检票员把伞交给孩子并拨通母亲电话。每场都改变人物的选择和关系。"
        ),
        "beats.md": (
            "# 变化节拍\n\n- 广播催促关门。\n- 她发现孩子仍在等待。\n"
            "- 孩子承认父亲可能不会来。\n- 她重新打开卷帘门并陪孩子离开。\n"
        ),
        "script.md": (
            "# 剧本与声音\n\n夜，空站。广播最后一次提示关门。检票员把钥匙插进卷帘门，"
            "忽然听见鞋底摩擦声。孩子抱着湿透的书包说：爸爸答应来。"
            "她停了很久，关掉广播，把门重新推开。"
        ),
    }
    for name, text in story.items():
        (project.story_dir / name).write_text(text + "\n", encoding="utf-8")

    # Give the sample a meaningful review state without changing the fixture's
    # append-only media. S003 remains the one explicit human decision.
    for sid in project.shot_ids():
        shot = project.load_shot(sid)
        selected = shot.status.selected_take
        if selected and sid != "S003":
            shot.status.take_notes[selected] = "推荐：动作和构图符合当前镜头。"
            shot.status.review = "approved"
            project.save_shot(shot)
    return project


def _css_bundles() -> dict[str, str]:
    from manju.gui import glossary, page, pages, project_action
    from manju.gui import create_page, edit, exports_page, storyboard, workspace

    base = "\n".join([
        page.render_css(), pages.render_pages_css(),
        glossary.render_glossary_css(), project_action.render_project_action_css(),
    ])
    return {
        "workspace": base + "\n" + workspace.render_workspace_css(),
        "create": base + "\n" + create_page.render_create_css(),
        "storyboard": base + "\n" + storyboard.render_storyboard_css(),
        "review": base,
        "edit": base + "\n" + edit.render_edit_css(),
        "exports": base + "\n" + exports_page.render_exports_css(),
    }


def _documents(project, token: str) -> dict[str, str]:
    from manju.gui import create_page, edit, exports_page, pages, storyboard, workspace

    return {
        "workspace": workspace.render_picker_page(token, bound=project, presets=[]),
        "create": create_page.render_create(project, token),
        "storyboard": storyboard.render(project, token),
        "review": pages.render_review(project, token),
        "edit": edit.render_edit(project, token, {}),
        "exports": exports_page.render(project, token),
    }


def _facts(page, *, name: str) -> dict[str, Any]:
    return page.evaluate(
        """(name) => {
          const ids = Array.from(document.querySelectorAll('[id]')).map(x => x.id);
          const duplicateIds = Array.from(new Set(ids.filter((id, i) => ids.indexOf(id) !== i)));
          const bodyText = document.body ? document.body.innerText : '';
          const main = document.querySelector('main#main-content');
          return {
            name,
            title: document.title,
            lang: document.documentElement.lang,
            viewport: {width: innerWidth, height: innerHeight},
            document: {
              width: document.documentElement.scrollWidth,
              height: document.documentElement.scrollHeight,
            },
            horizontalOverflow: Math.max(0, document.documentElement.scrollWidth - innerWidth),
            skipLinks: document.querySelectorAll('.mj-skip-link').length,
            mainLandmarks: document.querySelectorAll('main#main-content').length,
            mainFocusable: !!main && main.getAttribute('tabindex') === '-1',
            duplicateIds,
            literalUndefined: /(^|\\s)undefined($|\\s)/i.test(bodyText),
            h1: document.querySelector('h1')?.textContent?.trim() || '',
            elementCount: document.querySelectorAll('*').length,
          };
        }""",
        name,
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = _root()
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root))
    os.environ["MANJU_EXECUTION_MODE"] = "strict_zero_cost"

    owned_work = args.work_dir is None
    work = Path(args.work_dir).resolve() if args.work_dir else Path(
        tempfile.mkdtemp(prefix="manju-visual-acceptance-")
    )
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["MANJU_GUI_STATE"] = str(work / "gui-state.json")

    from manju.gui.state import project_identity
    from manju.gui.userstate import set_mode, set_show_pro_terms

    set_mode("beginner")
    set_show_pro_terms(False)
    project = _make_project(work / "fixture")
    token = project_identity(project)
    css = _css_bundles()
    all_docs = _documents(project, token)
    docs = {name: all_docs[name] for name in args.pages}

    result: dict[str, Any] = {
        "schema": "manju.product-visual-acceptance/v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "zero_cost": True,
        "mode": "offline-real-renderer",
        "live_http_e2e": False,
        "config": {
            "pages": args.pages,
            "viewports": args.viewports,
            "screenshots": not args.no_screenshots,
        },
        "pages": {},
        "errors": [],
    }
    viewports = {name: _VIEWPORTS[name] for name in args.viewports}

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                executable_path=args.chromium,
                headless=True,
                args=["--no-sandbox", "--disable-gpu"],
            )
            for page_name, raw_html in docs.items():
                result["pages"][page_name] = {}
                html_doc = _clean_html(raw_html, css[page_name], token)
                for viewport_name, viewport in viewports.items():
                    context = browser.new_context(viewport=viewport, device_scale_factor=1)
                    page = context.new_page()
                    browser_errors: list[str] = []
                    page.on("pageerror", lambda exc, rows=browser_errors: rows.append(str(exc)))
                    page.set_content(html_doc, wait_until="domcontentloaded")
                    page.wait_for_timeout(50)
                    facts = _facts(page, name=page_name)
                    facts["pageErrors"] = browser_errors
                    result["pages"][page_name][viewport_name] = facts
                    failures: list[str] = []
                    if facts["lang"] != "zh-CN":
                        failures.append("document language is not zh-CN")
                    if facts["skipLinks"] != 1:
                        failures.append(f"expected one skip link, got {facts['skipLinks']}")
                    if facts["mainLandmarks"] != 1 or not facts["mainFocusable"]:
                        failures.append("main#main-content landmark contract failed")
                    if facts["duplicateIds"]:
                        failures.append("duplicate IDs: " + ", ".join(facts["duplicateIds"][:8]))
                    if facts["literalUndefined"]:
                        failures.append("literal undefined is visible")
                    if facts["horizontalOverflow"] > 0:
                        failures.append(f"horizontal overflow {facts['horizontalOverflow']}px")
                    if browser_errors:
                        failures.append("page errors: " + " | ".join(browser_errors[:3]))
                    if failures:
                        result["errors"].append({
                            "page": page_name, "viewport": viewport_name,
                            "failures": failures,
                        })
                    if not args.no_screenshots and viewport_name != "zoom400-equivalent":
                        screenshot = out / f"{page_name}-{viewport_name}.png"
                        page.screenshot(path=str(screenshot), full_page=True)
                        facts["screenshot"] = screenshot.name
                    context.close()
            browser.close()
    finally:
        if owned_work and not args.keep_work_dir:
            shutil.rmtree(work, ignore_errors=True)

    result["passed"] = not result["errors"]
    result_path = out / "visual-acceptance.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep-work-dir", action="store_true")
    parser.add_argument(
        "--pages", type=_parse_pages, default=list(_PAGE_NAMES),
        metavar="NAME,...",
        help="surfaces to render (default: all core product surfaces)",
    )
    parser.add_argument(
        "--viewports", type=_parse_viewports, default=list(_VIEWPORTS),
        metavar="NAME,...",
        help="viewport names to render (default: desktop,narrow,zoom400-equivalent)",
    )
    parser.add_argument(
        "--no-screenshots", action="store_true",
        help="run structural checks without writing PNG files",
    )
    parser.add_argument("--chromium", default=os.environ.get("MANJU_DEV_CHROMIUM", "/usr/bin/chromium"))
    args = parser.parse_args(argv)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
