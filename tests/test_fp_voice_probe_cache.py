"""Audit FP Tier-1 #2 — the voice-duration probe cache.

The timeline compiler used to LIVE-ffprobe every shot's voice take on every
compile (``timeline/compiler.gather_compile_input`` — the main video/voice slot
AND the ``allow_missing_takes`` slate branch), while the VIDEO path already read
the cached ``TakeSidecar.probe``. The three voice-synthesis construction sites
(``providers/tts``, ``providers/edge_tts``, ``media/voicefix``) never populated
``VoiceTakeSidecar.probe``, so the cache the compiler could have read never
existed for voice.

This suite pins the fix, red-first:

  * synthesis FILLS ``VoiceTakeSidecar.probe`` at all three sites (via the same
    best-effort ``providers.base.probe_media`` the video path uses);
  * the compiler READS that cache first at BOTH voice-consumption sites (main +
    slate), making ZERO live voice probes when the sidecar carries a probe;
  * a legacy voice sidecar with no probe still triggers exactly the old single
    fallback probe — byte-identical behaviour;
  * the compiled timeline JSON is IDENTICAL whether the duration came from the
    cache or from a live probe of the same file;
  * a voicefix-repaired take keeps a probe.

Hermetic: the compiler-consumption tests use a spy ``probe_fn`` and sidecars
with a hand-set ``ProbeInfo`` — no ffprobe. The synthesis tests exercise the
REAL ``probe_media`` (ffprobe) on tiny stdlib-``wave`` files and skip cleanly
where ffprobe is absent. No network.
"""

from __future__ import annotations

import io
import shutil
import sys
import types
import wave
from pathlib import Path

import pytest

from manju.core.models import ProbeInfo, TakeSidecar, VoiceTakeSidecar
from manju.core.spec import compute_spec_hash, compute_voice_hash
from manju.core.yamlio import read_yaml, write_yaml
from manju.providers.generic_cloud import HttpResponse
from manju.timeline.compiler import compile_timeline, gather_compile_input

requires_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe required (real probe_media)"
)


# --------------------------------------------------------------- wav + probe fakes


def wav_bytes(ms: int) -> bytes:
    """A valid mono 16-bit PCM WAV of ``ms`` milliseconds (stdlib only) — real
    ffprobe reads an exact duration from it."""
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
    """Duration of a WAV in ms via stdlib ``wave`` (a non-wav falls back to a
    fixed value) — the hermetic ``probe_fn`` the voicefix suite uses."""
    import contextlib

    try:
        with contextlib.closing(wave.open(str(path), "rb")) as w:
            return int(round(w.getnframes() / float(w.getframerate()) * 1000))
    except Exception:
        return 1000


class ProbeSpy:
    """A counting ``probe_fn`` (``Callable[[Path], int | None]``). Records every
    path it is asked to probe so a test can assert the compiler made ZERO live
    VOICE probes. Returns a per-name-substring value, else ``default``."""

    def __init__(self, table: dict[str, int] | None = None, default: int | None = None):
        self.calls: list[Path] = []
        self.table = table or {}
        self.default = default

    def __call__(self, path: Path) -> int | None:
        p = Path(path)
        self.calls.append(p)
        for key, val in self.table.items():
            if key in p.name:
                return val
        return self.default

    @property
    def voice_calls(self) -> list[Path]:
        return [p for p in self.calls if "voice_take" in p.name]


# ------------------------------------------------------------- tts mock scaffold
# (duplicated from test_voice/test_voicefix — each provider suite is self-contained)


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
    body = payload if isinstance(payload, bytes) else __import__("json").dumps(payload).encode()
    return HttpResponse(200, {}, body)


@pytest.fixture
def tts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    monkeypatch.setenv("TTS_X_KEY", "k")
    write_yaml(tmp_path / "prov" / "tts_x" / "provider.yaml", _tts_manifest())


