from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

from manju.core.container import Project
from manju.core.yamlio import write_yaml
from manju.gui import create_page, director_page
from manju.build.director import propose

BRIEF = """# 故事与结尾\n\n一句话立意：夜班检票员准备永久关闭广播站，却在最后一刻重新推起卷帘门，让一个等待父亲的孩子还能听见母亲留下的声音。给谁看：喜欢克制都市短片、关心人与陌生人之间微小承诺的观众。平台与时长：竖屏短片，目标四十五秒；结尾必须让检票员亲手关闭广播，却重新打开现实中的门。\n"""
SCRIPT = """# 剧本与声音\n\n夜。空站。广播最后一次提示关门。检票员把钥匙插进卷帘门，忽然听见长椅下传来鞋底摩擦声。她回头，看见孩子抱着湿透的书包。孩子说：爸爸答应来。她停了很久，把门重新推开。广播被她亲手关掉，空站第一次真正安静下来。\n"""


def clean_html(page_html: str) -> str:
    # set_content cannot serve the normal localhost assets in this sandbox.
    # Remove external scripts; inject the exact page JavaScript separately.
    return re.sub(r"<script[^>]+src=[^>]+></script>", "", page_html, flags=re.I)


def make_project(root: Path) -> Project:
    p = Project.create(root / "wave8-interaction.manju", git_init=False)
    write_yaml(p.root / "bible" / "scenes.yaml", {"station": {"name": "空站", "description": "雨夜废站"}})
    write_yaml(p.root / "bible" / "characters.yaml", {"clerk": {"name": "检票员", "appearance": "深蓝制服"}})
    (p.root / "story" / "brief.md").write_text(BRIEF, encoding="utf-8")
    return p


def install_stubs(page, *, delay_ms: int = 0):
    page.add_script_tag(content=f"""
      window.TOKEN = 'tok';
      window.__calls = [];
      window.__toasts = [];
      window.__confirmResult = false;
      window.confirm = function () {{ return window.__confirmResult; }};
      window.toast = function (message, ok) {{ window.__toasts.push({{message:String(message), ok:!!ok}}); }};
      window.setTimeout = function () {{ return 1; }};
      window.post = function (url, payload) {{
        window.__calls.push({{url:url, payload:payload}});
        return new Promise(function (resolve) {{
          var done = function () {{ resolve({{status:200, data:{{ok:true}}}}); }};
          {'window.__realSetTimeout(done, ' + str(delay_ms) + ');' if delay_ms else 'done();'}
        }});
      }};
    """)


