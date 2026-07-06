"""Packaging kit media (round-N §13-14): intro/outro card assets, cover, teaser.

Two jobs, both reusing the existing card machinery and the proxy/final content-
key discipline:

- ``ensure_packaging_cards``: render the content-addressed intro/outro card MP4s
  the compiler points at, BEFORE the render pass. Same spec → the file already
  exists → reuse (never re-render); edited text → a new hash → a new file
  (append-only, §3). html_card when Chromium is available, drawtext floor
  otherwise — the same fallback shape as the caption_card provider (§8.4).
- ``make_package``: cut the cover PNG and teaser MP4 out of the current final,
  each guarded by a ``.key.json`` sidecar (hash of the final's bytes + the
  relevant spec) so an unchanged input is a skip, exactly like the proxy/final
  key sidecars in media/render.py. ``--force`` bypasses the skip.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from ..core.container import Project
from ..core.hashing import cache_key, hash_file
from ..core.models import CoverSpec, PackagingCard, PackagingSpec, TeaserSpec
from ..timeline.compiler import snap_to_frame_grid
from ..timeline.packaging import packaging_card_relpath
from .ffmpeg import MediaError, default_log, run_ffmpeg
from .render import _enc_params

Log = Callable[[str], None] | None


class PackagingError(RuntimeError):
    """A packaging step could not run (e.g. no final to cut from)."""


# ---------------------------------------------------------- card asset render


def _card_text(card: PackagingCard | CoverSpec) -> str:
    text = card.text or ""
    subtext = getattr(card, "subtext", "") or ""
    if subtext:
        return f"{text}\n{subtext}" if text else subtext
    return text


def render_packaging_card(
    card: PackagingCard, dest: Path, *, width: int, height: int, fps: int, log: Log = None
) -> Path:
    """Render one intro/outro card to ``dest`` (MP4). html_card preferred,
    drawtext floor — the caption_card fallback shape (§8.4). Duration is snapped
    to the frame grid the way the compiler snaps clip durations (FIX-B)."""
    duration_ms = snap_to_frame_grid(card.duration_ms, fps)
    text = _card_text(card)
    try:
        from .html_card import html_card_video

        return html_card_video(
            text, dest, width=width, height=height, fps=fps,
            duration_ms=duration_ms, template=card.template, log=log,
        )
    except Exception:  # adapter wall (§2.5): no/failed Chromium → drawtext floor
        from .card import caption_card

        return caption_card(
            text, dest, width=width, height=height, fps=fps,
            duration_ms=duration_ms, log=log,
        )


def ensure_packaging_cards(
    project: Project, packaging: PackagingSpec, *, log: Log = None
) -> list[str]:
    """Render any missing intro/outro card asset; return the project-relative
    paths actually rendered (an existing asset is reused, never re-rendered).
    Rendered into a temp file and atomically moved so a half-written asset can
    never masquerade as complete to the render pass."""
    if log is None:
        log = default_log(project.root, "packaging")
    config = project.load_config()
    rendered: list[str] = []
    for kind, card in (("intro", packaging.intro), ("outro", packaging.outro)):
        if not card.enabled:
            continue
        rel = packaging_card_relpath(kind, card, config.width, config.height, config.fps)
        dest = project.resolve(rel)
        if dest.exists():
            continue  # content-addressed reuse (§3 append-only)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
            staged = Path(tmp) / dest.name
            render_packaging_card(
                card, staged, width=config.width, height=config.height,
                fps=config.fps, log=log,
            )
            os.replace(staged, dest)
        rendered.append(rel)
    return rendered


# ------------------------------------------------------------- final locating


def newest_final(project: Project) -> Path | None:
    import re

    if not project.final_dir.exists():
        return None
    finals = [
        (int(m.group(1)), p)
        for p in project.final_dir.glob("final_v*.mp4")
        if (m := re.match(r"final_v(\d+)$", p.stem))
    ]
    return max(finals, key=lambda t: t[0])[1] if finals else None


# ----------------------------------------------------------- key sidecars


def _read_key(media_path: Path) -> str | None:
    sidecar = media_path.with_suffix(".key.json")
    if not sidecar.exists():
        return None
    try:
        return str(json.loads(sidecar.read_text(encoding="utf-8")).get("key", "")) or None
    except (json.JSONDecodeError, OSError):
        return None


def _write_key(media_path: Path, key: str, kind: str) -> None:
    media_path.with_suffix(".key.json").write_text(
        json.dumps(
            {"key": key, "kind": kind,
             "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
            ensure_ascii=False, indent=2,
        ) + "\n",
        encoding="utf-8",
    )


# --------------------------------------------------------------- cover/teaser


def _extract_cover_frame(
    final: Path, dest: Path, *, frame_ms: int, width: int, height: int, log: Log
) -> None:
    from .probe import probe_duration_ms

    at_ms = max(0, frame_ms)
    dur = probe_duration_ms(final)
    if dur:  # clamp inside the film so a too-late frame_ms still yields a frame
        at_ms = min(at_ms, max(0, dur - 1))
    run_ffmpeg(
        ["-ss", f"{at_ms / 1000.0:.3f}", "-i", str(final), "-frames:v", "1",
         "-vf", f"scale={width}:{height}:flags=bicubic,setsar=1", str(dest)],
        log=log,
    )


def _render_cover_card(
    cover: CoverSpec, dest: Path, *, width: int, height: int, fps: int, log: Log
) -> None:
    """Card-mode cover PNG at project resolution: html_card screenshot when
    Chromium is available, else a drawtext card's first frame (§8.4 floor)."""
    text = _card_text(cover)
    try:
        from .html_card import render_card_png

        render_card_png(text, dest, width=width, height=height, template=cover.template)
        return
    except Exception:  # drawtext floor: render a 1s card, pull frame 0
        from .card import caption_card

        with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
            card_mp4 = Path(tmp) / "cover.mp4"
            caption_card(text, card_mp4, width=width, height=height, fps=fps,
                         duration_ms=1000, log=log)
            run_ffmpeg(["-i", str(card_mp4), "-frames:v", "1", str(dest)], log=log)


