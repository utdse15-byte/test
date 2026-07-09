"""Last-round trust bar: caption/final freshness, volume/transition, smokes."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.locale import (
    add_locale,
    load_lines,
    locale_status,
    write_locale_captions_key,
    _locale_lines_content_key,
)
from manju.core.models import (
    CaptionLine,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    TransitionSpec,
    VideoClip,
)
from manju.core.yamlio import write_yaml

FFMPEG = shutil.which("ffmpeg")
has_ffmpeg = bool(FFMPEG)


def _tiny_mp4(path: Path, duration_s: float = 0.5) -> Path:
    """Real short clip with audible sine (loudnorm rejects pure silence/NaN)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=blue:s=320x240:d={duration_s}:r=24",
        "-f", "lavfi", "-i", f"sine=f=440:d={duration_s}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest",
        str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


# ------------------------------------------------------------------ 1 captions


def test_locale_captions_stale_when_shot_order_changes(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "One"
    lines["S002"]["text"] = "Two"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    # Base timeline with ordered cues
    tl = Timeline(
        meta=TimelineMeta(compiled_from="fp1"),
        fps=24, width=320, height=240, duration_ms=2000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="t", source="x.mp4",
                          start_ms=0, duration_ms=1000),
                VideoClip(shot="S002", take="t", source="y.mp4",
                          start_ms=1000, duration_ms=1000),
            ],
            captions=[
                CaptionLine(start_ms=0, end_ms=900, text="中1", shot="S001"),
                CaptionLine(start_ms=1000, end_ms=1900, text="中2", shot="S002"),
            ],
        ),
    )
    tmp_project.save_timeline(tl)

    # "Generate" locale captions key as if export just ran
    key1 = _locale_lines_content_key(tmp_project, "en")
    write_locale_captions_key(tmp_project, "en")
    cap_dir = tmp_project.captions_dir / "locales" / "en"
    cap_dir.mkdir(parents=True, exist_ok=True)
    (cap_dir / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nOne\n",
                                          encoding="utf-8")
    (cap_dir / "captions.ass").write_text("[Script Info]\n", encoding="utf-8")

    st = locale_status(tmp_project, "en")
    assert st["locales"]["en"]["captions"]["freshness"] == "ok"

    # Only reorder base shots — English text unchanged
    from manju.core.writes import permute_index
    permute_index(tmp_project, ["S002", "S001"], actor="t", via="test")
    # Also reorder timeline cues to match
    tl2 = Timeline(
        meta=TimelineMeta(compiled_from="fp2-reordered"),
        fps=24, width=320, height=240, duration_ms=2000,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S002", take="t", source="y.mp4",
                          start_ms=0, duration_ms=1000),
                VideoClip(shot="S001", take="t", source="x.mp4",
                          start_ms=1000, duration_ms=1000),
            ],
            captions=[
                CaptionLine(start_ms=0, end_ms=900, text="中2", shot="S002"),
                CaptionLine(start_ms=1000, end_ms=1900, text="中1", shot="S001"),
            ],
        ),
    )
    tmp_project.save_timeline(tl2)

    key2 = _locale_lines_content_key(tmp_project, "en")
    assert key2 != key1
    st2 = locale_status(tmp_project, "en")
    assert st2["locales"]["en"]["captions"]["freshness"] == "stale"


# ------------------------------------------------------------------ 2 final


def test_locale_final_freshness_no_silent_ok_on_error(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "Hi"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    # Plant a fake locale final + key
    fdir = tmp_project.final_dir / "locales" / "en"
    fdir.mkdir(parents=True, exist_ok=True)
    final = fdir / "final_v1.mp4"
    final.write_bytes(b"fake")
    (fdir / "final_v1.key.json").write_text(
        json.dumps({"final_key": "sha256:deadbeef"}), encoding="utf-8",
    )
    tl = Timeline(
        meta=TimelineMeta(compiled_from="x"),
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="t", source="x.mp4",
                             start_ms=0, duration_ms=1000)],
        ),
    )
    tmp_project.save_timeline(tl)

    def boom(*a, **k):
        raise RuntimeError("recompute failed for test")

    monkeypatch.setattr("manju.media.render.final_content_key", boom)
    st = locale_status(tmp_project, "en")
    fin = st["locales"]["en"]["final"]
    assert fin["freshness"] in ("有问题", "unknown")
    assert fin["freshness"] != "ok"
    assert fin.get("reason")
    assert "recompute" in fin["reason"] or "failed" in fin["reason"]


