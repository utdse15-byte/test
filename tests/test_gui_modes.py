"""`manju gui` round U — 新手/专业 view switch + plain-language glossary.

Two goal items:

  * item 18 — the §10 plain-language glossary adopted VERBATIM as product copy:
    the table completeness, ``term`` / ``tooltip_html`` escaping, and the
    显示专业术语 toggle that greys the English original (a body class);
  * item 16 — the 新手/专业 mode switch (§8 Resolve page-tab model): a per-user
    view mode persisted in ``~/.manju/gui_state.json``, defaulting to 新手 for a
    fresh user and 专业 for an existing one, flipped by a token-gated POST, that
    shapes the nav + panel visibility WITHOUT ever deleting project data or
    403-ing a page (every hidden page stays reachable by URL).

The HTTP tests follow :mod:`tests.test_gui_pages` fixtures: the server runs
in-process on a real socket, and we assert on the served HTML / JSON directly.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request

import pytest

from manju.gui.glossary import GLOSSARY, render_glossary_css, render_glossary_js, term, tooltip_html
from manju.gui.server import create_server
from manju.gui.userstate import (
    MODES,
    gui_state_path,
    is_mode_hint_dismissed,
    is_show_pro_terms,
    resolve_mode,
    set_mode,
    set_show_pro_terms,
)

# The §10 table of REPORTS/ROUND-U-REFERENCES.md — the authoritative rows the
# GLOSSARY must mirror VERBATIM (engineering term -> (中文用户词, 一句话说明)).
_REPORT_ROWS: dict[str, tuple[str, str]] = {
    "provider": ("生成来源", "这条画面/配音是用哪个 AI 模型或服务做出来的。"),
    "take": ("版本 / 这一条", "同一个镜头反复生成的不同版本，可并排对比、挑一条留用。"),
    "stale": ("待更新 / 需重做", "上游改过之后这一条还是旧的，得重新生成才跟得上。"),
    "fallback": ("备用方案 / 兜底", "首选来源失败时自动改用的替代方案，保证片子出得来。"),
    "routing": ("智能派单 / 线路选择", "系统自动决定每条任务交给哪个模型来做，你不用手动指定。"),
    "ledger": ("制作台账 / 制作记录", "完整记下每一步生成了什么、花了多少，随时可回查。"),
    "sidecar": ("配套信息", "跟素材一起存的说明文件（参数、来源、时间），不占画面。"),
    "manifest": ("成片清单 / 配方单", "记录整片由哪些镜头、素材、参数拼成，照它能一模一样再做一遍。"),
    "QC": ("质量检查 / 质检", "自动帮你查画面、字幕、音量有没有明显问题。"),
    "repair plan": ("修复方案", "针对质检查出的问题，系统给出的一键修补步骤。"),
    "lock": ("锁定", "锁住这一条，避免被重新生成或不小心改动。"),
    "snapshot": ("存档点", "把当前状态存下来，随时可以回到这一刻。"),
    "rollback": ("还原 / 回到上一版", "放弃这次改动，退回之前的存档点。"),
    "proxy": ("预览版 / 低清代理", "用小体积低清文件流畅预览，导出时自动换回高清。"),
    "render": ("合成导出 / 渲染", "把时间线上所有内容合成为最终成片。"),
    "timeline": ("时间线", "按时间先后排素材的编辑区（沿用剪映同名词）。"),
    "shot": ("镜头", "一段连续画面，分镜表里的一格。"),
    "storyboard": ("分镜 / 分镜脚本", "把整片拆成一个个镜头的计划表，先定好再生成。"),
    "budget breaker": ("花费护栏 / 预算上限", "花费到上限就自动暂停，避免不知不觉超支。"),
    "dry-run": ("试跑 / 预演", "只算不真正生成，先看计划和预估花费再决定要不要开工。"),
}


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    """Point the user-level shelves at tmp dirs so the providers/routing pages
    never touch a real ~/.manju (mirrors tests.test_gui_pages)."""
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "_library"))
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "_providers"))
    monkeypatch.setenv("MANJU_ROUTING", str(tmp_path / "_user_routing.yaml"))


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, host=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, dict(resp.headers), (payload if raw else
                                                     json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, dict(exc.headers), parsed


def _html(server, path, **kw):
    status, headers, body = _req(server, path, raw=True, **kw)
    return status, headers, (body.decode("utf-8") if isinstance(body, bytes) else body)


def _post(server, path, body, token="__default__", **kw):
    headers = {} if token is None else {"X-Manju-Token": server.token if token == "__default__" else token}
    return _req(server, path, method="POST", body=body, headers=headers, **kw)


# ================================================================ glossary (item 18)


def test_glossary_matches_report_verbatim():
    """The §10 table is the single source of truth — every row, verbatim."""
    assert GLOSSARY == _REPORT_ROWS
    assert len(GLOSSARY) == 20
    # every tooltip is a real one-line explanation (ends with the CJK full stop)
    for eng, (cn, tip) in GLOSSARY.items():
        assert cn and tip, eng
        assert tip.endswith("。"), eng


def test_glossary_covers_every_named_concept():
    """The concepts round-U named explicitly all resolve to a Chinese word."""
    for eng in ("provider", "take", "stale", "fallback", "routing", "ledger",
                "sidecar", "manifest", "QC", "repair plan", "lock", "snapshot",
                "rollback", "proxy", "render", "timeline", "shot", "storyboard",
                "budget breaker", "dry-run"):
        assert eng in GLOSSARY


def test_term_returns_primary_word():
    assert term("provider") == "生成来源"
    assert term("take") == "版本"          # first slash-alternative
    assert term("stale") == "待更新"
    assert term("fallback") == "备用方案"
    assert term("QC") == "质量检查"
    assert term("timeline") == "时间线"    # no slash: whole word


def test_tooltip_html_is_accessible_and_escaped():
    html = tooltip_html("provider")
    assert "生成来源" in html                                   # the 中文用户词
    assert "(provider)" in html                                 # greyed English original
    assert GLOSSARY["provider"][1] in html                      # the explanation
    assert 'role="tooltip"' in html and 'tabindex="0"' in html  # keyboard-focusable
    assert 'class="mj-en"' in html                              # greyed-term span

    # a caller-supplied label with HTML metacharacters is escaped, never raw
    evil = tooltip_html("take", label="<script>x</script>&")
    assert "<script>x</script>" not in evil
    assert "&lt;script&gt;x&lt;/script&gt;&amp;" in evil


def test_term_unknown_raises():
    with pytest.raises(KeyError):
        term("no_such_engineering_term")


# ================================================================ mode persistence (item 16)


def _state_file():
    return gui_state_path()


def test_fresh_user_defaults_to_beginner(tmp_path, monkeypatch):
    """No gui_state.json on disk (a truly fresh user) -> 新手, and it persists."""
    path = tmp_path / "brand_new.json"
    monkeypatch.setenv("MANJU_GUI_STATE", str(path))
    assert not path.exists()
    assert resolve_mode() == "beginner"
    # the resolution is written back so a later onboarding-dismiss can't flip it
    assert path.exists()
    assert json.loads(path.read_text())["mode"] == "beginner"
    assert resolve_mode() == "beginner"        # stable on the second read


def test_existing_user_defaults_to_pro(tmp_path, monkeypatch):
    """Any prior gui_state.json (an upgrade user) -> 专业, so nothing regresses."""
    path = tmp_path / "existing.json"
    path.write_text(json.dumps({"version": 1,
                                "onboarding_dismissed": {"/some/proj": "2026-01-01T00:00:00"}}),
                    encoding="utf-8")
    monkeypatch.setenv("MANJU_GUI_STATE", str(path))
    assert resolve_mode() == "pro"
    assert json.loads(path.read_text())["mode"] == "pro"


def test_explicit_mode_wins(tmp_path, monkeypatch):
    path = tmp_path / "explicit.json"
    monkeypatch.setenv("MANJU_GUI_STATE", str(path))
    set_mode("beginner")
    assert resolve_mode() == "beginner"        # even though the file now exists
    set_mode("pro")
    assert resolve_mode() == "pro"
    with pytest.raises(ValueError):
        set_mode("bogus")


def test_choosing_a_mode_dismisses_the_hint(tmp_path, monkeypatch):
    path = tmp_path / "hint.json"
    monkeypatch.setenv("MANJU_GUI_STATE", str(path))
    resolve_mode()                             # fresh -> beginner, hint live
    assert is_mode_hint_dismissed() is False
    set_mode("beginner")
    assert is_mode_hint_dismissed() is True     # found the switch


def test_show_pro_terms_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "terms.json"))
    assert is_show_pro_terms() is False
    set_show_pro_terms(True)
    assert is_show_pro_terms() is True
    set_show_pro_terms(False)
    assert is_show_pro_terms() is False


def test_modes_constant():
    assert MODES == ("beginner", "pro")


# ================================================================ nav + mode over HTTP


_PRO_LINKS = ('href="/providers"', 'href="/routing"', 'href="/doctor"', 'href="/compare"')
_KEPT_LINKS = ('href="/review"', 'href="/edit"', 'href="/mixer"', 'href="/packaging"')


def test_nav_differs_between_modes(gui):
    """新手 omits the pro-only page links + body is mj-mode-beginner + a hint bar;
    专业 restores every link. The switch controls appear in both."""
    # fresh user -> beginner
    _, _, beginner = _html(gui, "/")
    assert "mj-mode-beginner" in beginner
    for link in _PRO_LINKS:
        assert link not in beginner
    for link in _KEPT_LINKS:
        assert link in beginner
    assert 'id="mj-mode-hint"' in beginner              # fresh-user hint bar
    assert 'data-mode="beginner"' in beginner and 'data-mode="pro"' in beginner
    assert "显示专业术语" in beginner                     # §10 toggle present

    # flip to 专业 via the token-gated POST
    status, _, data = _post(gui, "/api/mode", {"mode": "pro"})
    assert status == 200 and data["mode"] == "pro"

    _, _, pro = _html(gui, "/")
    assert "mj-mode-pro" in pro
    for link in _PRO_LINKS + _KEPT_LINKS:
        assert link in pro                              # everything back
    assert 'id="mj-mode-hint"' not in pro               # no hint in pro


def test_hidden_pages_still_reachable_by_url(gui):
    """新手 hides the pro links but NEVER 403s the page — every one is reachable
    by URL (mode shapes nav, not access)."""
    assert resolve_mode() == "beginner"  # fresh default via the conftest tmp state
    for path in ("/providers", "/routing", "/doctor", "/compare"):
        status, _, body = _html(gui, path)
        assert status == 200, path
        # and its own nav still carries the beginner shape (pro links omitted)
        assert 'href="/providers"' not in body


def test_mode_switch_is_token_gated(gui):
    """The flip is a mutating POST: no token -> 403, bad token -> 403."""
    status, _, data = _req(gui, "/api/mode", method="POST", body={"mode": "pro"})
    assert status == 403 and "token" in data["error"].lower()
    assert _post(gui, "/api/mode", {"mode": "pro"}, token="wrong")[0] == 403
    assert _post(gui, "/api/pro-terms", {"show": True}, token=None)[0] == 403


def test_mode_invalid_value_rejected(gui):
    status, _, data = _post(gui, "/api/mode", {"mode": "expert"})
    assert status == 400 and "expert" in data["error"]


def test_mode_hint_dismiss(gui):
    """Dismissing the hint drops the bar without changing the mode."""
    _, _, before = _html(gui, "/")
    assert 'id="mj-mode-hint"' in before
    status, _, data = _post(gui, "/api/mode", {"dismiss_hint": True})
    assert status == 200 and data["hint_dismissed"] is True and data["mode"] == "beginner"
    _, _, after = _html(gui, "/")
    assert 'id="mj-mode-hint"' not in after and "mj-mode-beginner" in after


def test_show_terms_toggles_body_class(gui):
    """显示专业术语 is persisted and stamps mj-show-terms on the body server-side."""
    _, _, off = _html(gui, "/")
    assert "mj-show-terms" not in off
    status, _, data = _post(gui, "/api/pro-terms", {"show": True})
    assert status == 200 and data["show"] is True
    _, _, on = _html(gui, "/")
    assert "mj-show-terms" in on
    # and the checkbox renders checked
    assert 'id="mj-terms-toggle" checked' in on
    assert _post(gui, "/api/pro-terms", {"show": False})[0] == 200
    _, _, off2 = _html(gui, "/")
    assert "mj-show-terms" not in off2


def test_pro_only_panels_marked_but_present(gui):
    """新手 mode CSS-hides pro panels; the markup stays in the DOM (never deleted),
    so the strings remain and a switch to 专业 reveals them."""
    _post(gui, "/api/mode", {"mode": "beginner"})
    # mixer advanced rows (ducking) + transition-hit panel carry the class
    _, _, mixer = _html(gui, "/mixer")
    assert "mj-pro-only" in mixer
    assert "闪避参数" in mixer                    # ducking markup still present
    assert "转场音" in mixer                      # transition panel still present


def test_glossary_assets_served(gui):
    status, headers, css = _html(gui, "/glossary.css")
    assert status == 200 and "text/css" in headers["Content-Type"]
    assert ".mj-term" in css and "mj-show-terms" in css and "mj-pro-only" in css
    status, headers, js = _html(gui, "/glossary.js")
    assert status == 200 and "javascript" in headers["Content-Type"]
    assert "mj-mode-btn" in js and "/api/mode" in js and "/api/pro-terms" in js
    # the SPA links both chrome assets
    _, _, spa = _html(gui, "/")
    assert "/glossary.css" in spa and "/glossary.js" in spa


def test_glossary_tooltips_swept_into_pages(gui):
    """The 中文用户词 + tooltip appear as product copy at first appearance."""
    _post(gui, "/api/mode", {"mode": "pro"})              # ensure pages reachable in nav too
    _, _, providers = _html(gui, "/providers")
    assert "生成来源" in providers and GLOSSARY["provider"][1] in providers
    _, _, review = _html(gui, "/review")
    assert 'class="mj-term"' in review                   # tooltip spans woven in
    _, _, routing = _html(gui, "/routing")
    assert "智能派单" in routing                          # routing tooltip


def test_mode_toggle_readonly_403(tmp_project, monkeypatch, tmp_path):
    """A readonly workbench refuses the mode + terms POSTs (they are mutations),
    but the pages still render and default the mode."""
    monkeypatch.setenv("MANJU_GUI_STATE", str(tmp_path / "ro_state.json"))
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _html(server, "/")[0] == 200                 # renders read-only
        status, _, data = _post(server, "/api/mode", {"mode": "pro"})
        assert status == 403 and "readonly" in data["error"]
        assert _post(server, "/api/pro-terms", {"show": True})[0] == 403
    finally:
        server.shutdown()
        server.close()


def test_chrome_and_glossary_import_cleanly():
    """The chrome assets are pure functions renderable without a project."""
    assert len(render_glossary_css()) > 400
    assert len(render_glossary_js()) > 200
