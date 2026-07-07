"""Tests for the browser-safe preview + thumbnail cache (media/webpreview).

Three layers:
  (a) pure functions — no ffmpeg: the needs_preview truth table, the
      deterministic (path, mtime, size)-keyed cache naming for previews and
      thumbs (same digest, ``_thumb.jpg`` suffix), gc (one sweep covers both
      — they share a directory), and the audio-has-no-thumb policy.
  (b) end-to-end (ffmpeg required): a real tiny lavfi source transcodes to a
      playable .mp4/.m4a, real .mp4/.png sources grab ≤320px JPEG thumbs
      (clips shorter than the 0.5s seek fall back to the first frame), second
      calls are served from the cache without re-running ffmpeg, and corrupt
      sources fail cleanly with zero litter.
  (c) deliberately NOT tested: ensure_preview on an already-browser-safe
      source. The contract is "caller gates with needs_preview" — the
      module does not special-case safe inputs. (No such gate exists for
      thumbs: browser-safe .mp4 wants a thumb too.)
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from manju.media.webpreview import (
    ensure_preview,
    ensure_thumb,
    gc_previews,
    needs_preview,
    preview_cache_dir,
    preview_path_for,
    thumb_path_for,
)

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg required"
)
requires_ffprobe = pytest.mark.skipif(
    shutil.which("ffprobe") is None, reason="ffprobe required"
)


# ------------------------------------------------------------ (a) pure logic


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # browser-safe → no transcode needed
        ("clip.mp4", False),
        ("clip.webm", False),
        ("clip.m4v", False),
        ("poster.png", False),
        ("voice.mp3", False),
        # known media a browser cannot play → transcode
        ("master.mkv", True),
        ("prores.mov", True),
        ("music.flac", True),
        ("SHOUTY.MKV", True),  # suffix matching is case-insensitive
        # unknown suffix → not media, nothing to transcode
        ("data.xyz", False),
        ("notes.txt", False),
    ],
)
def test_needs_preview_truth_table(name: str, expected: bool):
    assert needs_preview(Path("some/dir") / name) is expected


def test_preview_cache_dir_is_in_disposable_runtime(tmp_path: Path):
    assert preview_cache_dir(tmp_path) == tmp_path / ".manju" / "webpreview"


def test_preview_path_for_is_deterministic(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "media").mkdir(parents=True)
    src = root / "media" / "带宽测试.mkv"  # CJK paths are first-class (§14)
    src.write_bytes(b"x" * 32)

    p1 = preview_path_for(root, src)
    p2 = preview_path_for(root, src)
    assert p1 == p2
    assert p1.parent == preview_cache_dir(root)
    assert p1.suffix == ".mp4"
    assert len(p1.stem) == 20  # sha256 hex prefix


def test_preview_path_for_changes_when_mtime_changes(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "media").mkdir(parents=True)
    src = root / "media" / "proxy.mkv"
    src.write_bytes(b"take one")

    before = preview_path_for(root, src)
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    after = preview_path_for(root, src)

    assert after != before  # overwritten source → fresh cache entry
    assert after.parent == before.parent


def test_preview_path_for_audio_gets_m4a(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "music.flac"
    src.write_bytes(b"not-really-flac")
    assert preview_path_for(root, src).suffix == ".m4a"


def test_gc_previews_frees_bytes_and_empties_dir(tmp_path: Path):
    root = tmp_path / "proj"
    cache = preview_cache_dir(root)
    cache.mkdir(parents=True)
    (cache / "aaaa.mp4").write_bytes(b"a" * 100)
    (cache / "bbbb.m4a").write_bytes(b"b" * 50)
    (cache / "cccc_thumb.jpg").write_bytes(b"c" * 25)  # thumbs share the sweep

    assert gc_previews(root) == 175
    assert list(cache.iterdir()) == []
    assert gc_previews(root) == 0  # second sweep: nothing left


def test_gc_previews_missing_cache_dir_is_zero(tmp_path: Path):
    assert gc_previews(tmp_path / "never-created") == 0


def test_ensure_preview_missing_source_is_none(tmp_path: Path):
    # total function: unreadable/missing source degrades to None, never raises
    assert ensure_preview(tmp_path, tmp_path / "ghost.mkv") is None


def test_thumb_path_for_is_deterministic_and_shares_preview_key(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "media").mkdir(parents=True)
    src = root / "media" / "带宽测试.mkv"  # CJK paths are first-class (§14)
    src.write_bytes(b"x" * 32)

    t1 = thumb_path_for(root, src)
    t2 = thumb_path_for(root, src)
    assert t1 == t2
    assert t1.parent == preview_cache_dir(root)  # same dir → one gc sweep
    assert t1.name.endswith("_thumb.jpg")
    # same key derivation as the preview, only the suffix differs
    assert t1.name == preview_path_for(root, src).stem + "_thumb.jpg"


def test_thumb_path_for_changes_when_mtime_changes(tmp_path: Path):
    root = tmp_path / "proj"
    (root / "media").mkdir(parents=True)
    src = root / "media" / "proxy.mp4"
    src.write_bytes(b"take one")

    before = thumb_path_for(root, src)
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    after = thumb_path_for(root, src)

    assert after != before  # overwritten source → fresh thumb entry
    assert after.parent == before.parent


@pytest.mark.parametrize("name", ["tone.flac", "voice.mp3", "raw.wav", "mix.m4a"])
def test_ensure_thumb_audio_source_is_none(tmp_path: Path, name: str):
    # policy, not failure: audio has no picture — the caller shows a glyph.
    # The suffix decides, so this holds with or without ffmpeg installed.
    root = tmp_path / "proj"
    root.mkdir()
    src = root / name
    src.write_bytes(b"contents never inspected")

    assert ensure_thumb(root, src) is None

    cache = preview_cache_dir(root)
    leftovers = list(cache.iterdir()) if cache.exists() else []
    assert leftovers == []  # and it never touched the cache


def test_ensure_thumb_missing_source_is_none(tmp_path: Path):
    # total function, same contract as ensure_preview
    assert ensure_thumb(tmp_path, tmp_path / "ghost.mp4") is None


# ------------------------------------------------------- (b) ffmpeg end-to-end


def _tiny_mkv(dest: Path) -> None:
    """A real half-second silent H.264 .mkv — plays nowhere near a browser."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=0.5:size=128x128:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ],
        check=True,
        capture_output=True,
    )


