"""FP loop V2 — onion skin + scopes on the EXISTING compare board (user item 6).

Item 6 named six surfaces; loop T1 shipped the first four (A/B sync, wipe,
difference, frame-lock) plus the boundary view. This loop lands the last two
words — ONION SKIN and SCOPES — as CLIENT-SIDE, VIEW-ONLY extensions of the
same serve-mode compare surface (``.compare-wrap`` / ``.cmp-ab`` A/B stack and
the ``.bnd-row`` boundary rows), never a rebuild:

  * onion skin — a fourth compare mode (``data-cmpmode="onion"``) rendering B
    over A at CSS opacity on the SAME A/B stack, plus an onion toggle+slider on
    each boundary row's two stills;
  * scopes — a collapsed-by-default panel drawing a Rec.709 luma histogram and
    a per-column luma waveform of video A's CURRENT PARKED frame, carrying a
    permanent honesty label (browser-decoded RGB, view-only; QC colorstats
    remain the measurement authority) and redrawn only on pause/step/slider.

Serve-mode only: the static board stays byte-for-byte identical (re-pinned here
with the new markers asserted ABSENT, extending T1's pin). This file NEVER edits
T1's ``test_fp_board_compare.py`` — its 15 pins stay green untouched.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import threading
from pathlib import Path
from typing import Callable, Iterator

import httpx

from manju.board import board as bd
from manju.board.server import make_server
from manju.core.container import Project

# --------------------------------------------------------------- fixtures


class _FrozenDatetime:
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return _dt.datetime(2026, 7, 12, 12, 0, 0, tzinfo=_dt.timezone.utc)


@contextlib.contextmanager
def running(project: Project) -> Iterator[str]:
    """A live BoardServer on an ephemeral port (the existing serve-test pattern,
    mirrored from T1 / test_board_serve — never imported from T1's module)."""
    server = make_server(project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _two_take_project(tmp_project: Project, add_shot: Callable, make_take: Callable) -> Project:
    """One shot, two fake-media takes, take_01 selected (board fixture shape)."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "manual")  # take_01
    make_take(tmp_project, "S001", "manual")  # take_02
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01")
    )
    return tmp_project


def _two_shot_project(tmp_project: Project, add_shot: Callable, make_take: Callable) -> Project:
    """Two shots in index order, each with a selected fake-media take."""
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
        make_take(tmp_project, sid, "manual")
        tmp_project.update_shot_raw(
            sid, lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01"))
    return tmp_project


# The exact substrings the scopes honesty label is BOUND to carry (addendum §2).
_SCOPES_REQUIRED = (
    "browser-decoded RGB",                              # what it samples
    "view-only",                                        # its standing
    "video A only",                                     # A, and A only
    "Rec.709 luma",                                     # the luma space
    "Y = 0.2126·R + 0.7152·G + 0.0722·B",  # the formula, spelled out
    "QC colorstats remain the measurement authority",  # the authority sentence
    "parked frames",                                    # redraw discipline, not a play loop
)


# ------------------------------------------------------- onion skin (A/B mode)


def test_onion_is_a_fourth_ab_mode_with_opacity_slider(tmp_project, add_shot, make_take):
    """The EXISTING A/B stack gains a fourth mode button + an opacity slider —
    B over A at CSS opacity, no canvas for the video pair."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True)
    assert 'data-cmpmode="onion"' in html          # the fourth mode toggle
    assert "data-onion" in html and 'type="range"' in html  # the opacity slider
    # driven purely by CSS opacity on the stacked B video (no new canvas here)
    assert '[data-mode="onion"] .ab-b' in html     # the mode's opacity baseline rule
    assert "onionMove" in html                     # the client-side opacity handler


def test_onion_extends_the_ab_stack_not_a_rebuild(tmp_project, add_shot, make_take):
    """Extend-not-rebuild pin: every T1 A/B marker (the three prior modes, the
    wipe slider, the diff canvas, frame-step) is STILL present next to onion."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True)
    for legacy in ('data-cmpmode="sbs"', 'data-cmpmode="wipe"', 'data-cmpmode="diff"',
                   "data-wipe", "data-diffcanvas", "data-diffgain", "amplified ×4",
                   'data-framestep="-1"', 'data-framestep="1"', 'class="cmp-ab"',
                   'class="compare-wrap"', 'data-syncplay="1"'):
        assert legacy in html, f"T1 compare marker vanished under V2: {legacy}"
    # and onion joins them
    assert 'data-cmpmode="onion"' in html


def test_onion_unavailable_without_two_media_takes(tmp_project, add_shot, make_take):
    """One take's media deleted → onion (like wipe/diff) is honestly gone; the
    existing cmp-ab-unavail note carries the reason (no silent broken stack)."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    project.get_take("S001", "take_02").media_path.unlink()
    html = bd.render_board(project, serve=True)
    assert 'data-cmpmode="onion"' not in html    # no onion mode button
    assert 'data-onion="1"' not in html          # no onion opacity slider markup
    assert "need two takes with media" in html   # T1's honest note, reused


# --------------------------------------------------- onion skin (boundary row)


def test_boundary_row_gains_onion_toggle_and_stacked_stage(
        tmp_project, add_shot, make_take, monkeypatch):
    """When both boundary stills resolve, the row gains an onion toggle + an
    opacity slider and a STACKED still stage (next shot's first over this
    ending) — reusing the row markup pattern, distinct from the diff canvas."""
    project = _two_shot_project(tmp_project, add_shot, make_take)
    # Make the EXISTING extractor yield a real jpg so both stills resolve
    # (fake bytes can't be decoded — that path is covered separately below).
    from manju.media import frames as frames_mod
    still = project.root / ".manju" / "frames" / "still.jpg"
    still.parent.mkdir(parents=True, exist_ok=True)
    still.write_bytes(b"jpegbytes")
    monkeypatch.setattr(frames_mod, "extract_frame", lambda *a, **k: still)

    html = bd.render_board(project, serve=True)
    assert 'data-bndonion="1"' in html          # the per-row onion toggle
    assert "data-bndonion-op" in html           # its opacity slider
    assert 'class="bnd-onion"' in html          # the stacked stage
    assert "bnd-onion-b" in html                # the overlaid (next-shot) still
    # the diff canvas is a DIFFERENT surface and must still be there too
    assert "data-bndcanvas" in html and 'data-bnddiff="1"' in html


def test_boundary_onion_absent_when_stills_cannot_be_extracted(
        tmp_project, add_shot, make_take):
    """Fake media can't be decoded → no stills → no onion tools/stage (the row
    still renders its honest failure slots, exactly as T1 pins)."""
    project = _two_shot_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True)
    assert 'class="bnd-row"' in html            # the row is present (T1)
    assert 'data-bndonion="1"' not in html      # but no onion tools without stills
    assert 'class="bnd-onion"' not in html


# ------------------------------------------------------------------- scopes


def test_scopes_panel_has_both_canvases_and_the_toggle(tmp_project, add_shot, make_take):
    """A collapsed scopes panel rides the compare wrap with BOTH scopes:
    a luma histogram canvas and a per-column luma waveform canvas."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True)
    assert 'data-scopes="1"' in html            # the toggle button
    assert 'class="cmp-scopes"' in html         # the panel
    assert "data-scope-hist" in html            # histogram canvas
    assert "data-scope-wave" in html            # waveform canvas
    assert html.count("<canvas") >= 3           # A/B diff canvas + 2 scope canvases


def test_scopes_carries_the_permanent_honesty_label(tmp_project, add_shot, make_take):
    """The BINDING honesty grammar: the label states browser-decoded RGB,
    view-only, video A ONLY, the Rec.709 formula, that QC colorstats remain the
    measurement authority, and that it redraws for parked frames (not a loop)."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True)
    for phrase in _SCOPES_REQUIRED:
        assert phrase in html, f"scopes honesty label missing required phrase: {phrase!r}"


def test_scopes_luma_is_rec709_in_the_drawing_code_too(tmp_project, add_shot, make_take):
    """The label is not the only place the formula lives: the client-side
    drawing uses the exact Rec.709 coefficients (never a naive average)."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True)
    assert "drawScopes" in html
    for coef in ("0.2126", "0.7152", "0.0722"):
        assert coef in html, f"Rec.709 coefficient {coef} absent from scopes JS"


def test_scopes_collapsed_by_default(tmp_project, add_shot, make_take):
    """Scopes are for parked frames on demand — the panel is present but
    collapsed on load (no ``open`` class until the user toggles it)."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True)
    assert 'class="cmp-scopes"' in html
    assert 'class="cmp-scopes open"' not in html  # collapsed until toggled


def test_scopes_absent_without_the_ab_stack(tmp_project, add_shot, make_take):
    """No A/B stack (fewer than two playable takes) → no video A to scope →
    no scopes panel at all (degraded, never a broken canvas)."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    project.get_take("S001", "take_02").media_path.unlink()
    html = bd.render_board(project, serve=True)
    assert 'data-scopes="1"' not in html         # no scopes toggle button markup
    assert 'class="cmp-scopes"' not in html      # no scopes panel


# ----------------------------------------------- static board stays pinned


def test_static_board_carries_none_of_the_new_markers(tmp_project, add_shot, make_take, monkeypatch):
    """Extend T1's pin: the static (non-serve) board is byte-for-byte what it
    always was and carries NONE of the onion/scopes markers."""
    project = _two_shot_project(tmp_project, add_shot, make_take)
    monkeypatch.setattr(bd, "datetime", _FrozenDatetime)
    written = bd.generate_board(project).read_text(encoding="utf-8")
    assert written == bd.render_board(project, serve=False)  # byte-identical
    for marker in ('data-cmpmode="onion"', "data-onion", "onionMove", "data-scopes",
                   "cmp-scopes", "data-scope-hist", "data-scope-wave", "drawScopes",
                   "data-bndonion", "bnd-onion", "browser-decoded RGB"):
        assert marker not in written, f"static board must not carry V2 marker {marker}"


# ------------------------------------------------- read-only / no new facts


def _tree_digest(root: Path, *, skip: tuple[str, ...]) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(rel.startswith(s) for s in skip):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def test_onion_and_scopes_write_nothing_outside_runtime(tmp_project, add_shot, make_take):
    """Onion is pure CSS opacity, scopes are a client-side canvas VIEW: neither
    is a fact source. Rendering the serve board (compare + boundary + scopes)
    changes NO byte outside the disposable ``.manju/`` runtime area."""
    project = _two_shot_project(tmp_project, add_shot, make_take)
    before = _tree_digest(project.root, skip=(".manju/",))
    bd.render_board(project, serve=True)
    after = _tree_digest(project.root, skip=(".manju/",))
    assert after == before  # the board only READS truth; scopes/onion write nothing


# --------------------------------------------------- serve smoke (live server)


def test_serve_smoke_carries_onion_and_scopes_over_http(tmp_project, add_shot, make_take):
    """The onion mode + scopes panel + honesty label actually reach the browser
    over HTTP (the existing live-server serve-test pattern). No NEW server route
    is needed — this proves the client-side surfaces ride the same page."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    with running(project) as base:
        html = httpx.get(base + "/").text
    assert 'data-cmpmode="onion"' in html and 'data-onion="1"' in html
    assert 'data-scopes="1"' in html and "data-scope-hist" in html and "data-scope-wave" in html
    assert "QC colorstats remain the measurement authority" in html  # honesty label rides