# ------------------------------------------------------------------ 3 volume/transition


def test_roundtrip_volume_and_transition_plan_apply(tmp_project, add_shot):
    from manju.build.roundtrip import apply_roundtrip, plan_roundtrip

    add_shot(tmp_project, "S001")
    base = {
        "materials": {"videos": [
            {"id": "v0", "type": "video", "material_name": "S001/take_01",
             "manju": {"shot": "S001", "take": "take_01", "kind": "video"}},
        ], "texts": []},
        "tracks": [
            {"type": "video", "segments": [
                {"material_id": "v0",
                 "target_timerange": {"start": 0, "duration": 1_000_000},
                 "manju": {
                     "shot": "S001", "take": "take_01", "kind": "video",
                     "source_gain_db": 0.0,
                     "transition_out": {"type": "fade", "duration_ms": 300},
                 }},
            ]},
        ],
        "canvas_config": {}, "duration": 1_000_000, "fps": 24,
    }
    edited = json.loads(json.dumps(base))
    edited["tracks"][0]["segments"][0]["manju"]["source_gain_db"] = -6.0
    edited["tracks"][0]["segments"][0]["manju"]["source_mute"] = False
    edited["tracks"][0]["segments"][0]["volume"] = 0.5
    edited["tracks"][0]["segments"][0]["manju"]["transition_out"] = {
        "type": "cut", "duration_ms": 0,
    }
    path = tmp_project.root / "exports" / "jianying" / "draft_content.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(edited), encoding="utf-8")
    base_dir = path.parent / ".baseline"
    base_dir.mkdir(exist_ok=True)
    (base_dir / "draft_content.json").write_text(
        json.dumps({"document": base, "compiled_from": ""}),
        encoding="utf-8",
    )
    plan = plan_roundtrip(tmp_project, path)
    classes = {r["class"] for r in plan["rows"]}
    assert "volume" in classes, plan["rows"]
    assert "transition" in classes, plan["rows"]

    result = apply_roundtrip(tmp_project, plan, actor="test")
    assert any(a.get("class") == "volume" for a in result["applied"])
    assert any(a.get("class") == "transition" for a in result["applied"])
    shot = tmp_project.load_shot("S001")
    assert abs(shot.source_audio.gain_db - (-6.0)) < 1e-6
    rules = tmp_project.load_rules()
    ov = rules.transition_overrides.get("S001")
    assert ov is not None
    assert ov.type == "cut"


