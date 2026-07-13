"""Round B: the M3 voice/TTS pipeline — compute_voice_hash wired as the
staleness anchor, §4.3 conservatism applied to sound, newest-voice-wins."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess

import pytest

from manju.core.yamlio import write_yaml
from manju.providers.generic_cloud import HttpResponse

# a real (tiny) wav so ffprobe can read registered voice takes
WAV_HEADER = (b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
              b"\x80>\x00\x00\x00}\x00\x00\x02\x00\x10\x00data\x00\x00\x00\x00")


def _tts_manifest(**overrides):
    base = {
        "id": "tts_x",
        "type": "tts",
        "adapter": "generic_tts",
        "auth": {"key_env": "TTS_X_KEY"},
        "submit": {
            "url": "https://api.example.com/v1/tts",
            "body_template": {"text": "{text}", "voice": "{voice}",
                              "ref": "{voice_ref}", "lang": "{language}"},
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


# ------------------------------------------------------------- synthesis


def test_synthesize_registers_voice_take_with_hash(tmp_project, add_shot, tts_env):
    from manju.core.spec import VOICE_VERSION, compute_voice_hash
    from manju.providers.tts import get_tts_provider, voice_provider_descriptor

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "这不可能。"})
    provider = get_tts_provider(
        transport=ScriptedTransport([
            _resp({"data": {"audio_url": "https://cdn.example.com/v.wav"}}),
            HttpResponse(200, {}, WAV_HEADER),
        ]),
        sleep_fn=lambda s: None,
    )
    media = provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert media.name == "voice_take_01.wav" and media.exists()
    voices = tmp_project.voice_takes("S001")
    assert len(voices) == 1
    _, sidecar = voices[0]
    assert sidecar is not None
    # round W (review #60): synthesis records VOICE_VERSION + the resolved
    # provider/manifest fingerprint, not just text/speaker/voice_ref.
    assert sidecar.voice_hash_version == VOICE_VERSION
    assert sidecar.voice_hash == compute_voice_hash(
        shot, tmp_project.load_bible(), version=VOICE_VERSION,
        provider=voice_provider_descriptor(provider),
    )
    assert sidecar.provider == "tts_x"
    assert sidecar.remote and sidecar.remote.cost == 0.02


def test_synthesize_rejects_html_error_page(tmp_project, add_shot, tts_env):
    """Goal 43/44/54: an audio_url that 200s with an HTML error page (expired
    signed URL, CDN error…) must never be written to disk as if it were the
    synthesized audio."""
    from manju.providers.base import ProviderFailure
    from manju.providers.tts import get_tts_provider

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "这不可能。"})
    provider = get_tts_provider(
        transport=ScriptedTransport([
            _resp({"data": {"audio_url": "https://cdn.example.com/v.wav"}}),
            HttpResponse(200, {"Content-Type": "text/html"},
                        b"<html>Link expired</html>"),
        ]),
        sleep_fn=lambda s: None,
    )
    with pytest.raises(ProviderFailure) as exc:
        provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert "html" in str(exc.value).lower()
    assert tmp_project.voice_takes("S001") == []


def test_synthesize_inline_b64_form(tmp_project, add_shot, tmp_path, monkeypatch):
    from manju.providers.tts import get_tts_provider

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "p2"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "p2" / "tts_b" / "provider.yaml", _tts_manifest(
        id="tts_b",
        tts={"audio_b64_path": "$.data.audio", "audio_format": "wav"},
    ))
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    provider = get_tts_provider("tts_b", transport=ScriptedTransport([
        _resp({"data": {"audio": base64.b64encode(WAV_HEADER).decode()}}),
    ]), sleep_fn=lambda s: None)
    media = provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert media.read_bytes() == WAV_HEADER


# ------------------------------------------------------------- F4: 429 mapping


def test_tts_poll_429_is_rate_limited(tmp_project, add_shot, tmp_path, monkeypatch):
    """F4: a 429 during TTS poll is retryable rate_limited. RED at HEAD: the tts
    poll mapped ALL >=400 to provider_error — internally inconsistent with its
    OWN submit path, which already special-cased 429 (F4 drift). Now both use the
    shared status_to_kind."""
    from manju.providers.base import FailureKind, ProviderFailure
    from manju.providers.tts import get_tts_provider

    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "p429"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "p429" / "tts_p" / "provider.yaml", _tts_manifest(
        id="tts_p",
        poll={"url": "https://api.example.com/v1/tts/{job_id}",
              "status_path": "$.data.status",
              "status_map": {"DONE": "succeeded", "RUNNING": "running"}},
    ))
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    provider = get_tts_provider("tts_p", transport=ScriptedTransport([
        _resp({"data": {"task_id": "t1"}}),               # submit ok (async form)
        HttpResponse(429, {}, b'{"error":"slow down"}'),  # poll 429
    ]), sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as exc:
        provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert exc.value.kind is FailureKind.rate_limited


def test_tts_submit_429_stays_rate_limited(tmp_project, add_shot, tts_env):
    """F4 characterization: the tts submit path already classified 429 as
    rate_limited; aligning it to the shared status_to_kind keeps that behavior
    (guards the alignment against regression)."""
    from manju.providers.base import FailureKind, ProviderFailure
    from manju.providers.tts import get_tts_provider

    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    provider = get_tts_provider(transport=ScriptedTransport([
        HttpResponse(429, {}, b'{"error":"slow down"}'),
    ]), sleep_fn=lambda s: None)
    with pytest.raises(ProviderFailure) as exc:
        provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert exc.value.kind is FailureKind.rate_limited


# ------------------------------------------------------- staleness matrix


def test_voice_staleness_matrix(tmp_project, add_shot, tts_env):
    from manju.build.voice import VoiceState, evaluate_voice
    from manju.providers.tts import get_tts_provider

    # NOT_NEEDED: no dialogue
    silent = add_shot(tmp_project, "S001", dialogue={"speaker": "", "text": ""})
    assert evaluate_voice(tmp_project, silent).state is VoiceState.NOT_NEEDED

    # MISSING: dialogue, no take
    shot = add_shot(tmp_project, "S002", dialogue={"speaker": "linxia", "text": "第一版台词"})
    assert evaluate_voice(tmp_project, shot).state is VoiceState.MISSING

    # FRESH after synthesis
    provider = get_tts_provider(transport=ScriptedTransport([
        _resp({"data": {"audio_url": "https://cdn/x.wav"}}),
        HttpResponse(200, {}, WAV_HEADER),
    ]), sleep_fn=lambda s: None)
    provider.synthesize(tmp_project, shot, tmp_project.load_bible())
    assert evaluate_voice(tmp_project, shot).state is VoiceState.FRESH

    # STALE after the line changes — flagged, never auto-redone (§4.3)
    tmp_project.update_shot_raw(
        "S002", lambda d: d.setdefault("dialogue", {}).__setitem__("text", "改过的台词")
    )
    status = evaluate_voice(tmp_project, tmp_project.load_shot("S002"))
    assert status.state is VoiceState.STALE

    # MANUAL: hand-dropped newest take (no sidecar) is never invalidated
    hand = tmp_project.takes_dir("S002") / "voice_take_09.wav"
    hand.write_bytes(WAV_HEADER)
    status = evaluate_voice(tmp_project, tmp_project.load_shot("S002"))
    assert status.state is VoiceState.MANUAL


def test_voice_hash_camera_change_stays_fresh(tmp_project, add_shot, tts_env):
    """Picture-side edits never invalidate a voice take (FIX-F split)."""
    from manju.build.voice import VoiceState, evaluate_voice
    from manju.providers.tts import get_tts_provider

    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    provider = get_tts_provider(transport=ScriptedTransport([
        _resp({"data": {"audio_url": "https://cdn/x.wav"}}),
        HttpResponse(200, {}, WAV_HEADER),
    ]), sleep_fn=lambda s: None)
    provider.synthesize(tmp_project, shot, tmp_project.load_bible())

    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("camera", {}).__setitem__("shot_size", "wide")
    )
    assert evaluate_voice(tmp_project, tmp_project.load_shot("S001")).state is VoiceState.FRESH


# --------------------------------------------------------- newest wins


def test_compiler_picks_newest_voice_take(tmp_project, add_shot):
    from manju.timeline.compiler import _find_voice

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "x"})
    tdir = tmp_project.takes_dir("S001")
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "voice_take_01.wav").write_bytes(WAV_HEADER)
    (tdir / "voice_take_02.wav").write_bytes(WAV_HEADER)
    found = _find_voice(tmp_project, "S001")
    assert found is not None and found.name == "voice_take_02.wav"


# ------------------------------------------------------ build integration


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_build_synthesizes_missing_voices_and_prices_them(
        tmp_project, add_shot, tts_env, monkeypatch):
    """Dry-run prices voice per_call; a real build synthesizes MISSING voices
    before compiling so the fresh voice drives the shot duration (§6); a
    second build synthesizes nothing (FRESH)."""
    from manju.build.graph import run_build
    from manju.providers.manual import register_manual_take
    import manju.providers.tts as tts_mod

    clip = tmp_project.runtime_dir / "c.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=540x960:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "这不可能。"})
    take = register_manual_take(tmp_project, "S001", clip)
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )

    plan = run_build(tmp_project, dry_run=True)
    voice_items = [p for p in plan.plan if p.get("kind") == "voice"]
    assert len(voice_items) == 1 and voice_items[0]["estimated_cost"] == 0.02

    # a 2s real voice wav so the compiler's audio-driven duration kicks in
    voice_wav = tmp_project.runtime_dir / "v.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=300:duration=2", str(voice_wav)],
        check=True,
    )
    real_bytes = voice_wav.read_bytes()

    def fake_get(provider=None, **kwargs):
        return tts_mod.GenericTtsProvider(
            tts_mod.tts_providers()["tts_x"],
            transport=ScriptedTransport([
                _resp({"data": {"audio_url": "https://cdn/v.wav"}}),
                HttpResponse(200, {}, real_bytes),
            ]),
            sleep_fn=lambda s: None,
        )

    monkeypatch.setattr("manju.providers.tts.get_tts_provider", fake_get)
    # the priced voice plan now hits the §8.3 ask_before engine gate first
    gated = run_build(tmp_project, target="qc")
    assert gated.waiting_user and not gated.ok
    result = run_build(tmp_project, target="qc", assume_yes=True)
    assert result.ok, result.errors
    assert any("voice_take_01" in g for g in result.generated)

    timeline = tmp_project.load_timeline()
    assert timeline.tracks.voice, "voice track missing from the compiled timeline"
    # audio drives picture (§6): 2000ms voice + 500ms padding = 2500ms clip
    assert timeline.tracks.video[0].duration_ms == 2500

    # second build: FRESH -> nothing new synthesized
    result2 = run_build(tmp_project, target="qc")
    assert result2.ok
    assert not [g for g in result2.generated if "voice" in g]
    assert len(tmp_project.voice_takes("S001")) == 1


def test_status_surfaces_voice_states(tmp_project, add_shot, tts_env):
    from manju.build.status import project_status

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    info = project_status(tmp_project)
    assert info["voice_by_state"].get("missing") == ["S001"]
