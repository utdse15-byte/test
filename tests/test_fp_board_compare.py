"""FP loop T1 — professional compare on the EXISTING board (user item 6).

Extends the serve-mode compare surface that already exists in board/board.py
(the ``.compare-wrap`` synced side-by-side grid) with:

  * a mode toggle — side-by-side (existing grid) | wipe (CSS clip-path slider)
    | difference (client-side <canvas>, honest "amplified ×N" label);
  * frame-lock stepping — pause-synced ±1-frame buttons carrying the EXACT
    frame period as ``data-fps-num``/``data-fps-den`` (the R2 rational timeline
    echo when present, else the project's int fps promoted exactly);
  * an honest duration-mismatch label (from the takes' EXISTING sidecar probe
    facts — never silent, never a new fact source);
  * the boundary view — for consecutive shots in index order, shot N's LAST
    frame vs shot N+1's FIRST frame (selected takes), stills extracted through
    the EXISTING ``media/frames.extract_frame`` cache (``.manju/frames``),
    with a client-side difference toggle and honest missing-media slots.

All of it is serve-mode only: the static board stays byte-for-byte identical
(pinned again here, alongside the original pin in test_board_serve).
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import shutil
import threading
from pathlib import Path
from typing import Callable, Iterator

import httpx
import pytest

from manju.board import board as bd
from manju.board.server import make_server
from manju.core.container import Project
from manju.core.models import ProbeInfo, TakeSidecar, Timeline

# ------------------------------------------------------------- helpers/fixtures


@contextlib.contextmanager
def running(project: Project) -> Iterator[tuple[str, object]]:
    """A live BoardServer on an ephemeral port (test_board_serve pattern)."""
    server = make_server(project, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _FrozenDatetime:
    @classmethod
    def now(cls, tz=None):  # noqa: ANN001
        return _dt.datetime(2026, 7, 12, 12, 0, 0, tzinfo=_dt.timezone.utc)


@pytest.fixture
def two_take_project(tmp_project: Project, add_shot: Callable, make_take: Callable) -> Project:
    """One shot, two fake-media takes, take_01 selected (board fixture shape)."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "manual")  # take_01
    make_take(tmp_project, "S001", "manual")  # take_02
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01")
    )
    return tmp_project


def _probed_take(project: Project, shot_id: str, duration_ms: int, tmp_path: Path,
                 tag: str) -> str:
    """Register a fake take that CARRIES a sidecar probe duration — the existing
    fact the board already renders in ``_take_meta`` (no new fact source)."""
    src = tmp_path / f"_probed_{tag}.mp4"
    src.write_bytes(b"fakevideo-" + tag.encode("ascii"))
    take = project.register_take(
        shot_id, src,
        TakeSidecar(provider="test", spec_hash="manual",
                    probe=ProbeInfo(duration_ms=duration_ms)),
    )
    return take.name


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe required for real boundary-still extraction",
)


# ------------------------------------------------- compare modes (wipe / diff)


def test_compare_carries_mode_toggles_wipe_and_diff(two_take_project):
    """The EXISTING compare wrap gains a 3-way mode toggle, the wipe slider and
    the difference canvas with its honest amplified label."""
    html = bd.render_board(two_take_project, serve=True)
    # mode toggle: side-by-side (existing grid) | wipe | difference
    assert 'data-cmpmode="sbs"' in html
    assert 'data-cmpmode="wipe"' in html
    assert 'data-cmpmode="diff"' in html
    # wipe: a range slider driving a CSS clip-path (client-side pixels only)
    assert 'data-wipe' in html and 'type="range"' in html
    # difference: a canvas overlay + brightness-gain slider, honestly labelled
    assert "<canvas" in html and "data-diffcanvas" in html
    assert "data-diffgain" in html
    assert "amplified ×4" in html          # the default gain, stated up front
    assert "not raw pixel deltas" in html  # the honesty clause


def test_compare_extends_the_existing_grid_not_a_rebuild(two_take_project):
    """Extend-not-rebuild pin: every marker of the PRE-EXISTING compare surface
    (synced side-by-side grid) is still present alongside the new modes."""
    html = bd.render_board(two_take_project, serve=True)
    for legacy in ('class="compare-wrap"', 'data-syncplay="1"', 'class="compare-grid"',
                   'class="cmp-cell"', 'class="cmp-meta"', "对比 compare",
                   'data-compare="1"'):
        assert legacy in html, f"pre-existing compare marker vanished: {legacy}"


