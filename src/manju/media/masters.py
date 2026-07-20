"""AI_IDE_18 WP7 — professional audio masters, produced for real with ffmpeg.

The 13C ``DeliveryManifest`` declares DIALOGUE_STEM / MUSIC_STEM / SFX_STEM /
FULL_MIX / M_AND_E_MASTER roles that were honestly SKIPPED because "no such
artifacts exist". This module makes them exist — as genuine renders off the
EXISTING compiled-timeline audio graph (voice / music / sfx / ambient buses,
:class:`manju.core.models.AudioClip`), never a placeholder byte.

What it produces, all real files under ``exports/masters/`` with a companion
``masters.json`` index the export centre (and thus the manifest) reads back:

    RAW_DIALOGUE_STEM ........... the voice bus alone (raw: pre-duck/pre-loudnorm)
    RAW_MUSIC_STEM .............. the music bus alone
    RAW_SFX_STEM ............... the sfx bus alone
    RAW_AMBIENT_STEM ........... the ambient bus alone (named honestly, not folded
                                 into sfx — the ambient bus is symmetric with the trio)
    RAW_STEM_SUM ............... all four raw buses summed (headroom-protected)
    M_AND_E_BUS_EXCLUSION_MASTER  the non-voice buses (music + sfx + ambient); its
                                 name STATES the claim — BUS EXCLUSION, never a
                                 content-level "no dialogue" proof

PROGRAM_MASTER is deliberately NOT emitted (CLOSEOUT C5 ruling 2 / A05): this
module renders raw stems + a raw sum PRE-duck / PRE-loudnorm and does NOT reuse
the final program mixer's ducking/loudness/automation chain (``media/render.py``
applies sidechain ducking + ``loudnorm`` then encodes lossy AAC into the mp4), so
no extractable, hash-verifiable program master exists here — a raw sum must never
masquerade as one. The index records the role as absent with that reason.

Every artifact binds its source/timeline/audio-input hashes and carries measured
loudness (integrated LUFS / true-peak dBTP / LRA via ffmpeg ``loudnorm`` /
``ebur128``) recorded as PROBE FACTS, plus sample-rate / channels / duration, and
a per-bus expected/resolved/dropped clip accounting (an unreadable expected
source blocks the bus's masters — digital silence never counts as verified).

Honesty boundaries (contract §6, §7, §12):

- Stems are rendered PRE-DUCK and PRE-loudnorm: a raw stem is one bus's own
  contribution, so the four raw stems reconstruct ``RAW_STEM_SUM`` — and ``M&E``
  equals the sum minus the dialogue bus — up to the DECLARED uniform sum
  headroom (``level_safety.headroom_db``: the two sums carry a fixed
  attenuation the raw single-bus stems do not, so the relation holds exactly
  in the attenuated domain, never as a silent sample-identical claim).
- M&E provably lacks the dialogue BUS: it is mixed from exactly the non-voice
  buses; a ``volumedetect`` on it never contains the dialogue signal. This is a
  BUS-exclusion guarantee, not a content-level proof (the claim is worded so).
- Loudness TARGETS are not hardcoded: they come from the delivery profile
  (:func:`manju.build.delivery` optional fields). This module only MEASURES;
  it records the measurement as a fact and, when a profile target is declared,
  renders an additionally loudness-normalised full-mix master beside the raw one.
- No local acoustic model, no LLM: pure ffmpeg DSP.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.hashing import hash_file, hash_value
from ..core.timebase import Rate, Rounding, frames_to_ms, frames_to_samples, ms_to_frames
from .ffmpeg import FFMPEG, MediaError, atomic_output

# Delivery-grade PCM: 48 kHz stereo, the broadcast/NLE interchange default.
SAMPLE_RATE = 48_000
CHANNELS = 2
_AFMT = f"aformat=sample_fmts=fltp:sample_rates={SAMPLE_RATE}:channel_layouts=stereo"

# role -> the timeline buses it renders from (CLOSEOUT C5 ruling 2 honest stem
# taxonomy). Each RAW_*_STEM is exactly ONE bus (ambient is symmetric with the
# trio — named honestly, not hidden inside sfx); RAW_STEM_SUM is the raw four-bus
# sum (the former FULL_MIX); M_AND_E_BUS_EXCLUSION_MASTER is the non-voice buses
# (the former M_AND_E_MASTER — the name now STATES the bus-exclusion semantics).
BUS_ROLES: dict[str, tuple[str, ...]] = {
    "RAW_DIALOGUE_STEM": ("voice",),
    "RAW_MUSIC_STEM": ("music",),
    "RAW_SFX_STEM": ("sfx",),
    "RAW_AMBIENT_STEM": ("ambient",),
    "RAW_STEM_SUM": ("voice", "music", "sfx", "ambient"),
    "M_AND_E_BUS_EXCLUSION_MASTER": ("music", "sfx", "ambient"),
}
# manifest kind token <-> role (the export-centre row kind; §6.3 additive).
ROLE_KIND = {
    "RAW_DIALOGUE_STEM": "dialogue_stem",
    "RAW_MUSIC_STEM": "music_stem",
    "RAW_SFX_STEM": "sfx_stem",
    "RAW_AMBIENT_STEM": "ambient_stem",
    "RAW_STEM_SUM": "stem_sum",
    "M_AND_E_BUS_EXCLUSION_MASTER": "mne",
}
KIND_ROLE = {v: k for k, v in ROLE_KIND.items()}

# Level safety (CLOSEOUT C5 ruling 3): the deliverable SUMS (RAW_STEM_SUM + the
# M&E bus-exclusion master) get a fixed HEADROOM attenuation applied UNIFORMLY —
# fixed gain preserves the relative levels between them (a raw single-bus stem is
# never attenuated), so summing hot buses cannot silently clip. True peak is then
# measured and any residual over-ceiling is a BLOCKING diagnostic (never a note).
SUM_HEADROOM_DB = -6.0
TRUE_PEAK_CEILING_DBTP = -1.0
_SUM_LEVEL_SAFETY = {
    "method": "headroom",
    "headroom_db": SUM_HEADROOM_DB,
    "ceiling_dbtp": TRUE_PEAK_CEILING_DBTP,
}


class MastersError(MediaError):
    """An audio master could not be produced (bad source, ffmpeg failure)."""


# --------------------------------------------------------------- source resolve


def _resolve_source(project: Project, source: str) -> Path | None:
    """A clip ``source`` -> an on-disk path, honouring the project sandbox.
    Missing / unresolvable sources are DROPPED (a stem renders what really
    exists — never a fabricated tone), the drop surfaces in the index."""
    if not source:
        return None
    try:
        p = Path(source)
        cand = p if p.is_absolute() else (project.root / source)
        cand = cand.resolve()
        # never escape the project root
        cand.relative_to(project.root.resolve())
    except (ValueError, OSError):
        return None
    return cand if cand.exists() and cand.stat().st_size > 0 else None


def _clip_chain(idx: int, clip: Any, label: str) -> str:
    """The per-clip filter chain: seek-into-source, gain, fades, then place at
    ``start_ms`` on the bus timeline. Mirrors the render's audio-graph knobs
    (start_offset_ms / gain_db / fade_in_ms / fade_out_ms) — ducking is a
    cross-bus effect and is deliberately NOT applied to a single-bus stem."""
    parts = [f"[{idx}:a]{_AFMT}"]
    off = int(getattr(clip, "start_offset_ms", 0) or 0)
    if off > 0:
        parts.append(f"atrim=start={off / 1000.0}")
        parts.append("asetpts=PTS-STARTPTS")
    dur = getattr(clip, "duration_ms", None)
    if dur:
        parts.append(f"atrim=duration={int(dur) / 1000.0}")
        parts.append("asetpts=PTS-STARTPTS")
    gain = float(getattr(clip, "gain_db", 0.0) or 0.0)
    if gain:
        parts.append(f"volume={gain}dB")
    fin = int(getattr(clip, "fade_in_ms", 0) or 0)
    if fin > 0:
        parts.append(f"afade=t=in:st=0:d={fin / 1000.0}")
    fout = int(getattr(clip, "fade_out_ms", 0) or 0)
    if fout > 0 and dur:
        st = max(0.0, (int(dur) - fout) / 1000.0)
        parts.append(f"afade=t=out:st={st}:d={fout / 1000.0}")
    start = max(0, int(getattr(clip, "start_ms", 0) or 0))
    parts.append(f"adelay={start}|{start}")
    return ",".join(parts) + f"[{label}]"


def _render_bus(project: Project, clips: list[Any], duration_ms: int,
                dest: Path) -> tuple[Path, list[str], dict[str, Any]]:
    """Render one bus's clips to ``dest`` (a real WAV of exactly ``duration_ms``).
    Returns (path, source_hashes, accounting) where accounting records
    expected/resolved/dropped clips (CLOSEOUT C5 ruling 1). An empty bus (nothing
    EXPECTED) renders true digital silence legitimately; a bus whose EXPECTED
    source is unreadable renders silence too but is recorded as a DROP with a
    reason so the master summing it can never be reported as verified."""
    dur_s = max(0.001, duration_ms / 1000.0)
    inputs: list[str] = []
    resolved: list[tuple[int, Any]] = []
    src_hashes: list[str] = []
    dropped: list[dict[str, Any]] = []
    for clip in clips:
        source = getattr(clip, "source", "") or ""
        path = _resolve_source(project, source)
        if path is None:
            dropped.append({
                "source": source,
                "reason": ("expected source missing/unreadable/empty or escapes "
                           "the project sandbox — digital silence substituted"),
            })
            continue
        idx = len(resolved)
        resolved.append((idx, clip))
        inputs += ["-i", str(path)]
        src_hashes.append(hash_file(path))

    accounting = {
        "expected_clips": len(clips),
        "resolved_clips": len(resolved),
        "dropped_clips": dropped,
    }
    with atomic_output(dest) as tmp:
        if not resolved:
            cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                   "-f", "lavfi", "-i",
                   f"anullsrc=r={SAMPLE_RATE}:cl=stereo", "-t", f"{dur_s}",
                   "-c:a", "pcm_s16le", str(tmp)]
        else:
            chains = [_clip_chain(i, c, f"c{i}") for i, c in resolved]
            mix_in = "".join(f"[c{i}]" for i, _ in resolved)
            graph = ";".join([
                *chains,
                f"{mix_in}amix=inputs={len(resolved)}:normalize=0:"
                f"dropout_transition=0[m]",
                f"[m]{_AFMT},apad,atrim=duration={dur_s},"
                "asetpts=PTS-STARTPTS[out]",
            ])
            cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                   *inputs, "-filter_complex", graph, "-map", "[out]",
                   "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE),
                   "-ac", str(CHANNELS), str(tmp)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not tmp.exists():
            tail = "\n".join((proc.stderr or "").splitlines()[-8:])
            raise MastersError(f"bus render failed for {dest.name}:\n{tail}")
    return dest, src_hashes, accounting


def _mix_files(sources: list[Path], duration_ms: int, dest: Path, *,
               headroom_db: float = 0.0) -> Path:
    """amix a set of already-rendered stems into a combined master (RAW_STEM_SUM /
    M&E). normalize=0 so the sum is the literal bus sum. ``headroom_db`` (< 0)
    applies a fixed attenuation to the SUM — a uniform gain that preserves the
    relative levels between sums while keeping hot buses from clipping (CLOSEOUT
    C5 ruling 3). Raw single-bus stems pass ``headroom_db=0`` and are untouched."""
    dur_s = max(0.001, duration_ms / 1000.0)
    inputs: list[str] = []
    for s in sources:
        inputs += ["-i", str(s)]
    vol = f",volume={headroom_db}dB" if headroom_db else ""
    with atomic_output(dest) as tmp:
        if len(sources) == 1 and not headroom_db:
            cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                   "-i", str(sources[0]), "-c:a", "pcm_s16le", str(tmp)]
        else:
            if len(sources) == 1:
                graph = f"[0:a]{_AFMT}{vol},atrim=duration={dur_s}[out]"
            else:
                graph = (f"amix=inputs={len(sources)}:normalize=0:"
                         f"dropout_transition=0,{_AFMT}{vol},"
                         f"atrim=duration={dur_s}[out]")
            cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                   *inputs, "-filter_complex", graph, "-map", "[out]",
                   "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE),
                   "-ac", str(CHANNELS), str(tmp)]
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0 or not tmp.exists():
            tail = "\n".join((proc.stderr or "").splitlines()[-8:])
            raise MastersError(f"mix failed for {dest.name}:\n{tail}")
    return dest


# ------------------------------------------------------------------- loudness


_LOUDNORM_JSON = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def measure_loudness(path: Path) -> dict[str, Any]:
    """EBU R128 measurement — REAL ffmpeg, recorded as probe facts (never a
    target, never hardcoded). integrated LUFS / true-peak dBTP / LRA come from
    ``loudnorm`` first-pass JSON; short-term max from an ``ebur128`` scan."""
    fact: dict[str, Any] = {
        "integrated_lufs": None, "true_peak_dbtp": None,
        "lra": None, "threshold_lufs": None, "short_term_max_lufs": None,
    }
    try:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
             "-af", "loudnorm=print_format=json", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        m = _LOUDNORM_JSON.search(proc.stderr or "")
        if m:
            data = json.loads(m.group(0))
            fact["integrated_lufs"] = _fl(data.get("input_i"))
            fact["true_peak_dbtp"] = _fl(data.get("input_tp"))
            fact["lra"] = _fl(data.get("input_lra"))
            fact["threshold_lufs"] = _fl(data.get("input_thresh"))
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        pass
    try:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
             "-af", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        st = re.findall(r"S:\s*(-?\d+(?:\.\d+)?)", proc.stderr or "")
        vals = [float(x) for x in st if float(x) > -120.0]
        if vals:
            fact["short_term_max_lufs"] = round(max(vals), 2)
    except (subprocess.SubprocessError, ValueError):
        pass
    return fact


def volume_stats(path: Path) -> dict[str, float | None]:
    """``volumedetect`` mean/max dBFS — the deterministic hook the §12 M&E
    silence check asserts on (no dialogue energy in the M&E band)."""
    out: dict[str, float | None] = {"mean_volume": None, "max_volume": None}
    try:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        for key, tag in (("mean_volume", "mean_volume"), ("max_volume", "max_volume")):
            m = re.search(rf"{tag}:\s*(-?\d+(?:\.\d+)?) dB", proc.stderr or "")
            if m:
                out[key] = float(m.group(1))
    except (subprocess.SubprocessError, ValueError):
        pass
    return out


def _fl(v: Any) -> float | None:
    import math

    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # NaN / ±inf (e.g. a truly silent stem measures -inf LUFS) are not clean
    # JSON and not useful facts — record them as None (honest "undefined").
    return round(f, 2) if math.isfinite(f) else None


# --------------------------------------------------------------- level safety


def _level_safety_diagnostics(artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan the SUM masters (the only artifacts carrying a ``level_safety``
    ceiling) and raise a BLOCKING ``AUDIO_CLIPPING`` diagnostic for any whose
    MEASURED true peak still exceeds the ceiling after headroom (CLOSEOUT C5
    ruling 3 — clipping/overs are a blocker, never a silent note). Raw single-bus
    stems carry no ceiling and are intentionally allowed to be hot."""
    diags: list[dict[str, Any]] = []
    for a in artifacts:
        ls = a.get("level_safety") or {}
        ceiling = ls.get("ceiling_dbtp")
        tp = (a.get("loudness") or {}).get("true_peak_dbtp")
        if ceiling is None or tp is None:
            continue
        if tp > ceiling:
            diags.append({
                "code": "AUDIO_CLIPPING",
                "severity": "blocking",
                "role": a.get("role"),
                "detail": (f"{a.get('role')} true peak {tp} dBTP exceeds the "
                           f"{ceiling} dBTP ceiling after {ls.get('method')} — "
                           "reduce level / add headroom before delivery"),
            })
    return diags


