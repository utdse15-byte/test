"""media/ integrity — regressions found by the media/ scan (adversarially
reproduced before fixing). Behavioral pins, never source-text greps. Each test
monkeypatches the ffmpeg/fc-match boundary so it runs on a toolless box.

1. probe._parse_fps: `r_frame_rate or avg_frame_rate` short-circuits on the
   truthy-but-unusable string "0/0", so avg_frame_rate is never consulted and
   fps comes back None for a stream that only carries a usable avg_frame_rate.
2. align.apply_multi_shot: the per-slice ffmpeg ran check=False and inferred
   success purely from output existence/size, so a non-zero exit that left a
   partial (non-empty) file registered a corrupt manual voice take (append-only,
   never auto-invalidated) — the UNKNOWN-guessed-into-PASS family.
3. card._fc_match / _fc_match_family: fc-match output decoded with strict UTF-8
   and no errors="replace", so a non-UTF-8 font path raised UnicodeDecodeError
   out of the find_font owner (documented to degrade to None / never crash).
4. voicefix._current_cues: the manual captions.srt was read with strict UTF-8
   catching only OSError, so a non-UTF-8 SRT raised an uncaught UnicodeDecodeError
   out of repair_voice.
5. ttspreview.preview_voice: the synthesized preview was placed with os.replace
   from a system-temp dir into the project dir; on different filesystems that is
   an uncaught cross-device OSError (EXDEV / WinError 17).
"""

from __future__ import annotations

import errno
import json
import types
from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# (1) probe fps falls back to avg_frame_rate when r_frame_rate is "0/0"          #
# --------------------------------------------------------------------------- #


def test_probe_fps_falls_back_to_avg_frame_rate(monkeypatch):
    import subprocess

    from manju.media.probe import probe

    payload = {
        "streams": [{"codec_type": "video", "width": 1920, "height": 1080,
                     "r_frame_rate": "0/0", "avg_frame_rate": "30/1"}],
        "format": {"duration": "10.0"},
    }

    def _fake_run(cmd, **kw):
        return types.SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    info = probe(Path("whatever.mp4"))
    # "0/0" is unusable -> fps must be derived from avg_frame_rate (30), not None
    assert info.fps == 30.0


# --------------------------------------------------------------------------- #
# (2) apply_multi_shot treats a non-zero ffmpeg slice as failed (not registered) #
# --------------------------------------------------------------------------- #


def test_apply_multi_shot_nonzero_slice_is_skipped(tmp_project, add_shot, monkeypatch):
    import subprocess

    from manju.media import align

    add_shot(tmp_project, "S001")
    media = tmp_project.root / "media" / "imports" / "src.wav"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"RIFF" + b"\x00" * 64)

    def _fake_run(cmd, **kw):
        out = Path(cmd[-1])  # ffmpeg's output path is the last argv token
        out.write_bytes(b"RIFF" + b"\x00" * 40)  # non-empty PARTIAL slice
        return types.SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    plan = {"media": str(media),
            "rows": [{"shot": "S001", "state": "ok",
                      "start_ms": 0, "end_ms": 1000}]}
    result = align.apply_multi_shot(tmp_project, plan)
    # ffmpeg exited non-zero -> the slice is failed, never registered as truth
    assert any(s.get("shot") == "S001" and "slice failed" in s.get("reason", "")
               for s in result["skipped"])
    assert not tmp_project.takes("S001")  # no corrupt take registered


# --------------------------------------------------------------------------- #
# (3) find_font's fc-match probes never crash on a non-UTF-8 font path            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("fn_name", ["_fc_match", "_fc_match_family"])
def test_card_fc_match_non_utf8_does_not_crash(monkeypatch, fn_name):
    from manju.media import card

    def _fake_run(cmd, **kw):
        # mirror CPython: a strict text-mode decode of non-UTF-8 bytes raises;
        # errors="replace" is what makes it degrade instead
        if kw.get("errors") != "replace":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
        return types.SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(card.subprocess, "run", _fake_run)
    # find_font contract: degrade to None, never raise
    assert getattr(card, fn_name)("Noto Sans CJK SC") is None


# --------------------------------------------------------------------------- #
# (4) _current_cues survives a non-UTF-8 manual captions.srt                     #
# --------------------------------------------------------------------------- #


def test_voicefix_current_cues_non_utf8_srt_does_not_crash(tmp_project):
    from manju.media.voicefix import _current_cues

    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    srt = tmp_project.captions_dir / "captions.srt"
    srt.write_bytes("1\r\n00:00:00,000 --> 00:00:01,000\r\n你好\r\n".encode("gbk"))
    # manual takeover reads captions.srt; a non-UTF-8 file must not crash
    cues = _current_cues(tmp_project, None, manual_locked=True)
    assert isinstance(cues, list)


# --------------------------------------------------------------------------- #
# (5) preview_voice falls back to a copy when temp and project cross filesystems #
# --------------------------------------------------------------------------- #


def test_preview_voice_cross_device_replace_falls_back(tmp_project, add_shot, monkeypatch):
    from manju.media import ttspreview
    from manju.media import voicefix

    add_shot(tmp_project, "S001")

    class _Tts:
        manifest = None

        def synthesize(self, *a, **k):
            pass

    monkeypatch.setattr(voicefix, "_resolve_tts", lambda provider=None: ("stub", _Tts()))

    def _fake_generic(tts, project, shot, line, tmp_dest, should_cancel=None):
        Path(tmp_dest).write_bytes(b"ID3fakeaudiodata")

    monkeypatch.setattr(ttspreview, "_synthesize_generic", _fake_generic)

    def _xdev(src, dst):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(ttspreview.os, "replace", _xdev)

    res = ttspreview.preview_voice(tmp_project, "S001", text="你好世界")
    # temp/project on different filesystems -> fall back to a copy, don't crash
    assert Path(res["path"]).exists()
    assert Path(res["path"]).read_bytes() == b"ID3fakeaudiodata"
