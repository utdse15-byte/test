"""Product-polish tests for the shared five-step finishing journey.

The rail is presentation-only: it links the existing edit/subtitle/mix/package/
export owners, loads status lazily from an existing read-only projection and
never becomes a second workflow or Picture-Lock store.
"""

from __future__ import annotations

import hashlib
import json
import threading
import urllib.request

import pytest

from manju.gui.finishing_journey import finishing_journey_html, finishing_status
from manju.gui.server import create_server


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _get(server, path: str, *, json_body: bool = False):
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}")
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
        if json_body:
            return response.status, json.loads(raw or b"{}")
        return response.status, raw.decode("utf-8")


def _tree_snapshot(root):
    """Exact tree shape and content hashes; status reads must not alter either."""
    rows = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            rows[rel] = ("symlink", path.readlink().as_posix())
        elif path.is_file():
            rows[rel] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
        elif path.is_dir():
            rows[rel] = ("dir", "")
    return rows


def test_journey_is_navigation_not_completion_state():
    body = finishing_journey_html("/mixer")
    assert body.count('class="mj-finish-step') == 5
    assert 'href="/edit"' in body
    assert 'href="/subtitles"' in body
    assert 'href="/mixer" aria-current="step"' in body
    assert 'href="/packaging"' in body
    assert 'href="/exports"' in body
    assert "不会替你锁片或改项目" in body


def test_finishing_status_degrades_honestly_on_empty_project(tmp_project):
    status = finishing_status(tmp_project)
    assert status["ok"] is True
    assert status["final"]["state"] == "missing"
    assert status["final"]["label"] == "尚无成片"
    assert status["picture_lock"]["eligible"] is False
    assert status["picture_lock"]["label"] == "尚无镜头可锁片"
    assert "还没有镜头" in status["picture_lock"]["summary"]


def test_final_status_failure_does_not_hide_lock_status(tmp_project, monkeypatch):
    from manju.build import exportstatus, readiness

    def broken_deliverables(_project):
        raise RuntimeError("export projection unavailable")

    monkeypatch.setattr(exportstatus, "deliverables", broken_deliverables)
    monkeypatch.setattr(
        readiness,
        "media_eligibility",
        lambda _project: {
            "counts": {
                "proxy-only": 0,
                "candidate": 1,
                "final-eligible": 0,
                "none": 0,
            },
            "picture_lock_eligible": False,
            "picture_lock_reasons": ["S001: candidate"],
        },
    )

    status = finishing_status(tmp_project)
    assert status["final"]["state"] == "unknown"
    assert "export projection unavailable" in status["final"]["basis"]
    assert status["picture_lock"]["label"] == "待人工确认"
    assert "1 个候选镜头" in status["picture_lock"]["summary"]


def test_lock_status_failure_does_not_hide_final_status(tmp_project, monkeypatch):
    from manju.build import exportstatus, readiness

    monkeypatch.setattr(
        exportstatus,
        "deliverables",
        lambda _project: [
            exportstatus.DeliverableRow(
                kind="final",
                label="成片",
                path="final/final_v1.mp4",
                freshness=exportstatus.Freshness.STALE,
                basis="上游镜头已经变化",
            )
        ],
    )

    def broken_readiness(_project):
        raise RuntimeError("readiness unavailable")

    monkeypatch.setattr(readiness, "media_eligibility", broken_readiness)
    status = finishing_status(tmp_project)
    assert status["final"]["state"] == "stale"
    assert status["final"]["label"] == "成片待更新"
    assert status["picture_lock"]["label"] == "锁片资格未知"
    assert "readiness unavailable" in status["picture_lock"]["reasons"][0]


def test_all_five_pages_share_one_journey_and_shared_chrome(gui):
    for path, label in (
        ("/edit", "剪辑"),
        ("/subtitles", "字幕"),
        ("/mixer", "混音"),
        ("/packaging", "包装"),
        ("/exports", "导出"),
    ):
        code, body = _get(gui, path)
        assert code == 200
        assert body.count("data-finishing-journey") == 1, path
        assert f'data-active="{path}"' in body, path
        assert f"当前：{label}" in body, path
        assert f'href="{path}" aria-current="step"' in body, path
        # Every finishing surface now uses the same mode/project chrome. The
        # export page previously bypassed this owner entirely.
        assert 'id="mj-ws-btn"' in body, path
        assert 'id="mj-terms-toggle"' in body, path
        assert 'class="mj-mode-beginner"' in body, path
        assert '<script src="/common.js" defer></script>' in body, path


def test_finishing_status_endpoint_and_common_js_are_wired(gui):
    code, payload = _get(gui, "/api/finishing/status", json_body=True)
    assert code == 200
    assert payload["ok"] is True
    assert set(payload) == {"ok", "final", "picture_lock"}

    code, js = _get(gui, "/common.js")
    assert code == 200
    assert "/api/finishing/status" in js
    assert "data-finish-final" in js
    assert "data-finish-lock" in js
    assert "不会替你锁片" in js


def test_finishing_status_endpoint_does_not_write_project(gui, tmp_project):
    before = _tree_snapshot(tmp_project.root)
    code, payload = _get(gui, "/api/finishing/status", json_body=True)
    after = _tree_snapshot(tmp_project.root)

    assert code == 200
    assert payload["ok"] is True
    assert after == before


def test_default_titles_are_chinese_first_and_english_is_progressive(gui):
    _code, subtitles = _get(gui, "/subtitles")
    assert '<h1>字幕<span class="mj-en"' in subtitles
    assert "(Subtitles)" in subtitles
    assert "字幕 Subtitles" not in subtitles

    _code, edit = _get(gui, "/edit")
    assert '<h1>剪辑<span class="mj-en"' in edit
    assert "(Edit)" in edit
    assert "剪辑 Edit" not in edit


def test_edit_first_paint_does_not_compute_finishing_status(tmp_project, monkeypatch):
    from manju.gui import finishing_journey
    from manju.gui.edit import render_edit

    def forbidden(_project):
        raise AssertionError("finishing status must remain lazy")

    monkeypatch.setattr(finishing_journey, "finishing_status", forbidden)
    body = render_edit(tmp_project, "token", {})
    assert "data-finishing-journey" in body
    assert "正在检查成片" in body


def test_status_preserves_stale_final_and_lock_eligibility(monkeypatch):
    from manju.build import exportstatus, readiness

    monkeypatch.setattr(
        exportstatus,
        "deliverables",
        lambda _project: [
            exportstatus.DeliverableRow(
                kind="final",
                label="成片",
                path="renders/final/final_v3.mp4",
                freshness=exportstatus.Freshness.STALE,
                basis="字幕已修改，需要重新构建",
            )
        ],
    )
    monkeypatch.setattr(
        readiness,
        "media_eligibility",
        lambda _project: {
            "picture_lock_eligible": True,
            "counts": {
                "proxy-only": 0,
                "candidate": 0,
                "final-eligible": 2,
                "none": 0,
            },
            "picture_lock_reasons": [],
        },
    )

    status = finishing_status(object())
    assert status["final"]["state"] == "stale"
    assert status["final"]["label"] == "成片待更新"
    assert status["picture_lock"]["eligible"] is True
    assert status["picture_lock"]["label"] == "可进入锁片评审"