# ---------------------------------------------------------------- public entry


def masters_dir(project: Project) -> Path:
    return project.root / "exports" / "masters"


def index_path(project: Project) -> Path:
    return masters_dir(project) / "masters.json"


def _timeline_digest(timeline: Any) -> str | None:
    from ..build.delivery import timeline_semantic_digest

    return timeline_semantic_digest(timeline)


def _rational_rate(timeline: Any) -> Rate | None:
    """The timeline's exact rational edit rate IFF it carries R2's rational echo,
    else ``None``. Defensive ``getattr`` so a duck-typed/legacy timeline without
    the echo simply yields no sample facts (int byte-identity); a whole-number
    echo also yields ``None`` (only a genuine 1001-family rate qualifies)."""
    echo = getattr(timeline, "rate_echo", None)
    if echo is None:
        return None
    rate = getattr(echo, "rate", None)
    return rate if isinstance(rate, Rate) and rate.exact_int is None else None


def _sample_facts(timeline: Any, duration_ms: int, sample_rate: int) -> dict[str, Any]:
    """Additive per-artifact audio sample facts — EMITTED ONLY for a rational-echo
    (R2 1001-family) project.

    RULING (FP_R4_ADDENDUM §3): ``masters.json``'s ``index_digest`` covers the
    artifact rows, so adding row keys shifts it for EVERY project. That is NOT
    acceptable for int byte-identity — an int project's masters.json (bytes and
    digest) MUST stay pinned. Therefore an int/whole-number timeline gets ``{}``
    (no new keys, byte-identical), and only a rational project's rows gain:

    * ``duration_samples`` — EXACT via :func:`~manju.core.timebase.frames_to_samples`
      when the render length maps to a WHOLE frame count at the edit rate
      (``sample_basis="frames"``: e.g. 48 frames @ 24000/1001 & 48 kHz = 96096
      samples, 2002/frame exactly); ELSE ``round(ms × sr / 1000)``
      (``sample_basis="ms"``) — honest provenance, the two bases are never mixed
      silently.

    Facts only: this changes nothing about what is rendered (the WAV bytes and the
    index-level ``sample_rate`` are untouched)."""
    rate = _rational_rate(timeline)
    if rate is None:
        return {}
    dur = int(duration_ms)
    frames = ms_to_frames(dur, rate, Rounding.ROUND_HALF_UP)
    if frames_to_ms(frames, rate, Rounding.ROUND_HALF_UP) == dur:
        # the render length lands exactly on a whole frame → exact sample count
        return {
            "duration_samples": frames_to_samples(frames, rate, sample_rate,
                                                  Rounding.ROUND_HALF_UP),
            "sample_basis": "frames",
        }
    # off a whole frame (e.g. a hand-built rational timeline) → honest ms basis
    return {
        "duration_samples": round(dur * sample_rate / 1000),
        "sample_basis": "ms",
    }


