from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from manju.core.container import Project
from manju.core.models import ShotSpec, TakeSidecar
from manju.core.spec import compute_spec_hash
from manju.core.yamlio import write_yaml
from manju.gui import glossary, page as app_page, pages, project_action
from manju.gui.cockpit import cockpit_data
from manju.gui.state import build_state, project_identity
from manju.gui.userstate import set_mode, set_show_pro_terms

BRIEF = (
    "# 故事与结尾\n\n"
    "雨夜的末班车停在空站，准备离开的检票员发现一个孩子一直等着不会回来的父亲。"
    "她本来只想按时关门，却最终陪孩子走完整条回家路，也因此放下了自己多年没有面对的离别和逃避。\n"
)
SYNOPSIS = (
    "# 场次梗概\n\n"
    "第一场从空站和催促关门开始，检票员发现孩子后被迫停下。第二场两人在雨里寻找线索，"
    "孩子承认父亲已经失约很多次。最后检票员把自己的伞交给孩子，并决定亲自送他回家，"
    "她也第一次拨通了多年未联系的母亲电话。\n"
)
BEATS = (
    "# 变化节拍\n\n"
    "- 广播催促关门，检票员准备结束一天。\n"
    "- 她发现孩子仍然等在空站，选择先询问而不是赶走。\n"
    "- 孩子承认父亲可能不会来，检票员的态度发生变化。\n"
    "- 两人共撑一把伞离开，检票员也拨出自己的和解电话。\n"
)
SCRIPT = (
    "# 剧本与声音\n\n"
    "夜。空站。广播最后一次提示关门。检票员把钥匙插进卷帘门，忽然听见长椅下传来鞋底摩擦声。"
    "她回头，看见孩子抱着湿透的书包。孩子说：爸爸答应来。她停了很久，把门重新推开。"
    "广播声被她亲手关掉，空站第一次真正安静下来。\n"
)


class EmptyRunner:
    def list(self):
        return []

    def interrupted(self):
        return []


def make_project(root: Path) -> Project:
    if root.exists():
        shutil.rmtree(root)
    p = Project.create(root, name="雨夜末班车", git_init=False)
    write_yaml(p.root / "bible" / "scenes.yaml", {
        "station": {"name": "雨夜站台", "description": "冷雨中的末班车站，远处广告灯映在积水里。"},
    })
    write_yaml(p.root / "bible" / "characters.yaml", {
        "clerk": {"name": "检票员", "appearance": "深蓝制服，旧手表，神情克制", "voice": "低而平静"},
        "child": {"name": "孩子", "appearance": "湿透的黄色书包，红色雨鞋", "voice": "轻声但固执"},
    })
    for name, text in {
        "brief.md": BRIEF, "synopsis.md": SYNOPSIS, "beats.md": BEATS, "script.md": SCRIPT,
    }.items():
        (p.root / "story" / name).write_text(text, encoding="utf-8")

    for i in range(1, 7):
        sid = f"S{i:03d}"
        shot = ShotSpec.model_validate({
            "id": sid,
            "scene": "station",
            "characters": ["clerk", "child"] if i >= 2 else ["clerk"],
            "dialogue": {"speaker": "clerk", "text": "等一下。"},
            "duration": "auto",
        })
        p.save_shot(shot)
        idx = p.load_index()
        idx.order.append(sid)
        p.save_index(idx)

    for i in range(1, 7):
        sid = f"S{i:03d}"
        shot = p.load_shot(sid)
        spec_hash = compute_spec_hash(shot, p.load_bible())
        src = root.parent / f"wave9-{sid}.mp4"
        src.write_bytes(b"local-demo-video-" + sid.encode("ascii"))
        take = p.register_take(sid, src, TakeSidecar(provider="local-demo", spec_hash=spec_hash))
        shot = p.load_shot(sid)
        shot.status.selected_take = take.name
        if sid != "S003":
            shot.status.take_notes[take.name] = "推荐：动作、构图和连续性符合当前镜头。"
            shot.status.review = "approved"
        p.save_shot(shot)
    return p


def clean_html(html: str, css: str, project_token: str) -> str:
    html = re.sub(r'<link rel="stylesheet" href="[^"]+">\n?', '', html)
    html = re.sub(r'<script[^>]*src="[^"]+"[^>]*></script>\n?', '', html)
    html = html.replace(
        '<meta name="manju-token"',
        f'<meta name="manju-project" content="{project_token}">\n<meta name="manju-token"',
        1,
    )
    return html.replace('</head>', '<style>' + css + '</style></head>')


