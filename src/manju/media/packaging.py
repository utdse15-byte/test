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
from ..core.models import CoverSpec, PackagingCard, PackagingSpec, ProjectConfig, TeaserSpec
from ..timeline.compiler import snap_to_frame_grid
from ..timeline.packaging import packaging_card_relpath
from .ffmpeg import MediaError, default_log, run_ffmpeg
from .render import _enc_params, _read_key_sidecar, final_content_key

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
    if not project.final_dir.exists():
        return None
    return project.newest_final_path()  # the one numeric resolver


def _stale_final_status(project: Project, final: Path) -> tuple[bool | None, str | None]:
    """Is the newest final stale relative to the CURRENT specs?

    `manju package` cuts the cover/teaser from whatever final is newest, even
    when the specs have moved on since that render — so the exports would show
    the OLD build. We recompute the current final content key exactly the way
    the render skip does — recompile the timeline from today's specs (as
    `manju explain` does), then media/render.final_content_key against the
    current captions.ass — and compare it to the final's own .key.json sidecar.
    A mismatch means the specs changed since the final was rendered.

    Returns ``(is_stale, message)``:
      - ``(False, None)`` — provably fresh, nothing to say.
      - ``(True, message)`` — PROVABLY stale (both keys computed and differ).
        Round-W (#56): this is the one case ``make_package`` now GATES on —
        the existing advisory computation becomes the refusal, not a second
        parallel check.
      - ``(None, None)`` — unknown (no compiled timeline, no sidecar on the
        final predating the key discipline, specs cannot be compiled, or key
        computation raised). Unknown NEVER gates — it stays advisory, same as
        before this round."""
    try:
        # Guard: only advise for a project that HAS a committed compiled
        # timeline (a real, built project); otherwise there is nothing to
        # call stale against — honestly unknown, not "fresh" (round-W #56:
        # unknown never gates, but it stays visible as a warning, not silence).
        if project.load_timeline() is None:
            return None, "无法判断 final 是否过期:项目没有 timeline.json(未 build 过?)"
        sidecar_key = _read_key_sidecar(final)
        if sidecar_key is None:  # final predates the key discipline → cannot judge
            return None, (
                "无法判断 final 是否过期:该 final 缺内容键 sidecar(在引入内容键校验之前渲染的)"
            )

        rules = project.load_rules()
        if rules.mode == "manual":
            # manual mode: timeline.json is human truth and IS what renders,
            # so the on-disk timeline is the current spec (explain's stance).
            effective = project.load_timeline()
        else:
            from ..media.probe import probe_duration_ms
            from ..timeline.compiler import compile_timeline, gather_compile_input

            effective = compile_timeline(gather_compile_input(project, probe_duration_ms))
        if effective is None:
            return None, "无法判断 final 是否过期:当前规格暂时编译不出时间线"

        ass = project.captions_dir / "captions.ass"
        current_key = final_content_key(
            project, effective, ass_file=ass if ass.exists() else None, target="final"
        )
        if current_key == sidecar_key:
            return False, None
        return True, (
            "newest final 相对当前规格已过期(stale,内容键不一致)— cover/teaser 会反映旧成片;"
            "请先 `manju build` 更新 final,或用 --force 明确跳过检查、用旧成片继续出封面/预告"
        )
    except Exception as exc:
        # unknown: never let key computation break packaging, but say WHY.
        return None, f"无法判断 final 是否过期:{' '.join(str(exc).split())[:200]}"


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


def cover_cache_key(config: ProjectConfig, final_hash: str, cover: CoverSpec) -> str:
    """THE cover.png content key: final bytes + cover spec + project resolution.

    Extracted so :mod:`manju.build.exportstatus` (the round-U export center) can
    judge cover freshness against the SAME key ``_make_cover`` writes into
    ``cover.key.json`` — one formula, never a parallel staleness path."""
    return cache_key(final_hash, "cover", cover.model_dump(), config.width, config.height)


def teaser_cache_key(config: ProjectConfig, final_hash: str, teaser: TeaserSpec) -> str:
    """THE teaser.mp4 content key: final bytes + teaser spec + final encoder
    params + fps. Shared with the export center for the same reason as
    :func:`cover_cache_key` (round-U)."""
    return cache_key(final_hash, "teaser", teaser.model_dump(), _enc_params("final"), config.fps)


# --------------------------------------------------------------- cover/teaser