def main():
    os.environ["MANJU_EXECUTION_MODE"] = "strict_zero_cost"
    evidence = {"schema": "manju.product-polish-browser-evidence/v1", "wave": 8}
    with tempfile.TemporaryDirectory(prefix="manju-wave8-") as td:
        p = make_project(Path(td))
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox"])

            # CREATE: dirty-state, scaffold refusal, switch confirmation, Ctrl+S.
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.set_content(clean_html(create_page.render_create(p, "tok")), wait_until="domcontentloaded")
            page.add_script_tag(content="window.__realSetTimeout = window.setTimeout.bind(window);")
            install_stubs(page)
            page.add_script_tag(content=create_page.render_create_js())

            current = page.locator('.cw-editor:not(.hidden) .cw-text')
            current_stage = current.get_attribute("data-stage")
            assert current_stage == "synopsis", current_stage
            current.fill("这是一份尚未保存的场景概览。")
            dirty_text = page.locator('.cw-editor:not(.hidden) .cw-save-state').inner_text()
            before_event = page.evaluate("""
              () => { const ev = new Event('beforeunload', {cancelable:true}); window.dispatchEvent(ev); return ev.defaultPrevented; }
            """)

            # Scaffold must refuse while unsaved text exists.
            page.locator('.cw-editor:not(.hidden) [data-act="scaffold"]').click()
            calls_after_scaffold = page.evaluate("window.__calls.slice()")
            toast_after_scaffold = page.evaluate("window.__toasts.slice()")

            # Refuse stage switch first: draft remains visible.
            page.evaluate("window.__confirmResult = false")
            page.locator('[data-source-stage="brief"]').click()
            stage_after_refused_switch = page.locator('.cw-editor:not(.hidden) .cw-text').get_attribute("data-stage")
            draft_after_refused_switch = page.locator('.cw-editor:not(.hidden) .cw-text').input_value()

            # Accept switch: old draft remains in DOM, new editor receives focus.
            page.evaluate("window.__confirmResult = true")
            page.locator('[data-source-stage="brief"]').click()
            stage_after_accepted_switch = page.locator('.cw-editor:not(.hidden) .cw-text').get_attribute("data-stage")
            preserved_draft = page.locator('.cw-editor[data-stage="synopsis"] .cw-text').input_value()

            # Switch back and save via Ctrl+S. Exact current stage is posted once.
            page.locator('[data-source-stage="synopsis"]').click()
            page.keyboard.press("Control+S")
            page.keyboard.press("Control+S")
            page.wait_for_timeout(20)
            calls_after_save = page.evaluate("window.__calls.slice()")
            saved_state = page.locator('.cw-editor:not(.hidden) .cw-save-state').inner_text()

            evidence["create"] = {
                "initial_stage": current_stage,
                "dirty_label": dirty_text,
                "beforeunload_prevented": before_event,
                "scaffold_network_calls": calls_after_scaffold,
                "scaffold_warning": toast_after_scaffold[-1] if toast_after_scaffold else None,
                "stage_after_refused_switch": stage_after_refused_switch,
                "draft_after_refused_switch": draft_after_refused_switch,
                "stage_after_accepted_switch": stage_after_accepted_switch,
                "draft_preserved_after_switch": preserved_draft,
                "save_calls": calls_after_save,
                "saved_label": saved_state,
            }
            assert dirty_text == "未保存"
            assert before_event is True
            assert calls_after_scaffold == []
            assert stage_after_refused_switch == "synopsis"
            assert draft_after_refused_switch == "这是一份尚未保存的场景概览。"
            assert stage_after_accepted_switch == "brief"
            assert preserved_draft == "这是一份尚未保存的场景概览。"
            assert calls_after_save == [{"url": "/api/create/save", "payload": {"stage": "synopsis", "text": "这是一份尚未保存的场景概览。"}}]
            assert saved_state == "已保存"
            page.close()

            # DIRECTOR: double click confirm is one call and never runs.
            (p.root / "story" / "synopsis.md").write_text("# 场次概览\n\n广播站关闭前的一夜。\n", encoding="utf-8")
            (p.root / "story" / "beats.yaml").write_text("beats:\n  - id: B1\n    text: 关门\n  - id: B2\n    text: 孩子出现\n  - id: B3\n    text: 重新开门\n", encoding="utf-8")
            (p.root / "story" / "script.md").write_text(SCRIPT, encoding="utf-8")
            proposal = propose(p, [{
                "type": "truth_patch_set",
                "patches": [{"path": "story/script.md", "content": SCRIPT.replace("把门重新推开", "先关掉广播，再把门重新推开")}],
            }], why="让选择通过动作成立", actor="ai")

            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_content(clean_html(director_page.render_director(p, "tok")), wait_until="domcontentloaded")
            page.add_script_tag(content="window.__realSetTimeout = window.setTimeout.bind(window);")
            install_stubs(page, delay_ms=80)
            page.add_script_tag(content=director_page.render_director_js())

            btn = page.locator(f'[data-act="confirm"][data-id="{proposal.id}"]')
            assert btn.count() == 1
            # dispatch two clicks synchronously: the first disables the button.
            page.evaluate("""(id) => {
              const b = document.querySelector('[data-act="confirm"][data-id="' + id + '"]');
              b.click(); b.click();
            }""", proposal.id)
            page.wait_for_timeout(140)
            director_calls = page.evaluate("window.__calls.slice()")
            director_toasts = page.evaluate("window.__toasts.slice()")
            button_disabled = btn.is_disabled()
            button_text = btn.inner_text()
            run_buttons = page.locator('[data-act="run"]').count()
            evidence["director"] = {
                "proposal_id": proposal.id,
                "calls": director_calls,
                "toasts": director_toasts,
                "confirm_button_disabled": button_disabled,
                "confirm_button_text": button_text,
                "run_button_count": run_buttons,
            }
            assert director_calls == [{"url": "/api/director/confirm", "payload": {"id": proposal.id}}]
            assert not any(call["url"] == "/api/director/run" for call in director_calls)
            assert button_disabled is True
            assert run_buttons == 0
            page.close()
            browser.close()

    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
