"""Real-browser verification of the GUI/board flows (dev harness, NOT a test).

Run: python scripts/dev/browser_verify.py   (needs playwright + a Chromium;
set MANJU_DEV_CHROMIUM to a chrome executable if playwright has no browsers).
First landed with the 2026-07-13 UX program: the 15-check pass over the live
gui + board servers caught a real bug HTTP tests could not (the first-
annotation insert no-op). Future sessions: re-run after touching gui/pages.py,
gui/server.py, board/board.py or board/server.py JS/flow behaviour.

Drives headless Chromium (the pre-provisioned Playwright build) against a
REAL `manju gui` server and a REAL board server on a scaffolded project with
real (tiny) mp4 takes — the flows the audit could only exercise over raw HTTP:

  F14 — /review: 好 then 弃 on ONE card without a refresh must both succeed
        (the CAS token refreshes from the response, no misleading 乐观锁 409).
  F19 — the keyboard hint says g 好 · x 弃.
  F16 — board: a successful annotate updates IN PLACE (a window marker set
        before the click must survive — no location.reload) and the banner
        confirms; the active tab survives in the URL hash across reload.
  F20 — an ERROR toast stays past the old 3.6s auto-dismiss (click-dismiss).
  F21 — navigating to a bad URL renders the HTML 404 page with a way home.
  F15 — the board banner is viewport-fixed (computed style position: fixed).
  #45 — stale-tab guard: after ANOTHER tab rebinds the server to a second
        project, a click on the old /review page must be refused (409
        project_switched) and the full-page overlay must appear — the write
        never lands in the wrong project.
  polish — /create is alive: the skill modal starts hidden (computed style,
        not just the attribute — the [hidden] app.css guard), opens from a
        chip, closes on Escape, and the page underneath accepts clicks.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "src"))

try:
    from playwright.sync_api import sync_playwright  # noqa: E402
except ImportError:
    sys.exit("需要 playwright(pip install playwright)+ 一个 Chromium;"
             "浏览器路径可用 MANJU_DEV_CHROMIUM 指定")

from manju.core.container import Project  # noqa: E402
from manju.core.models import TakeSidecar  # noqa: E402
from manju.core.yamlio import write_yaml  # noqa: E402

ROOT = Path(os.environ.get("MANJU_DEV_BV_DIR") or tempfile.mkdtemp(prefix="manju-bv-"))
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))


def make_project() -> Project:
    import shutil

    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    project = Project.create(ROOT / "浏览器验证", git_init=False)
    write_yaml(project.root / "bible" / "scenes.yaml",
               {"s": {"name": "场", "description": "d", "lighting": "l"}})
    write_yaml(project.root / "bible" / "characters.yaml",
               {"c": {"name": "角", "appearance": "a", "voice": "v", "personality": "p"}})
    write_yaml(project.root / "shots" / "S001.yaml", {
        "id": "S001", "scene": "s", "characters": ["c"],
        "action": {"main": "走进便利店"}, "dialogue": {"text": "……"},
        "duration_ms": 1000,
    })
    write_yaml(project.root / "shots" / "index.yaml", {"order": ["S001"]})
    for i in (1, 2):
        clip = ROOT / f"clip{i}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-t", "1", "-i", f"testsrc2=size=192x108:rate=24",
             "-f", "lavfi", "-t", "1", "-i", "sine=frequency=440",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(clip)],
            check=True, capture_output=True)
        project.register_take("S001", clip, TakeSidecar(provider="test", spec_hash=f"h{i}"))
    # select take_01 so /review has a player + verdict targets
    from manju.core.writes import select_take_checked
    from manju.runtime.buildlock import build_lock

    with build_lock(project.root, actor="human"):
        select_take_checked(project, "S001", "take_01", actor="human", via="cli")
    return project


def main() -> int:
    project = make_project()

    from manju.board.server import make_server
    from manju.gui.server import create_server

    gui = create_server(project, host="127.0.0.1", port=0, actor="human")
    threading.Thread(target=gui.serve_forever, daemon=True).start()
    board = make_server(project, host="127.0.0.1", port=0)
    threading.Thread(target=board.serve_forever, daemon=True).start()
    gui_base = f"http://127.0.0.1:{gui.port}"
    board_base = f"http://127.0.0.1:{board.server_address[1]}"

    with sync_playwright() as pw:
        exe = os.environ.get("MANJU_DEV_CHROMIUM")
        browser = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
        page = browser.new_page()

        # ---------------- F19 + F14: /review two actions on one card
        page.goto(gui_base + "/review", wait_until="networkidle")
        hint = page.locator(".rv-progress, header, body").first.inner_text()
        body_text = page.content()
        check("F19 hint says g 好 · x 弃", "g 好" in body_text and "x 弃" in body_text
              and "g 通过" not in body_text)

        card = page.locator(".rv-shot").first
        card.locator('[data-act="good"]').click()
        page.wait_for_selector("#toast .toast-item.good", timeout=8000)
        good_toast = page.locator("#toast .toast-item.good").last.inner_text()
        check("F14 first verdict (好) succeeds", "S001" in good_toast, good_toast)

        card.locator('[data-act="reject"]').click()
        time.sleep(1.2)
        bad = page.locator("#toast .toast-item.bad")
        lock_hit = any("乐观锁" in bad.nth(i).inner_text() for i in range(bad.count()))
        check("F14 second verdict (弃) NOT refused by own save", not lock_hit,
              "乐观锁 409 seen" if lock_hit else "no CAS refusal")

        note_in = card.locator(".rv-note-input")
        note_in.fill("光太硬,重打")
        card.locator('[data-act="note"]').click()
        time.sleep(1.0)
        toasts = page.locator("#toast .toast-item")
        note_saved = any("备注已存" in toasts.nth(i).inner_text() for i in range(toasts.count()))
        lock_hit2 = any("乐观锁" in toasts.nth(i).inner_text() for i in range(toasts.count()))
        check("F14 third action (备注) also lands", note_saved and not lock_hit2)

        # ---------------- F20: an error toast is sticky
        page.goto(gui_base + "/review", wait_until="networkidle")
        page.evaluate("() => window.toast('假装很长的引擎错误信息 x'.repeat(3), false)")
        time.sleep(4.5)  # well past the old 3.6s auto-dismiss
        sticky = page.locator("#toast .toast-item.bad").count() >= 1
        check("F20 error toast survives 4.5s", sticky)
        if sticky:
            page.locator("#toast .toast-item.bad").first.click()
            time.sleep(0.3)
            check("F20 error toast dismisses on click",
                  page.locator("#toast .toast-item.bad").count() == 0)

        # ---------------- F21: HTML 404 with a way home (gui + board)
        page.goto(gui_base + "/stale-bookmark")
        check("F21 gui 404 page", "页面不存在" in page.content() and 'href="/"' in page.content())
        page.goto(board_base + "/old-link")
        check("F21 board 404 page", "页面不存在" in page.content())

        # ---------------- board: F15 fixed banner + F16 in-place annotate + tab hash
        page.goto(board_base + "/", wait_until="networkidle")
        pos = page.evaluate(
            "() => getComputedStyle(document.getElementById('mj-banner')).position")
        check("F15 banner viewport-fixed", pos == "fixed", f"position={pos}")

        page.evaluate("() => { window.__no_reload_marker = 42; }")
        ann_text = page.locator(".ann-form .ann-text").first
        ann_text.fill("第3帧道具穿帮")
        page.locator('[data-annsubmit="1"]').first.click()
        page.wait_for_function("() => document.getElementById('mj-banner').textContent.includes('批注已记录')", timeout=8000)
        marker = page.evaluate("() => window.__no_reload_marker")
        check("F16 annotate succeeds IN PLACE (no reload)", marker == 42,
              f"marker={marker}")
        row_text = page.locator(".ann-list").first.inner_text()
        check("F16 new annotation row rendered client-side", "第3帧道具穿帮" in row_text)

        # second annotation immediately — the refreshed CAS token must hold
        ann_text.fill("同帧再补一条")
        page.locator('[data-annsubmit="1"]').first.click()
        page.wait_for_function("() => document.getElementById('mj-banner').textContent.includes('批注已记录')", timeout=8000)
        banner_txt = page.evaluate("() => document.getElementById('mj-banner').textContent")
        check("F16 second annotate lands (rev refreshed)", "批注已记录" in banner_txt)

        # tab hash persistence
        tabs = page.locator(".mj-tab")
        if tabs.count() >= 2:
            key = tabs.nth(1).get_attribute("data-tab")
            tabs.nth(1).click()
            time.sleep(0.2)
            check("F16 tab writes hash", page.evaluate("() => location.hash") == f"#mjtab-{key}")
            page.reload(wait_until="networkidle")
            active = page.locator(".mj-tab.active").first.get_attribute("data-tab")
            check("F16 tab restored after reload", active == key, f"active={active}")
        else:
            check("F16 tab hash (skipped: <2 tabs)", True, "only one tab rendered")

        # F17: the gui /review side column mirrors the board annotation
        page.goto(gui_base + "/review", wait_until="networkidle")
        content = page.content()
        check("F17 board annotations visible in gui /review",
              "第3帧道具穿帮" in content and "看板批注" in content)

        # ---------------- #48a: keyboard approve on /review (a = 通过)
        page.goto(gui_base + "/review", wait_until="networkidle")
        page.locator("body").press("a")
        try:
            page.wait_for_function(
                "() => document.querySelector('.rv-shot').getAttribute('data-review') === 'approved'",
                timeout=8000)
            check("#48a keyboard a approves the active card", True)
        except Exception:
            state = page.locator(".rv-shot").first.get_attribute("data-review")
            check("#48a keyboard a approves the active card", False,
                  f"data-review={state}")

        # ---------------- GUI polish wave: /create is ALIVE in a real browser.
        # It shipped with the skill modal permanently covering the page —
        # `.cw-modal{display:flex}` beat the UA [hidden] rule, so the overlay
        # rendered on load, blocked every click and could not be dismissed.
        # The app.css `[hidden]{display:none!important}` guard owns the fix.
        page.goto(gui_base + "/create", wait_until="load")
        time.sleep(0.5)
        disp = page.evaluate(
            "() => getComputedStyle(document.getElementById('cw-modal')).display")
        check("polish /create modal starts hidden", disp == "none", f"display={disp}")
        page.locator(".cw-skillchip").first.click()
        try:
            page.wait_for_function(
                "() => getComputedStyle(document.getElementById('cw-modal'))"
                ".display !== 'none'", timeout=8000)
            check("polish skill modal opens on chip click", True)
        except Exception:
            check("polish skill modal opens on chip click", False)
        page.keyboard.press("Escape")
        time.sleep(0.3)
        disp = page.evaluate(
            "() => getComputedStyle(document.getElementById('cw-modal')).display")
        check("polish skill modal closes on Escape", disp == "none", f"display={disp}")
        # with the overlay truly gone, the page accepts clicks again
        page.locator('button[data-act="save"]').first.click()
        try:
            page.wait_for_selector("#toast .toast-item.good", timeout=8000)
            check("polish /create page clickable (save lands + toasts)", True)
        except Exception:
            check("polish /create page clickable (save lands + toasts)", False)
        # the #45 block below assumes the tab sits on /review — restore that
        page.goto(gui_base + "/review", wait_until="networkidle")

        # ---------------- DECISIONS #45: the stale-tab guard, for real
        # This page (/review) rendered for 浏览器验证. Simulate another tab
        # switching the SERVER to a second project via the exempt rebind
        # action, then click a verdict here — the shared post() carries the
        # stale X-Manju-Project, the server answers 409 project_switched and
        # common.js raises the full-page overlay instead of writing.
        proj2 = Project.create(ROOT / "第二项目", git_init=False)
        page.evaluate(
            "(p) => fetch('/api/workspace/open', {method: 'POST',"
            " headers: {'Content-Type': 'application/json',"
            " 'X-Manju-Token': window.TOKEN},"
            " body: JSON.stringify({path: p})})",
            str(proj2.root))
        time.sleep(0.8)  # let the rebind land server-side
        page.locator(".rv-shot").first.locator('[data-act="good"]').click()
        try:
            page.wait_for_selector("#mj-proj-switched", timeout=8000)
            overlay_txt = page.locator("#mj-proj-switched").inner_text()
            check("#45 stale-tab click blocked by overlay",
                  "已停止读写" in overlay_txt, overlay_txt[:60])
        except Exception:
            check("#45 stale-tab click blocked by overlay", False,
                  "overlay never appeared")

        page.screenshot(path=str(ROOT / "review.png"), full_page=True)
        browser.close()

    gui.shutdown(); gui.close()
    board.shutdown(); board.server_close()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
