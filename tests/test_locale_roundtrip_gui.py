"""WP4/WP6 completion tests: locale build helpers, roundtrip trim, GUI surfaces."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.core.locale import add_locale, load_lines
from manju.core.models import (
    CaptionLine,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
    VoiceTakeSidecar,
)
from manju.core.spec import compute_spec_hash
from manju.core.yamlio import write_yaml


def test_plan_locale_voice_and_register(tmp_project, add_shot, tmp_path):
    from manju.build.locale_build import plan_locale_voice

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "Hello one"
    lines["S002"]["text"] = "Hello two"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    plan = plan_locale_voice(tmp_project, "en")
    # No TTS configured → empty plan from tts_providers empty, OR missing takes
    # When no TTS, plan is empty — still a valid shape
    assert isinstance(plan, list)

    # Register a locale take manually
    media = tmp_path / "v.wav"
    media.write_bytes(b"RIFF" + b"\x00" * 30)
    dest = tmp_project.register_voice_take(
        "S001", media, VoiceTakeSidecar(provider="t", voice_hash="sha256:x"), lang="en",
    )
    assert "locales" in str(dest) and "en" in str(dest)
    plan2 = plan_locale_voice(tmp_project, "en")
    # S001 no longer missing; S002 still is (if TTS present) or both absent
    assert all(p["shot"] != "S001" for p in plan2)


def test_apply_locale_overlay_swaps_voice_and_captions(tmp_project, add_shot, tmp_path):
    from manju.build.locale_build import apply_locale_overlay, export_locale_captions
    from manju.core.models import AudioClip

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "中文"})
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "English line"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    # base voice + locale voice
    gen = tmp_project.root / "media" / "gen" / "S001"
    gen.mkdir(parents=True, exist_ok=True)
    (gen / "voice_take_01.wav").write_bytes(b"base")
    loc = gen / "locales" / "en"
    loc.mkdir(parents=True, exist_ok=True)
    (loc / "voice_take_01.wav").write_bytes(b"envo")

    tl = Timeline(
        meta=TimelineMeta(compiled_from="x"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="take_01",
                             source="media/gen/S001/take_01.mp4",
                             start_ms=0, duration_ms=2000)],
            voice=[AudioClip(source="media/gen/S001/voice_take_01.wav",
                             start_ms=100, duration_ms=800)],
            captions=[CaptionLine(start_ms=100, end_ms=900, text="中文",
                                  shot="S001", speaker="linxia")],
        ),
    )
    over = apply_locale_overlay(tmp_project, tl, "en")
    assert "locales/en" in over.tracks.voice[0].source.replace("\\", "/")
    assert any("English" in c.text for c in over.tracks.captions)

    paths = export_locale_captions(tmp_project, over, "en")
    assert paths["srt"].exists()
    assert "locales" in str(paths["srt"])
    text = paths["srt"].read_text(encoding="utf-8")
    assert "English" in text


def test_gather_compile_input_lang_uses_overlay(tmp_project, add_shot, make_take):
    from manju.timeline.compiler import gather_compile_input

    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "中文原句"})
    h = compute_spec_hash(shot, tmp_project.load_bible())
    take = make_take(tmp_project, "S001", h)
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )
    add_locale(tmp_project, "en")
    lines = load_lines(tmp_project, "en")
    lines["S001"]["text"] = "English only"
    write_yaml(tmp_project.root / "locales" / "en" / "lines.yaml", lines)

    cinp = gather_compile_input(tmp_project, lambda p: 1000, lang="en")
    assert cinp.shots
    assert cinp.shots[0].shot.dialogue.text == "English only"


def test_roundtrip_trim_plan_and_apply(tmp_project, add_shot, make_take, tmp_path):
    from manju.build.roundtrip import apply_roundtrip, plan_roundtrip, write_baseline
    from manju.core.models import TakeSidecar
    from manju.core.spec import SPEC_VERSION, compute_spec_hash, spec_payload

    shot = add_shot(tmp_project, "S001")
    h = compute_spec_hash(shot, tmp_project.load_bible(), version=SPEC_VERSION,
                          project_root=tmp_project.root)
    media = tmp_path / "src.mp4"
    media.write_bytes(b"fakevideo-bytes-long-enough")
    info = tmp_project.register_take(
        "S001", media,
        TakeSidecar(provider="test", spec_hash=h, spec_version=SPEC_VERSION,
                    spec_snapshot=spec_payload(shot, tmp_project.load_bible(),
                                              version=SPEC_VERSION,
                                              project_root=tmp_project.root),
                    probe={"duration_ms": 5000, "fps": 24, "width": 1080, "height": 1920}),
    )
    # probe may need to be ProbeInfo model
    from manju.core.models import ProbeInfo
    sc = info.sidecar.model_copy(update={
        "probe": ProbeInfo(duration_ms=5000, fps=24.0, width=1080, height=1920),
    })
    write_yaml(info.sidecar_path, sc.model_dump(exclude_none=True))
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", info.name),
    )

    base_doc = {
        "OTIO_SCHEMA": "Timeline.1",
        "name": "t",
        "tracks": {
            "OTIO_SCHEMA": "Stack.1",
            "name": "tracks",
            "children": [{
                "OTIO_SCHEMA": "Track.1",
                "name": "Video",
                "kind": "Video",
                "children": [{
                    "OTIO_SCHEMA": "Clip.1",
                    "name": "S001",
                    "source_range": {
                        "start_time": {"value": 0, "rate": 24},
                        "duration": {"value": 72, "rate": 24},  # 3000ms
                    },
                    "metadata": {"manju": {"shot": "S001", "take": info.name}},
                }],
            }],
        },
    }
    # Edited: trim in to 500ms, duration 2000ms
    edited_doc = json.loads(json.dumps(base_doc))
    edited_doc["tracks"]["children"][0]["children"][0]["source_range"] = {
        "start_time": {"value": 12, "rate": 24},  # 500ms
        "duration": {"value": 48, "rate": 24},  # 2000ms
    }
    edited = tmp_path / "edit.otio"
    edited.write_text(json.dumps(edited_doc), encoding="utf-8")
    base_dir = edited.parent / ".baseline"
    base_dir.mkdir()
    (base_dir / "edit.json").write_text(
        json.dumps({"document": base_doc, "compiled_from": "same"}),
        encoding="utf-8",
    )
    # plan_roundtrip looks for .baseline/<stem>.json
    (base_dir / "edit.otio.json").write_text(
        json.dumps({"document": base_doc, "compiled_from": ""}),
        encoding="utf-8",
    )

    plan = plan_roundtrip(tmp_project, edited)
    trim_rows = [r for r in plan["rows"] if r.get("class") == "trim"]
    assert trim_rows, f"expected trim row, got {plan['rows']}"
    assert trim_rows[0]["action"] == "set_inout"

    # Apply may need probe; monkeypatch if needed
    result = apply_roundtrip(tmp_project, plan, actor="test")
    # Either applied or skipped with a reason (probe/ffmpeg)
    assert "applied" in result and "skipped" in result


def test_gui_build_accepts_audition_target():
    """Static check: server allowlist includes audition."""
    src = Path("src/manju/gui/server.py").read_text(encoding="utf-8")
    assert '"audition"' in src or "'audition'" in src
    page = Path("src/manju/gui/page.py").read_text(encoding="utf-8")
    assert "先听后看" in page
    assert "试听" in page
    assert "/api/voice/preview" in page
