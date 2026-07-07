"""On-demand, cached browser-safe previews for `manju gui` / the static board.

The real-world gap: the GUI and the static board embed hand project media
straight to ``<video src>`` / ``<audio src>`` tags, but browsers decode only a
narrow container/codec slice of what a project legitimately holds. A ProRes
``.mov``, a ``.mkv`` or a ``.flac`` master is a perfectly good *source* — and a
dead black box in the card. This module bridges that gap with a lazy transcode
to what every browser plays: H.264 + AAC in ``.mp4`` for video, AAC in ``.m4a``
for audio. Nothing is transcoded until somebody actually looks at it.

Where the cache lives — ``<root>/.manju/webpreview`` — is a §3 decision:
previews are derived, disposable artifacts. ``.manju/`` is never truth, may be
deleted or ``manju gc``-ed at any moment, and every entry here is rebuildable
from its source on the next request.

Cache entries are content-addressed by ``sha256(identity:mtime_ns:size)``,
where *identity* is the project-relative POSIX path when the source lives
inside the project root and the absolute POSIX path otherwise. The rationale:
a re-rendered ``proxy.mp4`` overwritten in place gets a new mtime/size, hence
a NEW cache name — no invalidation protocol, no stale playback. The orphaned
old entry is just garbage that :func:`gc_previews` sweeps.

The same cache also holds tiny poster thumbnails (``<digest>_thumb.jpg``,
JPEG, ≤320px wide) so the GUI's take wall can show every take at a glance,
not just the QC-postered selected one. :func:`ensure_thumb` grabs a single
frame — videos 0.5s in (first frame for shorter clips), images incl. animated
GIFs their first frame, audio none — under the exact same regime as previews:
(path, mtime, size)-keyed names, atomic tmp+``os.replace`` writes, total
functions that degrade to ``None``, and one :func:`gc_previews` sweep for the
whole directory.

Discipline: sources are strictly read-only here. Nothing in this module writes
under ``media/`` (media/imports is sacred — §3) or anywhere outside the
runtime dir. The module is also engine-core-free (no ``manju.core`` imports)
so the HTTP layer can use it without dragging in project machinery; the known
media suffixes below are a deliberate local copy of the
``core.container.MEDIA_EXTS`` notion.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

FFMPEG = "ffmpeg"

# What <video>/<audio>/<img> can be trusted to render natively, by suffix.
BROWSER_SAFE_VIDEO = {".mp4", ".webm", ".m4v"}
BROWSER_SAFE_AUDIO = {".mp3", ".wav", ".m4a"}
BROWSER_SAFE_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_BROWSER_SAFE = BROWSER_SAFE_VIDEO | BROWSER_SAFE_AUDIO | BROWSER_SAFE_IMAGE

# Local mirror of core.container.MEDIA_EXTS — duplicated ON PURPOSE so this
# module never imports the engine core (see module docstring).
MEDIA_EXTS = {
    ".mp4", ".mov", ".mkv", ".webm", ".m4v",
    ".png", ".jpg", ".jpeg",
    ".wav", ".mp3", ".m4a", ".flac",
}

# Audio-only sources get an AAC .m4a preview; everything else gets .mp4.
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac"}


def needs_preview(path: Path) -> bool:
    """Does this file need a browser-safe transcode before a browser can play it?

    ``False`` for browser-safe suffixes and for suffixes we do not recognise as
    media at all (nothing sensible to transcode); ``True`` only for known media
    suffixes a browser cannot be trusted with (.mkv, .mov, .flac, ...).
    """
    suffix = Path(path).suffix.lower()
    if suffix in _BROWSER_SAFE:
        return False
    return suffix in MEDIA_EXTS


def preview_cache_dir(project_root: Path) -> Path:
    """The preview cache: ``<root>/.manju/webpreview`` (§3 disposable runtime)."""
    return Path(project_root) / ".manju" / "webpreview"


def _source_digest(project_root: Path, source: Path) -> str:
    """The shared cache key: ``sha256(identity:mtime_ns:size)`` hex prefix.

    Identity is the project-relative POSIX path when the source is inside
    ``project_root``, else its absolute POSIX path. Raises ``OSError`` when
    the source cannot be stat'ed — the ``ensure_*`` wrappers stay total.
    """
    st = source.stat()
    root = Path(project_root).resolve()
    resolved = source.resolve()
    identity = (
        resolved.relative_to(root).as_posix()
        if resolved.is_relative_to(root)
        else resolved.as_posix()
    )
    key = f"{identity}:{st.st_mtime_ns}:{st.st_size}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def preview_path_for(project_root: Path, source: Path) -> Path:
    """Deterministic cache location for ``source`` (pure of ffmpeg; stats only).

    ``<cache>/<sha256(identity:mtime_ns:size)[:20]>.mp4`` (``.m4a`` for audio
    sources). Identity is the project-relative POSIX path when the source is
    inside ``project_root``, else its absolute POSIX path. Because mtime and
    size are baked into the name, an in-place overwrite maps to a fresh entry.

    Raises ``OSError`` if the source cannot be stat'ed; :func:`ensure_preview`
    is the total wrapper.
    """
    source = Path(source)
    digest = _source_digest(project_root, source)
    ext = ".m4a" if source.suffix.lower() in AUDIO_EXTS else ".mp4"
    return preview_cache_dir(project_root) / (digest + ext)


def thumb_path_for(project_root: Path, source: Path) -> Path:
    """Deterministic cache location for ``source``'s thumbnail (stats only).

    The exact key derivation of :func:`preview_path_for` with a
    ``_thumb.jpg`` suffix instead of the media extension, so a source's
    preview and thumb sit side by side in the cache and can never collide.

    Raises ``OSError`` if the source cannot be stat'ed; :func:`ensure_thumb`
    is the total wrapper.
    """
    source = Path(source)
    return preview_cache_dir(project_root) / (
        _source_digest(project_root, source) + "_thumb.jpg"
    )


def ensure_preview(project_root: Path, source: Path, *, timeout_s: float = 600.0) -> Path | None:
    """Return a browser-safe preview of ``source``, transcoding on first request.

    Cache hit → the existing file, untouched. Miss → transcode with ffmpeg
    (video: ≤720px-wide H.264+AAC faststart .mp4; audio: AAC .m4a) into a
    uuid-suffixed temp file, then ``os.replace`` it into place — atomic, so a
    reader can never see a half-written entry and two concurrent writers
    cannot corrupt each other (last writer wins; if the final path appears
    while we were transcoding, we simply serve it).

    Returns ``None`` — never raises — when ffmpeg is missing, the source is
    unreadable, or the transcode fails/times out; the caller degrades (e.g. to
    a poster image). Failures leave nothing behind in the cache. The source is
    only ever read.
    """
    source = Path(source)
    if shutil.which(FFMPEG) is None:
        return None
    try:
        dest = preview_path_for(project_root, source)
    except OSError:
        return None
    if dest.exists():
        return dest

    if dest.suffix == ".m4a":
        encode = ["-c:a", "aac", "-b:a", "128k"]
    else:
        encode = [
            # never upscale; -2 keeps the height even for yuv420p/H.264
            "-vf", "scale='min(720,iw)':-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            # a no-audio source is fine: without an explicit -map, ffmpeg
            # simply has no audio stream to apply -c:a to.
            "-c:a", "aac", "-b:a", "96k", "-ac", "2",
        ]
    # uuid suffix → concurrent transcodes of the same source never share a
    # temp file; the real extension stays last so ffmpeg picks the muxer.
    tmp = dest.with_name(f"{dest.stem}.{uuid.uuid4().hex}.tmp{dest.suffix}")
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), *encode, str(tmp),
    ]
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
        if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
            return None  # partial output is scrubbed by the finally below
        if dest.exists():
            return dest  # a concurrent writer finished first — equally valid
        os.replace(tmp, dest)
        return dest
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)  # no-op after a successful os.replace
        except OSError:
            pass


def ensure_thumb(project_root: Path, source: Path, *, timeout_s: float = 60.0) -> Path | None:
    """Return a ≤320px-wide JPEG thumbnail of ``source``, grabbing it lazily.

    Cache hit → the existing file, untouched. Miss → a single ffmpeg
    frame-grab (``-frames:v 1 -q:v 5``, ``scale='min(320,iw)':-2`` — nothing
    is ever upscaled) into a uuid-suffixed temp file, then ``os.replace`` into
    place: the same atomicity story as :func:`ensure_preview`.

    Videos (browser-safe ones included — a blank ``.mp4`` card wants a poster
    just as much) are sampled 0.5s in: a cheap fixed offset that skips
    leader/black frames without probing. A seek past EOF makes ffmpeg write
    nothing while still exiting 0, so when that happens — the clip is shorter
    than 0.5s — a second pass grabs the first frame instead. Images
    (.png/.jpg/.jpeg/.gif/.webp) take their first frame directly through the
    same scale/quality pipeline (animated GIF → first frame). Audio has no
    picture: ``None`` by design, and the caller shows its audio glyph.

    Returns ``None`` — never raises — when ffmpeg is missing, the source is
    unreadable/undecodable, or a grab fails/times out (``timeout_s`` bounds
    each ffmpeg pass); failures leave nothing behind in the cache. The source
    is only ever read.
    """
    source = Path(source)
    suffix = source.suffix.lower()
    if suffix in AUDIO_EXTS:
        return None  # no picture to grab — policy, not failure
    if shutil.which(FFMPEG) is None:
        return None
    try:
        dest = thumb_path_for(project_root, source)
    except OSError:
        return None
    if dest.exists():
        return dest

    grab = [
        "-vf", "scale='min(320,iw)':-2",  # cap width, keep AR, never upscale
        "-frames:v", "1", "-q:v", "5",
        "-update", "1",  # one image, not an image2 sequence
    ]
    # Stills have no 0.5s to seek to; videos try 0.5s, then frame zero.
    seeks: list[float | None] = [None] if suffix in BROWSER_SAFE_IMAGE else [0.5, None]
    tmp = dest.with_name(f"{dest.stem}.{uuid.uuid4().hex}.tmp{dest.suffix}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        for seek_s in seeks:
            seek = [] if seek_s is None else ["-ss", str(seek_s)]
            cmd = [
                FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
                *seek, "-i", str(source), *grab, str(tmp),
            ]
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout_s)
            if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
                continue  # incl. the rc-0-but-empty past-EOF seek
            if dest.exists():
                return dest  # a concurrent writer finished first — equally valid
            os.replace(tmp, dest)
            return dest
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            tmp.unlink(missing_ok=True)  # no-op after a successful os.replace
        except OSError:
            pass


def gc_previews(project_root: Path) -> int:
    """Delete every cached preview and thumbnail; return bytes freed.

    Thumbs live in the same directory as previews precisely so this stays a
    single sweep. Everything under the cache is rebuildable on demand (§3),
    so this is always safe — future ``manju gc`` wiring calls this.
    """
    cache = preview_cache_dir(project_root)
    if not cache.is_dir():
        return 0
    freed = 0
    for entry in cache.iterdir():
        try:
            if entry.is_file():
                size = entry.stat().st_size
                entry.unlink()
                freed += size
        except OSError:
            continue  # somebody else swept it first — fine
    return freed