def render_masters(project: Project, timeline: Any, *,
                   loudness_target_lufs: float | None = None,
                   true_peak_target_dbtp: float | None = None) -> dict[str, Any]:
    """Render the raw stem set + raw stem sum + M&E bus-exclusion master for
    ``timeline`` and write the ``masters.json`` index. Returns the index dict.
    All files are REAL WAVs. The index records per-bus expected/resolved/dropped
    clip accounting + master status (a dropped expected source blocks that
    master), sum-master level safety + true-peak clipping diagnostics, and the
    deliberately-absent PROGRAM_MASTER with its reason (CLOSEOUT C5).

    ``loudness_target_lufs`` (from the delivery profile) is recorded and, when
    present, drives an extra loudnorm'd sum master (a DISTINCT kind, never
    conflated with the raw sum) — the target is the profile's, never this
    module's."""
    tracks = getattr(timeline, "tracks", None)

    def _bus(name: str) -> list[Any]:
        return list(getattr(tracks, name, None) or []) if tracks is not None else []

    duration_ms = int(getattr(timeline, "duration_ms", 0) or 0)
    if duration_ms <= 0:
        # fall back to the furthest audio clip end so a hand-built timeline
        # without an explicit duration still renders a truthful length.
        ends = [int(getattr(c, "start_ms", 0) or 0) + int(getattr(c, "duration_ms", 0) or 0)
                for name in ("voice", "music", "sfx", "ambient") for c in _bus(name)]
        duration_ms = max(ends) if ends else 1000

    out = masters_dir(project)
    out.mkdir(parents=True, exist_ok=True)

    # 1) render the four raw buses once each, capturing per-bus clip accounting.
    bus_paths: dict[str, Path] = {}
    bus_src: dict[str, list[str]] = {}
    bus_acct: dict[str, dict[str, Any]] = {}
    for name in ("voice", "music", "sfx", "ambient"):
        p, hashes, acct = _render_bus(project, _bus(name), duration_ms,
                                      out / f"_bus_{name}.wav")
        bus_paths[name] = p
        bus_src[name] = hashes
        bus_acct[name] = acct

    tdigest = _timeline_digest(timeline)
    artifacts: list[dict[str, Any]] = []

    def _emit(role: str, path: Path, buses: tuple[str, ...], *,
              level_safety: dict[str, Any] | None = None) -> None:
        srcs = sorted({h for b in buses for h in bus_src.get(b, [])})
        expected = sum(bus_acct[b]["expected_clips"] for b in buses)
        resolved = sum(bus_acct[b]["resolved_clips"] for b in buses)
        dropped = [dict(d, bus=b) for b in buses for d in bus_acct[b]["dropped_clips"]]
        blocked = bool(dropped)   # any expected source dropped ⇒ master not verified
        status = ("COMPLETE" if not blocked
                  else ("BLOCKED" if resolved == 0 else "INCOMPLETE"))
        art: dict[str, Any] = {
            "role": role,
            "kind": ROLE_KIND[role],
            "path": project.relpath(path),
            "sha256": hash_file(path),
            "bytes": path.stat().st_size,
            "mime": "audio/wav",
            "buses": list(buses),
            "duration_ms": duration_ms,
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "source_refs": srcs,
            "timeline_digest": tdigest,
            "loudness": measure_loudness(path),
            "excludes_dialogue": "voice" not in buses,   # back-compat field
            "excludes_voice_bus": "voice" not in buses,
            "expected_clips": expected,
            "resolved_clips": resolved,
            "dropped_clips": dropped,
            "status": status,
            "blocked": blocked,
            "is_program_master": False,
        }
        if role == "M_AND_E_BUS_EXCLUSION_MASTER":
            # the claim is BUS exclusion — never a content-level "no dialogue" proof
            art["mne_claim"] = "bus_exclusion"
            art["content_verified"] = False
        if level_safety is not None:
            art["level_safety"] = level_safety
        # R4 (folded R3 remnant): additive audio sample facts — {} for int
        # projects (masters.json + index_digest byte-identical), duration_samples/
        # sample_basis for a rational-echo project. Merged LAST so an int row is
        # untouched and a rational row carries the facts as its trailing keys.
        art.update(_sample_facts(timeline, duration_ms, SAMPLE_RATE))
        artifacts.append(art)

    # 2) raw stems are single buses (ambient is its own stem, symmetric with the
    #    trio); the sums are amix of the raw buses (headroom-protected) so the
    #    raw-sum relationship holds while hot buses cannot silently clip.
    dialogue = out / "raw_dialogue_stem.wav"
    _mix_files([bus_paths["voice"]], duration_ms, dialogue)
    _emit("RAW_DIALOGUE_STEM", dialogue, ("voice",))

    music = out / "raw_music_stem.wav"
    _mix_files([bus_paths["music"]], duration_ms, music)
    _emit("RAW_MUSIC_STEM", music, ("music",))

    sfx = out / "raw_sfx_stem.wav"
    _mix_files([bus_paths["sfx"]], duration_ms, sfx)
    _emit("RAW_SFX_STEM", sfx, ("sfx",))

    ambient = out / "raw_ambient_stem.wav"
    _mix_files([bus_paths["ambient"]], duration_ms, ambient)
    _emit("RAW_AMBIENT_STEM", ambient, ("ambient",))

    stem_sum = out / "raw_stem_sum.wav"
    _mix_files([bus_paths[n] for n in ("voice", "music", "sfx", "ambient")],
               duration_ms, stem_sum, headroom_db=SUM_HEADROOM_DB)
    _emit("RAW_STEM_SUM", stem_sum, ("voice", "music", "sfx", "ambient"),
          level_safety=dict(_SUM_LEVEL_SAFETY))

    mne = out / "mne_bus_exclusion_master.wav"
    _mix_files([bus_paths[n] for n in ("music", "sfx", "ambient")], duration_ms,
               mne, headroom_db=SUM_HEADROOM_DB)
    _emit("M_AND_E_BUS_EXCLUSION_MASTER", mne, ("music", "sfx", "ambient"),
          level_safety=dict(_SUM_LEVEL_SAFETY))

    # 3) optional profile-targeted loudness-normalised sum — a DISTINCT kind,
    #    never conflated with the raw sum (CLOSEOUT C5 ruling 2).
    loudnorm_master = None
    if loudness_target_lufs is not None:
        tp = true_peak_target_dbtp if true_peak_target_dbtp is not None else -1.0
        ln = out / "raw_stem_sum.loudnorm.wav"
        dur_s = max(0.001, duration_ms / 1000.0)
        ok = False
        try:
            with atomic_output(ln) as tmp:
                cmd = [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                       "-i", str(stem_sum),
                       "-af", f"loudnorm=I={loudness_target_lufs}:TP={tp}:LRA=11,"
                              f"atrim=duration={dur_s}",
                       "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE),
                       "-ac", str(CHANNELS), str(tmp)]
                proc = subprocess.run(cmd, capture_output=True, text=True,
                                      encoding="utf-8", errors="replace")
                if proc.returncode != 0 or not tmp.exists():
                    # RAISE (don't just flag ok=False): atomic_output finalizes on
                    # any NORMAL block exit — its cleanup is exception-driven, and a
                    # non-zero ffmpeg exit is not a Python exception — so the old
                    # non-raising path let it PUBLISH a truncated .loudnorm.wav.
                    # Raising takes atomic_output's failure path (unlink temp, leave
                    # dest untouched), mirroring _render_bus / _mix_files.
                    tail = "\n".join((proc.stderr or "").splitlines()[-8:])
                    raise MastersError(f"loudnorm master failed:\n{tail}")
            ok = ln.exists()
        except MastersError:
            # the loudnorm sum master is OPTIONAL — a failure omits it (with NO
            # partial file left behind), it never aborts the whole master render.
            ok = False
        if ok and ln.exists():
            loudnorm_master = {
                "role": "RAW_STEM_SUM",
                "kind": "stem_sum_loudnorm",
                "path": project.relpath(ln),
                "sha256": hash_file(ln),
                "bytes": ln.stat().st_size,
                "target_lufs": loudness_target_lufs,
                "target_true_peak_dbtp": tp,
                "loudness": measure_loudness(ln),
                "is_program_master": False,
            }

    # 4) drop the private per-bus temporaries (they are not deliverables).
    for p in bus_paths.values():
        p.unlink(missing_ok=True)

    # 5) index-level roll-ups: dropped clips, level-safety/clipping diagnostics,
    #    source-incompleteness blockers, and the deliberately-absent PROGRAM_MASTER.
    dropped_index = [dict(d, bus=name)
                     for name in ("voice", "music", "sfx", "ambient")
                     for d in bus_acct[name]["dropped_clips"]]
    diagnostics = _level_safety_diagnostics(artifacts)
    for a in artifacts:
        if a.get("blocked"):
            diagnostics.append({
                "code": "MASTER_SOURCE_INCOMPLETE",
                "severity": "blocking",
                "role": a["role"],
                "detail": (f"{a['role']} has {len(a['dropped_clips'])} dropped "
                           "expected source(s); digital silence was substituted — "
                           "the master is INCOMPLETE, never verified"),
            })
    roles_absent = [{
        "role": "PROGRAM_MASTER",
        "reason": ("no PROGRAM_MASTER emitted: this module renders RAW stems + a "
                   "raw sum PRE-duck / PRE-loudnorm and does NOT reuse the final "
                   "program mixer's ducking/loudness/automation chain "
                   "(media/render.py). The final's program audio is lossy AAC "
                   "muxed into the mp4, so no extractable, hash-verifiable program "
                   "master exists here — a raw sum must not masquerade as one"),
    }]

    index = {
        "schema": "manju.audio-masters/v1",
        "timeline_digest": tdigest,
        "duration_ms": duration_ms,
        "sample_rate": SAMPLE_RATE,
        "channels": CHANNELS,
        "loudness_target_lufs": loudness_target_lufs,
        "level_safety": {"sum_headroom_db": SUM_HEADROOM_DB,
                         "true_peak_ceiling_dbtp": TRUE_PEAK_CEILING_DBTP},
        "artifacts": artifacts,
        "loudnorm_master": loudnorm_master,
        "dropped_clips": dropped_index,
        "roles_absent": roles_absent,
        "diagnostics": diagnostics,
    }
    index["index_digest"] = hash_value({"a": artifacts, "t": tdigest})
    with atomic_output(index_path(project)) as tmp:
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return index


def load_index(project: Project) -> dict[str, Any] | None:
    p = index_path(project)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