def test_wipe_and_diff_need_two_media_takes_honest_note(two_take_project):
    """One take's media deleted → wipe/difference are honestly unavailable
    (a labelled note), never a silent broken stack."""
    take = two_take_project.get_take("S001", "take_02")
    take.media_path.unlink()
    html = bd.render_board(two_take_project, serve=True)
    assert 'data-cmpmode="wipe"' not in html
    assert 'data-cmpmode="diff"' not in html
    assert "need two takes with media" in html


# ------------------------------------------------------- frame-lock stepping


def test_frame_step_buttons_carry_exact_integer_period(two_take_project):
    """±1-frame buttons + the exact frame period on the wrap: an int project
    carries fps/1 (num/den), never a rounded millisecond value."""
    fps = two_take_project.load_config().fps
    html = bd.render_board(two_take_project, serve=True)
    assert 'data-framestep="-1"' in html and 'data-framestep="1"' in html
    assert f'data-fps-num="{fps}"' in html
    assert 'data-fps-den="1"' in html
    assert f"1/{fps} s" in html  # display honesty: the period as a fraction


def test_frame_step_rational_echo_carries_num_den(two_take_project):
    """R2 rational echo present on the timeline ⇒ the frame-step attributes
    carry the EXACT num/den (24000/1001), and the label says so."""
    timeline = Timeline.model_validate(
        {"fps": 24, "duration_ms": 1000, "edit_rate": {"num": 24000, "den": 1001}})
    two_take_project.save_timeline(timeline)
    html = bd.render_board(two_take_project, serve=True)
    assert 'data-fps-num="24000"' in html
    assert 'data-fps-den="1001"' in html
    assert "24000/1001" in html          # display honesty: the exact rational
    assert "1001/24000 s" in html        # the exact frame period as a fraction


# ------------------------------------------------- duration-mismatch honesty


def test_duration_mismatch_is_labelled_never_silent(tmp_project, add_shot, tmp_path):
    """Two takes whose EXISTING sidecar probes disagree on duration → a
    server-rendered fact label; equal durations → no fact label."""
    add_shot(tmp_project, "S001")
    a = _probed_take(tmp_project, "S001", 1000, tmp_path, "a")
    _probed_take(tmp_project, "S001", 2500, tmp_path, "b")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", a))
    html = bd.render_board(tmp_project, serve=True)
    assert "data-durfact" in html
    assert "1.000" in html and "2.500" in html  # both durations named


