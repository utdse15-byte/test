"""W5.4 (MANJU_WINDOWS_ONLY_LEAN_V3) — hardware-encoder ELIGIBILITY, facts only.

The plan's W5.4 asks for hw-encode "qualification". For a personal Windows
pipeline the honest minimal form is an ELIGIBILITY ladder that never
auto-enables anything:

* ``encoder_inventory()`` (media/ffmpeg — the module that owns "what can
  this ffmpeg do"; build/'s doctor may import media but NEVER core.toolchain,
  the manifest boundary pin) — parse ``ffmpeg -encoders`` ONCE
  (process-cached like the other collectors) into per-encoder presence FACTS
  for the h264 hardware family (nvenc/qsv/amf) + the libx264 software floor.
  Absence is a fact, never an error; a missing ffmpeg answers all-absent.
* the facts ride the record-only toolchain manifest (additive block — the
  manifest is NEVER a build input, so an added fact is drift-visible, not a
  rebuild trigger; S4 cache keys read the -version line, not the manifest).
* ``hw_encode_eligibility()`` — a PURE predicate over the inventory:
  candidates = the listed hw encoders; level is honest ("LISTED" — an encoder
  ffmpeg lists can still fail at runtime without the driver; VERIFIED needs a
  real canary encode, deferred until something would CONSUME it). It never
  flips any encode path: render/_SEG_ENC/normalize stay libx264 byte-for-byte.
* doctor shows the facts as an informational row (never gates the exit code).

REJECTED this wave (recorded): an actual ``video_encoder`` project switch —
consuming eligibility to change encodes needs the full lockstep (render
_SEG_ENC + _enc_params + normalize._encode_args + every cache key + concat
profile compatibility) and a VERIFIED canary; eligibility without a consumer
is deliberately inert evidence, not dead config (doctor/status read it).
"""

from __future__ import annotations

import shutil

import pytest

from manju.media.ffmpeg import (
    HW_ENCODER_CANDIDATES,
    encoder_inventory,
    hw_encode_eligibility,
)

ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


# --------------------------------------------------------------- the inventory


def test_hw_candidate_set_is_the_h264_family():
    """Exactly the three Windows h264 hardware encoders + nothing else — the
    pipeline's segment/final profile is h264; other codecs are out of scope."""
    assert set(HW_ENCODER_CANDIDATES) == {"h264_nvenc", "h264_qsv", "h264_amf"}


@ffmpeg
def test_encoder_inventory_reads_real_ffmpeg():
    inv = encoder_inventory()
    # the software floor is present in every real ffmpeg build we support
    assert inv["libx264"] is True
    # every candidate is answered as a FACT (True/False), never missing
    for cand in HW_ENCODER_CANDIDATES:
        assert isinstance(inv[cand], bool)


def test_encoder_inventory_missing_ffmpeg_answers_all_absent(monkeypatch):
    """No ffmpeg → every fact is False (absence is a fact, not a crash) —
    the collector contract of cached_tool_version_line ('missing')."""
    import manju.media.ffmpeg as ffmpeg_mod

    monkeypatch.setattr(ffmpeg_mod.shutil, "which", lambda name: None)
    inv = ffmpeg_mod._encoder_inventory_uncached()
    assert inv["libx264"] is False
    assert all(inv[c] is False for c in HW_ENCODER_CANDIDATES)


def test_encoder_inventory_is_process_cached(monkeypatch):
    """One subprocess per process, like the other collectors — key computation
    or repeated doctor runs never re-probe."""
    import manju.media.ffmpeg as ffmpeg_mod

    calls = {"n": 0}
    real = ffmpeg_mod._encoder_inventory_uncached

    def counting():
        calls["n"] += 1
        return real()

    monkeypatch.setattr(ffmpeg_mod, "_encoder_inventory_uncached", counting)
    ffmpeg_mod.encoder_inventory.cache_clear()
    try:
        encoder_inventory()
        encoder_inventory()
        assert calls["n"] == 1
    finally:
        ffmpeg_mod.encoder_inventory.cache_clear()


@ffmpeg
def test_toolchain_manifest_carries_the_encoder_block():
    """The record-only manifest gains an additive ``encoders`` fact block —
    drift-visible when a machine gains/loses a hw encoder, never a build
    input (FP_TOOLCHAIN discipline is unchanged)."""
    from manju.core.toolchain import toolchain_manifest

    facts = toolchain_manifest()["facts"]
    enc = facts["encoders"]
    assert enc["libx264"] is True
    for cand in HW_ENCODER_CANDIDATES:
        assert isinstance(enc[cand], bool)


# --------------------------------------------------------------- eligibility


def test_eligibility_pure_predicate_no_candidates():
    verdict = hw_encode_eligibility(
        {"libx264": True, "h264_nvenc": False, "h264_qsv": False, "h264_amf": False})
    assert verdict["eligible"] is False
    assert verdict["candidates"] == []
    assert verdict["level"] == "NONE_LISTED"


def test_eligibility_pure_predicate_with_candidates():
    verdict = hw_encode_eligibility(
        {"libx264": True, "h264_nvenc": True, "h264_qsv": False, "h264_amf": True})
    assert verdict["eligible"] is True
    assert verdict["candidates"] == ["h264_amf", "h264_nvenc"]  # sorted, deterministic
    # honest level: LISTED is not VERIFIED — a listed encoder can still fail
    # at runtime without the driver; nothing may treat this as PRODUCTION_READY
    assert verdict["level"] == "LISTED"
    assert "driver" in verdict["note"] or "驱动" in verdict["note"]


def test_eligibility_never_auto_enables_the_encoder():
    """The admission rule (UNKNOWN is never PASS; eligibility never flips a
    default): the render/segment/normalize encode profiles stay libx264
    regardless of any inventory — source-pinned at the three owners."""
    from pathlib import Path

    import manju.media.normalize as normalize
    import manju.media.render as render

    assert render._SEG_ENC[1] == "libx264"
    assert render._enc_params("final")[1] == "libx264"
    assert render._enc_params("proxy")[1] == "libx264"
    for mod in (render, normalize):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "encoder_inventory" not in src, (
            f"{mod.__name__} consults the encoder inventory — eligibility "
            "must never auto-enable a hardware encoder")


# --------------------------------------------------------------- doctor row


def test_doctor_shows_hw_encoder_facts_informationally(tmp_path):
    """An informational row (•/✓): names which hw encoders this ffmpeg lists,
    or that none are; NEVER gates doctor's exit code."""
    from manju.build.doctor import run_doctor

    doc = run_doctor(None)
    rows = [c for c in doc["checks"] if c["name"] == "hw_encoders"]
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg absent — the row rides the env-tools presence")
    assert len(rows) == 1
    assert rows[0]["ok"] is True  # informational, never gating
    # environment-agnostic: the row names the listed candidates or says none.
    # (This very container LISTS nvenc/qsv with no GPU present — the live
    # proof that LISTED is a build fact, not a runtime verification.)
    detail = rows[0]["detail"]
    assert ("listed:" in detail and "h264_" in detail) or "none listed" in detail
