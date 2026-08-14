"""Product-polish behavior tests for the shared Manju application chrome.

The chrome is presentation-only: it exposes the existing six production stages,
current execution policy, project switcher, view controls and current stage
sub-navigation without creating project state or bypassing beginner-mode gates.
"""

from __future__ import annotations

from http.client import HTTPConnection
import threading

from manju.gui.pages import nav_html
from manju.gui.server import create_server


def _get_html(server, path: str) -> str:
    host, port = server.server_address[:2]
    conn = HTTPConnection(host, port, timeout=5)
    conn.request("GET", path)
    response = conn.getresponse()
    body = response.read().decode("utf-8")
    conn.close()
    assert response.status == 200, (path, response.status, body[:300])
    return body


def test_shared_chrome_exposes_six_stable_stages_and_current_subnav(monkeypatch):
    monkeypatch.setenv("MANJU_EXECUTION_MODE", "strict_zero_cost")

    body = nav_html(
        "/storyboard",
        mode="beginner",
        show_terms=False,
        hint_dismissed=True,
        project_name="我的长片项目",
    )

    assert 'class="pnav-appbar"' in body
    assert 'class="pnav-stagebar"' in body
    assert body.count('<a class="pnav-stage') == 6
    for label in ("工作台", "创作", "镜头", "审片", "成片", "工具"):
        assert f">{label}</" in body

    assert 'aria-current="step"' in body
    assert 'class="pnav-subbar"' in body
    assert 'href="/storyboard"' in body
    assert 'href="/lab"' in body
    assert 'href="/ingest"' in body
    assert 'aria-current="page"' in body

    # Beginner mode still removes advanced destinations from ordinary
    # discovery, while retaining the stable six-stage product model.
    assert 'href="/providers"' not in body
    assert 'href="/routing"' not in body
    assert 'href="/doctor"' not in body
    assert 'data-execution-status="strict"' in body
    assert "本地安全" in body
    assert "我的长片项目" in body


def test_finishing_stage_does_not_duplicate_its_existing_five_step_journey(monkeypatch):
    monkeypatch.delenv("MANJU_EXECUTION_MODE", raising=False)

    body = nav_html("/mixer", mode="pro", hint_dismissed=True)

    assert 'aria-current="step"' in body
    assert "成片" in body
    # The finishing pages already render their richer 剪辑→字幕→混音→包装→导出
    # journey in-page; the global chrome must not add a second copy.
    assert 'class="pnav-subbar"' not in body
    assert 'data-execution-status="standard"' in body
    assert "标准执行" in body


def test_beginner_hint_is_compact_home_guidance_not_a_repeated_page_banner():
    home = nav_html("/", mode="beginner", hint_dismissed=False)
    deep = nav_html("/review", mode="beginner", hint_dismissed=False)

    assert 'id="mj-mode-hint"' in home
    assert "新手视图已开启" in home
    assert "查看全部功能" in home
    assert 'data-mode="pro"' in home
    assert 'id="mj-mode-hint"' not in deep


def test_view_controls_are_progressively_disclosed_but_keep_existing_actions():
    body = nav_html("/review", mode="beginner", show_terms=True)

    assert 'class="mj-view-menu"' in body
    assert "新手视图" in body
    assert 'data-mode="beginner"' in body
    assert 'data-mode="pro"' in body
    assert 'id="mj-terms-toggle" checked' in body
    assert "显示专业术语" in body


def test_invalid_execution_mode_is_visible_and_fail_closed_in_chrome(monkeypatch):
    monkeypatch.setenv("MANJU_EXECUTION_MODE", "strict-zero-cost")

    body = nav_html("/", mode="pro")

    assert 'data-execution-status="invalid"' in body
    assert "模式错误" in body
    assert "strict-zero-cost" in body


def test_direct_render_pages_share_mode_policy_project_and_glossary_chrome(tmp_project):
    # These four surfaces historically called nav_html() directly, which made
    # them silently render as pro mode and omit the shared glossary/project
    # behavior. Their actual HTTP documents must now use the same chrome owner.
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path in ("/director", "/storyboard", "/lab", "/ingest"):
            body = _get_html(server, path)
            assert 'class="mj-mode-beginner' in body, path
            assert body.count('/glossary.js') == 1, path
            assert body.count('/project-action.js') == 1, path
            assert 'id="mj-ws-btn"' in body, path
            assert 'class="mj-ws-label"' in body, path
            assert 'data-execution-status="standard"' in body, path
            assert 'class="pnav-stagebar"' in body, path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_primary_workflow_headings_are_chinese_first_with_optional_terms(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        expected = {
            "/create": ("创作", "Create"),
            "/director": ("导演助手", "Director"),
            "/storyboard": ("分镜工作台", "Storyboard"),
            "/lab": ("镜头实验室", "Shot lab"),
            "/ingest": ("批量入库", "Batch ingest"),
        }
        for path, (chinese, english) in expected.items():
            body = _get_html(server, path)
            assert f'<h1>{chinese}<span class="mj-en" aria-hidden="true"> ({english})</span></h1>' in body, path
            assert f'<h1>{chinese} {english}</h1>' not in body, path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_active_advanced_page_explains_location_without_relisting_advanced_links():
    body = nav_html("/providers", mode="beginner", hint_dismissed=True)

    assert 'aria-current="step"' in body
    assert 'class="pnav-hidden-current"' in body
    assert "服务商" in body and "专业功能" in body
    assert 'href="/providers"' not in body
    assert 'href="/library"' in body
