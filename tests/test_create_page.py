"""`manju gui` — the 创作 /create workspace (round V, goal item 2 GUI half).

The creation funnel (build/funnel.py) rendered as a *product surface*, not a
protocol: a horizontal stage rail (立意→梗概→节拍→剧本→分镜→生成计划→生成) with
the current stage's textarea workspace over story/*.md, a skill modal, and a
read-only echo of the director's proposals (approve stays on /director).

These tests pin, against the SAME engine core the CLI drives:
  * the rail renders all seven stages with the funnel's states (fresh → 立意
    current; writing brief advances the current highlight to 梗概);
  * the workbench textarea loads the file, saves it verbatim + lands an event,
    refuses a path-escape stage, and honours the readonly-403 + token gates;
  * the 生成模板 button writes byte-for-byte the SAME template `manju create`
    scaffolds (no forked template);
  * the skill modal endpoint serves a skill's escaped body (the human twin of
    `manju skills show`);
  * the plan stage lists the director's proposals but offers no approve button;
  * the nav carries the 创作 entry and the page ships no inline JS (CSP-safe).

Server-rendered, so a plain GET carries the real fixture content; the POST gates
are exercised against a real in-process server (no browser). No ffmpeg.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from manju.build import director as d
from manju.build import funnel
from manju.core.events import tail_events
from manju.gui import create_page, pages
from manju.gui.server import create_server

# Bodies that clear the funnel's >80-char real-content gate (headings excluded).
BRIEF_BODY = (
    "# 立意\n\n"
    "一句话立意:雨夜便利店里,一个陌生人递来一把伞,改变了收银员整晚的心情与选择。\n"
    "给谁看:喜欢都市温情短片、常刷抖音的年轻观众,痛点是孤独与被看见的渴望。\n"
    "平台与时长:竖屏,目标三十秒,发抖音与小红书。\n"
)
SYNOPSIS_BODY = (
    "# 梗概\n\n"
    "深夜的便利店只剩收银员小夏一人,窗外暴雨如注。一个浑身湿透的陌生人进门买了瓶热饮,"
    "却在离开时把伞留在柜台,只留下一句你下班也会淋雨吧。小夏打烊后追出门,雨里两个人的"
    "距离,从一把伞开始慢慢拉近。\n"
)


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    """Keep the page off the real ~/.manju (skill user-overlay, gui state)."""
    monkeypatch.setenv("MANJU_SKILLS_DIR", str(tmp_path / "_skills"))


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


@pytest.fixture
def gui_readonly(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human",
                           readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), (
                payload.decode("utf-8") if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload.decode("utf-8") if raw else json.loads(payload or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _html(server, path):
    return _req(server, path, raw=True)


def _post(server, path, body, token="__use__"):
    tok = server.token if token == "__use__" else token
    headers = {"X-Manju-Token": tok} if tok is not None else {}
    return _req(server, path, method="POST", body=body, headers=headers)


def _write(project, relpath, text):
    p = project.root / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# ------------------------------------------------------------- stage rail (A)


def test_rail_renders_all_seven_stages_fresh_current_brief(tmp_project):
    """Fresh project: the rail shows all seven stages and 立意 is the ONE
    current stage; the rest are current/done exactly per funnel_status."""
    html = create_page.render_create(tmp_project, "tok")
    status = funnel.funnel_status(tmp_project)

    # every stage's 中文名 + id is on the rail
    for s in status["stages"]:
        assert s["cn"] in html
        assert f'data-stage="{s["id"]}"' in html
    # exactly the funnel's current stage is highlighted
    assert 'cw-stage cw-current' in html
    assert html.count("cw-stage cw-current") == 1
    assert status["current"] == "brief"
    # the current stage carries its next_action as the hero affordance
    assert "cw-next" in html
    # every stage carries its skill chip
    assert 'data-act="skill"' in html
    assert "creation-funnel" in html


def test_rail_advances_current_when_brief_written(tmp_project):
    """Walk: writing a real brief advances the current highlight to 梗概."""
    assert funnel.funnel_status(tmp_project)["current"] == "brief"
    _write(tmp_project, "story/brief.md", BRIEF_BODY)

    html = create_page.render_create(tmp_project, "tok")
    assert funnel.funnel_status(tmp_project)["current"] == "synopsis"
    # brief is now done (✓), synopsis is current
    assert 'data-stage="brief"' in html
    # the synopsis card is the current one
    assert 'cw-stage cw-current" data-stage="synopsis"' in html
    # a done stage renders the done class
    assert "cw-stage cw-done" in html


# --------------------------------------------------- workbench: load + save (B)


def test_workbench_textarea_loads_current_file(gui):
    """The current stage's textarea is prefilled with the file's content."""
    _write(gui.project, "story/brief.md", BRIEF_BODY)
    status, _, html = _html(gui, "/create")
    assert status == 200
    assert "cw-text" in html
    # synopsis is current now; brief content loads in the brief editor (reachable)
    assert "一句话立意" in html  # brief body baked into its (done) editor
    # the AI-proposes-human-decides hint is present (part C)
    assert "让 AI 起草" in html
    assert 'manju auto' in html


def test_workbench_save_writes_file_and_event(gui):
    """Saving the textarea writes the story file verbatim + lands an event."""
    status, _, data = _post(gui, "/api/create/save",
                            {"stage": "brief", "text": BRIEF_BODY})
    assert status == 200, data
    assert data["ok"] is True and data["path"] == "story/brief.md"
    # file written verbatim (trailing newline is the only normalization)
    on_disk = (gui.project.root / "story" / "brief.md").read_text(encoding="utf-8")
    assert on_disk.startswith(BRIEF_BODY.rstrip("\n"))
    # event recorded (same jsonl the CLI would write)
    actions = [e.get("action") for e in tail_events(gui.project.root, 20)]
    assert "funnel_edit" in actions
    # and the funnel now sees brief as done → current advanced
    assert funnel.funnel_status(gui.project)["current"] == "synopsis"


def test_workbench_save_creates_absent_file(gui):
    """Saving a not-yet-existing stage file creates it (created=True)."""
    assert not (gui.project.root / "story" / "synopsis.md").exists()
    status, _, data = _post(gui, "/api/create/save",
                            {"stage": "synopsis", "text": SYNOPSIS_BODY})
    assert status == 200
    assert data["created"] is True
    assert (gui.project.root / "story" / "synopsis.md").exists()


def test_workbench_save_refuses_path_escape(gui):
    """A stage that is not a known pre-storyboard file is refused — the fixed
    allow-list makes a path-escape attempt an unknown stage, nothing written."""
    status, _, data = _post(
        gui, "/api/create/save",
        {"stage": "../../../etc/passwd", "text": "x" * 200})
    assert status == 400
    assert "unknown stage" in data["error"]
    # nothing escaped the project root
    assert not (gui.project.root.parent / "etc").exists()


def test_workbench_save_readonly_403(gui_readonly):
    status, _, data = _post(gui_readonly, "/api/create/save",
                            {"stage": "brief", "text": BRIEF_BODY})
    assert status == 403
    assert "readonly" in data["error"]


def test_workbench_save_token_guard(gui):
    status, _, data = _post(gui, "/api/create/save",
                            {"stage": "brief", "text": BRIEF_BODY}, token=None)
    assert status == 403
    assert "Token" in data["error"] or "token" in data["error"]


# ------------------------------------------------ scaffold parity with CLI (B)


def test_scaffold_endpoint_matches_cli_template(gui):
    """The 生成模板 button writes byte-for-byte the SAME template the CLI
    `manju create <stage>` scaffolds (the funnel's own SCAFFOLDS)."""
    status, _, data = _post(gui, "/api/create/scaffold", {"stage": "beats"})
    assert status == 200, data
    assert data["path"] == "story/beats.md"
    on_disk = (gui.project.root / "story" / "beats.md").read_text(encoding="utf-8")
    _, template = funnel.SCAFFOLDS["beats"]
    assert on_disk == template
    # a funnel_scaffold event (same as the CLI) is recorded
    actions = [e.get("action") for e in tail_events(gui.project.root, 20)]
    assert "funnel_scaffold" in actions


def test_scaffold_endpoint_refuses_non_scaffoldable(gui):
    """script/storyboard are not scaffoldable via create → clean 400."""
    status, _, data = _post(gui, "/api/create/scaffold", {"stage": "script"})
    assert status == 400
    assert "error" in data


# ----------------------------------------------------- skill modal endpoint


def test_skill_endpoint_serves_body(gui):
    """The skill modal endpoint serves a skill's full SKILL.md body (the human
    twin of `manju skills show`)."""
    status, _, data = _req(gui, "/api/create/skill?id=creation-funnel")
    assert status == 200
    assert data["id"] == "creation-funnel"
    # the body is the real skill text
    assert isinstance(data["text"], str) and len(data["text"]) > 100
    assert "创作漏斗" in data["text"]
    # the raw markdown travels as JSON DATA (never injected as HTML); the client
    # renders it via textContent, so a body is delivered intact, not escaped-away
    assert "name:" in data["text"] or "#" in data["text"]


def test_skill_endpoint_unknown_404(gui):
    status, _, data = _req(gui, "/api/create/skill?id=no-such-skill")
    assert status == 404
    assert "error" in data


def test_skill_body_is_rendered_escaped_client_side(tmp_project):
    """The page renders the skill body via textContent (not innerHTML) — the
    modal markup carries an empty <pre>, never server-injected skill HTML."""
    html = create_page.render_create(tmp_project, "tok")
    assert '<pre id="cw-modalbody"' in html
    # the JS only ever assigns textContent for the modal body (no innerHTML)
    js = create_page.render_create_js()
    assert "innerHTML" not in js
    assert "textContent" in js


# --------------------------------------------------------- plan stage (B)


def _advance_to_plan(project, add_shot):
    for rel, body in (("story/brief.md", BRIEF_BODY),
                      ("story/synopsis.md", SYNOPSIS_BODY),
                      ("story/beats.md",
                       "# 节拍\n\n- Hook: 暴雨夜便利店陌生人把伞留在柜台的特写只有三秒钟真好。\n"
                       "- Value: 小夏的犹豫与回忆交代她一个人撑过的无数个孤独深夜时光真长。\n"
                       "- Payoff: 她冲进雨里追上陌生人伞下两个人第一次久久地对视没有说话。\n"
                       "- CTA: 你也遇到过那把突然出现的伞吗点赞关注看下一集温柔继续。\n"),
                      ("story/script.md",
                       "# 剧本\n\n场景一 便利店 内景 夜。小夏靠在收银台后打哈欠,雨声盖过了空调的嗡鸣声。\n"
                       "小夏(自语):这种天,应该不会再有人来了吧,还是早点打烊回家好了。\n"
                       "陌生人推门而入,风把门帘吹得乱响,他浑身湿透,买了瓶热饮又默默离开。\n")):
        _write(project, rel, body)
    add_shot(project, "S001")


def test_plan_stage_lists_proposals_no_approve_button(tmp_project, add_shot):
    """At the 生成计划 stage the workbench lists the director's proposals and
    links to /director — but renders NO confirm/execute button (one approval
    surface, §2d)."""
    _advance_to_plan(tmp_project, add_shot)
    assert funnel.funnel_status(tmp_project)["current"] == "plan"

    prop = d.propose(tmp_project, [{"type": "snapshot", "label": "cp"}],
                     actor="human")
    html = create_page.render_create(tmp_project, "tok")

    assert "生成计划 Plan" in html
    assert "approve-before-spend" in html
    assert prop.id in html              # the proposal is listed
    assert 'href="/director"' in html   # links to the ONE approval surface
    # no approve buttons duplicated here
    assert 'data-act="confirm"' not in html
    assert 'data-act="run"' not in html


# --------------------------------------------------------------- chrome


def test_nav_entry_present(gui):
    """The 创作 nav entry is present (first after 工作台) on every surface."""
    assert ("/create", "创作") in pages._NAV
    idx = pages._NAV.index(("/create", "创作"))
    assert pages._NAV[idx - 1] == ("/", "工作台")
    status, _, html = _html(gui, "/create")
    assert 'href="/create"' in html and "创作" in html


def test_page_is_csp_safe_no_inline_js(gui):
    """CSP-safe by construction: no inline <script> body, no inline handlers,
    no inline style=; JS/CSS load from external files under a strict CSP."""
    status, headers, html = _html(gui, "/create")
    assert status == 200
    assert 'src="/create.js"' in html
    assert 'href="/create.css"' in html
    # no inline script body, no inline event handlers, no inline styles
    assert "<script>" not in html
    assert "onclick=" not in html and "onload=" not in html
    assert "style=" not in html
    # the strict CSP header is attached (script-src 'self', no unsafe-inline)
    csp = headers.get("Content-Security-Policy", "")
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp


def test_state_endpoint_matches_funnel(gui):
    """/api/create/state serves the funnel status verbatim (the CLI --json shape)."""
    status, _, data = _req(gui, "/api/create/state")
    assert status == 200
    assert [s["id"] for s in data["stages"]] == list(funnel.STAGE_IDS)
    assert data["current"] == "brief"