def test_equal_durations_render_no_mismatch_fact(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    a = _probed_take(tmp_project, "S001", 1000, tmp_path, "c")
    _probed_take(tmp_project, "S001", 1000, tmp_path, "d")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", a))
    html = bd.render_board(tmp_project, serve=True)
    assert "data-durfact" not in html


# ----------------------------------------------------------- boundary view


@pytest.fixture
def two_shot_project(tmp_project: Project, add_shot: Callable, make_take: Callable) -> Project:
    """Two shots in index order, each with a selected fake-media take."""
    for sid in ("S001", "S002"):
        add_shot(tmp_project, sid)
        make_take(tmp_project, sid, "manual")
        tmp_project.update_shot_raw(
            sid, lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01"))
    return tmp_project


def test_boundary_section_renders_adjacent_pairs(two_shot_project):
    """Consecutive shots (index order) get a boundary row: shot N's ending vs
    shot N+1's start. Fake media can't be decoded, so the slots are HONEST
    failure notes — the page never crashes."""
    html = bd.render_board(two_shot_project, serve=True)
    assert 'class="bnd-row"' in html
    assert "S001 → S002" in html
    assert "last frame" in html and "first frame" in html
    # fake bytes are unprobeable → an honest reason, never a broken <img>
    assert "frame extraction failed" in html
    assert 'class="bnd-missing"' in html


def test_boundary_honest_slot_no_selected_take(two_shot_project):
    two_shot_project.update_shot_raw(
        "S002", lambda d: d.setdefault("status", {}).__setitem__("selected_take", None))
    html = bd.render_board(two_shot_project, serve=True)
    assert "no selected take" in html


def test_boundary_honest_slot_media_gone(two_shot_project):
    take = two_shot_project.get_take("S002", "take_01")
    take.media_path.unlink()
    html = bd.render_board(two_shot_project, serve=True)
    # the honest reason must sit in a BOUNDARY slot (the take card's own
    # "no media on disk" nomedia div is a different, pre-existing surface)
    assert 'class="bnd-missing"' in html
    assert html.count("no media on disk") >= 2  # take card AND boundary slot


def test_boundary_absent_for_single_shot(two_take_project):
    """One shot ⇒ no adjacent pair ⇒ no boundary section at all."""
    html = bd.render_board(two_take_project, serve=True)
    assert 'class="bnd-row"' not in html


@needs_ffmpeg
def test_boundary_stills_extracted_and_served_e2e(tmp_path):
    """Real takes: the boundary stills are extracted through the EXISTING
    frames machinery into ``.manju/frames``, referenced as ``/media/...`` <img>
    tags, and actually served by the board server (endpoint smoke)."""
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(tmp_path / "边界样片", shots=3, clip_seconds=0.5,
                               with_bgm=False)
    project = Project(root)
    with running(project) as (base, server):
        # 载荷敏感对策: this GET runs REAL ffmpeg still-extraction server-side
        # for every boundary pair; httpx's default 5s deadline fired on a
        # contended windows-latest runner. Failure-detection latency only —
        # every assertion below is unchanged.
        html = httpx.get(base + "/", timeout=120.0).text
        assert 'class="bnd-row"' in html
        assert "S001 → S002" in html and "S002 → S003" in html
        assert 'src="/media/.manju/frames/' in html
        # difference toggle + honest amplified label ride along per pair
        assert 'data-bnddiff="1"' in html and "data-bndcanvas" in html
        assert "amplified ×4" in html
        # the referenced stills exist on disk in the EXISTING cache …
        rels = [seg.split('"', 1)[0] for seg in html.split('src="/media/')[1:]
                if seg.startswith(".manju/frames/")]
        assert rels, "no boundary <img> refs found"
        for rel in rels:
            assert (project.root / rel).is_file()
        # … and the server actually serves them (allowlisted preview surface)
        r = httpx.get(base + "/media/" + rels[0], timeout=120.0)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/")


# ------------------------------------------------- server media-route scope


def test_media_route_serves_frames_cache_but_truth_stays_403(two_take_project):
    """The one server change: ``.manju/frames/`` joins the preview allowlist.
    Truth files (state.sqlite, project.yaml, shot YAML) must STILL be 403."""
    cache = two_take_project.root / ".manju" / "frames"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "still.jpg").write_bytes(b"jpegbytes")
    with running(two_take_project) as (base, server):
        ok = httpx.get(base + "/media/.manju/frames/still.jpg")
        assert ok.status_code == 200 and ok.content == b"jpegbytes"
        for rel in ("project.yaml", "shots/S001.yaml", "events.jsonl",
                    ".manju/state.sqlite"):
            r = httpx.get(base + "/media/" + rel)
            assert r.status_code == 403, f"{rel} must never be served"


# ------------------------------------------------- static board stays pinned


def test_static_board_carries_none_of_it(two_shot_project, monkeypatch):
    """The static board is byte-for-byte what it always was: no compare modes,
    no frame stepping, no boundary view, no canvas, no amplified label."""
    monkeypatch.setattr(bd, "datetime", _FrozenDatetime)
    written = bd.generate_board(two_shot_project).read_text(encoding="utf-8")
    assert written == bd.render_board(two_shot_project, serve=False)
    for marker in ("data-cmpmode", "data-wipe", "data-framestep", "data-fps-num",
                   "bnd-row", "data-bnddiff", "data-diffcanvas", "amplified",
                   "<canvas"):
        assert marker not in written, f"static board must not carry {marker}"


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


def test_serve_render_writes_only_under_derived_runtime(two_shot_project):
    """NO new fact source: rendering the serve board (compare + boundary) may
    only ever WRITE under the disposable ``.manju/`` runtime area — every
    truth/source file outside it stays byte-identical, and nothing new appears
    outside it."""
    before = _tree_digest(two_shot_project.root, skip=(".manju/",))
    bd.render_board(two_shot_project, serve=True)
    after = _tree_digest(two_shot_project.root, skip=(".manju/",))
    assert after == before  # same files, same bytes — the board only READS truth
