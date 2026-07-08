"""Round U — the voice repair loop (media/voicefix, goal item 11).

The full loop under a MOCK TTS provider (no ffmpeg/ffprobe): keep the picture,
regenerate the voice (append-only + repaired_from/audio_repaired lineage),
realign the shot's captions proportionally (manual cues left untouched + a 中文
advisory), record failures, dry-run mutates nothing, and the changed voice
invalidates the final content key so a subsequent compile consumes it.

Durations are read with the stdlib ``wave`` module (a valid WAV of a chosen
length), so the whole suite is hermetic — the loop takes ``probe_fn`` exactly so
these tests never need ffprobe.
"""

from __future__ import annotations

import contextlib
import io
import json
import wave
from pathlib import Path

import pytest

from manju.core.models import VoiceTakeSidecar
from manju.core.spec import VOICE_VERSION, compute_spec_hash, compute_voice_hash
from manju.core.yamlio import write_yaml
from manju.providers.generic_cloud import HttpResponse


# ------------------------------------------------------------- wav helpers


def wav_bytes(ms: int) -> bytes:
    """A valid mono 16-bit PCM WAV of ``ms`` milliseconds (stdlib only)."""
    buf = io.BytesIO()
    fr = 8000
    n = max(1, int(round(fr * ms / 1000)))
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        w.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


def wav_ms(path: Path) -> int | None:
    """Duration of a WAV in ms via stdlib ``wave``; a non-wav (the fake video
    take) falls back to a fixed value so the compiler's audio-driven duration
    still has a picture length to fall back on."""
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as w:
            return int(round(w.getnframes() / float(w.getframerate()) * 1000))
    except Exception:
        return 1000


# ----------------------------------------------------------- tts mock scaffold


def _tts_manifest(**overrides):
    base = {
        "id": "tts_x",
        "type": "tts",
        "adapter": "generic_tts",
        "auth": {"key_env": "TTS_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/tts",
            "body_template": {"text": "{text}", "voice": "{voice}", "lang": "{language}"},
            "job_id_path": "$.data.task_id",
        },
        "tts": {"audio_url_path": "$.data.audio_url", "audio_format": "wav"},
        "cost": {"per_call": 0.02, "currency": "CNY"},
    }
    base.update(overrides)
    return base


class ScriptedTransport:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def __call__(self, method, url, headers, body):
        self.requests.append((method, url, headers, body))
        return self.script.pop(0)


def _resp(payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return HttpResponse(200, {}, body)


@pytest.fixture
def tts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "prov" / "tts_x" / "provider.yaml", _tts_manifest())


def _mock_new_voice(monkeypatch, *, new_ms: int = 3000, fail_status: int | None = None):
    """Monkeypatch the TTS resolution the way the build path is patched: a
    ``get_tts_provider`` returning a real GenericTtsProvider wired to a scripted
    transport that yields a WAV of ``new_ms`` (or an HTTP error for the failure
    path)."""
    import manju.providers.tts as tts_mod

    def fake_get(name=None, **kwargs):
        if fail_status is not None:
            script = [HttpResponse(fail_status, {}, b"upstream boom")]
        else:
            script = [
                _resp({"data": {"audio_url": "https://cdn/v.wav"}}),
                HttpResponse(200, {}, wav_bytes(new_ms)),
            ]
        return tts_mod.GenericTtsProvider(
            tts_mod.tts_providers()["tts_x"],
            transport=ScriptedTransport(script),
            sleep_fn=lambda s: None,
        )

    monkeypatch.setattr("manju.providers.tts.get_tts_provider", fake_get)


# ------------------------------------------------------------------- prep


def _prep(project, add_shot, make_take, tmp_path, *, text="这不可能。",
          old_ms=2000, captions_manual=False):
    """A shot with a selected FRESH video take + an old voice take, and a
    compiled 'before' timeline. Returns (shot, video_take, before_timeline)."""
    from manju.timeline.compiler import build_timeline

    shot = add_shot(project, "S001", dialogue={"speaker": "linxia", "text": text})
    spec = compute_spec_hash(shot, project.load_bible())
    vid = make_take(project, "S001", spec)
    project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", vid.name),
    )
    old = tmp_path / "old_voice.wav"
    old.write_bytes(wav_bytes(old_ms))
    project.register_voice_take(
        "S001", old,
        VoiceTakeSidecar(provider="tts_x",
                         voice_hash=compute_voice_hash(shot, project.load_bible())),
    )
    if captions_manual:
        rules = project.load_rules()
        rules.captions.mode = "manual"
        project.save_rules(rules)
    timeline, _, _ = build_timeline(project, wav_ms)
    if captions_manual:
        (project.captions_dir / "captions.srt").write_text(
            "1\n00:00:00,200 --> 00:00:02,200\n人工字幕\n\n", encoding="utf-8")
    return shot, vid, timeline