@requires_ffmpeg
def test_ensure_preview_transcodes_then_serves_cache(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    src = tmp_path / "clip.mkv"  # outside the root → abspath identity branch
    _tiny_mkv(src)

    out = ensure_preview(root, src)
    assert out is not None
    assert out.suffix == ".mp4"
    assert out.parent == preview_cache_dir(root)
    assert out.exists() and out.stat().st_size > 0
    assert list(out.parent.iterdir()) == [out]  # exactly one entry, no temp litter

    first_mtime_ns = out.stat().st_mtime_ns
    again = ensure_preview(root, src)
    assert again == out
    assert again.stat().st_mtime_ns == first_mtime_ns  # cache hit, no re-transcode


@requires_ffmpeg
def test_ensure_preview_audio_source_becomes_m4a(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "tone.flac"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3", str(src),
        ],
        check=True,
        capture_output=True,
    )

    out = ensure_preview(root, src)
    assert out is not None
    assert out.suffix == ".m4a"
    assert out.exists() and out.stat().st_size > 0


@requires_ffmpeg
def test_ensure_preview_corrupt_source_none_and_no_litter(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    bad = tmp_path / "fake.mkv"
    bad.write_bytes(b"nonsense")

    assert ensure_preview(root, bad) is None

    cache = preview_cache_dir(root)
    leftovers = list(cache.iterdir()) if cache.exists() else []
    assert leftovers == []  # no cache entry AND no .tmp litter


def _tiny_mp4(dest: Path, *, duration: float, size: str) -> None:
    """A real H.264 .mp4 — browser-safe, yet its take card still wants a thumb."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=10",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ],
        check=True,
        capture_output=True,
    )


def _probe_width(path: Path) -> int:
    """Pixel width straight from ffprobe's JSON — no PIL dependency."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_streams", str(path),
        ],
        check=True,
        capture_output=True,
    ).stdout
    return int(json.loads(out)["streams"][0]["width"])


@requires_ffmpeg
@requires_ffprobe
def test_ensure_thumb_video_grabs_capped_jpg_then_serves_cache(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    src = tmp_path / "clip.mp4"  # outside the root → abspath identity branch
    _tiny_mp4(src, duration=2.0, size="640x360")  # 2s: the 0.5s seek lands mid-clip

    out = ensure_thumb(root, src)
    assert out is not None
    assert out.parent == preview_cache_dir(root)
    assert out.name.endswith("_thumb.jpg")
    assert out.read_bytes()[:3] == b"\xff\xd8\xff"  # real JPEG, not just the name
    assert _probe_width(out) == 320  # 640-wide source → capped at 320, AR kept
    assert list(out.parent.iterdir()) == [out]  # exactly one entry, no temp litter

    first_mtime_ns = out.stat().st_mtime_ns
    again = ensure_thumb(root, src)
    assert again == out
    assert again.stat().st_mtime_ns == first_mtime_ns  # cache hit, no re-grab


@requires_ffmpeg
@requires_ffprobe
def test_ensure_thumb_clip_shorter_than_seek_falls_back_to_first_frame(tmp_path: Path):
    # 0.2s clip: ffmpeg exits 0 on the past-EOF 0.5s seek yet writes nothing;
    # the first-frame fallback must cover it.
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "blip.mp4"
    _tiny_mp4(src, duration=0.2, size="128x128")

    out = ensure_thumb(root, src)
    assert out is not None
    assert out.read_bytes()[:3] == b"\xff\xd8\xff"
    assert _probe_width(out) == 128  # min(320,iw): never upscaled


@requires_ffmpeg
@requires_ffprobe
def test_ensure_thumb_image_source_gets_downscaled_jpg(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    src = root / "poster.png"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=duration=0.1:size=640x480:rate=1",
            "-frames:v", "1", str(src),
        ],
        check=True,
        capture_output=True,
    )

    out = ensure_thumb(root, src)
    assert out is not None
    assert out.name.endswith("_thumb.jpg")
    assert out.read_bytes()[:3] == b"\xff\xd8\xff"  # png in, jpg out
    assert _probe_width(out) == 320


@requires_ffmpeg
def test_ensure_thumb_corrupt_source_none_and_no_litter(tmp_path: Path):
    root = tmp_path / "proj"
    root.mkdir()
    bad = tmp_path / "fake.mp4"
    bad.write_bytes(b"nonsense")

    assert ensure_thumb(root, bad) is None

    cache = preview_cache_dir(root)
    leftovers = list(cache.iterdir()) if cache.exists() else []
    assert leftovers == []  # no cache entry AND no .tmp litter
