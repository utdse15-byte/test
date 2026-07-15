"""WP2 — TTS 试听 preview (never a take) + audition compile flag."""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.core.spec import compute_spec_hash
from manju.media.ttspreview import PreviewUnavailable, preview_voice
from manju.timeline.compiler import CompileError, gather_compile_input


def test_preview_voice_cached_and_never_registers_take(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "你好世界"})

    calls = {"n": 0}

    class FakeTts:
        id = "fake_edge"
        manifest = type("M", (), {
            "adapter": "manju.providers.edge_tts:EdgeTtsProvider",
            "cost": type("C", (), {"per_call": 0.0, "currency": "CNY"})(),
            "tts": type("T", (), {"default_voice": "zh-CN-XiaoxiaoNeural",
                                  "audio_format": "mp3", "language": "zh-CN"})(),
        })()

        def _voice_for(self, shot, bible):
            return "zh-CN-XiaoxiaoNeural"

        def synthesize(self, project, shot, bible):
            raise AssertionError("preview must not call full synthesize for edge path")

    def fake_resolve(provider):
        return "fake_edge", FakeTts()

    monkeypatch.setattr("manju.media.voicefix._resolve_tts", fake_resolve)

    def fake_edge(provider, text, voice_id, dest, **_kw):
        calls["n"] += 1
        dest.write_bytes(b"ID3fake-audio-" + text.encode("utf-8")[:20])

    monkeypatch.setattr("manju.media.ttspreview._synthesize_edge", fake_edge)

    r1 = preview_voice(tmp_project, "S001")
    assert r1["cached"] is False
    assert Path(r1["path"]).exists()
    assert calls["n"] == 1
    # no media/gen voice take
    gen = tmp_project.root / "media" / "gen" / "S001"
    assert not list(gen.glob("voice_take_*")) if gen.exists() else True

    r2 = preview_voice(tmp_project, "S001")
    assert r2["cached"] is True
    assert r2["path"] == r1["path"]
    assert calls["n"] == 1  # no re-synthesis


def test_preview_missing_dialogue_raises(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": ""})
    with pytest.raises(PreviewUnavailable):
        preview_voice(tmp_project, "S001")


def test_allow_missing_takes_compiles_slates(tmp_project, add_shot):
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "第一句"})
    add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "第二句"})

    def probe(_p):
        return 1000

    # Without flag: hard fail
    with pytest.raises(CompileError):
        gather_compile_input(tmp_project, probe)

    cinp = gather_compile_input(tmp_project, probe, allow_missing_takes=True)
    assert len(cinp.shots) == 2
    assert all(s.take_name == "__slate__" for s in cinp.shots)

    from manju.timeline.compiler import compile_timeline
    tl = compile_timeline(cinp)
    assert len(tl.tracks.video) == 2
    assert all(c.take == "__slate__" for c in tl.tracks.video)


def test_audition_content_key_stable(tmp_project, add_shot):
    from manju.core.models import (
        CaptionLine, Timeline, TimelineMeta, TimelineTracks, VideoClip,
    )
    from manju.media.audition import audition_content_key

    add_shot(tmp_project, "S001")
    tl = Timeline(
        meta=TimelineMeta(compiled_from="a"),
        fps=24, width=1080, height=1920, duration_ms=2000,
        tracks=TimelineTracks(
            video=[VideoClip(shot="S001", take="__slate__", source="__slate__/S001",
                             start_ms=0, duration_ms=2000)],
            captions=[CaptionLine(start_ms=100, end_ms=900, text="hi", shot="S001")],
        ),
    )
    k1 = audition_content_key(tmp_project, tl, ass_file=None)
    k2 = audition_content_key(tmp_project, tl, ass_file=None)
    assert k1 == k2
    assert k1.startswith("sha256:") or len(k1) > 10