def main() -> None:
    repo = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2]).resolve()
    work = Path(sys.argv[3]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ["MANJU_EXECUTION_MODE"] = "strict_zero_cost"
    os.environ["MANJU_GUI_STATE"] = str(work.parent / "wave9-gui-state.json")
    set_mode("beginner")
    set_show_pro_terms(False)
    p = make_project(work)
    token = project_identity(p)

    state = build_state(p, EmptyRunner())
    state.update({
        "fp": "wave9-home-demo",
        "project_token": token,
        "readonly": False,
        "index_rev": "wave9-index",
        "workspace": {"active": "wave9-demo", "count": 1},
    })
    # Product evidence only: local in-memory job rows, no worker and no transport.
    state["jobs"] = [
        {"id": "job-running", "kind": "edit_preview", "kind_zh": "本地预览", "state": "running", "progress": 0.62},
        {"id": "job-done", "kind": "export", "kind_zh": "审片包", "state": "done"},
    ]
    cockpit = cockpit_data(p)
    css = "\n".join([
        app_page.render_css(), pages.render_pages_css(),
        glossary.render_glossary_css(), project_action.render_project_action_css(),
    ])
    html = clean_html(app_page.render_page(p.load_config().name, "tok"), css, token)

    responses = {
        "/api/state": state,
        "/api/cockpit": cockpit,
        "/api/ui-state": {"ui": {"migrated_from_localstorage": True}},
        "/api/app/status": {"app_mode": True, "running_job": None, "queued_count": 0},
        "/api/meta/job-kinds": {"kinds": {}},
        "/api/proposals": {"proposals": []},
        "/api/tasks": {"available": False, "tasks": [], "pending": [], "spend": {"total": 0, "currency": "CNY"}},
        "/api/build": {"result": {"estimated_cost": 0, "plan": []}},
        "/api/timeline": {"timeline": None},
        "/api/evaluate": {"totals": {}},
    }
    js_stub = """
      window.__apiCalls = [];
      window.__realSetTimeout = window.setTimeout.bind(window);
      window.setTimeout = function () { return 1; };
      window.requestJson = async function(method, path, body, options) {
        window.__apiCalls.push({method:String(method), path:String(path), body:body || null});
        const key = String(path).split('?')[0];
        if (method === 'POST' && key === '/api/ui-state') {
          return {ui: Object.assign({migrated_from_localstorage:true}, (body && body.ui) || {})};
        }
        const data = window.__responses[key];
        if (data === undefined) return {};
        if (options && options.returnStatus) return {status:200, data:data};
        return data;
      };
      window.fetch = async function(url) {
        window.__apiCalls.push({method:'FETCH', path:String(url), body:null});
        return {ok:true, status:200, json:async()=>({changed:false}), text:async()=>'{"changed":false}'};
      };
    """

    evidence = {"schema": "manju.product-polish-home-evidence/v1", "wave": 9, "screens": {}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path="/usr/bin/chromium", headless=True, args=["--no-sandbox", "--disable-gpu"])
        for name, viewport in {
            "home-cockpit-desktop": {"width": 1440, "height": 900},
            "home-cockpit-narrow": {"width": 390, "height": 844},
        }.items():
            context = browser.new_context(viewport=viewport, device_scale_factor=1)
            page = context.new_page()
            page.set_content(html, wait_until="domcontentloaded")
            page.add_script_tag(content="window.__responses = " + json.dumps(responses, ensure_ascii=False) + ";")
            page.add_script_tag(content=js_stub)
            page.add_script_tag(content=app_page.render_js())
            page.wait_for_selector(".ck-focus-card")
            page.wait_for_function("document.querySelectorAll('.ck-journey').length === 3")
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)
            facts = page.evaluate("""() => ({
              viewport:{w:innerWidth,h:innerHeight},
              document:{w:document.documentElement.scrollWidth,h:document.documentElement.scrollHeight},
              horizontalOverflow:document.documentElement.scrollWidth-innerWidth,
              focus:document.querySelector('.ck-focus-label')?.textContent || '',
              focusAction:document.querySelector('.ck-focus-actions .btn')?.textContent || '',
              journeys:Array.from(document.querySelectorAll('.ck-journey')).map(x=>x.textContent.trim()),
              priorityCards:Array.from(document.querySelectorAll('.ck-priority-card h2')).map(x=>x.textContent.trim()),
              workbenchOpen:document.querySelector('#workbench-details')?.open,
              shotsPainted:document.querySelector('#shots')?.children.length || 0,
              headerPainted:document.querySelector('#header')?.children.length || 0,
              headerHTML:document.querySelector('#header')?.innerHTML || '',
              elementCount:document.querySelectorAll('*').length,
              cockpitHeight:Math.round(document.querySelector('#cockpit')?.getBoundingClientRect().height || 0),
              calls:window.__apiCalls.slice(),
            })""")
            assert facts["horizontalOverflow"] <= 0
            assert facts["workbenchOpen"] is False
            assert facts["shotsPainted"] == 0
            assert facts["headerPainted"] == 0
            before_paths = [c["path"].split("?")[0] for c in facts["calls"]]
            assert "/api/build" not in before_paths
            assert "/api/timeline" not in before_paths
            assert "/api/proposals" not in before_paths
            page.locator("#workbench-details > summary").click()
            page.wait_for_function("document.querySelector('#workbench-details').open === true")
            page.wait_for_function("document.querySelector('#shots').children.length > 0")
            expanded = page.evaluate("""() => ({
              workbenchOpen:document.querySelector('#workbench-details').open,
              shotsPainted:document.querySelector('#shots').children.length,
              headerPainted:document.querySelector('#header').children.length,
              elementCount:document.querySelectorAll('*').length,
              calls:window.__apiCalls.slice(),
            })""")
            assert expanded["workbenchOpen"] is True
            assert expanded["shotsPainted"] > 0
            assert expanded["headerPainted"] > 0
            facts["expanded"] = expanded
            evidence["screens"][name] = facts
            context.close()
        browser.close()
    (out / "home-cockpit-facts.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