def _make_cover(
    project: Project, final: Path, cover: CoverSpec, final_hash: str, *,
    force: bool, log: Log,
) -> tuple[Path, bool]:
    """Return (cover_path, skipped). Idempotent via cover.key.json."""
    config = project.load_config()
    dest = project.exports_dir / "packaging" / "cover.png"
    dest.parent.mkdir(parents=True, exist_ok=True)
    key = cache_key(final_hash, "cover", cover.model_dump(), config.width, config.height)
    if not force and dest.exists() and _read_key(dest) == key:
        return dest, True
    if cover.mode == "card":
        _render_cover_card(cover, dest, width=config.width, height=config.height,
                           fps=config.fps, log=log)
    else:
        _extract_cover_frame(final, dest, frame_ms=cover.frame_ms,
                             width=config.width, height=config.height, log=log)
    _write_key(dest, key, "cover")
    return dest, False


def _make_teaser(
    project: Project, final: Path, teaser: TeaserSpec, final_hash: str, *,
    force: bool, log: Log,
) -> tuple[Path, bool]:
    """Return (teaser_path, skipped). Idempotent via teaser.key.json. The cut is
    re-encoded with the final encoder params; duration is frame-snapped so it
    lands within one frame of the ask."""
    config = project.load_config()
    enc = _enc_params("final")
    dest = project.exports_dir / "packaging" / "teaser.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    key = cache_key(final_hash, "teaser", teaser.model_dump(), enc, config.fps)
    if not force and dest.exists() and _read_key(dest) == key:
        return dest, True
    dur_ms = snap_to_frame_grid(teaser.duration_ms, config.fps)
    run_ffmpeg(
        ["-ss", f"{max(0, teaser.from_ms) / 1000.0:.3f}", "-i", str(final),
         "-t", f"{dur_ms / 1000.0:.3f}", *enc, str(dest)],
        log=log,
    )
    _write_key(dest, key, "teaser")
    return dest, False


def make_package(project: Project, *, force: bool = False, log: Log = None) -> dict:
    """Produce cover (+ teaser when enabled) from the current final.

    Requires a final render — an absent one is a clean failure pointing at
    ``manju build``. Returns a dict of project-relative paths plus the list of
    steps skipped by a content-key match."""
    if log is None:
        log = default_log(project.root, "packaging")
    final = newest_final(project)
    if final is None:
        raise PackagingError(
            "no final render to package — run `manju build` first (§13-14)"
        )
    packaging = project.load_packaging()
    final_hash = hash_file(final)

    result: dict = {"final": project.relpath(final), "cover": None,
                    "teaser": None, "skipped": []}

    cover_path, cover_skipped = _make_cover(
        project, final, packaging.cover, final_hash, force=force, log=log
    )
    result["cover"] = project.relpath(cover_path)
    if cover_skipped:
        result["skipped"].append("cover")

    if packaging.teaser.enabled:
        teaser_path, teaser_skipped = _make_teaser(
            project, final, packaging.teaser, final_hash, force=force, log=log
        )
        result["teaser"] = project.relpath(teaser_path)
        if teaser_skipped:
            result["skipped"].append("teaser")

    return result
