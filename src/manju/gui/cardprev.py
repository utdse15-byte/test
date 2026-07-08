"""Live card-preview render for the /packaging page (round-T).

The packaging editor shows what an intro/outro/cover card will look like BEFORE
a build, by rendering the card PNG through the SAME card chain the compiler uses
(media/html_card.render_card_png when a headless Chromium exists, the drawtext
floor otherwise — the §8.4 fallback shape) into the disposable ``.manju/frames``
cache. Content-addressed by (text, template, size), so a re-open of the same
card is free and an edited card lands on a fresh entry.

This only PREVIEWS. The real cover/card assets are still written by
media/packaging.py at build time; nothing here is the source of truth.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..core.container import Project
from ..core.hashing import cache_key, short_hash
from ..media.frames import frames_cache_dir


def _preview_dims(project: Project) -> tuple[int, int]:
    """A small, aspect-correct preview size derived from the project config —
    fast to render, and shaped like the real frame so the safe area reads true."""
    try:
        config = project.load_config()
        w, h = int(config.width), int(config.height)
    except Exception:
        w, h = 1080, 1920
    pw = 480
    ph = max(2, round(pw * h / max(1, w)))
    if ph % 2:
        ph -= 1
    return pw, ph


def render_card_preview(
    project: Project, *, text: str, subtext: str = "", template: str = "chapter",
    preset: str = "",
) -> tuple[Path | None, bool]:
    """Render (or reuse) a card-preview PNG. Returns ``(path, cache_hit)``.

    ``path`` is ``None`` only when NEITHER renderer is available (no Chromium and
    no working ffmpeg) — the caller then serves a placeholder. The write is
    atomic (temp + ``os.replace``) so a cached entry is always a complete PNG.

    ``preset`` (round X, agent XG §C) is one of core.models.
    PACKAGING_CARD_PRESETS — forwarded to both renderers so the live preview
    matches ``media.packaging.render_packaging_card`` exactly. This cache is
    ``.manju/frames`` (disposable, §3) so folding ``preset`` into the key is a
    free re-key, never a byte-identity concern.
    """
    pw, ph = _preview_dims(project)
    body = f"{text}\n{subtext}".strip() if subtext else (text or "")
    key = short_hash(cache_key(body, template, pw, ph, "cardpreview", preset))
    cache = frames_cache_dir(project.root)
    dest = cache / f"card_{key}.png"
    if dest.exists():
        return dest, True
    cache.mkdir(parents=True, exist_ok=True)
    render_text = body or "（空卡片 empty card）"

    # 1) preferred: html_card screenshot (Chromium).
    try:
        from ..media.html_card import render_card_png

        with tempfile.NamedTemporaryFile(dir=cache, suffix=".png", delete=False) as tf:
            staged = Path(tf.name)
        try:
            render_card_png(render_text, staged, width=pw, height=ph,
                            template=template, preset=preset)
            os.replace(staged, dest)
            return dest, False
        finally:
            staged.unlink(missing_ok=True)
    except Exception:
        pass  # adapter wall (§2.5): fall through to the drawtext floor

    # 2) floor: a drawtext card's first frame (§8.4).
    try:
        from ..media.card import CARD_STYLE_PRESETS, caption_card
        from ..media.ffmpeg import run_ffmpeg

        config_fps = 24
        try:
            config_fps = int(project.load_config().fps)
        except Exception:
            pass
        floor_style = CARD_STYLE_PRESETS.get(preset, {})
        with tempfile.TemporaryDirectory(dir=cache) as tmp:
            mp4 = Path(tmp) / "card.mp4"
            caption_card(render_text, mp4, width=pw, height=ph, fps=config_fps,
                         duration_ms=400,
                         bg=floor_style.get("bg", "black"),
                         fontcolor=floor_style.get("fontcolor", "white"),
                         font_scale=floor_style.get("font_scale", 1.0))
            staged = Path(tmp) / "card.png"
            run_ffmpeg(["-i", str(mp4), "-frames:v", "1", str(staged)])
            if staged.is_file() and staged.stat().st_size > 0:
                os.replace(staged, dest)
                return dest, False
    except Exception:
        pass

    return None, False
