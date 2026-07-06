"""``manju board --serve`` — the actionable local workspace (§1-⑦, §11).

Drives a real :class:`manju.board.server.BoardServer` bound to port 0 on a
background thread. Covers: the static board stays byte-identical, serve-mode HTML
carries the action JS + ``/media/`` URLs, the media route (traversal guard +
Range), the safe API surface (select/rollback), that the dangerous surface
(unlock/gc) is 404, the busy lock (409), and one ffmpeg-gated build end-to-end.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import shutil
import threading
import urllib.error
import urllib.request
from typing import Callable, Iterator

import httpx
import pytest

from manju.board import board as bd
from manju.board.server import API_ACTIONS, make_server
from manju.core.container import Project
from manju.core.events import append_event, tail_events
from manju.core.yamlio import write_yaml


# ------------------------------------------------------------- helpers/fixtures


@contextlib.contextmanager
def running(project: Project) -> Iterator[tuple[str, object]]:
    """A live BoardServer on an ephemeral port; torn down on exit."""
    server = make_server(project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def one_shot_project(tmp_project: Project, add_shot: Callable, make_take: Callable) -> Project:
    """A project with one shot, two takes, take_01 selected."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "manual")  # take_01
    make_take(tmp_project, "S001", "manual")  # take_02
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01")
    )
    return tmp_project


