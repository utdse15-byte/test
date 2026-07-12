"""FP loop X1 — professional review TRANSPORT on the EXISTING compare board.

Item 6 polish: the serve-mode ``.compare-wrap`` already carries T1's synced
side-by-side / wipe / difference / frame-lock stepping and V2's onion + scopes.
This loop EXTENDS the ONE delegated ``keydown`` listener (previously Space-only)
with a professional review transport grammar — and NOTHING else:

  * K = pause the active compare videos;
  * L = play; each L cycles the NATIVE ``playbackRate`` 1→2→4→1 with a visible
    rate label (never a re-timed shuttle);
  * J = the HONEST ruling — browsers cannot play ``<video>`` backwards natively,
    so J performs a single −1-frame step per press (same exact period as
    ArrowLeft) and SAYS so; never a fake interval shuttle;
  * ArrowLeft/ArrowRight = ±1 frame via the EXISTING exact den/num period
    (shared through one ``framePeriod`` helper, never duplicated arithmetic);
  * Shift+Arrow = ±1 second (exact);
  * active ONLY inside an OPEN ``.compare-wrap`` and NEVER when typing in a form
    field (tagName guard) — no key is ever stolen from an input;
  * a bilingual discoverability hint line + the live transport label ride the
    compare head so the grammar is never a hidden chord;
  * the parked-frame scope redraw rides V2's EXISTING ``seeked`` hook (verified
    here, not rewired).

Serve-mode only: the static board stays byte-for-byte identical (re-pinned here,
extending T1's + V2's pins). This file NEVER edits T1's ``test_fp_board_compare``
or V2's ``test_fp_board_compare2`` — their 15 + 13 pins stay green untouched.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable, Iterator

import httpx
import pytest

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
    mirrored from T1 / V2 — never imported from their modules)."""
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


# The EXACT honest J label the addendum binds the transport to carry: browsers
# have NO native reverse, so J is a single frame step-back that says so.
_J_LABEL = "J: 逐帧回退 step-back (浏览器不支持倒放 no native reverse)"


# ------------------------------------------------- the delegated keydown handler


def test_transport_keys_extend_the_one_keydown_handler():
    """K / L / J / Arrow handling lands in the SAME single delegated keydown
    listener — never a second listener architecture — and the pre-existing Space
    (frame.io PlayPause) branch is still there untouched."""
    js = bd._SERVE_JS
    # exactly ONE keydown listener on the document (extend, don't rebuild)
    assert js.count('addEventListener("keydown"') == 1
    # the pre-existing Space→focused-video branch survives verbatim
    assert 'e.code === "Space"' in js or 'e.code !== "Space"' in js
    assert 'a.tagName === "VIDEO"' in js
    # the pro transport dispatch the same listener now calls
    assert "function cmpTransport(e, wrap)" in js
    for key in ('k === "k" || k === "K"', 'k === "l" || k === "L"',
                'k === "j" || k === "J"', 'k === "ArrowLeft" || k === "ArrowRight"'):
        assert key in js, f"transport key branch missing: {key}"


def test_l_cycles_native_playbackrate_1_2_4():
    """L plays via the browser's NATIVE playbackRate and each press cycles it
    1→2→4→1 — a visible rate label, never a re-timed interval shuttle."""
    js = bd._SERVE_JS
    assert "function cmpRate(wrap)" in js
    # the honest cycle over the browser's own rate
    assert "vids[i].playbackRate = rate" in js
    assert "(cur >= 4) ? 1 : cur * 2" in js          # 1→2→4→1, exactly
    # the label shows the current rate (addendum example: "L ×2")
    assert '"L ▶ 播放 play ×" + rate' in js
    # NO fake shuttle: no interval/timeout timer drives a pretend reverse/rate
    assert "setInterval" not in js and "setTimeout" not in js


def test_j_is_the_honest_single_step_back_never_a_fake_reverse():
    """J's binding ruling: <video> has no native reverse, so J is a single
    −1-frame step (same period as ArrowLeft) whose label SAYS there is no native
    reverse — never a fake smooth-shuttle interval."""
    js = bd._SERVE_JS
    # the exact honest label
    assert _J_LABEL in js
    # J steps by ONE negative frame period through the shared seek path
    j_branch = js.split('k === "j" || k === "J"', 1)[1].split("} else", 1)[0]
    assert "framePeriod(wrap)" in j_branch          # same exact period as ArrowLeft
    assert "cmpSeek(wrap, -" in j_branch            # a single negative frame step
    # NO fake shuttle timer anywhere drives a pretend reverse
    assert "setInterval" not in js and "setTimeout" not in js


def test_arrows_and_j_share_one_frameperiod_helper_no_duplicated_math():
    """±1-frame stepping (arrows, J AND the existing buttons) all read the exact
    den/num period through ONE ``framePeriod`` helper — the arithmetic is shared,
    never copied. The old inline ``dir * den / num`` duplication is gone."""
    js = bd._SERVE_JS
    assert "function framePeriod(wrap)" in js
    assert "return den / num" in js                 # the den/num math lives here, once
    assert "var per = framePeriod(wrap)" in js      # the frame-step BUTTON delegates to it
    # the button, J and both arrows all call the shared reader (def + 3 callers)
    assert js.count("framePeriod(wrap)") >= 4
    # the pre-refactor duplicated inline arithmetic no longer exists
    assert "dir * den / num" not in js


def test_shift_arrow_is_exact_one_second():
    """Shift+Arrow steps ±1 second exactly (not a frame), and the label says 1s."""
    js = bd._SERVE_JS
    assert "e.shiftKey" in js
    assert "cmpSeek(wrap, dir * 1)" in js           # exactly one second, signed
    assert "1s" in js                               # the second-step label states it