# ================================================================ the full loop


def test_full_loop_regenerates_realigns_and_marks(
        tmp_project, add_shot, make_take, tts_env, tmp_path, monkeypatch):
    from manju.core.events import tail_events
    from manju.media.voicefix import repair_voice

    shot, vid, before = _prep(tmp_project, add_shot, make_take, tmp_path, old_ms=2000)
    # the 'before' cue is the whole voiced window [200, 2200] (padding_before=200)
    assert [(c.start_ms, c.end_ms) for c in before.tracks.captions] == [(200, 2200)]

    _mock_new_voice(monkeypatch, new_ms=3000)
    result = repair_voice(tmp_project, "S001", probe_fn=wav_ms, actor="ai")

    assert result.ok, result.failure
    # (1) picture untouched — still exactly one video take, the selected one
    assert [t.name for t in tmp_project.takes("S001")] == [vid.name]

    # (2) append-only voice regen with repair lineage
    voices = tmp_project.voice_takes("S001")
    assert [m.stem for m, _ in voices] == ["voice_take_01", "voice_take_02"]
    new_media, new_sidecar = voices[-1]
    assert result.new_take == "voice_take_02" and result.old_take == "voice_take_01"
    assert new_sidecar is not None
    assert new_sidecar.repaired_from == "voice_take_01"
    assert new_sidecar.audio_repaired is True  # the MARK
    # round W (review #60): the repaired take was RE-synthesized (not copied),
    # so it goes through the same VOICE_VERSION + provider-descriptor hashing
    # as any fresh synthesis — mirrors test_voice.py's assertion.
    import manju.providers.tts as tts_mod
    from manju.providers.tts import voice_provider_descriptor

    assert new_sidecar.voice_hash_version == VOICE_VERSION
    assert new_sidecar.voice_hash == compute_voice_hash(
        shot, tmp_project.load_bible(), version=VOICE_VERSION,
        provider=voice_provider_descriptor(tts_mod.get_tts_provider("tts_x")),
    )

    # (3) captions realigned PROPORTIONALLY: span 2000→3000 about cap_start=200
    assert len(result.realigned) == 1 and not result.locked_cues
    mv = result.realigned[0]
    assert (mv.old_start_ms, mv.old_end_ms) == (200, 2200)
    assert (mv.new_start_ms, mv.new_end_ms) == (200, 3200)

    # (5) actor-attributed event
    ev = tail_events(tmp_project.root, 5)[-1]
    assert ev["actor"] == "ai" and ev["action"] == "repair"
    assert ev["detail"]["op"] == "voice" and ev["detail"]["audio_repaired"] is True
    assert ev["detail"]["new_take"] == "voice_take_02"


# ============================================================== content keys (REMIX)


def test_content_key_invalidation_and_new_voice_consumed(
        tmp_project, add_shot, make_take, tts_env, tmp_path, monkeypatch):
    """(4) REMIX: the changed voice must move the FINAL content key, and a
    subsequent compile must consume the newest (repaired) voice — the render
    naturally re-renders while nothing here writes a render output."""
    from manju.media.render import final_content_key
    from manju.media.voicefix import repair_voice
    from manju.timeline.compiler import build_timeline

    shot, vid, before = _prep(tmp_project, add_shot, make_take, tmp_path, old_ms=2000)
    key_before = final_content_key(tmp_project, before, ass_file=None, target="final")

    _mock_new_voice(monkeypatch, new_ms=3000)
    result = repair_voice(tmp_project, "S001", probe_fn=wav_ms)
    assert result.ok

    after, _, _ = build_timeline(tmp_project, wav_ms)
    key_after = final_content_key(tmp_project, after, ass_file=None, target="final")

    assert key_after != key_before, "voice change must re-key the final (re-render)"
    # the recompile consumes the NEW voice take (newest wins, §3)
    assert after.tracks.voice[0].source.endswith("voice_take_02.wav")
    # audio-driven picture followed the new voice: 3000 + 500 padding = 3500
    assert after.tracks.video[0].duration_ms == 3500


# ============================================================ manual takeover guard