# ------------------------------------------------------------------ 4 smokes


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not on PATH")
def test_smoke_audition_build(tmp_project, add_shot, tmp_path):
    """Audition smoke: slate + voice, captions disabled (ASS path escape on
    Windows temp+CJK is a known filter-escape issue, not audition logic)."""
    from manju.build.graph import run_build

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "试听一句"})
    rules = tmp_project.load_rules()
    rules.captions.enabled = False
    tmp_project.save_rules(rules)
    wav = tmp_path / "v.wav"
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=f=440:d=0.4", "-c:a", "pcm_s16le", str(wav)],
        check=True, capture_output=True,
    )
    gen = tmp_project.root / "media" / "gen" / "S001"
    gen.mkdir(parents=True, exist_ok=True)
    shutil.copy2(wav, gen / "voice_take_01.wav")

    result = run_build(
        tmp_project, target="audition", gen="off", assume_yes=True, actor="test",
    )
    assert result.ok, result.errors
    assert result.render_path
    out = tmp_project.root / result.render_path
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not on PATH")
def test_smoke_jianying_roundtrip_reorder_caption(tmp_project, add_shot, make_take):
    from manju.build.roundtrip import plan_roundtrip
    from manju.core.spec import compute_spec_hash
    from manju.exporters.jianying import export_jianying

    s1 = add_shot(tmp_project, "S001")
    s2 = add_shot(tmp_project, "S002")
    t1 = make_take(tmp_project, "S001",
                   compute_spec_hash(s1, tmp_project.load_bible()))
    t2 = make_take(tmp_project, "S002",
                   compute_spec_hash(s2, tmp_project.load_bible()))
    for sid, tn in (("S001", t1.name), ("S002", t2.name)):
        p = tmp_project.root / "media" / "gen" / sid / f"{tn}.mp4"
        if not p.exists():
            _tiny_mp4(p, 0.4)
        tmp_project.update_shot_raw(
            sid, lambda d, n=tn: d.setdefault("status", {}).__setitem__(
                "selected_take", n),
        )
    tl = Timeline(
        meta=TimelineMeta(compiled_from="smoke"),
        fps=24, width=320, height=240, duration_ms=800,
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take=t1.name,
                          source=f"media/gen/S001/{t1.name}.mp4",
                          start_ms=0, duration_ms=400),
                VideoClip(shot="S002", take=t2.name,
                          source=f"media/gen/S002/{t2.name}.mp4",
                          start_ms=400, duration_ms=400),
            ],
            captions=[
                CaptionLine(start_ms=0, end_ms=300, text="一", shot="S001"),
                CaptionLine(start_ms=400, end_ms=700, text="二", shot="S002"),
            ],
        ),
    )
    draft_path = export_jianying(tmp_project, tl)
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    vtr = next(t for t in draft["tracks"] if t.get("type") == "video")
    vtr["segments"] = list(reversed(vtr["segments"]))
    texts = draft["materials"]["texts"]
    texts[0]["content"] = "改一"
    texts[0].setdefault("manju", {})["text"] = "改一"
    for t in draft["tracks"]:
        if t.get("type") == "text" and t.get("segments"):
            t["segments"][0].setdefault("manju", {})["text"] = "改一"
    edited = draft_path.parent / "edited_draft_content.json"
    edited.write_text(json.dumps(draft), encoding="utf-8")
    # baseline next to edited
    bdir = edited.parent / ".baseline"
    bdir.mkdir(exist_ok=True)
    orig = json.loads(draft_path.read_text(encoding="utf-8"))
    (bdir / "edited_draft_content.json").write_text(
        json.dumps({"document": orig, "compiled_from": "smoke"}),
        encoding="utf-8",
    )
    plan = plan_roundtrip(tmp_project, edited)
    classes = [r["class"] for r in plan["rows"] if r.get("class") != "no_changes"]
    assert "reorder" in classes
    assert "caption_edit" in classes


@pytest.mark.skipif(not has_ffmpeg, reason="ffmpeg not on PATH")
def test_smoke_locale_final_content_key_path(tmp_project, add_shot, make_take):
    """Locale final under locales/en/ with content key (no ASS burn — Windows
    path escaping for ass= filter is a separate issue)."""
    from manju.build.locale_build import apply_locale_overlay, next_locale_final_path
    from manju.core.hashing import cache_key
    from manju.core.spec import compute_spec_hash
    from manju.media.render import _write_key_sidecar, final_content_key, render_timeline

    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "中文"})
    h = compute_spec_hash(shot, tmp_project.load_bible())
    take = make_take(tmp_project, "S001", h)
    media = tmp_project.root / "media" / "gen" / "S001" / f"{take.name}.mp4"
    _tiny_mp4(media, 0.5)
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "English"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    tl = Timeline(
        meta=TimelineMeta(compiled_from="loc"),
        fps=24, width=320, height=240, duration_ms=500,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take=take.name,
                             source=f"media/gen/S001/{take.name}.mp4",
                             start_ms=0, duration_ms=500)],
        ),
    )
    tmp_project.save_timeline(tl)
    over = apply_locale_overlay(tmp_project, tl, "en")
    out = next_locale_final_path(tmp_project, "en")
    rendered = render_timeline(
        tmp_project, over, target="final", out_path=out,
        ass_file=None, force=True,
    )
    key = cache_key({
        "locale": "en",
        "base_key": final_content_key(
            tmp_project, over, ass_file=None, target="final",
        ),
    })
    _write_key_sidecar(rendered, key, "final:en")
    assert rendered.exists() and rendered.stat().st_size > 0
    st = locale_status(tmp_project, "en")
    assert st["locales"]["en"]["final"]["path"]
    assert st["locales"]["en"]["final"]["freshness"] in (
        "ok", "stale", "有问题", "unknown",
    )