def _mock_new_voice(monkeypatch, *, new_ms: int = 3000):
    """A real GenericTtsProvider wired to a scripted transport that yields a WAV
    of ``new_ms`` — exactly the voicefix suite's fake, so repair goes through the
    genuine synthesize() path (which now fills the sidecar probe)."""
    import manju.providers.tts as tts_mod

    def fake_get(name=None, **kwargs):
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


# ---------------------------------------------------------------- edge mock scaffold


def _edge_manifest(**overrides):
    base = {
        "id": "edge",
        "type": "tts",
        "adapter": "manju.providers.edge_tts:EdgeTtsProvider",
        "tts": {"default_voice": "zh-CN-YunxiNeural", "audio_format": "wav"},
        "cost": {"per_call": 0.0, "currency": "CNY"},
    }
    base.update(overrides)
    return base


@pytest.fixture
def edge_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(tmp_path / "prov"))
    write_yaml(tmp_path / "prov" / "edge" / "provider.yaml", _edge_manifest())


def _install_fake_edge(monkeypatch, *, ms: int = 1500):
    """Inject a hermetic ``edge_tts`` module whose Communicate streams a real WAV
    (no network). The provider's ``import edge_tts`` resolves to this fake."""

    class _FakeCommunicate:
        def __init__(self, text, voice, *, boundary=None, proxy=None):
            self._text = text

        async def stream(self):
            yield {"type": "audio", "data": wav_bytes(ms)}
            yield {"type": "WordBoundary", "offset": 0,
                   "duration": ms * 10_000, "text": "hi"}

    fake = types.ModuleType("edge_tts")
    fake.Communicate = _FakeCommunicate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edge_tts", fake)


# -------------------------------------------------------------- project helpers


def _selected_video(project, shot, *, probe_ms: int | None = 4000):
    """Register a FRESH, selected video take for ``shot`` — with a probe so the
    VIDEO path never touches ``probe_fn`` and any live probe the test observes is
    unambiguously the VOICE path."""
    spec = compute_spec_hash(shot, project.load_bible())
    tmp = project.root / f"_vid_{shot.id}.mp4"
    tmp.write_bytes(b"fakevideo-" + shot.id.encode())
    sidecar = TakeSidecar(
        provider="test", spec_hash=spec,
        probe=ProbeInfo(duration_ms=probe_ms) if probe_ms is not None else None,
    )
    take = project.register_take(shot.id, tmp, sidecar)
    project.update_shot_raw(
        shot.id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )
    return take


def _register_voice(project, shot_id, *, probe_ms: int | None, provider="tts_x"):
    """Register a voice take whose sidecar carries a probe (``probe_ms`` set) or
    not (``probe_ms is None`` — a legacy/hand-dropped take)."""
    src = project.root / f"_voice_{shot_id}.wav"
    src.write_bytes(wav_bytes(probe_ms or 2000))
    sidecar = VoiceTakeSidecar(
        provider=provider,
        voice_hash=compute_voice_hash(project.load_shot(shot_id), project.load_bible()),
        probe=ProbeInfo(duration_ms=probe_ms) if probe_ms is not None else None,
    )
    return project.register_voice_take(shot_id, src, sidecar)


# =====================================================================
# (1) SYNTHESIS FILLS THE SIDECAR PROBE — the three construction sites
# =====================================================================


@requires_ffprobe
def test_tts_synthesis_fills_sidecar_probe(tmp_project, add_shot, tts_env):
    """providers/tts.py: a synthesized voice take records its duration in
    ``VoiceTakeSidecar.probe`` (was always None before FP-L2)."""
    from manju.providers.tts import get_tts_provider

    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "这不可能。"})
    provider = get_tts_provider(
        transport=ScriptedTransport([
            _resp({"data": {"audio_url": "https://cdn/v.wav"}}),
            HttpResponse(200, {}, wav_bytes(1500)),
        ]),
        sleep_fn=lambda s: None,
    )
    provider.synthesize(tmp_project, shot, tmp_project.load_bible())

    _, sidecar = tmp_project.voice_takes("S001")[-1]
    assert sidecar is not None and sidecar.probe is not None
    assert sidecar.probe.duration_ms and 1400 <= sidecar.probe.duration_ms <= 1600


