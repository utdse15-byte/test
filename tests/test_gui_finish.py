"""`manju gui` round-T finishing pages: 字幕 subtitles, 混音 mixer, 打包 packaging-v2.

Companions to :mod:`tests.test_gui_pages`. These assert the three surfaces that
let a normal video's captions, sound and packaging be finished WITHOUT opening
JianYing land in the SAME truth files + events the CLI would write:

  * subtitles → captions/captions.srt with the §3 manual-takeover flip (explicit
    confirm; captions.generated.srt kept for comparison; revert flips back),
    split/merge/validation;
  * mixer → build/mixer.read_mixer/apply_mixer verbatim, incl. the SFX full-list
    replace and a music picker that offers a seeded library asset;
  * packaging → the real PackagingSpec (validators surfaced inline), cover
    frame_ms picked off the frame strip, live card-preview endpoint.

Server-rendered, so a plain GET carries real content; the frame/strip/cover and
card-preview assertions that need real pixels are ffmpeg-gated.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest

from manju.core.events import tail_events
from manju.gui.captions_edit import merge_cues, split_cue, validate_cues
from manju.gui.server import create_server

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# ---------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _isolate_user_dirs(monkeypatch, tmp_path):
    """Keep the private library off the real ~/.manju (read at call time by the
    core, and the server runs in-process, so this reaches it)."""
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "_library"))


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, host=None,
         raw=False, timeout=20):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


def _write_srt(project, text):
    project.captions_dir.mkdir(parents=True, exist_ok=True)
    (project.captions_dir / "captions.srt").write_text(text, encoding="utf-8")


def _real_final(project, seconds=2):
    project.final_dir.mkdir(parents=True, exist_ok=True)
    final = project.final_dir / "final_v1.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", f"testsrc=duration={seconds}:size=128x72:rate=24",
         "-pix_fmt", "yuv420p", str(final)],
        check=True, capture_output=True)
    return final


def _seed_library_audio(name="room.wav"):
    from manju.core.library import Library, _hex

    src = Path(os.environ["MANJU_LIBRARY"]).parent / name
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"RIFF" + b"0" * 60 + b"WAVE")
    entry = Library().add(src, tags=["bgm"], note="room tone")["entry"]
    return _hex(entry["hash"])[:8], entry


# =============================================================== 字幕 subtitles


def test_subtitles_page_renders(gui, tmp_project):
    _write_srt(tmp_project, "1\n00:00:00,000 --> 00:00:01,000\n你好\n\n")
    status, _, body = _html(gui, "/subtitles")
    assert status == 200
    assert '<h1>字幕<span class="mj-en"' in body
    assert "你好" in body                                    # baked cue text
    assert "compiled" in body                                # mode badge
    assert "保存字幕" in body and "＋ 添加字幕" in body        # editor affordances
    assert 'data-page="/subtitles"' in body
    # nav discoverability: the subtitles link is in the shared nav
    assert 'href="/mixer"' in body and 'href="/packaging"' in body


def test_subtitles_manual_flip_needs_confirm_then_writes(gui, tmp_project):
    """First edit in compiled mode asks for confirmation; confirming flips
    rules.captions.mode → manual, snapshots the compiled captions into
    captions.generated.srt and writes the human cues to captions.srt + one event."""
    _write_srt(tmp_project, "1\n00:00:00,000 --> 00:00:01,000\n自动字幕\n\n")

    # no confirm → the takeover-explaining prompt, nothing written
    status, _, data = _post(gui, "/api/subtitles/save",
                            {"cues": [{"start_ms": 0, "end_ms": 1200, "text": "手改字幕"}]})
    assert status == 200 and data["needs_confirm"] is True
    assert "manual" in data["message"] and "generated" in data["message"]
    assert tmp_project.load_rules().captions.mode == "compiled"   # untouched

    # confirm → flip + write + snapshot + event
    status, _, data = _post(gui, "/api/subtitles/save",
                            {"cues": [{"start_ms": 0, "end_ms": 1200, "text": "手改字幕"}],
                             "confirm_manual": True})
    assert status == 200 and data["ok"] and data["flipped"] is True
    assert tmp_project.load_rules().captions.mode == "manual"
    srt = (tmp_project.captions_dir / "captions.srt").read_text(encoding="utf-8")
    assert "手改字幕" in srt and "00:00:01,200" in srt
    gen = (tmp_project.captions_dir / "captions.generated.srt").read_text(encoding="utf-8")
    assert "自动字幕" in gen                                       # compiled comparison kept
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "captions_edit"
    assert event["detail"]["mode_flip"] is True and event["detail"]["via"] == "gui"

    # in manual mode the page now offers the 还原自动字幕 affordance
    _, _, body = _html(gui, "/subtitles")
    assert "manual" in body and "sub-revert" in body


def test_subtitles_revert_restores_compiled(gui, tmp_project):
    # arrive in manual mode with a generated comparison on disk
    _write_srt(tmp_project, "1\n00:00:00,000 --> 00:00:01,000\n人工\n\n")
    (tmp_project.captions_dir / "captions.generated.srt").write_text(
        "1\n00:00:00,000 --> 00:00:00,800\n编译版\n\n", encoding="utf-8")
    rules = tmp_project.load_rules()
    rules.captions.mode = "manual"
    tmp_project.save_rules(rules)

    status, _, data = _post(gui, "/api/subtitles/revert", {})
    assert status == 200 and data["ok"] and data["restored"] is True
    assert tmp_project.load_rules().captions.mode == "compiled"
    srt = (tmp_project.captions_dir / "captions.srt").read_text(encoding="utf-8")
    assert "编译版" in srt and "人工" not in srt                    # compiled restored
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "captions_revert" and event["detail"]["via"] == "gui"


def test_subtitles_validation_rejects_empty_and_inverted(gui, tmp_project):
    # empty cue → 400 with a field error, nothing written
    status, _, data = _post(gui, "/api/subtitles/save",
                            {"cues": [{"start_ms": 0, "end_ms": 1000, "text": "  "}],
                             "confirm_manual": True})
    assert status == 400 and any("空" in e for e in data["errors"])
    # inverted window (end ≤ start) → 400
    status, _, data = _post(gui, "/api/subtitles/save",
                            {"cues": [{"start_ms": 1000, "end_ms": 500, "text": "x"}],
                             "confirm_manual": True})
    assert status == 400 and any("时间窗" in e for e in data["errors"])
    assert not (tmp_project.captions_dir / "captions.srt").exists()
    assert tmp_project.load_rules().captions.mode == "compiled"


def test_subtitles_overlap_warns_but_saves(gui, tmp_project):
    # two cues overlapping beyond the 120ms tolerance → warning, still saved
    cues = [{"start_ms": 0, "end_ms": 1500, "text": "甲"},
            {"start_ms": 1000, "end_ms": 2000, "text": "乙"}]
    status, _, data = _post(gui, "/api/subtitles/save",
                            {"cues": cues, "confirm_manual": True})
    assert status == 200 and data["ok"]
    assert data["warnings"] and any("重叠" in w for w in data["warnings"])
    assert (tmp_project.captions_dir / "captions.srt").exists()


def test_subtitles_split_and_merge_helpers():
    cues = [{"start_ms": 0, "end_ms": 1000, "text": "你好世界", "speaker": "a"}]
    out = split_cue(cues, 0, 500)
    assert len(out) == 2
    assert out[0] == {"start_ms": 0, "end_ms": 500, "text": "你好", "speaker": "a"}
    assert out[1] == {"start_ms": 500, "end_ms": 1000, "text": "世界", "speaker": "a"}
    merged = merge_cues(out, 0)
    assert len(merged) == 1
    assert merged[0]["start_ms"] == 0 and merged[0]["end_ms"] == 1000
    assert merged[0]["text"] == "你好世界"
    # split point outside the cue is rejected
    with pytest.raises(Exception):
        split_cue(cues, 0, 1000)
    # empty cue text is a hard error in validate_cues
    errs, _ = validate_cues([{"start_ms": 0, "end_ms": 100, "text": ""}])
    assert errs


def test_subtitles_split_result_saves_two_cues(gui, tmp_project):
    cues = [{"start_ms": 0, "end_ms": 1000, "text": "你好世界"}]
    split = split_cue([dict(c) for c in cues], 0, 500)
    status, _, data = _post(gui, "/api/subtitles/save",
                            {"cues": split, "confirm_manual": True})
    assert status == 200 and data["ok"] and data["cues"] == 2
    srt = (tmp_project.captions_dir / "captions.srt").read_text(encoding="utf-8")
    assert srt.count("-->") == 2 and "你好" in srt and "世界" in srt


# ================================================================ 混音 mixer


def test_mixer_page_offers_library_and_imports_sources(gui, tmp_project):
    hash8, entry = _seed_library_audio()
    tmp_project.imports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.imports_dir / "bgm.mp3").write_bytes(b"ID3xx")

    status, _, body = _html(gui, "/mixer")
    assert status == 200
    assert '<h1>混音<span class="mj-en"' in body
    assert "人声" in body and "背景音乐" in body and "环境" in body and "音效" in body
    assert ("lib:" + hash8) in body                          # seeded library asset option
    assert entry["name"] in body                             # its name in the picker
    assert "imports/bgm.mp3" in body                         # project import option
    assert 'href="/edit"' in body                            # per-shot audio delegated to /edit


def test_mixer_apply_roundtrips_through_apply_mixer(gui, tmp_project):
    """Voice + BGM + SFX (full-list replace) go through apply_mixer verbatim,
    landing in rules.yaml, returning the rebuild verdict + one mixer event."""
    changes = {
        "voice_gain_db": -3.5,
        "music": {"source": None, "gain_db": -12.0, "ducking": True,
                  "start_offset_ms": 800, "fade_in_ms": 500, "fade_out_ms": 1500,
                  "duck": {"threshold": 0.05, "ratio": 8.0, "attack_ms": 5, "release_ms": 250}},
        "sfx": [{"source": "media/imports/whoosh.wav", "at": "shot:S001:end",
                 "offset_ms": 0, "gain_db": -6.0}],
    }
    status, _, data = _post(gui, "/api/mixer/apply", {"changes": changes})
    assert status == 200 and data["ok"]
    assert "music" in data["changed"] and "sfx" in data["changed"]
    assert data["rebuild"]["final"] == "re-render"
    assert "segments_restale" in data["rebuild"]

    rules = tmp_project.load_rules()
    assert rules.audio.voice_gain_db == -3.5
    assert rules.music.start_offset_ms == 800 and rules.music.fade_in_ms == 500
    assert len(rules.audio.sfx) == 1 and rules.audio.sfx[0].at == "shot:S001:end"
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "mixer" and "sfx" in event["detail"]["changed"]

    # a subsequent replace fully swaps the sfx list (API contract)
    status, _, data = _post(gui, "/api/mixer/apply", {"changes": {"sfx": []}})
    assert status == 200 and tmp_project.load_rules().audio.sfx == []


def test_mixer_lib_source_resolved_into_imports(gui, tmp_project):
    hash8, _ = _seed_library_audio()
    status, _, data = _post(gui, "/api/mixer/apply",
                            {"changes": {"music": {"source": "lib:" + hash8}}})
    assert status == 200 and data["ok"]
    src = tmp_project.load_rules().music.source
    assert src and src.startswith("media/imports/")           # copied in, now a project asset
    assert tmp_project.resolve(src).exists()


def test_mixer_apply_rejects_bad_value(gui, tmp_project):
    # an sfx entry with no source fails SfxClipSpec validation → 400, nothing written
    status, _, data = _post(gui, "/api/mixer/apply",
                            {"changes": {"sfx": [{"at": "shot:S001", "gain_db": -6}]}})
    assert status == 400 and "invalid" in data["error"]


# ============================================================ 打包 packaging


def test_packaging_page_renders(gui, tmp_project):
    status, _, body = _html(gui, "/packaging")
    assert status == 200
    assert '<h1>包装<span class="mj-en"' in body
    assert "片头" in body and "片尾" in body                    # intro/outro forms
    assert "信息卡" in body                                     # info cards editor
    assert '<h2>封面<span class="mj-en"' in body
    assert '<h2>预告片<span class="mj-en"' in body                # Chinese-first
    assert "Logo" in body and "水印" in body and "角标" in body and "CTA" in body
    assert 'data-page="/packaging"' in body


def test_packaging_apply_writes_intro_and_cover(gui, tmp_project):
    patch = {"intro": {"enabled": True, "text": "标题", "subtext": "副题",
                       "duration_ms": 2500, "template": "chapter"},
             "cover": {"mode": "frame", "frame_ms": 1234}}
    status, _, data = _post(gui, "/api/packaging/apply", {"patch": patch})
    assert status == 200 and data["ok"]
    assert "intro" in data["changed"] and "cover" in data["changed"]
    spec = tmp_project.load_packaging()
    assert spec.intro.enabled and spec.intro.text == "标题" and spec.intro.duration_ms == 2500
    assert spec.cover.frame_ms == 1234
    event = tail_events(tmp_project.root, 1)[0]
    assert event["action"] == "edit_packaging" and event["detail"]["via"] == "gui"


def test_packaging_validator_surfaced_inline(gui, tmp_project):
    # opacity out of [0,1] is a model validator error, surfaced inline; nothing written
    status, _, data = _post(gui, "/api/packaging/apply",
                            {"patch": {"logo": {"enabled": True, "opacity": 2.0}}})
    assert status == 400
    assert "opacity" in data["error"] and "invalid" in data["error"]
    assert tmp_project.load_packaging().logo.enabled is False   # untouched
    # a size_pct outside (0,100] likewise
    status, _, data = _post(gui, "/api/packaging/apply",
                            {"patch": {"logo": {"size_pct": 0}}})
    assert status == 400 and "size_pct" in data["error"]


def test_packaging_info_cards_full_replace(gui, tmp_project):
    patch = {"info_cards": [
        {"kind": "chapter", "text": "第一章", "at": "shot:S001", "offset_ms": 0,
         "duration_ms": 1500, "template": "chapter"}]}
    status, _, data = _post(gui, "/api/packaging/apply", {"patch": patch})
    assert status == 200 and data["ok"]
    cards = tmp_project.load_packaging().info_cards
    assert len(cards) == 1 and cards[0].text == "第一章"
    # replace with an empty list wipes it (full-list semantics)
    assert _post(gui, "/api/packaging/apply", {"patch": {"info_cards": []}})[0] == 200
    assert tmp_project.load_packaging().info_cards == []


# ---------------------------------------------------- card preview + frames


def test_card_preview_endpoint_200s(gui, tmp_project):
    # 载荷敏感对策: with the chromium lane open on windows-latest (#31) this
    # endpoint launches a REAL Chrome screenshot inside the request — the
    # sibling test in test_edit_v3 fired its socket deadline on the slowest
    # gate runner. Detection latency only; assertions unchanged.
    url = "/api/card-preview?text=" + quote("你好") + "&template=chapter"
    status, headers, body = _req(gui, url, raw=True, timeout=120)
    assert status == 200
    ctype = headers.get("Content-Type", "")
    assert ctype.startswith("image/")                          # png or svg placeholder


@pytest.mark.skipif(not _HAS_FFMPEG, reason="card render needs ffmpeg (drawtext floor)")
def test_card_preview_renders_and_caches(gui, tmp_project):
    from manju.media.frames import frames_cache_dir

    url = "/api/card-preview?text=" + quote("封面") + "&template=chapter"
    status, headers, body = _req(gui, url, raw=True, timeout=120)
    assert status == 200 and body[:4] == b"\x89PNG"            # real PNG via the card chain
    cached = list(frames_cache_dir(tmp_project.root).glob("card_*.png"))
    assert cached                                              # cached into .manju/frames
    # a second identical request is served from the cache (byte-identical)
    _, _, body2 = _req(gui, url, raw=True, timeout=120)
    assert body2 == body


@pytest.mark.skipif(not _HAS_FFMPEG, reason="frame/strip need ffmpeg")
def test_frame_and_strip_endpoints(gui, tmp_project):
    final = _real_final(tmp_project)
    rel = tmp_project.relpath(final)
    status, headers, body = _req(gui, f"/frame?src={rel}&ms=1000&w=120", raw=True)
    assert status == 200 and body[:2] == b"\xff\xd8"           # JPEG
    # strip: one pass, N frames with mapped timestamps + serveable urls
    status, _, data = _req(gui, f"/api/strip?src={rel}&count=6")
    assert status == 200 and data["duration_ms"] > 0 and len(data["frames"]) == 6
    fstatus, _, fbody = _req(gui, data["frames"][2]["url"], raw=True)
    assert fstatus == 200 and fbody[:2] == b"\xff\xd8"
    # a src outside the served allowlist is refused
    assert _req(gui, "/frame?src=../../etc/passwd&ms=0")[0] in (403, 404)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="cover pick needs ffmpeg strip")
def test_packaging_cover_frame_from_picked_strip(gui, tmp_project):
    """The cover picker's real flow: read the strip over the final, pick a
    frame's ms, and write it through packaging.apply into cover.frame_ms."""
    final = _real_final(tmp_project)
    rel = tmp_project.relpath(final)
    _, _, strip = _req(gui, f"/api/strip?src={rel}&count=8")
    picked = strip["frames"][4]["ms"]
    assert picked > 0
    status, _, data = _post(gui, "/api/packaging/apply",
                            {"patch": {"cover": {"mode": "frame", "frame_ms": picked}}})
    assert status == 200 and data["ok"]
    assert tmp_project.load_packaging().cover.frame_ms == picked


