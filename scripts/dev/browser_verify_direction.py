"""Real-browser verification of the #50 direction program (dev harness).

Run: python scripts/dev/browser_verify_direction.py   (same requirements as
browser_verify.py: playwright + a Chromium; MANJU_DEV_CHROMIUM overrides).
Sibling of browser_verify.py — kept separate because these checks need a
TWO-shot project (one reviewable + one take-less) and clipboard permissions,
and the original's checks assume its single-shot scaffold. Future sessions:
re-run after touching gui/pages.py review JS, gui/page.py cockpit/nav JS, or
pages.py nav_html.

What it drives (DECISIONS #50-#50b, REPORTS/GUI_DIRECTION_2026-07-14.md):

  nav   — the six use-frequency groups: beginner collapses 审片/工具箱 to
          their one visible page and hides PRO_ONLY links; a group menu
          opens on hover; exactly ONE aria-current="page".
  queue — /review opens IN queue mode when unreviewed work exists; the
          queue walks most-blocking first (a take-less shot trails); j/k
          move; g marks reviewed AND advances; u restores the overwritten
          note + reviewed state; the explicit toggle persists per project.
  home  — the cockpit renders 继续上次工作 (from the manju-last trail +
          the /review position) and clickable state-strip counts that
          drive the shots filter.
  AI    — 复制给 Claude puts the structured shot context on the clipboard.
  50a/b — playbackRate survives a reload (per-project AV memory); the
          A/B 对比 link deep-links /?compare=<shot> into the workbench
          takes overlay; the cockpit shows the 新 take 未阅 row once a
          review snapshot exists.
"""

import os
import sys
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "scripts" / "dev"))

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    sys.exit("需要 playwright(pip install playwright)+ 一个 Chromium;"
             "浏览器路径可用 MANJU_DEV_CHROMIUM 指定")

from browser_verify import check, make_project  # noqa: E402  (shared scaffold)

from manju.core.yamlio import write_yaml  # noqa: E402
from manju.gui.server import create_server  # noqa: E402


