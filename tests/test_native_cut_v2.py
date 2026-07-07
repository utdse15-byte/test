"""`manju gui` — Native Cut v2 (round V, agent VF).

Deepens the /edit 剪辑 page into a deeply-optimized native-cut UX, per REPORTS
`ROUND-V-REFERENCES-2.md` "Native Cut v2 spec sketch":

  * a 6-key accelerator layer (Space / ←→ / I O / N / Z / [] / Ctrl+Z / ?), one
    keydown handler with an input-focus guard — every key ALSO reachable by
    click (WCAG 2.5.7 stays by construction);
  * snapping (magnet) of the playhead + trim I/O to clip/caption boundaries and
    whole seconds, toggled by ``N``, persisted per-user (gui_state.json);
  * honest git-backed undo — a 撤销 surface over the events.jsonl tail with a
    tier-1 field revert (re-apply the previous value through the same engine),
    tier-2 file rollback, tier-3 /history; a revert is itself an event;
  * Tier-1 playback — a ``<video>`` over the newest final/proxy served by the
    Range-capable /media endpoint, honest empty state before any render.

Companion to :mod:`tests.test_native_cut` and :mod:`tests.test_gui_edit`, both
of which stay green: every new surface is a thin accelerator over an existing
engine mutation, and no surface bypasses the token / readonly / host gates.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from manju.core.events import tail_events
from manju.gui.server import create_server


# ---------------------------------------------------------------- fixtures


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


def _post(server, path, body, token=None, **kw):
    headers = {"X-Manju-Token": token if token is not None else server.token}
    return _req(server, path, method="POST", body=body, headers=headers, **kw)


def _plant(project, rel, data=b"\x00\x01\x02\x03\x04\x05\x06\x07"):
    p = project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return rel


# =============================================== A. keyboard accelerator layer


def test_edit_js_carries_the_six_key_map_and_input_guard(gui):
    """The served /edit.js has ONE keydown handler with an input-focus guard and
    the whole universal map (Space/←→/I/O/N/Z/[]/Ctrl+Z/?)."""
    st, _, js = _req(gui, "/edit.js", raw=True)
    assert st == 200
    js = js.decode("utf-8")
    # exactly one keydown listener (one handler, per the page.py discipline)
    assert js.count('addEventListener("keydown"') == 1
    # the input-focus guard: typing in a field never triggers accelerators
    assert "typingInField" in js
    for tok in ('=== "input"', '=== "select"', '=== "textarea"', "isContentEditable"):
        assert tok in js
    # the guard is consulted against BOTH the event target and the active element
    assert "document.activeElement" in js
    # every universal verb is wired
    assert "togglePlay()" in js                       # Space
    assert "stepFrame(-1" in js and "stepFrame(1" in js  # ← / →
    assert 'setInOut("in")' in js and 'setInOut("out")' in js  # I / O
    assert "setSnap(!snapOn" in js                    # N
    assert "cycleZoom()" in js                        # Z
    assert "setZoom(zoom - 1)" in js and "setZoom(zoom + 1)" in js  # [ / ]
    assert "revertNewest()" in js                     # Ctrl+Z
    assert "keymapToggle()" in js                     # ?
    # ctrl/meta chords are ignored EXCEPT the undo chord
    assert "e.ctrlKey || e.metaKey" in js


def test_keymap_overlay_and_footer_hints_render(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _, _, body = _html(gui, "/edit")
    # the ? overlay dialog (CSP-safe: role/aria, no inline handler)
    assert 'id="ed-keymap-modal"' in body
    assert "键盘快捷键 Keyboard map" in body
    assert 'role="dialog"' in body and 'aria-modal="true"' in body
    # the map advertises the six keys in 中文
    assert "播放 / 暂停" in body and "逐帧移动播放头" in body
    assert "开关吸附" in body
    # the footer hint bar advertises the map (train users to press ?)
    assert 'id="ed-footer-hints"' in body
    assert "空格 播放" in body and "? 快捷键" in body
    # header carries the click paths (WCAG 2.5.7): a 快捷键 + 撤销 button
    assert 'id="ed-keymap-btn"' in body and 'id="ed-undo-btn"' in body


# ============================================================= B. snapping


def test_snap_state_persists_via_endpoint(gui):
    # default ON (Kdenlive-family convention)
    st, _, data = _req(gui, "/api/edit/snap")
    assert st == 200 and data["enabled"] is True
    # toggle OFF (persisted per-user)
    st2, _, data2 = _post(gui, "/api/edit/snap", {"enabled": False})
    assert st2 == 200 and data2["enabled"] is False
    # a fresh GET reads back the persisted state
    assert _req(gui, "/api/edit/snap")[2]["enabled"] is False
    # back on
    assert _post(gui, "/api/edit/snap", {"enabled": True})[2]["enabled"] is True
    assert _req(gui, "/api/edit/snap")[2]["enabled"] is True


def test_snap_state_bakes_into_the_lanes_and_shows_ticks(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    from manju.core.models import Timeline, TimelineTracks, VideoClip

    tmp_project.save_timeline(Timeline(
        fps=24, duration_ms=6000, tracks=TimelineTracks(video=[
            VideoClip(shot="S001", take="t", source="a", start_ms=0, duration_ms=3000),
            VideoClip(shot="S002", take="t", source="b", start_ms=3000, duration_ms=3000)])))
    # snap OFF then render — the lanes carry the persisted state + fps for the JS
    _post(gui, "/api/edit/snap", {"enabled": False})
    _, _, body = _html(gui, "/edit")
    assert 'id="ed-lanes"' in body
    assert 'data-fps="24"' in body and 'data-snap="0"' in body
    assert 'id="ed-snap-toggle"' in body and 'aria-pressed="false"' in body
    assert 'id="ed-snapticks"' in body  # the ruler snap-tick overlay
    # snap ON reflects in the toggle
    _post(gui, "/api/edit/snap", {"enabled": True})
    _, _, body2 = _html(gui, "/edit")
    assert 'data-snap="1"' in body2 and 'aria-pressed="true"' in body2


# ============================================================= C. honest undo


def test_undo_panel_renders_and_lists_recent_edit_events(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    # the panel shell renders (server-side, CSP-safe) with the honesty note
    _, _, body = _html(gui, "/edit")
    assert 'id="ed-undo-panel"' in body
    assert "最近改动 · 撤销" in body
    assert "素材/takes 追加不回滚,只选择版本" in body  # append-only stance
    assert 'href="/history"' in body                     # tier-3 pointer

    # after some edits the /api/edit/undo feed lists them newest-first, each with
    # a revert affordance
    _post(gui, "/api/edit/transition", {"type": "fade", "duration_ms": 200})
    _post(gui, "/api/edit/transition-override",
          {"shot": "S001", "type": "xfade_fade", "duration_ms": 300})
    st, _, data = _req(gui, "/api/edit/undo")
    assert st == 200 and data["note"]
    evs = data["events"]
    assert len(evs) >= 2
    # newest first
    assert evs[0]["action"] == "edit_rules" and "转场覆盖 S001" in evs[0]["what"]
    # every listed event carries the honest revert metadata
    for e in evs:
        assert "index" in e and "tier" in e and "revertable" in e


def test_tier1_revert_round_trip_transition_default(gui, tmp_project, add_shot):
    """Set the default transition A→B, then revert → A through the SAME engine
    endpoint; the revert is itself a logged event (undo = visible history)."""
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)

    # A = fade 200
    assert _post(gui, "/api/edit/transition", {"type": "fade", "duration_ms": 200})[0] == 200
    # B = xfade_fade 400
    assert _post(gui, "/api/edit/transition",
                 {"type": "xfade_fade", "duration_ms": 400})[0] == 200
    rules = tmp_project.load_rules()
    assert rules.transition_default.type == "xfade_fade"
    assert rules.transition_default.duration_ms == 400

    # find the tier-1 revert affordance for the B edit (newest)
    _, _, data = _req(gui, "/api/edit/undo")
    top = data["events"][0]
    assert top["tier"] == 1 and top["revertable"] is True
    assert "交叉溶解" in top["what"]  # B, in 中文

    # revert B → re-applies A (fade 200)
    st, _, rd = _post(gui, "/api/edit/revert", {"index": top["index"], "ts": top["ts"]})
    assert st == 200 and rd.get("tier") == 1
    back = tmp_project.load_rules()
    assert back.transition_default.type == "fade"
    assert back.transition_default.duration_ms == 200

    # the revert is a NORMAL event: a fresh edit_rules with the A value + revert_of
    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "edit_rules"
    assert ev["detail"]["transition_default"] == {"type": "fade", "duration_ms": 200}
    assert ev["detail"].get("revert_of") is not None


def test_tier1_revert_transition_override_back_to_default(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    # a single override on S001's out-edge (no prior override → reverts to 无覆盖)
    assert _post(gui, "/api/edit/transition-override",
                 {"shot": "S001", "type": "xfade_fade", "duration_ms": 300})[0] == 200
    assert "S001" in tmp_project.load_rules().transition_overrides

    _, _, data = _req(gui, "/api/edit/undo")
    top = data["events"][0]
    assert top["tier"] == 1
    st, _, _rd = _post(gui, "/api/edit/revert", {"index": top["index"], "ts": top["ts"]})
    assert st == 200
    # the override key is gone → back to following the global default
    assert "S001" not in tmp_project.load_rules().transition_overrides


def test_revert_refuses_append_only_and_bad_index(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # out-of-range index → 404
    assert _post(gui, "/api/edit/revert", {"index": 99999})[0] == 404
    # a non-int index → 400
    assert _post(gui, "/api/edit/revert", {"index": "nope"})[0] == 400


def test_undo_marks_append_only_takes_non_revertable(gui, tmp_project, add_shot):
    """A trim/handle-rebuild event (mints a NEW take) is append-only: the undo
    panel says 选择版本, never offers a destructive revert (§C)."""
    from manju.core.events import append_event

    add_shot(tmp_project, "S001")
    append_event(tmp_project.root, "human", "repair",
                 {"shot": "S001", "op": "inout", "new_take": "take_02", "via": "gui"})
    _, _, data = _req(gui, "/api/edit/undo")
    row = data["events"][0]
    assert row["action"] == "repair"
    assert row["revertable"] is False
    assert row["tier"] == "append"
    assert "只在下方选择版本" in row["note"]


# ======================================================= D. Tier-1 playback


def test_playback_source_resolves_final_over_proxy_over_empty(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # (1) nothing rendered yet → honest empty state
    st, _, data = _req(gui, "/api/edit/playback-source")
    assert st == 200 and data["kind"] is None and data["url"] is None
    _, _, body = _html(gui, "/edit")
    assert "还没有成片或预览版可播放" in body and "先构建" in body

    # (2) only a proxy → the proxy is the source
    _plant(tmp_project, "renders/proxy/proxy.mp4")
    d2 = _req(gui, "/api/edit/playback-source")[2]
    assert d2["kind"] == "proxy"
    assert d2["url"] == "/media/renders/proxy/proxy.mp4"

    # (3) a final wins over the proxy (newest, highest fidelity)
    _plant(tmp_project, "renders/final/final_v1.mp4")
    d3 = _req(gui, "/api/edit/playback-source")[2]
    assert d3["kind"] == "final"
    assert d3["url"] == "/media/renders/final/final_v1.mp4"

    # the page mounts a <video> over that source (Tier 1)
    _, _, body2 = _html(gui, "/edit")
    assert 'id="ed-preview-video"' in body2
    assert 'src="/media/renders/final/final_v1.mp4"' in body2


def test_media_endpoint_serves_range_206(gui, tmp_project, add_shot):
    """The preview player seeks via HTTP Range — the /media endpoint must answer
    206 Partial Content with a Content-Range (the <video> depends on it)."""
    add_shot(tmp_project, "S001")
    _plant(tmp_project, "renders/final/final_v1.mp4",
           data=bytes(range(32)))  # 32 bytes

    # a full GET advertises Range support
    st, headers, body = _req(gui, "/media/renders/final/final_v1.mp4", raw=True)
    assert st == 200
    assert headers.get("Accept-Ranges") == "bytes"
    assert headers.get("Content-Type") == "video/mp4"
    assert len(body) == 32

    # a ranged GET → 206 with a Content-Range and exactly the requested bytes
    st2, h2, body2 = _req(gui, "/media/renders/final/final_v1.mp4", raw=True,
                          headers={"Range": "bytes=0-3"})
    assert st2 == 206
    assert h2.get("Content-Range") == "bytes 0-3/32"
    assert body2 == bytes(range(4))

    # an unsatisfiable range → 416
    st3, _, _b3 = _req(gui, "/media/renders/final/final_v1.mp4", raw=True,
                       headers={"Range": "bytes=999-"})
    assert st3 == 416


# ===================================================== E. guards / no-regress


def test_v2_token_and_readonly_guards(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # CSRF: the new mutating POSTs need the token
    for path, b in (("/api/edit/snap", {"enabled": True}),
                    ("/api/edit/revert", {"index": 0})):
        status, _, data = _req(gui, path, method="POST", body=b)
        assert status == 403 and "token" in data["error"].lower()
    # DNS-rebinding guard on the new GET surfaces
    assert _req(gui, "/api/edit/snap", host="evil.example.com")[0] == 403
    assert _req(gui, "/api/edit/undo", host="evil.example.com")[0] == 403
    assert _req(gui, "/api/edit/playback-source", host="evil.example.com")[0] == 403


def test_v2_readonly_blocks_snap_and_revert_but_keeps_reads(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert _html(server, "/edit")[0] == 200
        for path, b in (("/api/edit/snap", {"enabled": False}),
                        ("/api/edit/revert", {"index": 0})):
            status, _, data = _post(server, path, b)
            assert status == 403 and "readonly" in data["error"]
        # the read surfaces stay available read-only
        assert _req(server, "/api/edit/snap")[0] == 200
        assert _req(server, "/api/edit/undo")[0] == 200
        assert _req(server, "/api/edit/playback-source")[0] == 200
    finally:
        server.shutdown()
        server.close()
