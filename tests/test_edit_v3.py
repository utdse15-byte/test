"""`manju gui` — 剪辑 EDIT depth v3 (round X, agent XG).

Closes three of the concrete "still need an external NLE" gaps named in
user pain #5, per REPORTS/ROUND-V-REFERENCES-2.md §1's deferred Tier-2 plan:

  A. **Tier-2 timeline preview** — a playback manifest
     (:func:`manju.gui.edit.playback_manifest` / ``/api/edit/playback-manifest``)
     giving the compiled timeline's video track as ordered
     ``{start_ms, duration_ms, source_in_ms, preview_url, status}`` rows, honest
     about clips whose webpreview hasn't been generated yet (lazily enqueued
     through the jobs runner, deduped across polls) — and a pooled ``<video>``
     sequencer in ``/edit.js`` (the Chromium ~75-WebMediaPlayer-per-frame cap,
     §1d: never one ``<video>`` per clip). Tier 1 (play the render) stays the
     default the instant a final/proxy exists, with a toggle to Tier 2.
  B. **字幕样式 caption style panel** — the ASS burn-in knobs
     ``exporters/srt_ass.py`` already reads (font/size/primary colour/outline/
     margin-v/alignment), now DECLARED on ``CaptionRules`` (round W:
     ``extra="allow"`` meant they were reachable but untyped/unbounded) and
     writable from ``/edit`` through ``/api/edit/caption-style`` — additive,
     ``None`` defaults render byte-identical ASS output.
  C. **卡片预设 card presets** — four named style combos
     (``PACKAGING_CARD_PRESETS``) for packaging intro/outro cards, additive on
     ``PackagingCard.style_preset`` (default ``""`` renders byte-identical to
     before), selectable in ``/packaging``'s card forms.

Companion to :mod:`tests.test_native_cut`, :mod:`tests.test_native_cut_v2`,
:mod:`tests.test_gui_edit`, :mod:`tests.test_gui_finish` and
:mod:`tests.test_caption_breaks`/:mod:`tests.test_packaging` — all of which
stay green (run alongside this file, not duplicated here).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from manju.core.events import tail_events
from manju.core.hashing import hash_value
from manju.core.models import (
    CaptionLine,
    PackagingCard,
    Timeline,
    TimelineTracks,
    VideoClip,
)
from manju.exporters.srt_ass import _caption_style, compile_ass
from manju.gui.server import create_server
from manju.timeline.packaging import packaging_card_hash

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


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


def _wait_job(server, job_id, timeout=90.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _, data = _req(server, "/api/jobs")
        assert status == 200
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def _plant(project, rel, data=b"\x00\x01\x02\x03\x04\x05\x06\x07"):
    p = project.resolve(rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return rel


def _timeline_with_two_clips(tmp_project):
    """Two video clips whose sources are already-browser-safe .mp4 bytes —
    the manifest resolves them "ready" via /media with no ffmpeg needed
    (mirrors test_native_cut.py's ffmpeg-optional fixture discipline)."""
    v1 = _plant(tmp_project, "media/gen/S001/take_01.mp4", b"v1-bytes")
    v2 = _plant(tmp_project, "media/gen/S002/take_01.mp4", b"v2-bytes")
    tl = Timeline(fps=24, width=1080, height=1920, duration_ms=4000,
                 tracks=TimelineTracks(video=[
                     VideoClip(shot="S001", take="take_01", source=v1,
                              start_ms=0, duration_ms=2000, source_in_ms=500),
                     VideoClip(shot="S002", take="take_01", source=v2,
                              start_ms=2000, duration_ms=2000),
                 ]))
    tmp_project.save_timeline(tl)
    return tl


# ======================================================= A. playback manifest


def test_playback_manifest_orders_clips_with_source_in_offsets(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    _timeline_with_two_clips(tmp_project)

    status, _, data = _req(gui, "/api/edit/playback-manifest")
    assert status == 200
    assert data["duration_ms"] == 4000
    clips = data["clips"]
    assert [c["shot"] for c in clips] == ["S001", "S002"]
    assert clips[0]["start_ms"] == 0 and clips[0]["duration_ms"] == 2000
    assert clips[0]["source_in_ms"] == 500          # source_in offset carried through
    assert clips[1]["start_ms"] == 2000 and clips[1]["source_in_ms"] == 0
    # already-browser-safe .mp4 sources → "ready" straight off /media, no job
    assert clips[0]["status"] == "ready"
    assert clips[0]["preview_url"] == "/media/" + clips[0]["source"]
    assert clips[1]["status"] == "ready"
    assert data["job"] is None
    assert data["note"] == "时间线预览:无混音,粗剪画面为准"


def test_playback_manifest_honest_empty_before_any_timeline(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _req(gui, "/api/edit/playback-manifest")
    assert status == 200
    assert data["clips"] == [] and data["duration_ms"] == 0
    assert data["job"] is None


def test_playback_manifest_marks_missing_source_unavailable(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tl = Timeline(fps=24, width=1080, height=1920, duration_ms=1000,
                 tracks=TimelineTracks(video=[
                     VideoClip(shot="S001", take="t", source="media/gen/nope.mp4",
                              start_ms=0, duration_ms=1000)]))
    tmp_project.save_timeline(tl)

    status, _, data = _req(gui, "/api/edit/playback-manifest")
    assert status == 200
    row = data["clips"][0]
    assert row["status"] == "unavailable"
    assert row["preview_url"] is None
    assert row["reason"]


def test_playback_manifest_pending_row_lazy_generation_and_dedup(gui, tmp_project, add_shot,
                                                                 monkeypatch):
    """A source that needs a transcode is honestly 'pending' on the first
    manifest GET; the GET itself enqueues ONE job (jobs-runner, never blocks
    the request on ffmpeg) and a second GET while it is still in flight must
    reuse the SAME job — not spam a new one per client poll."""
    import manju.media.webpreview as webpreview

    add_shot(tmp_project, "S001")
    src = tmp_project.root / "media" / "imports" / "clip.mov"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"fake-mov-bytes-not-really-video")
    rel = tmp_project.relpath(src)
    tl = Timeline(fps=24, width=1080, height=1920, duration_ms=1000,
                 tracks=TimelineTracks(video=[
                     VideoClip(shot="S001", take="t", source=rel, start_ms=0, duration_ms=1000)]))
    tmp_project.save_timeline(tl)

    release = threading.Event()
    calls = []

    def slow_ensure_preview(root, source, **kw):
        calls.append(source)
        release.wait(5)
        return None  # simulate a transcode failure — degrade, never raise

    monkeypatch.setattr(webpreview, "ensure_preview", slow_ensure_preview)

    status1, _, data1 = _req(gui, "/api/edit/playback-manifest")
    assert status1 == 200
    assert data1["clips"][0]["status"] == "pending"
    assert data1["clips"][0]["preview_url"] is None
    assert data1["job"] is not None

    status2, _, data2 = _req(gui, "/api/edit/playback-manifest")
    assert status2 == 200
    # same in-flight job reused, not a second one queued
    assert data2["job"]["id"] == data1["job"]["id"]

    release.set()
    job = _wait_job(gui, data1["job"]["id"])
    assert job["state"] == "done"
    assert len(calls) == 1  # ensure_preview called exactly once for the one source


@pytest.mark.skipif(not _HAS_FFMPEG, reason="a real webpreview transcode needs ffmpeg")
def test_playback_manifest_resolves_to_ready_after_real_transcode(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    src = tmp_project.root / "media" / "imports" / "clip.mov"
    src.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x48:rate=24",
         "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True)
    rel = tmp_project.relpath(src)
    tl = Timeline(fps=24, width=1080, height=1920, duration_ms=1000,
                 tracks=TimelineTracks(video=[
                     VideoClip(shot="S001", take="t", source=rel, start_ms=0, duration_ms=1000)]))
    tmp_project.save_timeline(tl)

    status, _, data = _req(gui, "/api/edit/playback-manifest")
    assert status == 200 and data["clips"][0]["status"] == "pending"
    _wait_job(gui, data["job"]["id"])

    status2, _, data2 = _req(gui, "/api/edit/playback-manifest")
    assert data2["clips"][0]["status"] == "ready"
    assert data2["clips"][0]["preview_url"].startswith("/preview/")
    # the preview URL is actually servable
    st3, h3, _ = _req(gui, data2["clips"][0]["preview_url"], raw=True)
    assert st3 == 200


# ================================================== A. Tier-1 default + toggle


def test_edit_page_neither_tier_available_keeps_original_honest_empty_state(gui, tmp_project,
                                                                             add_shot):
    add_shot(tmp_project, "S001")
    status, _, body = _html(gui, "/edit")
    assert status == 200
    assert "还没有成片或预览版可播放" in body and "先构建" in body
    assert 'id="ed-play-tier-toggle"' not in body  # nothing to toggle to


def test_edit_page_defaults_to_tier2_when_only_timeline_exists(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    _timeline_with_two_clips(tmp_project)

    status, _, body = _html(gui, "/edit")
    assert status == 200
    assert 'data-default-tier="2"' in body
    assert 'id="ed-tier2">' in body            # visible (no "hidden" attr)
    assert 'id="ed-tier1" hidden' in body       # Tier 1 present but hidden
    assert body.count('class="ed-t2-video"') == 3   # TIER2_POOL_SIZE
    assert 'data-pool-size="3"' in body
    assert 'id="ed-play-tier-toggle"' not in body    # only one tier truly available


def test_edit_page_defaults_to_tier1_with_toggle_when_final_exists(gui, tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    _timeline_with_two_clips(tmp_project)
    _plant(tmp_project, "renders/final/final_v1.mp4")

    status, _, body = _html(gui, "/edit")
    assert status == 200
    assert 'data-default-tier="1"' in body
    assert 'id="ed-tier1">' in body
    assert 'id="ed-tier2" hidden' in body
    assert 'id="ed-play-tier-toggle"' in body   # both tiers exist → offer the toggle
    assert 'id="ed-preview-video"' in body
    assert 'src="/media/renders/final/final_v1.mp4"' in body


def test_edit_js_carries_tier2_pool_sequencing_structure(gui):
    status, _, js = _html(gui, "/edit.js")
    assert status == 200
    # pool sequencing markers (current plays, next preloads, swap on boundary)
    assert "t2Pool" in js and "T2_POOL_SIZE" in js
    assert "t2ActivateClip" in js
    assert "t2PreloadNext" in js
    assert "t2SeekAndPlay" in js
    assert "/api/edit/playback-manifest" in js
    # currentTime derivation honors source_in_ms with NO extra scale factor
    # (previews are same-duration scaled copies — see webpreview.py)
    assert "source_in_ms" in js
    # tier dispatch wired into the shared Space/←→/ruler accelerators
    assert "activeTier" in js and "setTier" in js


# ================================================================= B. captions


def test_caption_style_default_is_byte_identical_to_pre_change():
    """Pin: compile_ass with NO style overrides renders the EXACT historical
    Style: line (Outline=3, Alignment=2 were hard-coded before round X)."""
    tl = Timeline(width=1080, height=1920, duration_ms=2000,
                 tracks=TimelineTracks(captions=[
                     CaptionLine(start_ms=0, end_ms=1000, text="测试字幕")]))
    ass = compile_ass(tl, width=1080, height=1920)
    style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
    assert style_line == (
        "Style: Default,Noto Sans CJK SC,87,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "0,0,0,0,100,100,0,0,1,3,0,2,54,54,160,1"
    )


def test_caption_rules_new_fields_default_to_none(tmp_project):
    cap = tmp_project.load_rules().captions
    assert cap.font is None and cap.size is None and cap.primary_colour is None
    assert cap.margin_v is None and cap.outline is None and cap.alignment is None
    # an untouched rules.yaml never surfaces the NEW style keys to the ASS
    # writer (max_chars_per_line is the one pre-existing key always present)
    style = _caption_style(tmp_project)
    assert style == {"max_chars_per_line": 18}
    for key in ("font", "size", "primary_colour", "margin_v", "outline", "alignment"):
        assert key not in style


def test_caption_style_round_trips_rules_write_into_ass_output(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, data = _post(gui, "/api/edit/caption-style",
                            {"font": "Source Han Sans", "size": 60, "outline": 6,
                             "margin_v": 220, "alignment": 8,
                             "primary_colour": "&H0000FFFF"})
    assert status == 200 and data.get("ok") is True

    cap = tmp_project.load_rules().captions
    assert cap.font == "Source Han Sans" and cap.size == 60 and cap.outline == 6
    assert cap.margin_v == 220 and cap.alignment == 8
    assert cap.primary_colour == "&H0000FFFF"

    style = _caption_style(tmp_project)
    tl = Timeline(width=1080, height=1920, duration_ms=2000,
                 tracks=TimelineTracks(captions=[
                     CaptionLine(start_ms=0, end_ms=1000, text="测试字幕")]))
    ass = compile_ass(tl, width=1080, height=1920, style=style)
    style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
    assert "Source Han Sans,60,&H0000FFFF" in style_line
    assert ",1,6,0,8," in style_line   # Outline=6, Alignment=8

    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "edit_rules"
    assert ev["detail"]["caption_style"]["outline"] == 6
    assert ev["detail"]["via"] == "gui"


def test_caption_style_clear_resets_every_field_to_none(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/edit/caption-style", {"font": "X", "size": 50})[0] == 200
    status, _, data = _post(gui, "/api/edit/caption-style",
                            {"font": None, "size": None, "primary_colour": None,
                             "outline": None, "margin_v": None, "alignment": None})
    assert status == 200 and data.get("ok") is True
    cap = tmp_project.load_rules().captions
    assert (cap.font, cap.size, cap.primary_colour, cap.outline, cap.margin_v, cap.alignment) \
        == (None, None, None, None, None, None)


def test_caption_style_rejects_invalid_values(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    assert _post(gui, "/api/edit/caption-style", {"alignment": 15})[0] == 400
    assert _post(gui, "/api/edit/caption-style", {"outline": -1})[0] == 400
    assert _post(gui, "/api/edit/caption-style", {"margin_v": -5})[0] == 400
    assert _post(gui, "/api/edit/caption-style", {"primary_colour": "red"})[0] == 400
    # rules.captions is untouched by the rejected write
    assert tmp_project.load_rules().captions.alignment is None


def test_edit_page_renders_caption_style_panel_with_hints(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, _, body = _html(gui, "/edit")
    assert status == 200
    assert "字幕样式 Caption style" in body
    assert 'id="ed-cs-font"' in body and 'id="ed-cs-size"' in body
    assert 'id="ed-cs-primary"' in body and 'id="ed-cs-outline"' in body
    assert 'id="ed-cs-marginv"' in body and 'id="ed-cs-align"' in body
    # subtitle-standards hint (default project is portrait 1080x1920)
    assert "竖屏建议每行" in body and "安全区" in body


@pytest.mark.skipif(not _HAS_FFMPEG, reason="caption style preview burns a real frame")
def test_caption_style_preview_serves_an_image(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, headers, body = _req(
        gui, "/edit/caption-style-preview?size=48&outline=2&alignment=8", raw=True)
    assert status == 200 and len(body) > 0
    assert headers.get("Content-Type", "").startswith("image/")


# =================================================================== C. cards


def test_packaging_card_hash_default_preset_is_byte_identical_to_pre_change():
    """Pin: DECLARING PackagingCard.style_preset must not shift the content-
    addressed card path for a project that never opts into a preset — the
    hash is computed the exact way the OLD (pre-round-X) formula did: raw
    model_dump() with no style_preset key at all."""
    card = PackagingCard(enabled=True, template="chapter", text="片头", duration_ms=1800)
    assert card.style_preset == ""
    got = packaging_card_hash(card, 1080, 1920, 24)

    old_formula_payload = card.model_dump()
    old_formula_payload.pop("style_preset")   # the key that did not exist before
    want = hash_value({"card": old_formula_payload, "width": 1080, "height": 1920, "fps": 24})
    assert got == want

    # a REAL preset choice DOES re-key — that is a genuine content change
    card2 = PackagingCard(enabled=True, template="chapter", text="片头",
                          duration_ms=1800, style_preset="neon")
    assert packaging_card_hash(card2, 1080, 1920, 24) != got


def test_packaging_card_style_preset_validates_known_names():
    from pydantic import ValidationError

    PackagingCard(style_preset="mono_black")  # does not raise
    with pytest.raises(ValidationError):
        PackagingCard(style_preset="not-a-real-preset")


def test_caption_card_default_style_matches_explicit_defaults(monkeypatch, tmp_path):
    """caption_card()'s new bg/fontcolor/font_scale params must be a strict
    no-op at their defaults: an implicit-default call and an EXPLICIT
    bg="black"/fontcolor="white"/font_scale=1.0 call build the identical
    ffmpeg invocation (byte-identity, one level below "run real ffmpeg twice
    and diff bytes" — which is not guaranteed deterministic container
    metadata-wise and isn't what changed here)."""
    from manju.media import card as card_mod

    captured: list[list[str]] = []

    def fake_run_ffmpeg(args, **kw):
        captured.append([str(a) for a in args])

    monkeypatch.setattr(card_mod, "run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr(card_mod, "find_font", lambda: None)

    card_mod.caption_card("标题文字", tmp_path / "a.mp4", width=640, height=360,
                          fps=24, duration_ms=200)
    card_mod.caption_card("标题文字", tmp_path / "b.mp4", width=640, height=360,
                          fps=24, duration_ms=200, bg="black", fontcolor="white",
                          font_scale=1.0)

    def normalize(args):
        out = [re.sub(r"textfile=\S+", "textfile=<tmp>", a) for a in args]
        return out[:-1]  # drop the destination path (varies by construction)

    assert normalize(captured[0]) == normalize(captured[1])
    # and the computed fontsize really is the historical formula
    assert "fontsize=30" in captured[0][captured[0].index("-filter_complex") + 1]


def test_caption_card_preset_knobs_change_the_command(monkeypatch, tmp_path):
    from manju.media import card as card_mod

    captured: list[list[str]] = []
    monkeypatch.setattr(card_mod, "run_ffmpeg",
                        lambda args, **kw: captured.append([str(a) for a in args]))
    monkeypatch.setattr(card_mod, "find_font", lambda: None)

    style = card_mod.CARD_STYLE_PRESETS["neon"]
    card_mod.caption_card("标题", tmp_path / "neon.mp4", width=640, height=360, fps=24,
                          duration_ms=200, bg=style["bg"], fontcolor=style["fontcolor"],
                          font_scale=style["font_scale"])
    filt = captured[0][captured[0].index("-filter_complex") + 1]
    assert style["fontcolor"] in filt
    color_i = captured[0].index("-i") + 1
    assert style["bg"] in captured[0][color_i]


def test_html_card_preset_changes_background_default_is_byte_identical(monkeypatch, tmp_path):
    """render_card_png()'s preset param: "" must format the EXACT historical
    CARD_TEMPLATES CSS (byte-identical HTML), a real preset must visibly
    change it. No Chromium actually invoked — subprocess.run is mocked so
    this test needs neither ffmpeg nor a browser."""
    from manju.media import html_card as hc

    monkeypatch.setattr(hc, "find_chromium", lambda: Path("/usr/bin/true"))
    captured_html: dict[str, str] = {}

    def fake_run(cmd, **kw):
        dest = next(a.split("=", 1)[1] for a in cmd if a.startswith("--screenshot="))
        uri = cmd[-1]
        from urllib.parse import urlparse
        from urllib.request import url2pathname

        # url2pathname strips the Windows drive-slash ("/C:/Users/..." ->
        # "C:\\Users\\...") and unquotes; on POSIX it is a plain unquote.
        html_path = Path(url2pathname(urlparse(uri).path))
        captured_html[dest] = html_path.read_text(encoding="utf-8")
        Path(dest).write_bytes(b"fake-png")

        class _Result:
            returncode = 0
            stderr = ""

        return _Result()

    monkeypatch.setattr(hc.subprocess, "run", fake_run)

    d1 = str(tmp_path / "d1.png")
    d2 = str(tmp_path / "d2.png")
    hc.render_card_png("标题", d1, width=200, height=200, template="caption")
    hc.render_card_png("标题", d2, width=200, height=200, template="caption", preset="")
    assert captured_html[d1] == captured_html[d2]
    assert "0d1117" in captured_html[d1]   # the historical caption-template bg is untouched

    d3 = str(tmp_path / "d3.png")
    hc.render_card_png("标题", d3, width=200, height=200, template="caption", preset="neon")
    assert captured_html[d3] != captured_html[d1]
    assert "39ff9c" in captured_html[d3]   # the neon preset's text colour


def test_cardprev_render_card_preview_threads_preset(monkeypatch, tmp_project):
    """gui/cardprev.py's live-preview cache key folds in `preset` (a real
    preset must not collide with the classic cache entry) and forwards it to
    the same renderer chain media/packaging.py uses at build time."""
    from manju.gui import cardprev

    seen_presets: list[str] = []

    def fake_render_card_png(text, dest, *, width, height, template, preset=""):
        seen_presets.append(preset)
        Path(dest).write_bytes(b"png")
        return Path(dest)

    monkeypatch.setattr("manju.media.html_card.render_card_png", fake_render_card_png)

    dest1, _ = cardprev.render_card_preview(tmp_project, text="标题", template="chapter")
    dest2, _ = cardprev.render_card_preview(tmp_project, text="标题", template="chapter",
                                            preset="warm_gradient")
    assert dest1 != dest2   # distinct cache entries
    assert seen_presets == ["", "warm_gradient"]


@pytest.mark.skipif(not _HAS_FFMPEG, reason="render_packaging_card's drawtext floor needs ffmpeg")
def test_render_packaging_card_preset_changes_rendered_bytes(tmp_path, monkeypatch):
    from manju.media import html_card as hc
    from manju.media.packaging import render_packaging_card

    monkeypatch.setattr(hc, "find_chromium", lambda: None)  # force the drawtext floor

    classic = PackagingCard(enabled=True, template="chapter", text="片头", duration_ms=200)
    neon = PackagingCard(enabled=True, template="chapter", text="片头", duration_ms=200,
                         style_preset="neon")

    dest1 = tmp_path / "classic.mp4"
    dest2 = tmp_path / "neon.mp4"
    render_packaging_card(classic, dest1, width=160, height=120, fps=24)
    render_packaging_card(neon, dest2, width=160, height=120, fps=24)
    assert dest1.is_file() and dest2.is_file()
    assert dest1.read_bytes() != dest2.read_bytes()


def test_packaging_page_renders_preset_select_with_labels(gui, tmp_project):
    status, _, body = _html(gui, "/packaging")
    assert status == 200
    assert 'class="pk-preset"' in body
    for label in ("经典", "简约黑", "白底大字", "暖色渐变", "霓虹"):
        assert label in body


def test_packaging_apply_writes_and_validates_style_preset(gui, tmp_project):
    status, _, data = _post(gui, "/api/packaging/apply", {"patch": {
        "intro": {"enabled": True, "template": "chapter", "style_preset": "warm_gradient",
                 "text": "开场", "subtext": "", "duration_ms": 1800}}})
    assert status == 200 and data.get("ok") is True
    pk = tmp_project.load_packaging()
    assert pk.intro.style_preset == "warm_gradient"
    ev = tail_events(tmp_project.root, 1)[0]
    assert ev["action"] == "edit_packaging"

    status2, _, data2 = _post(gui, "/api/packaging/apply", {"patch": {
        "intro": {"style_preset": "not-a-real-preset"}}})
    assert status2 == 400
    assert tmp_project.load_packaging().intro.style_preset == "warm_gradient"  # unchanged


def test_card_preview_endpoint_accepts_preset_param(gui, tmp_project):
    from urllib.parse import quote

    url = ("/api/card-preview?text=" + quote("标题") + "&template=chapter&preset=neon")
    status, headers, body = _req(gui, url, raw=True)
    assert status == 200
    assert headers.get("Content-Type", "").startswith("image/")
    # an unknown preset degrades quietly to classic rather than erroring
    url_bad = ("/api/card-preview?text=" + quote("标题") + "&template=chapter&preset=bogus")
    status2, _, _ = _req(gui, url_bad, raw=True)
    assert status2 == 200


# ============================================================== D. guards


def test_v3_dangerous_surface_still_guarded(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    for path, body in (
        ("/api/edit/caption-style", {"font": "X"}),
    ):
        status, _, data = _req(gui, path, method="POST", body=body)
        assert status == 403 and "token" in data["error"].lower()
    assert _req(gui, "/api/edit/playback-manifest", host="evil.example.com")[0] == 403
    assert _req(gui, "/edit/caption-style-preview", host="evil.example.com")[0] == 403
    assert _req(gui, "/api/edit/caption-style", method="POST",
               body={"font": "X"}, host="evil.example.com")[0] == 403


def test_v3_readonly_blocks_writes_but_keeps_reads(tmp_project, add_shot):
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
    _timeline_with_two_clips(tmp_project)
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # reads still work
        assert _html(server, "/edit")[0] == 200
        assert _req(server, "/api/edit/playback-manifest")[0] == 200
        # writes are refused
        status, _, data = _post(server, "/api/edit/caption-style", {"font": "X"})
        assert status == 403 and "readonly" in data["error"]
        status2, _, data2 = _post(server, "/api/packaging/apply",
                                  {"patch": {"intro": {"style_preset": "neon"}}})
        assert status2 == 403 and "readonly" in data2["error"]
    finally:
        server.shutdown()
        server.close()