@requires_ffprobe
def test_edge_tts_synthesis_fills_sidecar_probe(tmp_project, add_shot, edge_env, monkeypatch):
    """providers/edge_tts.py: the Edge adapter fills the sidecar probe too."""
    from manju.providers.tts import get_tts_provider

    _install_fake_edge(monkeypatch, ms=1500)
    shot = add_shot(tmp_project, "S001",
                    dialogue={"speaker": "linxia", "text": "凌晨三点。"})
    provider = get_tts_provider("edge")
    provider.synthesize(tmp_project, shot, tmp_project.load_bible())

    _, sidecar = tmp_project.voice_takes("S001")[-1]
    assert sidecar is not None and sidecar.probe is not None
    assert sidecar.probe.duration_ms and 1400 <= sidecar.probe.duration_ms <= 1600


@requires_ffprobe
def test_voicefix_fallback_construction_fills_probe(tmp_project, add_shot):
    """media/voicefix.py:_mark_repaired — the FALLBACK sidecar (mock/provider
    that left no sidecar on disk) is now constructed WITH a probe."""
    from manju.media.voicefix import VoiceRepairResult, _mark_repaired

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    # Drop a voice media file with NO sidecar next to it — forces _mark_repaired
    # down its except branch (read_yaml of a missing sidecar raises).
    tdir = tmp_project.takes_dir("S001")
    tdir.mkdir(parents=True, exist_ok=True)
    new_media = tdir / "voice_take_02.wav"
    new_media.write_bytes(wav_bytes(1800))
    assert not (tdir / "voice_take_02.sidecar.yaml").exists()

    result = VoiceRepairResult(shot_id="S001", provider="repair",
                               voice_hash="vh", old_take="voice_take_01")
    _mark_repaired(tmp_project, "S001", new_media, result)

    written = VoiceTakeSidecar.model_validate(
        read_yaml(tdir / "voice_take_02.sidecar.yaml"))
    assert written.repaired_from == "voice_take_01" and written.audio_repaired is True
    assert written.probe is not None and written.probe.duration_ms
    assert 1700 <= written.probe.duration_ms <= 1900


@requires_ffprobe
def test_voicefix_repaired_take_keeps_probe(
        tmp_project, add_shot, make_take, tts_env, monkeypatch):
    """End-to-end repair (primary path): the re-synthesized take carries a probe
    (from providers/tts) and _mark_repaired preserves it through the lineage
    rewrite."""
    from manju.media.voicefix import repair_voice

    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "这不可能。"})
    old = tmp_project.root / "_old.wav"
    old.write_bytes(wav_bytes(2000))
    tmp_project.register_voice_take(
        "S001", old,
        VoiceTakeSidecar(provider="tts_x",
                         voice_hash=compute_voice_hash(shot, tmp_project.load_bible())),
    )  # legacy old take: NO probe

    _mock_new_voice(monkeypatch, new_ms=3000)
    result = repair_voice(tmp_project, "S001", probe_fn=wav_ms, actor="ai")
    assert result.ok, result.failure

    new_media, new_sidecar = tmp_project.voice_takes("S001")[-1]
    assert new_media.stem == "voice_take_02"
    assert new_sidecar is not None and new_sidecar.audio_repaired is True
    assert new_sidecar.probe is not None and new_sidecar.probe.duration_ms
    assert 2900 <= new_sidecar.probe.duration_ms <= 3100


# =====================================================================
# (2) THE COMPILER READS THE CACHE FIRST — zero live voice probes
# =====================================================================


