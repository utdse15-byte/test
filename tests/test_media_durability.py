"""Media write durability (OPTIMIZATION-ASSESSMENT §3 #2/#3).

Text truth always had crash safety (temp+rename, ``core/yamlio.py``); media
encodes used to go DIRECTLY to their durable destinations, so a killed ffmpeg
/ disk-full / Ctrl-C left a truncated file exactly where the pipeline trusts
files to be complete:

- ``_build_segment`` had ffmpeg encode straight into the content-addressed
  ``renders/segments/<key>.mp4`` and treated bare ``seg_path.exists()`` as a
  permanent cache hit — the truncated segment was silently embedded in every
  future final until someone wiped the cache.
- ``render_timeline`` encoded straight into the freshly minted
  ``renders/final/final_vN.mp4`` (a crash burned the version name on garbage
  that status/board surface as the latest final) and straight over
  ``renders/proxy/proxy.mp4`` (a crashed RE-encode destroyed the previous good
  proxy while its stale ``.key.json`` sidecar kept vouching for the truncated
  bytes — a later build whose content key matched the stale sidecar would
  "reuse" the corrupt proxy).

These are regression tests for the fix (each docstring names the pre-fix RED):
durable media paths only ever RECEIVE complete files (sibling dot-temp +
``os.replace`` — ``media/ffmpeg.py: atomic_output``); cache hits must pass an
ffprobe readability gate or be evicted and rebuilt (the policy for legacy
corrupt entries from before the fix); the key sidecar is written only after
the mp4 is in place; finals stay append-only even at swap time.

ffmpeg is real but every clip is tiny (256x448, 1s); crash simulation
monkeypatches the module-level ``run_ffmpeg`` bindings so the suite stays fast.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.hashing import short_hash
from manju.core.models import Timeline, TimelineTracks, TransitionSpec, VideoClip
from manju.media.ffmpeg import MediaError, atomic_output
from manju.media.probe import probe_duration_ms
from manju.media.render import final_content_key, render_timeline

pytestmark = [pytest.mark.ffmpeg,  # F43: the fast-loop deselector
              pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg required",
)]

W, H, FPS = 256, 448, 24
CLIP_MS = 1000


def _gen_clip(dest: Path, freq: int) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration={CLIP_MS / 1000}",
         "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={CLIP_MS / 1000}",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(dest)],
        check=True, capture_output=True,
    )


def _make_project(root: Path, *, fade: bool) -> tuple[Project, Timeline]:
    """A minimal renderable pair: a scaffolded project with two real tiny
    clips in media/imports/ and a hand-built 2-clip timeline over them (no
    shots/takes needed — render_timeline only resolves clip sources)."""
    project = Project.create(root, git_init=False)
    clips: list[VideoClip] = []
    for i, freq in enumerate((440, 880), start=1):
        media = project.imports_dir / f"clip{i}.mp4"
        _gen_clip(media, freq)
        clips.append(
            VideoClip(
                shot=f"S{i:03d}", take="take_01", source=project.relpath(media),
                start_ms=(i - 1) * CLIP_MS, duration_ms=CLIP_MS,
                transition_out=(
                    TransitionSpec(type="fade", duration_ms=250) if fade and i == 1 else None
                ),
            )
        )
    timeline = Timeline(
        fps=FPS, width=W, height=H, duration_ms=2 * CLIP_MS,
        tracks=TimelineTracks(video=clips),
    )
    return project, timeline


def _segment_paths(project: Project, timeline: Timeline, target: str) -> list[Path]:
    """The exact cache paths render.py will use — computed with the same
    key formula (``_segment_cache_key`` + ``_fade_params``), so these tests
    break loudly if the formula and the cache location ever drift."""
    from manju.media.render import _fade_params, _segment_cache_key

    clips = list(timeline.tracks.video)
    paths = []
    for i, clip in enumerate(clips):
        fade_in_ms, fade_out_ms = _fade_params(clips, i)
        key = _segment_cache_key(
            project, clip, width=timeline.width, height=timeline.height,
            fps=timeline.fps, target=target, fade_in_ms=fade_in_ms, fade_out_ms=fade_out_ms,
        )
        paths.append(project.segments_dir / f"{short_hash(key)}.mp4")
    return paths


@pytest.fixture(scope="module")
def faded(tmp_path_factory) -> tuple[Project, Timeline]:
    """Shared project whose timeline includes a fade, so the ``_apply_fades``
    write path (segment cache via a second encode) is exercised for real."""
    return _make_project(tmp_path_factory.mktemp("durability") / "雨夜耐久", fade=True)


@pytest.fixture()
def fresh(tmp_path) -> tuple[Project, Timeline]:
    """Fadeless per-test project for crash simulations (pristine cache/dirs)."""
    return _make_project(tmp_path / "崩溃现场", fade=False)


# ---------------------------------------------------------- segment cache


def test_corrupt_cached_segment_is_evicted_and_rebuilt(faded):
    """Pre-fix RED: ``_build_segment`` treated bare ``seg_path.exists()`` as a
    permanent cache hit, so garbage planted at the exact cache path (what a
    killed ffmpeg used to leave there) was fed to the concat demuxer — the
    render failed hard, and a subtler truncation shipped silently corrupt
    finals forever. Policy under the fix: a hit must be ffprobe-readable
    (truncated MP4s have no moov atom and are not); unreadable entries are
    evicted, logged, and rebuilt from source."""
    project, timeline = faded
    seg = _segment_paths(project, timeline, "proxy")[0]
    seg.write_bytes(b"\x00\x00\x00\x18ftypmp42 TRUNCATED-BY-SIMULATED-CRASH")

    out = render_timeline(project, timeline, target="proxy")

    assert out == project.proxy_dir / "proxy.mp4"
    assert probe_duration_ms(out) is not None
    # the poisoned entry was evicted and rebuilt into a readable segment
    assert probe_duration_ms(seg) is not None
    assert b"TRUNCATED-BY-SIMULATED-CRASH" not in seg.read_bytes()[:256]


def test_valid_cached_segments_survive_the_gate_untouched(faded):
    """The integrity gate must not break incremental rendering: readable
    cached segments stay byte-for-byte untouched (mtime preserved) across a
    forced re-render — eviction fires only on unreadable entries."""
    project, timeline = faded
    render_timeline(project, timeline, target="proxy")
    before = {p.name: p.stat().st_mtime_ns for p in project.segments_dir.glob("*.mp4")}
    assert before, "expected cached segments after a proxy render"

    render_timeline(project, timeline, target="proxy", force=True)

    after = {p.name: p.stat().st_mtime_ns for p in project.segments_dir.glob("*.mp4")}
    assert after == before, "a valid cache hit was rewritten or evicted"


def test_crashed_segment_encode_leaves_no_partial_in_cache(fresh, monkeypatch):
    """Pre-fix RED: normalize encoded straight into the cache path, so this
    simulated mid-write crash left a partial file AT the content-addressed
    path — and the retry then 'reused' it as a cache hit and failed (or worse,
    silently embedded it). Under the fix the cache path only ever receives
    complete files via os.replace: after the crash the segments dir holds
    nothing (no partial, no leaked temp), and an unpatched retry rebuilds from
    scratch and succeeds."""
    project, timeline = fresh
    import manju.media.normalize as normalize_mod

    def crash(args, *, log=None):
        Path(str(args[-1])).write_bytes(b"PARTIAL-SEGMENT")  # ffmpeg died mid-file
        raise MediaError("simulated: ffmpeg killed mid-encode (disk full)")

    with monkeypatch.context() as m:
        m.setattr(normalize_mod, "run_ffmpeg", crash)
        with pytest.raises(MediaError, match="simulated"):
            render_timeline(project, timeline, target="proxy")

    debris = sorted(p.name for p in project.segments_dir.glob("*"))
    assert debris == [], f"crash left debris at the cache: {debris}"
    assert not (project.proxy_dir / "proxy.mp4").exists()

    out = render_timeline(project, timeline, target="proxy")  # clean retry
    assert probe_duration_ms(out) is not None
    assert all(probe_duration_ms(s) is not None for s in project.segments_dir.glob("*.mp4"))


# ------------------------------------------------------------ final / proxy


def test_interrupted_final_leaves_no_partial_and_no_sidecar(fresh, monkeypatch):
    """Pre-fix RED: the composition pass encoded straight into the freshly
    minted renders/final/final_vN.mp4, so this crash left a truncated final
    there — surfaced by status/board as the project's latest, with only the
    missing sidecar keeping FIX-A from reusing it. Under the fix a failed
    final leaves renders/final/ with no final_v*, no leaked temp and no
    .key.json (the sidecar is written only AFTER os.replace lands the mp4),
    and the version number is not burned."""
    project, timeline = fresh
    import manju.media.render as render_mod

    expected_next = project.next_final_path()
    real_run = render_mod.run_ffmpeg

    def crash_final_pass(args, *, log=None):
        argv = [str(a) for a in args]
        if "[vout]" in argv:  # the ④⑤⑥ composition pass that writes the final
            Path(argv[-1]).write_bytes(b"\x00\x00\x00\x18ftypisom PARTIAL-FINAL")
            raise MediaError("simulated: ffmpeg killed writing the final")
        real_run(args, log=log)

    with monkeypatch.context() as m:
        m.setattr(render_mod, "run_ffmpeg", crash_final_pass)
        with pytest.raises(MediaError, match="simulated"):
            render_timeline(project, timeline, target="final")

    debris = sorted(p.name for p in project.final_dir.glob("*"))
    assert debris == [], f"failed final left debris: {debris}"
    assert project.next_final_path() == expected_next, "failed render burned a version number"
    # the crash was final-pass only: segments really rendered, and completely
    segs = list(project.segments_dir.glob("*.mp4"))
    assert segs and all(probe_duration_ms(s) is not None for s in segs)


def test_interrupted_proxy_reencode_preserves_previous_good_proxy(fresh, monkeypatch):
    """Pre-fix RED twice over: ``ffmpeg -y`` re-encoded the proxy IN PLACE, so
    a crashed re-encode (a) destroyed the previous good proxy.mp4 and (b) left
    the old .key.json vouching for the truncated bytes — a later build whose
    content key matched the stale sidecar reused the corrupt proxy as an
    up-to-date hit. Under the fix a failed re-encode leaves the old
    proxy + sidecar pair intact, coherent, and still honestly reusable."""
    project, timeline = fresh
    import manju.media.render as render_mod

    proxy = render_timeline(project, timeline, target="proxy")
    good_bytes = proxy.read_bytes()
    sidecar = proxy.with_suffix(".key.json")
    good_sidecar = sidecar.read_text(encoding="utf-8")

    changed = timeline.model_copy(deep=True)
    changed.tracks.video = list(reversed(changed.tracks.video))  # new content key
    for start_ms, clip in zip((0, CLIP_MS), changed.tracks.video):
        clip.start_ms = start_ms

    real_run = render_mod.run_ffmpeg

    def crash_final_pass(args, *, log=None):
        argv = [str(a) for a in args]
        if "[vout]" in argv:
            Path(argv[-1]).write_bytes(b"PARTIAL-PROXY")
            raise MediaError("simulated: ffmpeg killed re-encoding the proxy")
        real_run(args, log=log)

    with monkeypatch.context() as m:
        m.setattr(render_mod, "run_ffmpeg", crash_final_pass)
        with pytest.raises(MediaError, match="simulated"):
            render_timeline(project, changed, target="proxy")

    assert proxy.read_bytes() == good_bytes, "crash destroyed the previous good proxy"
    assert sidecar.read_text(encoding="utf-8") == good_sidecar, "sidecar no longer coherent"
    extras = sorted(p.name for p in project.proxy_dir.glob("*") if p not in (proxy, sidecar))
    assert extras == [], f"crash left debris in renders/proxy: {extras}"

    # the surviving pair is still honestly reusable for the ORIGINAL content
    mtime = proxy.stat().st_mtime_ns
    again = render_timeline(project, timeline, target="proxy")
    assert again == proxy
    assert proxy.stat().st_mtime_ns == mtime, "reuse should not re-encode the proxy"


def test_final_success_writes_mp4_then_sidecar_and_leaves_no_temps(faded):
    """Ordering by construction: the sidecar is written strictly after
    os.replace lands the mp4 (the failed-render test proves the converse: no
    mp4 → no sidecar). On success both exist, the sidecar key matches
    ``final_content_key``, and no ``.tmp-`` debris remains under renders/.
    Pre-fix the ordering happened to hold, but nothing enforced completeness
    of the mp4 the sidecar described."""
    project, timeline = faded
    out = render_timeline(project, timeline, target="final")

    assert out.parent == project.final_dir
    assert probe_duration_ms(out) is not None
    data = json.loads(out.with_suffix(".key.json").read_text(encoding="utf-8"))
    assert data["final_key"] == final_content_key(
        project, timeline, ass_file=None, target="final"
    )
    assert data["target"] == "final"
    temps = [p for p in (project.root / "renders").rglob("*") if ".tmp-" in p.name]
    assert temps == [], f"leaked temp files: {temps}"


# ------------------------------------------------- atomic_output primitive


def test_atomic_output_failure_leaves_nothing(tmp_path):
    """The primitive itself: on failure the temp is unlinked and the
    destination never appears (pre-fix equivalent: ffmpeg's own partial file
    stayed at the destination)."""
    dest = tmp_path / "seg.mp4"
    with pytest.raises(RuntimeError, match="boom"):
        with atomic_output(dest) as tmp:
            tmp.write_bytes(b"partial")
            raise RuntimeError("boom")
    assert list(tmp_path.iterdir()) == []


def test_atomic_output_refuses_to_clobber_existing_final(tmp_path):
    """Append-only preserved at swap time: if something occupies the
    destination by replace time (two builds minting the same final_vN — the
    concurrent-writer race), the swap raises instead of silently overwriting
    (pre-fix ``ffmpeg -y`` clobbered it). The existing file survives untouched
    and the temp is cleaned up."""
    dest = tmp_path / "final_v3.mp4"
    dest.write_bytes(b"THE EXISTING FINAL")
    with pytest.raises(MediaError, match="append-only"):
        with atomic_output(dest, must_not_exist=True) as tmp:
            tmp.write_bytes(b"racing build output")
    assert dest.read_bytes() == b"THE EXISTING FINAL"
    assert list(tmp_path.iterdir()) == [dest]


def test_atomic_temp_name_is_muxer_safe_and_invisible_to_final_globs(tmp_path):
    """ffmpeg picks the muxer from the LAST extension, and every final/status
    lookup globs ``final_v*.mp4`` — the in-flight temp must keep ``.mp4`` last
    (or the encode would fail to pick a format) while never matching those
    globs (a visible temp would surface as the project's 'latest final'
    mid-encode, and ``next_final_path`` could collide with it)."""
    dest = tmp_path / "final_v1.mp4"
    with atomic_output(dest) as tmp:
        assert tmp.parent == dest.parent  # same directory → same fs → atomic replace
        assert tmp.suffix == ".mp4"  # muxer inference intact
        assert tmp.name.startswith(".")  # hidden from name-anchored globs
        tmp.write_bytes(b"x")
        assert list(tmp_path.glob("final_v*.mp4")) == []
    assert dest.read_bytes() == b"x"
    assert list(tmp_path.iterdir()) == [dest]
