"""Product Polish R1 Wave 11 — shared language, feedback and access behavior.

These tests exercise rendered documents and shared helpers rather than scanning
implementation source.  The GUI has several server-rendered shells, so this
suite pins the user-facing contract they must share: Chinese document language,
one keyboard skip link, one focusable main landmark, an honest no-JavaScript
fallback, non-duplicated scripts, and usable first-run / glossary surfaces.
"""

from __future__ import annotations

from collections import Counter
from http.client import HTTPConnection
import re
import threading

import pytest

from manju.gui import (
    create_page,
    director_page,
    edit,
    exports_page,
    ingest_page,
    lab_page,
    page,
    pages,
    pages_t,
    series_page,
    storyboard,
    workspace,
)
from manju.gui.a11y import HTML_LANG, NOSCRIPT_HTML, SKIP_LINK_HTML, main_open
from manju.gui.glossary import render_glossary_css, tooltip_html
from manju.gui.server import create_server

_SCRIPT_RE = re.compile(r'<script[^>]*\bsrc="([^"]+)"')


def _documents(project) -> dict[str, str]:
    body = '<section class="panel"><h1>测试页面</h1></section>'
    return {
        "home": page.render_page("测试项目", "tok"),
        "pages": pages._shell("审片", "tok", "/review", body, project),
        "pages-t": pages_t._shell("字幕", "tok", "/subtitles", body, project),
        "create": create_page._shell("创作", "tok", "/create", body, project),
        "director": director_page._shell("导演助手", "tok", "/director", body, project),
        "storyboard": storyboard._shell("分镜", "tok", body, project),
        "lab": lab_page._shell("镜头实验室", "tok", body, project),
        "ingest": ingest_page._shell("批量入库", "tok", body, project),
        "edit": edit._shell("剪辑", "tok", body, project),
        "exports": exports_page._shell("导出", "tok", body, project),
        "series": series_page._shell("剧集", "tok", body),
        "workspace": workspace.render_picker_page("tok", bound=project),
    }


def test_shared_accessibility_fragments_escape_classes_and_are_focusable():
    assert HTML_LANG == "zh-CN"
    assert SKIP_LINK_HTML == '<a class="mj-skip-link" href="#main-content">跳到主要内容</a>'
    assert 'role="alert"' in NOSCRIPT_HTML
    assert "项目文件不会因此被修改" in NOSCRIPT_HTML
    assert main_open('x" onfocus="bad') == (
        '<main id="main-content" tabindex="-1" class="x&quot; onfocus=&quot;bad">'
    )


def test_every_primary_document_has_one_language_skip_link_and_main(tmp_project):
    for name, document in _documents(tmp_project).items():
        assert f'<html lang="{HTML_LANG}">' in document, name
        assert document.count(SKIP_LINK_HTML) == 1, name
        assert document.count('id="main-content"') == 1, name
        assert '<main id="main-content" tabindex="-1"' in document, name
        assert document.index(SKIP_LINK_HTML) < document.index('id="main-content"'), name
        assert "需要启用 JavaScript 才能使用工作台" in document, name
        assert "项目文件不会因此被修改" in document, name
        assert "requires JavaScript" not in document, name


def test_primary_documents_do_not_load_any_script_twice(tmp_project):
    for name, document in _documents(tmp_project).items():
        counts = Counter(_SCRIPT_RE.findall(document))
        duplicates = {src: count for src, count in counts.items() if count > 1}
        assert not duplicates, f"{name}: {duplicates}"


def test_glossary_help_is_a_real_button_and_escapes_user_text():
    rendered = tooltip_html("provider", label='来源 <script>alert("x")</script>')
    assert '<button type="button" class="mj-help" aria-expanded="false"' in rendered
    assert 'role="tooltip"' in rendered
    assert "术语说明：" in rendered
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "tabindex=\"0\"" not in rendered  # native button supplies keyboard semantics

    css = render_glossary_css()
    assert "min-width: 24px" in css
    assert "min-height: 24px" in css
    assert '.mj-help[aria-expanded="true"]' in css


def test_workspace_first_run_is_chinese_first_and_preserves_input_on_failure(tmp_project):
    document = workspace.render_picker_page("tok", bound=tmp_project)
    assert "打开或新建项目" in document
    assert "项目工作区" in document
    assert "Manju 会恢复上次的页面和工作位置" in document
    assert "打开现有项目" in document
    assert "新建项目" in document
    assert "项目没有被修改，刚才填写的内容仍然保留" in document
    assert 'id="ws-feedback"' in document and "hidden" in document
    for noisy in ("(workspace)", "(Open)", "(Create)", "(orientation)"):
        assert noisy not in document


def test_browser_404_keeps_navigation_and_protection_context(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        conn = HTTPConnection(host, port, timeout=5)
        conn.request("GET", "/this-page-does-not-exist", headers={"Accept": "text/html"})
        response = conn.getresponse()
        document = response.read().decode("utf-8")
        conn.close()
        assert response.status == 404
        assert f'<html lang="{HTML_LANG}">' in document
        assert SKIP_LINK_HTML in document
        assert '<main id="main-content" tabindex="-1"' in document
        assert "这里没有这个页面" in document
        assert "项目文件没有被修改" in document
        assert 'href="/"' in document
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