class _FrozenDatetime:
    """Deterministic ``now`` so the static/serve footer timestamps match."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return _dt.datetime(2026, 7, 6, 12, 0, 0, tzinfo=_dt.timezone.utc)


# --------------------------------------------------------------------- static


def test_static_board_is_byte_identical(one_shot_project, monkeypatch):
    """Adding serve mode must not change one byte of the static board."""
    monkeypatch.setattr(bd, "datetime", _FrozenDatetime)
    written = bd.generate_board(one_shot_project).read_text(encoding="utf-8")
    rendered = bd.render_board(one_shot_project, serve=False)
    assert written == rendered
    # and the static board carries NONE of the serve veneer
    assert 'fetch("/api/' not in written
    assert "/media/" not in written
    assert "mj-overlay" not in written


# ---------------------------------------------------------------------- GET /


def test_serve_html_has_action_js_and_media_urls(one_shot_project):
    with running(one_shot_project) as (base, _):
        r = httpx.get(base + "/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert 'fetch("/api/' in body            # the action JS layer
    assert "/media/" in body                  # media served, not a relative file path
    assert "构建 build" in body and "质检 qc" in body  # header actions
    assert "选用 select" in body              # per-take select button (on the non-selected take)
    assert "★ 已选用" in body                 # the selected take is disabled + starred
    assert "mj-overlay" in body               # busy overlay present


# --------------------------------------------------------------------- /media


def test_media_serves_legit_file(one_shot_project):
    take = one_shot_project.get_take("S001", "take_01")
    rel = one_shot_project.relpath(take.media_path)
    expected = take.media_path.read_bytes()
    with running(one_shot_project) as (base, _):
        r = httpx.get(base + "/media/" + rel)
    assert r.status_code == 200
    assert r.headers.get("Accept-Ranges") == "bytes"
    assert r.content == expected


def test_media_range_returns_206(one_shot_project):
    take = one_shot_project.get_take("S001", "take_01")
    rel = one_shot_project.relpath(take.media_path)
    full = take.media_path.read_bytes()
    with running(one_shot_project) as (base, _):
        r = httpx.get(base + "/media/" + rel, headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.headers["Content-Range"] == f"bytes 2-5/{len(full)}"
    assert r.headers.get("Accept-Ranges") == "bytes"
    assert r.content == full[2:6]


def test_media_traversal_is_blocked(one_shot_project):
    with running(one_shot_project) as (base, _):
        # percent-encoded ".." bypasses client-side normalization → hits the guard
        r = httpx.get(base + "/media/%2e%2e/%2e%2e/etc/passwd")
        assert r.status_code == 403
        # an absolute-looking path is treated as project-relative → not found, never escapes
        r_abs = httpx.get(base + "/media//etc/passwd")
        assert r_abs.status_code in (403, 404)
        # a raw "../" traversal (urllib does not normalize it away) → guard 403
        try:
            urllib.request.urlopen(base + "/media/../../etc/passwd")
            raw_status = 200
        except urllib.error.HTTPError as exc:
            raw_status = exc.code
        assert raw_status in (403, 404)


# ----------------------------------------------------------------------- /api


def test_select_flips_selection_and_records_event(one_shot_project, monkeypatch):
    monkeypatch.setenv("MANJU_ACTOR", "director")
    with running(one_shot_project) as (base, _):
        r = httpx.post(base + "/api/select", json={"shot": "S001", "take": "take_02"})
    assert r.status_code == 200 and r.json()["ok"] is True
    # status.selected_take flipped on disk
    assert one_shot_project.load_shot("S001").status.selected_take == "take_02"
    # an event was appended with the actor from MANJU_ACTOR
    last = tail_events(one_shot_project.root, n=1)[0]
    assert last["action"] == "select"
    assert last["actor"] == "director"
    assert last["detail"] == {"shot": "S001", "take": "take_02"}


def test_select_bad_take_is_clean_error(one_shot_project):
    with running(one_shot_project) as (base, _):
        r = httpx.post(base + "/api/select", json={"shot": "S001", "take": "nope"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "nope" in body["error"]


def test_rollback_shot_round_trips(one_shot_project):
    with running(one_shot_project) as (base, _):
        # two selects give rollback something to return to
        assert httpx.post(base + "/api/select", json={"shot": "S001", "take": "take_01"}).json()["ok"]
        assert httpx.post(base + "/api/select", json={"shot": "S001", "take": "take_02"}).json()["ok"]
        assert one_shot_project.load_shot("S001").status.selected_take == "take_02"
        r = httpx.post(base + "/api/rollback_shot", json={"shot": "S001"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["take"] == "take_01" and body["was"] == "take_02"
    assert one_shot_project.load_shot("S001").status.selected_take == "take_01"


@pytest.mark.parametrize("action", ["unlock", "gc", "pack", "unpack", "import", "bogus"])
def test_dangerous_and_unknown_actions_are_404(one_shot_project, action):
    with running(one_shot_project) as (base, _):
        r = httpx.post(base + "/api/" + action, json={})
    assert r.status_code == 404
    assert r.json()["ok"] is False
    # and they are genuinely not in the API surface
    assert action not in API_ACTIONS


def test_get_unknown_route_is_404(one_shot_project):
    with running(one_shot_project) as (base, _):
        assert httpx.get(base + "/nope").status_code == 404


def test_busy_lock_returns_409(one_shot_project):
    with running(one_shot_project) as (base, server):
        server.mutation_lock.acquire()  # simulate an in-flight mutation
        try:
            r = httpx.post(base + "/api/select", json={"shot": "S001", "take": "take_02"})
        finally:
            server.mutation_lock.release()
    assert r.status_code == 409
    body = r.json()
    assert body["ok"] is False and "busy" in body["error"]
    # the rejected click did NOT mutate anything
    assert one_shot_project.load_shot("S001").status.selected_take == "take_01"


# ------------------------------------------------------- compare + panels (serve)


def test_compare_markup_present_for_multi_take_shot(one_shot_project):
    """A shot with >=2 takes gets a compare toggle + a side-by-side grid driven
    by ONE synchronized play button, each cell carrying its select + metadata."""
    with running(one_shot_project) as (base, _):
        body = httpx.get(base + "/").text
    assert "对比 compare" in body and 'data-compare="1"' in body   # per-shot toggle
    assert 'class="compare-wrap"' in body                          # the compare grid
    assert 'data-syncplay="1"' in body                             # one button, all videos
    assert 'class="cmp-cell"' in body and 'class="cmp-meta"' in body  # per-take metadata
    assert "播放全部 play all" in body and 'data-playall="1"' in body  # play-all control
    assert 'class="cmp-video"' in body                             # large compare videos


def test_panels_render_with_real_content(one_shot_project):
    """The tabbed inspector renders every panel from real fixture content:
    a caption file, the Bible entry, an import asset, and a log event."""
    p = one_shot_project
    p.captions_dir.mkdir(exist_ok=True)
    (p.captions_dir / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,500\n第一行字幕\n\n"
        "2\n00:00:02,500 --> 00:00:05,000\n第二行字幕\n",
        encoding="utf-8",
    )
    (p.imports_dir / "bgm.wav").write_bytes(b"RIFF0000WAVE")
    append_event(p.root, "director", "select", {"shot": "S001", "take": "take_02"})

    with running(p) as (base, _):
        body = httpx.get(base + "/").text

    # the tabbed strip carries all six panels, server-rendered
    assert 'class="mj-panels"' in body
    for key in ("project", "subs", "bible", "log", "assets", "qc"):
        assert f'data-tab="{key}"' in body and f'data-panel="{key}"' in body
    # 项目 Project: the project_status payload as a definition list
    assert "下一步 next step" in body and "预设 preset" in body
    # 字幕 Subtitles: real cues with numbers + timing (read-only)
    assert "第一行字幕" in body and "第二行字幕" in body and "00:00:02,500" in body
    # 圣经 Bible: the tmp_project character entry
    assert "林夏" in body
    # 日志 Log: the appended event surfaced (ts/actor/action)
    assert "director" in body and "<b>select</b>" in body
    # 资产 Assets: the import listed + the "imports are sacred" reminder
    assert "bgm.wav" in body and "神圣" in body


def test_subs_panel_flags_manual_mode(one_shot_project):
    p = one_shot_project
    rules = p.load_rules()
    rules.captions.mode = "manual"
    p.save_rules(rules)
    p.captions_dir.mkdir(exist_ok=True)
    (p.captions_dir / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\n手改字幕\n", encoding="utf-8")
    with running(p) as (base, _):
        body = httpx.get(base + "/").text
    assert "MANUAL" in body and "手改字幕" in body


def test_bible_panel_marks_locked_fields(one_shot_project):
    write_yaml(
        one_shot_project.root / "bible" / "characters.yaml",
        {"linxia": {"name": "林夏", "appearance": "短发黑风衣",
                    "locked": {"appearance": "sha256:deadbeef"}}},
    )
    with running(one_shot_project) as (base, _):
        body = httpx.get(base + "/").text
    assert "🔒" in body


def test_export_registered_but_dangerous_surface_absent(one_shot_project):
    """Export joins the safe API surface; unlock/gc/pack stay 404 and unlisted."""
    assert "export" in API_ACTIONS
    for danger in ("unlock", "gc", "pack", "unpack"):
        assert danger not in API_ACTIONS
    with running(one_shot_project) as (base, _):
        for danger in ("unlock", "gc", "pack"):
            assert httpx.post(base + "/api/" + danger, json={}).status_code == 404


def test_export_unknown_profile_is_clean_error(one_shot_project):
    with running(one_shot_project) as (base, _):
        r = httpx.post(base + "/api/export", json={"profiles": ["bogus"]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "bogus" in body["error"]


# ---------------------------------------------------------------- e2e (ffmpeg)


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required for the build end-to-end test",
)


@needs_ffmpeg
def test_build_endpoint_produces_a_final(tmp_path):
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path / "样片", shots=2)
    project = Project(root)
    assert project.newest_final_path() is None  # nothing built yet
    with running(project) as (base, _):
        r = httpx.post(base + "/api/build", json={"target": "final"}, timeout=300.0)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["build_ok"] is True
    final = project.newest_final_path()
    assert final is not None and final.exists()
    assert body["render_path"] and body["render_path"].endswith(".mp4")


@needs_ffmpeg
def test_export_endpoint_produces_files(tmp_path):
    """/api/export runs the same core `manju export` calls, on a built project,
    and returns the produced project-relative paths (which exist on disk)."""
    from tests.fixtures.make_sample import make_sample_project

    from manju.build.graph import run_build

    root = make_sample_project(tmp_path / "导出样片", shots=2)
    project = Project(root)
    assert run_build(project, target="final").ok  # compiles timeline.json + renders
    with running(project) as (base, _):
        r = httpx.post(base + "/api/export",
                       json={"profiles": ["srt", "otio"]}, timeout=120.0)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    outs = body["outputs"]
    assert "srt" in outs and "otio" in outs
    assert (project.root / outs["srt"]).exists()
    assert (project.root / outs["otio"]).exists()
    # the export was logged like the CLI does
    assert tail_events(project.root, 1)[0]["action"] == "export"