def main() -> int:
    project = make_project()
    # a second, take-less shot: two distinct states → the filter bar renders,
    # and the queue's "nothing to judge trails" rule becomes observable
    write_yaml(project.root / "shots" / "S002.yaml", {
        "id": "S002", "scene": "s", "characters": ["c"],
        "action": {"main": "转身离开"}, "dialogue": {"text": "……"},
        "duration_ms": 1000,
    })
    write_yaml(project.root / "shots" / "index.yaml", {"order": ["S001", "S002"]})

    gui = create_server(project, host="127.0.0.1", port=0, actor="human")
    threading.Thread(target=gui.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{gui.port}"

    with sync_playwright() as pw:
        exe = os.environ.get("MANJU_DEV_CHROMIUM")
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                                  permissions=["clipboard-read", "clipboard-write"])
        page = ctx.new_page()
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))

        # ---------------- nav: six groups (fresh state = beginner) --------
        page.goto(base + "/review", wait_until="networkidle")
        groups = page.locator(".pnav-group")
        check("nav groups (beginner: 创作/镜头/成片)", groups.count() == 3,
              str(groups.count()))
        check("beginner hides providers",
              page.locator('.pnav a[href="/providers"]').count() == 0)
        check("beginner 工具箱 collapses to 素材库",
              page.locator('.pnav > a[href="/library"]').count() == 1)
        page.locator(".pnav-glabel", has_text="成片").first.hover()
        time.sleep(0.3)
        check("group menu opens on hover",
              page.locator('.pnav-menu a[href="/subtitles"]').first.is_visible())
        check("aria-current unique",
              page.locator('.pnav a[aria-current="page"]').count() == 1)

        # ---------------- review queue: default, order, advance, undo -----
        check("queue mode default ON (unreviewed exist)",
              page.evaluate("() => document.body.classList.contains('rv-queue-on')"))
        vis = page.locator(".rv-shot:visible")
        check("single card visible in queue", vis.count() == 1, str(vis.count()))
        check("queue leads with the reviewable shot",
              vis.first.get_attribute("data-shot") == "S001",
              "take-less S002 must trail")
        check("legend teaches u 撤回", "u 撤回" in page.content())
        page.keyboard.press("j")
        time.sleep(0.3)
        check("j advances queue",
              page.locator(".rv-shot:visible").first.get_attribute("data-shot") == "S002")
        page.keyboard.press("k")
        time.sleep(0.3)
        page.keyboard.press("g")
        time.sleep(1.0)
        check("g marks reviewed", page.locator('.rv-shot[data-shot="S001"]')
              .get_attribute("data-reviewed") == "1")
        page.keyboard.press("u")
        time.sleep(1.0)
        check("u undoes the verdict", page.locator('.rv-shot[data-shot="S001"]')
              .get_attribute("data-reviewed") != "1")
        page.locator("#rv-queue-toggle").click()
        time.sleep(0.3)
        page.reload(wait_until="networkidle")
        check("queue-off preference survives reload",
              not page.evaluate("() => document.body.classList.contains('rv-queue-on')"))
        page.locator("#rv-queue-toggle").click()
        time.sleep(0.2)

        # ---------------- 复制给 Claude -----------------------------------
        page.locator('.rv-shot[data-shot="S001"]').locator('[data-act="ai-ctx"]').click()
        time.sleep(0.5)
        clip = page.evaluate("() => navigator.clipboard.readText()")
        check("AI context copied", "镜头: S001" in clip and "shots/S001.yaml" in clip)

        # ---------------- home: continue chip + clickable counts ----------
        page.goto(base + "/", wait_until="load")
        page.wait_for_selector(".ck-hero", timeout=10000)
        time.sleep(0.8)
        chip = page.locator("a.ck-continue-btn")
        check("continue chip renders", chip.count() == 1)
        check("continue chip links to /review",
              chip.count() == 1 and chip.get_attribute("href") == "/review")
        check("continue chip carries position",
              chip.count() == 1 and "S00" in chip.inner_text())
        strip_btn = page.locator("button.ck-scount").first
        check("strip counts are buttons", strip_btn.count() == 1)
        strip_btn.click()
        time.sleep(0.6)
        on = page.locator('.fchips .fchip[aria-pressed="true"]')
        check("strip click filters shots grid", on.count() == 1)

        # ---------------- #50a/b: A/B deep-link, unread row, AV memory ----
        page.goto(base + "/review", wait_until="networkidle")
        ab = page.locator('a[href="/?compare=S001"]')
        check("review card offers A/B link (2 takes)", ab.count() == 1)
        ab.first.click()
        try:
            page.wait_for_selector(".cmp-overlay", timeout=10000)
            check("A/B deep-link opens the takes overlay", True)
        except Exception:
            check("A/B deep-link opens the takes overlay", False)
        page.keyboard.press("Escape")
        page.evaluate(
            "() => localStorage.setItem('manju-reviewed-' + "
            "document.querySelector('meta[name=\"manju-project\"]').content, "
            "JSON.stringify({ts: 1, takes: {S001: ['take_01']}}))")
        page.goto(base + "/", wait_until="load")
        page.wait_for_selector(".ck-strip", timeout=10000)
        time.sleep(1.0)
        check("cockpit shows unread-takes row",
              page.locator("button.ck-scount", has_text="新 take 未阅").count() == 1)
        page.goto(base + "/review", wait_until="networkidle")
        page.evaluate("() => { document.querySelector('.rv-shot video').playbackRate = 1.5; }")
        time.sleep(0.4)
        page.reload(wait_until="networkidle")
        time.sleep(0.5)
        rate = page.evaluate("() => document.querySelector('.rv-shot video').playbackRate")
        check("playback rate survives reload", abs(rate - 1.5) < 0.01, str(rate))

        check("no page errors", not errs, "; ".join(errs)[:200])
        browser.close()

    gui.shutdown()
    gui.close()

    from browser_verify import RESULTS
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
