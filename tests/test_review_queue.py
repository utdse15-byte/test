"""Tests for the /review QUEUE mode (round X agent XF — user pains #7/#8:
batch review by shot status + fine-grained clip previews).

Coverage:
  * filter chips + per-shot data-buildstate/data-review attributes (the
    server-side data the client-side queue JS filters over) — combining
    build/stale ShotState with the three-state review, exactly as the
    contract asks (待选/待审/已通过/待更新);
  * queue toolbar markup (filter chips, mode toggle, position indicator);
  * per-take previews on the review page's alt-take cards are LAZY —
    data-src/data-poster only, never an eagerly-wired src= (never block
    page GET on ffmpeg) — and the underlying /preview endpoint they point
    at actually serves;
  * approve (/api/storyboard/approve) and note (/api/take-note) round-trips
    reflect back into the next /review GET's data-review / data-reviewed
    attributes — the SAME checked/locked paths every other surface uses;
  * guards: token/readonly on the actions the queue toolbar drives.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from manju.gui.server import create_server


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
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


def _post(server, path, body, token="__use__"):
    tok = server.token if token == "__use__" else token
    headers = {} if tok is None else {"X-Manju-Token": tok}
    return _req(server, path, method="POST", body=body, headers=headers)


# ======================================================= queue toolbar markup


def test_queue_toolbar_renders_filter_chips_and_toggle(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, html = _html(gui, "/review")
    assert status == 200
    assert 'id="rv-queue-toggle"' in html
    assert 'id="rv-queue-pos"' in html
    assert 'id="rv-q-prev"' in html and 'id="rv-q-next"' in html
    for key in ("all", "needs_selection", "needs_review", "approved", "stale"):
        assert f'data-filter="{key}"' in html


def test_shot_carries_buildstate_and_review_attributes(gui, tmp_project, add_shot, make_take):
    # no takes at all -> build state "missing", review defaults to needs_review
    add_shot(tmp_project, "S001")
    # a take exists but none selected -> "needs_selection"
    add_shot(tmp_project, "S002")
    make_take(tmp_project, "S002", "h")

    status, _, html = _html(gui, "/review")
    assert status == 200
    assert 'data-shot="S001"' in html
    assert 'data-buildstate="missing"' in html
    assert 'data-review="needs_review"' in html
    assert 'data-buildstate="needs_selection"' in html
    assert "无版本" in html and "待挑选" in html   # the 中文 build-state badges


def test_approved_review_state_reflected_in_buildstate_attrs(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/storyboard/approve", {"shot": "S001", "review": "approved"})
    assert status == 200 and data["ok"] is True

    status2, _, html = _html(gui, "/review")
    assert status2 == 200
    assert 'data-shot="S001"' in html
    assert 'data-review="approved"' in html


# ==================================================== per-take lazy previews


def test_alt_take_preview_is_lazy_not_eager(gui, tmp_project, add_shot, make_take):
    """round X agent XF (pain #8): an alt-take card gets a ▶ button + a
    <video data-src=...> — never an eagerly-wired src=, so opening /review
    with many shots/takes never triggers an ffmpeg transcode just from the
    page load (never block page GET on ffmpeg)."""
    add_shot(tmp_project, "S001")
    selected = make_take(tmp_project, "S001", "h")
    alt = make_take(tmp_project, "S001", "h2")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", selected.name))

    status, _, html = _html(gui, "/review")
    assert status == 200
    assert 'class="rv-alt-play"' in html
    assert 'class="rv-alt-video hidden"' in html
    assert "data-src=" in html
    assert f'换用 {alt.name}' in html
    # lazy only — no eagerly-wired src= on the alt preview element itself
    assert 'rv-alt-video hidden" muted controls preload="none" data-src=' in html


def test_take_preview_source_actually_serves(gui, tmp_project, add_shot, make_take):
    """The lazy data-src the JS assigns on click points at a URL that really
    serves (browser-safe .mp4 takes route straight through /media)."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    rel = tmp_project.relpath(take.media_path)

    status, _, html = _html(gui, "/review")
    assert status == 200
    from urllib.parse import quote

    assert ("/media/" + quote(rel, safe="/")) in html

    status2, _, body = _html(gui, "/media/" + quote(rel, safe="/"))
    assert status2 == 200 and body


# ==================================================== approve / note round-trips


def test_approve_round_trip_through_existing_checked_path(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/storyboard/approve", {"shot": "S001", "review": "approved"})
    assert status == 200 and data["changed"] == 1
    shot = tmp_project.load_shot("S001")
    assert shot.status.review == "approved" and shot.status.approved is True

    # cycling back through the SAME endpoint (as the queue "换一条" flow would)
    status2, _, data2 = _post(gui, "/api/storyboard/approve",
                              {"shot": "S001", "review": "needs_review"})
    assert status2 == 200
    assert tmp_project.load_shot("S001").status.review == "needs_review"


def test_note_round_trip_marks_reviewed(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name))

    status, _, data = _post(gui, "/api/take-note",
                            {"shot": "S001", "take": take.name, "text": "队列模式备注"})
    assert status == 200 and data["ok"] is True
    assert tmp_project.load_shot("S001").status.take_notes[take.name] == "队列模式备注"

    _, _, html = _html(gui, "/review")
    assert 'data-reviewed="1"' in html


def test_select_different_take_round_trip(gui, tmp_project, add_shot, make_take):
    """换一条 (select a different take) reuses the SAME checked select path
    the plain review page's alt buttons already call."""
    add_shot(tmp_project, "S001")
    first = make_take(tmp_project, "S001", "h")
    second = make_take(tmp_project, "S001", "h2")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", first.name))

    status, _, data = _post(gui, "/api/select", {"shot": "S001", "take": second.name})
    assert status == 200 and data["take"] == second.name
    assert tmp_project.load_shot("S001").status.selected_take == second.name


# ================================================================== guards


def test_queue_actions_require_token(gui, tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    for path, body in (
        ("/api/storyboard/approve", {"shot": "S001", "review": "approved"}),
        ("/api/take-note", {"shot": "S001", "take": take.name, "text": "x"}),
        ("/api/select", {"shot": "S001", "take": take.name}),
    ):
        status, _, data = _post(gui, path, body, token=None)
        assert status == 403 and "Token" in data["error"]
        assert _post(gui, path, body, token="wrong")[0] == 403


def test_queue_actions_refused_readonly(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "h")
    server = create_server(tmp_project, host="127.0.0.1", port=0,
                           actor="human", readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for path, body in (
            ("/api/storyboard/approve", {"shot": "S001", "review": "approved"}),
            ("/api/take-note", {"shot": "S001", "take": take.name, "text": "x"}),
        ):
            status, _, data = _post(server, path, body)
            assert status == 403 and "readonly" in data["error"]
        assert tmp_project.load_shot("S001").status.review is None
    finally:
        server.shutdown()
        server.close()