@pytest.mark.skipif(not _HAS_FFMPEG, reason="teaser warnings need a real final to probe")
def test_packaging_teaser_final_length_warning(gui, tmp_project):
    _real_final(tmp_project, seconds=2)  # ~2000ms final
    status, _, data = _post(gui, "/api/packaging/apply",
                            {"patch": {"teaser": {"enabled": True, "from_ms": 5000,
                                                  "duration_ms": 1000}}})
    assert status == 200 and data["ok"]
    assert data["warnings"] and any("final" in w for w in data["warnings"])


# ================================================================ guards


def test_new_routes_guarded(gui, tmp_project):
    # CSRF: every mutating action needs the token
    assert _req(gui, "/api/subtitles/save", method="POST", body={"cues": []})[0] == 403
    assert _req(gui, "/api/mixer/apply", method="POST", body={"changes": {}})[0] == 403
    assert _req(gui, "/api/packaging/apply", method="POST", body={"patch": {}})[0] == 403
    # DNS-rebinding guard applies to the new pages
    assert _html(gui, "/subtitles", host="evil.example.com")[0] == 403
    assert _html(gui, "/mixer", host="evil.example.com")[0] == 403
    # containment (§5): dangerous surfaces never exist here (with a valid token
    # it is a clean 404, not the pre-token 403)
    assert _post(gui, "/api/unlock", {})[0] == 404
    # pages carry the CSP + token like the S8b pages
    status, headers, body = _html(gui, "/packaging")
    assert "script-src 'self'" in headers.get("Content-Security-Policy", "")
    assert gui.token in body


def test_readonly_blocks_finishing_actions(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # pages still render read-only
        assert _html(server, "/subtitles")[0] == 200
        assert _html(server, "/mixer")[0] == 200
        assert _html(server, "/packaging")[0] == 200
        # but every mutation is refused
        assert _post(server, "/api/subtitles/save", {"cues": []})[0] == 403
        assert _post(server, "/api/mixer/apply", {"changes": {}})[0] == 403
        assert _post(server, "/api/packaging/apply", {"patch": {}})[0] == 403
    finally:
        server.shutdown()
        server.close()
