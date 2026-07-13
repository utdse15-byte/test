"""FP provider-floor — direct offline tests of the §8.4 degradation FLOOR.

The three always-available, network-independent providers (kenburns /
caption_card / manual) are the safety floor the fallback chain always ends at,
yet the audit (F9) found they had only INCIDENTAL coverage via build e2e —
their most consequential branches (no-ref refusal, per-candidate zoom
determinism, html→drawtext fallback + honest renderer lineage, the manual
NeedsHumanInput on-ramp) were never directly pinned. This file pins them.

Also F13: the caption_card auto-mode html failure used to degrade to drawtext
SILENTLY; it now records a best-effort ``level="info"`` degradation so a
persistently-broken renderer is diagnosable.

Everything here is OFFLINE — no network, no paid calls, no ffmpeg/Chromium: the
media renderers are stubbed so we pin the PROVIDER's selection/lineage logic,
not the encode.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.providers.base import (
    FailureKind,
    GenerationRequest,
    NeedsHumanInput,
    ProviderFailure,
)


def _req(project, shot, *, candidates=1, **params):
    return GenerationRequest(
        project=project, shot=shot, bible=project.load_bible(),
        spec_hash="sha256:x", duration_ms=2000, candidates=candidates,
        params=dict(params))


# =============================================================== kenburns floor


def test_kenburns_no_reference_is_invalid(tmp_project, add_shot):
    """F9: kenburns refuses a shot with NO reference image as user-fixable INPUT
    (FailureKind.invalid) — checked params.image, bible ref_image, media/refs.
    Raises BEFORE any render, so no ffmpeg is needed."""
    from manju.providers.kenburns import KenburnsProvider

    shot = add_shot(tmp_project, "S001")  # no ref image anywhere
    with pytest.raises(ProviderFailure) as exc:
        KenburnsProvider().generate(_req(tmp_project, shot))
    assert exc.value.kind is FailureKind.invalid
    assert "reference image" in str(exc.value)


def test_kenburns_zoom_variance_is_index_deterministic(tmp_project, add_shot, monkeypatch):
    """F9: per-candidate motion variance is index-deterministic —
    zoom_to = round(1.10 + 0.03*i, 4), no random / wall-clock. Offline: the
    ffmpeg render is stubbed to CAPTURE the zoom_to per candidate, so we pin the
    exact zoom schedule AND that it lands in the take lineage."""
    import importlib

    from manju.providers.kenburns import KenburnsProvider

    # the media.kenburns MODULE (not the re-exported same-named function on the
    # media package): grab it via importlib so setattr targets the module the
    # provider's lazy `from ..media.kenburns import kenburns` reads at call time.
    media_kb = importlib.import_module("manju.media.kenburns")

    # a (fake) reference image in media/refs so resolution finds a primary image
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.refs_dir / "ref.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    seen_zoom: list[float] = []

    def _fake_kenburns(image, dest, *, zoom_to, **kw):
        seen_zoom.append(zoom_to)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fakeclip")
        return dest

    monkeypatch.setattr(media_kb, "kenburns", _fake_kenburns)

    shot = add_shot(tmp_project, "S001")
    takes = KenburnsProvider().generate(_req(tmp_project, shot, candidates=3))
    assert len(takes) == 3
    assert seen_zoom == [1.10, 1.13, 1.16]  # 1.10 + 0.03*i, index-deterministic
    assert [t.sidecar.params["zoom_to"] for t in takes] == [1.10, 1.13, 1.16]
    # motion is monotonically increasing per candidate (variety, deterministic)
    assert seen_zoom == sorted(seen_zoom)


# ============================================================ caption_card floor


def test_caption_card_records_html_renderer_when_it_runs(tmp_project, add_shot, monkeypatch):
    """F9: caption_card records the renderer that ACTUALLY ran in params so
    lineage stays honest (§4.2). When the html renderer succeeds → 'html'."""
    import manju.media.html_card as html_mod
    from manju.providers.caption_card import CaptionCardProvider

    def _fake_html(text, dest, **kw):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"htmlclip")
        return dest

    monkeypatch.setattr(html_mod, "html_card_video", _fake_html)

    shot = add_shot(tmp_project, "S001")
    takes = CaptionCardProvider().generate(_req(tmp_project, shot))
    assert len(takes) == 1
    assert takes[0].sidecar.params["renderer"] == "html"


def test_caption_card_falls_back_to_drawtext_and_records_why(tmp_project, add_shot, monkeypatch):
    """F9 + F13: when the html renderer raises in AUTO mode, caption_card
    degrades to drawtext, records renderer='drawtext' (honest lineage), AND
    records a best-effort level='info' DEGRADATION so the silent swallow is now
    diagnosable. RED at HEAD for the diagnostic: the except branch recorded
    nothing. The fallback FLOOR still produced a watchable take."""
    import manju.media.card as card_mod
    import manju.media.html_card as html_mod
    from manju.core.failures import read_failures
    from manju.providers.caption_card import CaptionCardProvider

    def _boom(*a, **k):
        raise RuntimeError("no chromium here")

    def _fake_drawtext(text, dest, **kw):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"drawtextclip")
        return dest

    monkeypatch.setattr(html_mod, "html_card_video", _boom)
    monkeypatch.setattr(card_mod, "caption_card", _fake_drawtext)

    shot = add_shot(tmp_project, "S001")
    takes = CaptionCardProvider().generate(_req(tmp_project, shot))
    # the floor never dead-ends: drawtext still produced a take
    assert len(takes) == 1
    assert takes[0].sidecar.params["renderer"] == "drawtext"

    # F13: the WHY is now a level="info" degradation (not an error — never
    # counted against the build), diagnosable from the record alone.
    recs = read_failures(tmp_project, 5)
    assert recs, "html degradation was not recorded (F13 silent swallow)"
    r = recs[0]
    assert r["level"] == "info"
    assert r["step"] == "generate" and r["subject"] == "S001"
    assert r["detail"]["renderer"] == "html"
    assert r["detail"]["fell_back_to"] == "drawtext"
    assert "no chromium here" in r["evidence"]


def test_caption_card_explicit_html_surfaces_failure(tmp_project, add_shot, monkeypatch):
    """F9 boundary: renderer='html' EXPLICITLY requested → a renderer failure is
    surfaced (raised), never silently downgraded (only 'auto' degrades)."""
    import manju.media.html_card as html_mod
    from manju.providers.caption_card import CaptionCardProvider

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(html_mod, "html_card_video", _boom)
    shot = add_shot(tmp_project, "S001")
    with pytest.raises(RuntimeError):
        CaptionCardProvider().generate(_req(tmp_project, shot, renderer="html"))


# ================================================================ manual floor


def test_manual_import_needs_human_input_when_no_file(tmp_project, add_shot):
    """F9: manual .generate raises NeedsHumanInput (NOT a ProviderFailure) when
    no file is supplied — the human on-ramp branch. generate_with_fallback treats
    that as 'try the next provider', never a hard failure (§8.4)."""
    from manju.providers.manual import ManualImportProvider

    shot = add_shot(tmp_project, "S001")
    with pytest.raises(NeedsHumanInput):
        ManualImportProvider().generate(_req(tmp_project, shot))


def test_manual_import_generate_registers_supplied_file(tmp_project, add_shot, tmp_path):
    """F9: manual .generate WITH a supplied file registers it as a manual take
    (spec_hash = MANUAL_HASH, never auto-invalidated); imports are sacred — the
    source file is COPIED, never consumed (§3)."""
    from manju.core.hashing import MANUAL_HASH
    from manju.providers.manual import ManualImportProvider

    src = tmp_path / "handmade.mp4"
    src.write_bytes(b"humanmedia")
    shot = add_shot(tmp_project, "S001")
    takes = ManualImportProvider().generate(_req(tmp_project, shot, file=str(src)))
    assert len(takes) == 1
    assert takes[0].sidecar.spec_hash == MANUAL_HASH
    assert takes[0].sidecar.provider == "manual_import"
    assert src.exists()  # copied, never consumed