def _extract_cover_frame(
    final: Path, dest: Path, *, frame_ms: int, width: int, height: int,
    fps: int, log: Log
) -> None:
    from .probe import probe_duration_ms

    at_ms = max(0, frame_ms)
    dur = probe_duration_ms(final)
    if dur:
        # Clamp inside the film so a too-late frame_ms still yields a frame.
        # dur-1ms can sit past the LAST frame's presentation time, so clamp to
        # one whole frame before the end (review round N finding).
        frame_len_ms = -(-1000 // max(1, fps))  # ceil(1000/fps)
        at_ms = min(at_ms, max(0, dur - frame_len_ms))
    run_ffmpeg(
        ["-ss", f"{at_ms / 1000.0:.3f}", "-i", str(final), "-frames:v", "1",
         "-vf", f"scale={width}:{height}:flags=bicubic,setsar=1", str(dest)],
        log=log,
    )
    if not dest.exists():  # a silent zero-frame extract must not be cached
        raise PackagingError(
            f"cover frame extraction produced no image at {frame_ms}ms — "
            "check packaging.yaml cover.frame_ms against the final's length"
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
    key = cover_cache_key(config, final_hash, cover)
    if not force and dest.exists() and _read_key(dest) == key:
        return dest, True
    if cover.mode == "card":
        _render_cover_card(cover, dest, width=config.width, height=config.height,
                           fps=config.fps, log=log)
    else:
        _extract_cover_frame(final, dest, frame_ms=cover.frame_ms,
                             width=config.width, height=config.height,
                             fps=config.fps, log=log)
    _write_key(dest, key, "cover")
    return dest, False


def _make_teaser(
    project: Project, final: Path, teaser: TeaserSpec, final_hash: str, *,
    force: bool, log: Log,
) -> tuple[Path, bool, str | None]:
    """Return (teaser_path, skipped, warning). Idempotent via teaser.key.json.
    The cut is re-encoded with the final encoder params; duration is
    frame-snapped so it lands within one frame of the ask.

    The window is validated against the final's real length: a from_ms at or
    past the end is a clean failure (ffmpeg would exit 0 with a streamless
    file, which must never be key-cached); a window overrunning the end is
    clamped with a warning instead of silently truncating."""
    from .probe import probe_duration_ms

    config = project.load_config()
    enc = _enc_params("final")
    dest = project.exports_dir / "packaging" / "teaser.mp4"
    dest.parent.mkdir(parents=True, exist_ok=True)
    key = teaser_cache_key(config, final_hash, teaser)
    if not force and dest.exists() and _read_key(dest) == key:
        return dest, True, None

    from_ms = max(0, teaser.from_ms)
    dur_ms = snap_to_frame_grid(teaser.duration_ms, config.fps)
    warning: str | None = None
    final_ms = probe_duration_ms(final)
    if final_ms:
        if from_ms >= final_ms:
            raise PackagingError(
                f"teaser.from_ms ({from_ms}ms) is at/past the end of the final "
                f"({final_ms}ms) — fix packaging.yaml or rebuild"
            )
        if from_ms + dur_ms > final_ms:
            dur_ms = snap_to_frame_grid(final_ms - from_ms, config.fps)
            warning = (
                f"teaser window overran the final ({final_ms}ms); "
                f"clamped to {dur_ms}ms from {from_ms}ms"
            )
    run_ffmpeg(
        ["-ss", f"{from_ms / 1000.0:.3f}", "-i", str(final),
         "-t", f"{dur_ms / 1000.0:.3f}", *enc, str(dest)],
        log=log,
    )
    if not dest.exists() or (probe_duration_ms(dest) or 0) <= 0:
        raise PackagingError(
            "teaser cut produced no playable output — check packaging.yaml "
            "teaser.from_ms/duration_ms against the final's length"
        )
    _write_key(dest, key, "teaser")
    return dest, False, warning


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
                    "teaser": None, "skipped": [], "warnings": []}

    # With an enabled intro the final's opening span IS the intro card, so a
    # frame-mode cover or a teaser window starting inside it captures the
    # placeholder card, not content. Legal, but rarely intended — advise.
    if packaging.intro.enabled:
        intro_ms = packaging.intro.duration_ms
        if packaging.cover.mode == "frame" and packaging.cover.frame_ms < intro_ms:
            result["warnings"].append(
                f"cover.frame_ms ({packaging.cover.frame_ms}ms) lands inside the "
                f"intro card (0–{intro_ms}ms) — the cover will show the intro, "
                "not content; raise frame_ms past the intro if unintended"
            )
        if packaging.teaser.enabled and packaging.teaser.from_ms < intro_ms:
            result["warnings"].append(
                f"teaser.from_ms ({packaging.teaser.from_ms}ms) starts inside the "
                f"intro card (0–{intro_ms}ms); raise from_ms if unintended"
            )

    # Staleness gate (round-W #56): the newest final may predate the current
    # specs, in which case the cover/teaser we are about to cut reflect the OLD
    # build. A PROVABLY stale final now refuses by default — the same
    # computation that used to be advisory-only becomes the gate; --force
    # keeps working exactly as before (bypasses the refusal, still notes it as
    # a warning). An UNKNOWN verdict (key computation could not run) is never
    # able to gate — it stays advisory, same as before this round.
    is_stale, stale_message = _stale_final_status(project, final)
    if is_stale is True:
        if not force:
            raise PackagingError(stale_message)
        result["warnings"].append(stale_message)
    elif is_stale is None and stale_message:
        result["warnings"].append(stale_message)

    cover_path, cover_skipped = _make_cover(
        project, final, packaging.cover, final_hash, force=force, log=log
    )
    result["cover"] = project.relpath(cover_path)
    if cover_skipped:
        result["skipped"].append("cover")

    if packaging.teaser.enabled:
        teaser_path, teaser_skipped, teaser_warning = _make_teaser(
            project, final, packaging.teaser, final_hash, force=force, log=log
        )
        result["teaser"] = project.relpath(teaser_path)
        if teaser_skipped:
            result["skipped"].append("teaser")
        if teaser_warning:
            result["warnings"].append(teaser_warning)

    return result