def test_keys_active_only_inside_an_open_compare_wrap():
    """The transport fires ONLY when the event target is inside an OPEN
    .compare-wrap — never globally, so a second shot's keys never cross-fire."""
    js = bd._SERVE_JS
    assert 'closest(".compare-wrap.open")' in js
    assert "if (wrap){ cmpTransport(e, wrap); }" in js


def test_keys_never_stolen_from_a_form_field_tagname_guard():
    """Never steal keys while the user is typing: an INPUT / TEXTAREA /
    contenteditable target short-circuits the transport (tagName guard)."""
    js = bd._SERVE_JS
    assert 't.tagName === "INPUT"' in js
    assert 't.tagName === "TEXTAREA"' in js
    assert "t.isContentEditable" in js


def test_scopes_redraw_rides_the_existing_seeked_hook_not_rewired():
    """V2's parked-frame scope redraw hook is REUSED, not rewired: the 'seeked'
    listener is unchanged and the shared seek helper both the buttons and the
    transport call ends by redrawing the scopes (which 'seeked' also triggers)."""
    js = bd._SERVE_JS
    # V2's discrete-event hook is present and untouched
    assert 'addEventListener("seeked", scopesFromVideoEvent, true)' in js
    assert 'addEventListener("pause", scopesFromVideoEvent, true)' in js
    # the shared pause+seek helper redraws scopes at the end of every step
    assert "function cmpSeek(wrap, dt)" in js
    seek_body = js.split("function cmpSeek(wrap, dt)", 1)[1].split("\n  function ", 1)[0]
    assert "drawScopes(wrap)" in seek_body


# ------------------------------------------------- the discoverable surface


def test_compare_head_carries_bilingual_transport_hint_and_rate_label(
        tmp_project, add_shot, make_take):
    """The compare head gains a bilingual hint line documenting the keys AND a
    live transport label — the grammar is discoverable, never a hidden chord."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True)
    # the live transport status label
    assert "data-transport" in html
    assert 'class="cmp-transport"' in html
    # a bilingual hint line documenting every key (Chinese + English both present)
    assert 'class="compare-hint cmp-transport-hint"' in html
    for token in ("K=", "pause", "L=", "play", "J=", "step-back",
                  "±1帧 frame", "±1秒 1s"):
        assert token in html, f"transport hint missing token: {token}"
    # the honesty (no native reverse) rides BOTH the runtime label and the hint
    assert html.count("no native reverse") >= 2


def test_transport_reaches_the_browser_over_http(tmp_project, add_shot, make_take):
    """The transport JS + hint + label actually reach the browser over HTTP (the
    existing live-server pattern). No NEW server route — it rides the same page."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    with running(project) as base:
        html = httpx.get(base + "/").text
    assert "function cmpTransport(e, wrap)" in html
    assert _J_LABEL in html
    assert 'class="cmp-transport"' in html


# ------------------------------------------------- static board stays pinned


def test_static_board_carries_none_of_the_transport(
        tmp_project, add_shot, make_take, monkeypatch):
    """Extend T1's + V2's static pin: the non-serve board is byte-for-byte what
    it always was and carries NONE of the transport markers (serve-only)."""
    project = _two_shot_project(tmp_project, add_shot, make_take)
    monkeypatch.setattr(bd, "datetime", _FrozenDatetime)
    written = bd.generate_board(project).read_text(encoding="utf-8")
    assert written == bd.render_board(project, serve=False)  # byte-identical
    for marker in ("cmpTransport", "cmp-transport", "data-transport", "framePeriod",
                   "cmpSeek", "cmpRate", "playbackRate", "no native reverse",
                   "逐帧回退", "compare-wrap.open"):
        assert marker not in written, f"static board must not carry transport marker {marker}"


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


def test_transport_writes_nothing_outside_runtime(tmp_project, add_shot, make_take):
    """The transport is pure client-side view behavior — not a fact source.
    Rendering the serve board changes NO byte outside the disposable ``.manju/``
    runtime area."""
    project = _two_shot_project(tmp_project, add_shot, make_take)
    before = _tree_digest(project.root, skip=(".manju/",))
    bd.render_board(project, serve=True)
    after = _tree_digest(project.root, skip=(".manju/",))
    assert after == before


# ------------------------------------------------- discipline guards


def test_board_never_carries_the_edit_rate_token():
    """The rational build spine's ``edit_rate`` token must NEVER leak into the
    board (ratemig R1 grep pin) — the compare stepper reads the derived
    ``data-fps-num``/``data-fps-den`` echo, never the raw field."""
    src = Path(bd.__file__).read_text(encoding="utf-8")
    assert "edit_rate" not in src


def test_transport_introduces_no_forbidden_specialized_view_marker(
        tmp_project, add_shot, make_take):
    """The transport adds no c21g-forbidden specialized-view surface (vectorscope
    / waveform-monitor / beat-grid / …) — it is pure playback transport."""
    project = _two_take_project(tmp_project, add_shot, make_take)
    html = bd.render_board(project, serve=True, token="tok").lower()
    for marker in ("vectorscope", "color-scope", "waveform-monitor", "beat-grid",
                   "crop-keyframe", "world-state", "lineage-graph", "viseme"):
        assert marker not in html, f"transport must not add forbidden view: {marker}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node required for --check")
def test_served_js_passes_node_syntax_check(tmp_path):
    """The served transport JS is syntactically valid (node --check on the whole
    IIFE) — a static guard that the extension never ships a parse error."""
    js_file = tmp_path / "serve.js"
    js_file.write_text(bd._SERVE_JS, encoding="utf-8")
    proc = subprocess.run(["node", "--check", str(js_file)],
                          capture_output=True, text=True)
    assert proc.returncode == 0, f"node --check failed:\n{proc.stderr}"