def test_compiler_main_path_reads_cache_zero_voice_probes(tmp_project, add_shot):
    """Main video/voice slot: when the voice sidecar carries a probe, the compile
    makes ZERO live voice probes (and, with a cached video take, ZERO probes at
    all)."""
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    _selected_video(tmp_project, shot, probe_ms=4000)
    _register_voice(tmp_project, "S001", probe_ms=2000)

    spy = ProbeSpy()  # any call would return None → would corrupt duration if hit
    inp = gather_compile_input(tmp_project, spy)
    timeline = compile_timeline(inp)

    assert spy.calls == [], f"expected zero live probes, got {spy.calls}"
    # cache actually drove the picture: 2000ms voice + 500ms padding = 2500ms
    assert timeline.tracks.video[0].duration_ms == 2500


def test_compiler_main_path_legacy_take_falls_back_to_one_probe(tmp_project, add_shot):
    """A legacy voice sidecar with NO probe triggers exactly the old single
    fallback probe — byte-identical to pre-FP-L2 behaviour."""
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    _selected_video(tmp_project, shot, probe_ms=4000)  # cached → not probed
    _register_voice(tmp_project, "S001", probe_ms=None)  # legacy → must be probed

    spy = ProbeSpy(table={"voice_take": 2000})
    inp = gather_compile_input(tmp_project, spy)
    timeline = compile_timeline(inp)

    assert len(spy.voice_calls) == 1, f"expected one voice fallback, got {spy.calls}"
    assert len(spy.calls) == 1  # nothing else probed
    assert timeline.tracks.video[0].duration_ms == 2500  # same 2000 + 500


def test_compiler_slate_path_reads_cache_zero_voice_probes(tmp_project, add_shot):
    """allow_missing_takes slate branch (compiler :871): also cache-first. A shot
    with no usable take but a probed voice sidecar makes ZERO live voice probes."""
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    _register_voice(tmp_project, "S001", probe_ms=2000)  # no video take selected

    spy = ProbeSpy()
    inp = gather_compile_input(tmp_project, spy, allow_missing_takes=True)
    timeline = compile_timeline(inp)

    assert spy.voice_calls == [], f"slate branch live-probed voice: {spy.calls}"
    # slate duration is still voice-driven: 2000 + 500 padding
    assert timeline.tracks.video[0].duration_ms == 2500


def test_compiler_slate_path_legacy_take_falls_back_to_one_probe(tmp_project, add_shot):
    """slate branch, legacy sidecar (no probe): exactly one fallback voice probe."""
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    _register_voice(tmp_project, "S001", probe_ms=None)

    spy = ProbeSpy(table={"voice_take": 2000})
    inp = gather_compile_input(tmp_project, spy, allow_missing_takes=True)
    compile_timeline(inp)

    assert len(spy.voice_calls) == 1, f"expected one fallback, got {spy.calls}"


# =====================================================================
# (3) TIMELINE OUTPUT UNCHANGED — cache vs live probe of the same file
# =====================================================================


def test_compiler_timeline_identical_cache_vs_live(tmp_project, add_shot):
    """The compiled timeline JSON is IDENTICAL whether the voice duration came
    from the cached sidecar probe or from a live ``probe_fn`` of the same file."""
    shot = add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    _selected_video(tmp_project, shot, probe_ms=4000)
    _register_voice(tmp_project, "S001", probe_ms=2000)

    # (a) cache path: a probe_fn that would return a WRONG value if ever called,
    # proving the cached 2000 is what's used.
    spy_cache = ProbeSpy(default=99999)
    tl_cache = compile_timeline(gather_compile_input(tmp_project, spy_cache))
    assert spy_cache.calls == []

    # (b) live path: strip the probe from the on-disk voice sidecar, then compile
    # with a probe_fn that returns the SAME 2000 for the voice file.
    scp = tmp_project.takes_dir("S001") / "voice_take_01.sidecar.yaml"
    data = read_yaml(scp)
    data.pop("probe", None)
    write_yaml(scp, data)

    spy_live = ProbeSpy(table={"voice_take": 2000})
    tl_live = compile_timeline(gather_compile_input(tmp_project, spy_live))
    assert len(spy_live.voice_calls) == 1  # the live path really ran

    assert tl_cache.model_dump_json() == tl_live.model_dump_json()