def test_manual_cues_left_untouched_with_advisory(
        tmp_project, add_shot, make_take, tts_env, tmp_path, monkeypatch):
    from manju.media.voicefix import repair_voice

    shot, vid, before = _prep(tmp_project, add_shot, make_take, tmp_path,
                              old_ms=2000, captions_manual=True)
    srt_path = tmp_project.captions_dir / "captions.srt"
    srt_before = srt_path.read_bytes()

    _mock_new_voice(monkeypatch, new_ms=3000)
    result = repair_voice(tmp_project, "S001", probe_fn=wav_ms)

    assert result.ok
    assert result.manual_locked is True and result.captions_mode == "manual"
    # HARD CONSTRAINT: human cues NEVER moved — none realigned, one locked, verbatim
    assert not result.realigned
    assert len(result.locked_cues) == 1
    lk = result.locked_cues[0]
    assert (lk.old_start_ms, lk.old_end_ms) == (lk.new_start_ms, lk.new_end_ms) == (200, 2200)
    # a 中文 advisory names the cue that needs human attention
    assert any("人工接管" in a and "#1" in a for a in result.advisories)
    # and the human truth on disk is byte-identical (nothing silently rewritten)
    assert srt_path.read_bytes() == srt_before
    # the voice itself WAS still regenerated (only the captions are frozen)
    assert result.new_take == "voice_take_02"


# =============================================================== failure recording


def test_failure_record_on_tts_failure(
        tmp_project, add_shot, make_take, tts_env, tmp_path, monkeypatch):
    from manju.core.failures import read_failures
    from manju.media.voicefix import repair_voice

    _prep(tmp_project, add_shot, make_take, tmp_path, old_ms=2000)
    _mock_new_voice(monkeypatch, fail_status=500)

    result = repair_voice(tmp_project, "S001", probe_fn=wav_ms, actor="ai")

    assert result.ok is False
    assert result.failure and result.failure["step"] == "voice"
    assert result.failure["subject"] == "S001"
    assert result.failure.get("hint")
    # a structured record landed on disk with cause + hint (core/failures)
    recs = read_failures(tmp_project, n=5)
    assert recs and recs[0]["step"] == "voice" and recs[0]["subject"] == "S001"
    assert recs[0]["hint"] and recs[0]["level"] == "error"
    # append-only integrity: the failed synth left NO new voice take
    assert [m.stem for m, _ in tmp_project.voice_takes("S001")] == ["voice_take_01"]


def test_no_dialogue_is_a_recorded_failure(
        tmp_project, add_shot, make_take, tts_env, tmp_path):
    from manju.core.failures import read_failures
    from manju.media.voicefix import repair_voice

    add_shot(tmp_project, "S001", dialogue={"speaker": "", "text": ""})
    result = repair_voice(tmp_project, "S001", probe_fn=wav_ms)
    assert result.ok is False and result.failure["step"] == "voice"
    assert read_failures(tmp_project, n=1)


# =================================================================== dry-run


def test_dry_run_mutates_nothing(
        tmp_project, add_shot, make_take, tts_env, tmp_path):
    from manju.media.voicefix import repair_voice

    _prep(tmp_project, add_shot, make_take, tmp_path, old_ms=2000)
    events_path = tmp_project.root / "events.jsonl"
    events_before = events_path.read_text(encoding="utf-8")
    voices_before = [m.stem for m, _ in tmp_project.voice_takes("S001")]
    failures_path = tmp_project.root / "reports" / "failures.jsonl"

    # dry-run resolves the REAL tts manifest (read-only) and prints the plan
    result = repair_voice(tmp_project, "S001", dry_run=True, probe_fn=wav_ms)

    assert result.dry_run is True and result.ok is True
    assert result.plan and any("重新合成配音" in line for line in result.plan)
    assert any("重混" in line for line in result.plan)
    assert result.new_take is None  # nothing synthesized
    # nothing on disk moved: no new take, no event, no failure record
    assert [m.stem for m, _ in tmp_project.voice_takes("S001")] == voices_before
    assert events_path.read_text(encoding="utf-8") == events_before
    assert not failures_path.exists()


def test_dry_run_flags_manual_cues_in_plan(
        tmp_project, add_shot, make_take, tts_env, tmp_path):
    from manju.media.voicefix import repair_voice

    _prep(tmp_project, add_shot, make_take, tmp_path, old_ms=2000, captions_manual=True)
    result = repair_voice(tmp_project, "S001", dry_run=True, probe_fn=wav_ms)
    assert result.dry_run and result.ok
    assert any("人工接管" in line for line in result.plan)


# ============================================================= CLI wiring smoke


def test_cli_repair_op_voice_dry_run(
        tmp_project, add_shot, make_take, tts_env, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    _prep(tmp_project, add_shot, make_take, tmp_path, old_ms=2000)
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()
    res = runner.invoke(app, ["repair", "--op", "voice", "--shot", "S001",
                              "--dry-run", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["dry_run"] is True and payload["new_take"] is None
    assert payload["plan"]
    # dry-run left the project alone
    assert [m.stem for m, _ in tmp_project.voice_takes("S001")] == ["voice_take_01"]
