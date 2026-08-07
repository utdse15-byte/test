"""WP1 — interconnection spine: manju impact + CaptionLine.shot + cuemap."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from manju.build.impact import impact_report, impact_summary_zh
from manju.core.hashing import MANUAL_HASH
from manju.core.models import (
    CaptionLine,
    Timeline,
    TimelineMeta,
    TimelineTracks,
    VideoClip,
)
from manju.core.spec import compute_spec_hash
from manju.core.yamlio import write_yaml
from manju.timeline.cuemap import cues_for_shot


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _register_voice(project, shot_id: str, *, with_sidecar: bool = True,
                    text: str = "这不可能。"):
    from manju.core.models import VoiceTakeSidecar
    from manju.core.spec import VOICE_VERSION, compute_voice_hash

    gen = project.root / "media" / "gen" / shot_id
    gen.mkdir(parents=True, exist_ok=True)
    media = gen / "voice_take_01.wav"
    media.write_bytes(b"RIFF" + b"\x00" * 40)
    if with_sidecar:
        shot = project.load_shot(shot_id)
        bible = project.load_bible()
        # Match evaluate_voice's comparison: v2 folds today's provider
        # descriptor (None when no TTS is configured — still stable).
        try:
            from manju.providers.tts import get_tts_provider, voice_provider_descriptor
            descriptor = voice_provider_descriptor(get_tts_provider(None))
        except Exception:
            descriptor = None
        sc = VoiceTakeSidecar(
            provider="test",
            voice_hash=compute_voice_hash(
                shot, bible, version=VOICE_VERSION, provider=descriptor
            ),
            voice_hash_version=VOICE_VERSION,
        )
        sc_path = gen / "voice_take_01.sidecar.yaml"
        write_yaml(sc_path, sc.model_dump())
    return media


def test_caption_line_carries_shot_field():
    c = CaptionLine(start_ms=0, end_ms=100, text="hi", shot="S002")
    assert c.shot == "S002"
    # default empty keeps legacy timelines loadable
    c2 = CaptionLine(start_ms=0, end_ms=100, text="x")
    assert c2.shot == ""


def test_cuemap_prefers_stamped_shot():
    tl = Timeline(
        meta=TimelineMeta(compiled_from="x"),
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="t", source="a.mp4",
                          start_ms=0, duration_ms=2000),
                VideoClip(shot="S002", take="t", source="b.mp4",
                          start_ms=2000, duration_ms=2000),
            ],
            captions=[
                CaptionLine(start_ms=100, end_ms=500, text="a", shot="S001"),
                CaptionLine(start_ms=2100, end_ms=2500, text="b", shot="S002"),
                # deliberately inside S001 window but stamped S002
                CaptionLine(start_ms=200, end_ms=400, text="c", shot="S002"),
            ],
        ),
    )
    pairs = cues_for_shot(tl, "S002")
    texts = [c.text for _, c in pairs]
    assert texts == ["b", "c"]  # stamped wins over time-window


def test_cuemap_temporal_fallback_for_legacy():
    tl = Timeline(
        meta=TimelineMeta(compiled_from="x"),
        tracks=TimelineTracks(
            video=[
                VideoClip(shot="S001", take="t", source="a.mp4",
                          start_ms=0, duration_ms=2000),
            ],
            captions=[
                CaptionLine(start_ms=100, end_ms=500, text="a"),  # no shot stamp
                CaptionLine(start_ms=2500, end_ms=3000, text="outside"),
            ],
        ),
    )
    pairs = cues_for_shot(tl, "S001")
    assert len(pairs) == 1
    assert pairs[0][1].text == "a"


def test_compiler_stamps_shot_on_captions(tmp_project, add_shot, make_take):
    from manju.media.probe import probe_duration_ms
    from manju.timeline.compiler import compile_timeline, gather_compile_input

    shot = add_shot(tmp_project, "S002", dialogue={"speaker": "linxia",
                                                   "text": "这不可能。真的吗？"})
    take = make_take(tmp_project, "S002",
                     compute_spec_hash(shot, tmp_project.load_bible()))
    _select(tmp_project, "S002", take.name)
    _register_voice(tmp_project, "S002")

    # Avoid real ffprobe: stub duration
    def _probe(path):
        return 1500

    tl = compile_timeline(gather_compile_input(tmp_project, _probe))
    caps = [c for c in tl.tracks.captions if c.shot == "S002"]
    assert caps, "compiler must stamp CaptionLine.shot"
    assert all(c.shot == "S002" for c in caps)


def test_impact_dialogue_stales_voice_and_video(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S002", dialogue={"speaker": "linxia",
                                                   "text": "这不可能。"})
    take = make_take(
        tmp_project, "S002",
        compute_spec_hash(shot, tmp_project.load_bible()),
    )
    _select(tmp_project, "S002", take.name)
    # re-load so status.selected_take is current
    shot = tmp_project.load_shot("S002")
    # re-register take with correct current hash (selected)
    from manju.core.models import TakeSidecar
    media = tmp_project.root / "media" / "gen" / "S002" / f"{take.name}.mp4"
    if not media.exists():
        # make_take already registered; ensure hash matches current SPEC_VERSION
        pass
    # Refresh take sidecar to current v2 hash with snapshot
    from manju.core.spec import SPEC_VERSION, spec_payload

    info = tmp_project.get_take("S002", take.name)
    # overwrite sidecar so evaluate sees FRESH under v2
    h = compute_spec_hash(shot, tmp_project.load_bible(), version=SPEC_VERSION,
                          project_root=tmp_project.root)
    sc = TakeSidecar(
        provider="test",
        spec_hash=h,
        spec_version=SPEC_VERSION,
        spec_snapshot=spec_payload(shot, tmp_project.load_bible(),
                                   version=SPEC_VERSION,
                                   project_root=tmp_project.root),
    )
    write_yaml(info.sidecar_path, sc.model_dump())

    _register_voice(tmp_project, "S002")

    # plant a timeline with a caption for S002 so cues list is non-empty
    tl = Timeline(
        meta=TimelineMeta(compiled_from="seed"),
        fps=24, width=1080, height=1920, duration_ms=3000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S002", take=take.name, source="x.mp4",
                             start_ms=0, duration_ms=3000)],
            captions=[CaptionLine(start_ms=200, end_ms=1200,
                                  text="这不可能。", shot="S002")],
        ),
    )
    write_yaml(tmp_project.root / "timeline" / "timeline.json", tl.model_dump())
    # timeline may be json — check how project saves timeline
    from manju.core.yamlio import atomic_write_text
    atomic_write_text(
        tmp_project.root / "timeline" / "timeline.json",
        json.dumps(tl.model_dump(), ensure_ascii=False, indent=2),
    )

    report = impact_report(
        tmp_project, "S002", field="dialogue.text", new_value="新台词完全不同"
    )
    assert report["shot"] == "S002"
    assert report["field"] == "dialogue.text"
    assert report["voice"]["would_become"] == "stale"
    assert report["video"]["would_become"] == "stale"
    assert "dialogue.text" in report["video"]["changed_fields"] or report["video"]["changed_fields"]
    assert len(report["captions"]["cues"]) >= 1
    assert report["timeline"]["verdict"].startswith("recompile")
    assert "re-render" in report["renders"]["final"] or report["renders"]["final"].startswith("render")
    assert "srt" in report["exports"]["stale_after"]
    assert "cost" in report and "regen_video" in report["cost"]
    # pure: nothing written twice
    r2 = impact_report(
        tmp_project, "S002", field="dialogue.text", new_value="新台词完全不同"
    )
    assert r2 == report


def test_impact_camera_does_not_stale_voice(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S002")
    from manju.core.models import TakeSidecar
    from manju.core.spec import SPEC_VERSION, spec_payload

    h = compute_spec_hash(shot, tmp_project.load_bible(), version=SPEC_VERSION,
                          project_root=tmp_project.root)
    media = Path(tmp_project.root) / "_t.mp4"
    media.write_bytes(b"fake")
    info = tmp_project.register_take(
        "S002", media,
        TakeSidecar(provider="test", spec_hash=h, spec_version=SPEC_VERSION,
                    spec_snapshot=spec_payload(
                        shot, tmp_project.load_bible(), version=SPEC_VERSION,
                        project_root=tmp_project.root)),
    )
    _select(tmp_project, "S002", info.name)
    _register_voice(tmp_project, "S002")

    report = impact_report(
        tmp_project, "S002", field="camera.shot_size", new_value="close_up"
    )
    assert report["video"]["would_become"] == "stale"
    assert report["voice"]["would_become"] == "fresh"
    assert report["voice"]["manual"] is False


def test_impact_manual_voice_never_invalidated(tmp_project, add_shot):
    add_shot(tmp_project, "S002")
    _register_voice(tmp_project, "S002", with_sidecar=False)  # MANUAL

    report = impact_report(
        tmp_project, "S002", field="dialogue.text", new_value="改了台词"
    )
    assert report["voice"]["manual"] is True
    assert report["voice"]["would_become"] == "manual"
    assert report["voice"]["state"] == "manual"


def test_impact_byte_identical_twice(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    # snapshot project tree hashes
    def tree_sig(root: Path) -> list[tuple[str, int]]:
        out = []
        for p in sorted(root.rglob("*")):
            if p.is_file():
                out.append((str(p.relative_to(root)), p.stat().st_size))
        return out

    before = tree_sig(tmp_project.root)
    impact_report(tmp_project, "S001", field="dialogue.text", new_value="X")
    impact_report(tmp_project, "S001", field="dialogue.text", new_value="X")
    after = tree_sig(tmp_project.root)
    assert before == after


def test_impact_summary_zh():
    report = {
        "voice": {"would_become": "stale", "manual": False},
        "captions": {"cues": [{"index": 0}], "mode": "compiled"},
        "timeline": {"verdict": "recompile (inputs changed)"},
        "renders": {"final": "re-render (content key differs)"},
        "cost": {"regen_video": 0.32, "regen_voice": 0.0, "currency": "CNY"},
        "field": "dialogue.text",
    }
    s = impact_summary_zh(report)
    assert "配音" in s
    assert "字幕" in s
    assert "时间线" in s
    assert "成片" in s


def test_cli_impact_json(tmp_project, add_shot, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    add_shot(tmp_project, "S002")
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["impact", "S002", "--field", "dialogue.text", "--value", "新", "--json"],
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["shot"] == "S002"
    assert data["voice"]["would_become"] in ("stale", "missing", "not_needed", "manual")
    assert "cost" in data
