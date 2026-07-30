"""html_render — HTML+CSS → deterministic MP4 (§2.5 P1 plugin slot).

The HyperFrames idea implemented against the locally available headless
Chromium: a styled HTML card is screenshotted at exact project resolution and
looped into a video with silent audio. Used by the caption_card provider as
its preferred renderer (M3: caption_card 优先用 HTML 渲染实现,drawtext 兜底)
and available for title/character/chapter cards and packaging.

Adapter wall (§2.5): no Chromium -> callers fall back to drawtext; nothing
here ever crashes a build.
"""

from __future__ import annotations

import glob
import html
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .ffmpeg import MediaError, run_ffmpeg

_CHROMIUM_CANDIDATES = (
    "chromium", "chromium-browser", "google-chrome", "headless_shell",
)

# W2 (§4.5/§5.6): os.name is process-constant; a module flag keeps the Windows
# path list patchable in tests without touching the global ``os`` module.
_IS_WINDOWS = os.name == "nt"

_EDGE_PATH_CANDIDATES = ("msedge", "microsoft-edge", "microsoft-edge-stable")
# Standard Windows install roots, checked relative to the env vars so a test
# (or an unusual install) can redirect them. Edge ships per-machine under
# Program Files and per-user under LOCALAPPDATA.
_EDGE_WINDOWS_SUFFIX = ("Microsoft", "Edge", "Application", "msedge.exe")


def find_edge() -> Path | None:
    """Locate Microsoft Edge (the board's §5.6 ``--app`` host) — PATH first,
    then the standard Windows install roots. ``None`` degrades honestly: the
    caller falls back to the default browser, never guesses."""
    for name in _EDGE_PATH_CANDIDATES:
        found = shutil.which(name)
        if found:
            return Path(found)
    if _IS_WINDOWS:
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            base = os.environ.get(env)
            if not base:
                continue
            candidate = Path(base).joinpath(*_EDGE_WINDOWS_SUFFIX)
            if candidate.exists():
                return candidate
    return None


def find_chromium() -> Path | None:
    """Locate a headless-capable Chromium: CHROME_BIN, PATH, then the
    Playwright browsers dir (PLAYWRIGHT_BROWSERS_PATH, default /opt/pw-browsers)."""
    env_bin = os.environ.get("CHROME_BIN")
    if env_bin and Path(env_bin).exists():
        return Path(env_bin)
    for name in _CHROMIUM_CANDIDATES:
        found = shutil.which(name)
        if found:
            return Path(found)
    browsers_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    patterns = (
        f"{browsers_dir}/chromium",
        f"{browsers_dir}/chromium-*/chrome-linux/chrome",
        f"{browsers_dir}/chromium_headless_shell-*/chrome-linux/headless_shell",
    )
    for pattern in patterns:
        for candidate in sorted(glob.glob(pattern)):
            p = Path(candidate)
            if p.is_file() and os.access(p, os.X_OK):
                return p
    return None


def html_available() -> bool:
    return find_chromium() is not None


CARD_TEMPLATES = {
    # caption/dialogue card: dark gradient, centered text.
    # Canvas background is the SOLID {bg_edge}, the real {bg} paints on
    # body::before{{position:fixed;inset:0}}: headless Chromium here lays out
    # a viewport 87px SHORTER than --window-size while screenshotting at full
    # window size, and nothing rasterizes beyond the viewport — the excess
    # rows can only ever show the canvas BASE color, which a solid provides
    # and a gradient cannot (gradient canvas => white band). On a healthy
    # build ::before covers 100% and {bg_edge} never shows.
    # The width cap sits on .stack (the flex ITEM, resolved against the
    # definite body) — on an inner block the cyclic percentage is ignored
    # during intrinsic sizing and re-applied at layout, which broke the tail
    # of EVERY one-line caption (「谢谢。」→「谢/谢。」).
    "caption": """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;
background:{bg_edge};
display:flex;align-items:center;justify-content:{justify};
font-family:"Noto Sans CJK SC","WenQuanYi Zen Hei","PingFang SC",sans-serif}}
body::before{{content:"";position:fixed;inset:0;background:{bg};z-index:-1}}
.stack{{max-width:82%}}
.card{{color:{text_color};font-size:{font_px}px;font-weight:600;text-align:center;
line-height:1.65;letter-spacing:.04em;
text-shadow:0 2px 14px rgba(0,0,0,.85)}}
.rule{{width:56px;height:3px;margin:28px auto 0;border-radius:2px;
background:{accent};opacity:.85}}</style></head>
<body><div class="stack"><div class="card">{text}</div><div class="rule"></div></div></body></html>""",
    # chapter/title card: bigger, with a kicker line (.wrap is already the
    # flex item, so its max-width percentage was never part of the wrap bug)
    "chapter": """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;
background:{bg_edge};
display:flex;align-items:center;justify-content:{justify};
font-family:"Noto Sans CJK SC","WenQuanYi Zen Hei","PingFang SC",sans-serif}}
body::before{{content:"";position:fixed;inset:0;background:{bg};z-index:-1}}
.wrap{{text-align:center;max-width:84%}}
.kicker{{color:{accent};font-size:{kicker_px}px;letter-spacing:.5em;
text-indent:.5em;margin-bottom:30px}}
.title{{color:{text_color};font-size:{font_px}px;font-weight:700;line-height:1.5;
letter-spacing:.06em;text-shadow:0 3px 18px rgba(0,0,0,.9)}}</style></head>
<body><div class="wrap"><div class="kicker">CHAPTER</div>
<div class="title">{text}</div></div></body></html>""",
}

# Round X (agent XG): each base template's HISTORICAL hard-coded look, kept as
# data so the "" preset (core/models.py:PACKAGING_CARD_PRESETS[0]) formats the
# SAME CSS values as before this table existed — byte-identical HTML/PNG/MP4
# for a card that never opts into a preset.
_TEMPLATE_DEFAULTS: dict[str, dict[str, Any]] = {
    # bg_edge: the SOLID canvas base color behind the bg — the tone at the
    # gradient's frame-exit edge, declared as data (never parsed out of the
    # CSS string). Solid bgs simply repeat themselves.
    "caption": {"bg": "linear-gradient(165deg,#0d1117 0%,#161f2e 55%,#1d2a3a 100%)",
                "bg_edge": "#1d2a3a",
                "text_color": "#f5f6f8", "accent": "#4a90d9",
                "font_scale": 1.0, "justify": "center"},
    "chapter": {"bg": "radial-gradient(120% 90% at 50% 20%,#1b2838 0%,#0b1017 70%)",
                "bg_edge": "#0b1017",
                "text_color": "#ffffff", "accent": "#7fa6c9",
                "font_scale": 1.0, "justify": "center"},
}

# Named preset combos (font scale / bg colour-or-gradient / text position) —
# the SAME preset names as media/card.py:CARD_STYLE_PRESETS (that table is the
# solid-colour approximation the drawtext floor uses; this one can spend CSS
# gradients since Chromium renders it). Applied identically across templates,
# so switching template never resets the chosen preset's look.
CARD_STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "mono_black": {"bg": "#000000", "bg_edge": "#000000",
                   "text_color": "#ffffff", "accent": "#8a93a3",
                   "font_scale": 1.0, "justify": "center"},          # 简约黑
    "white_big": {"bg": "#ffffff", "bg_edge": "#ffffff",
                  "text_color": "#14161b", "accent": "#4b5162",
                  "font_scale": 1.32, "justify": "flex-end"},        # 白底大字
    "warm_gradient": {"bg": "linear-gradient(160deg,#ff7a45 0%,#ff3d67 55%,#7a1cac 100%)",
                       "bg_edge": "#7a1cac",
                       "text_color": "#fff6ef", "accent": "#ffd9b8",
                       "font_scale": 1.0, "justify": "center"},      # 暖色渐变
    "neon": {"bg": "#05010c", "bg_edge": "#05010c",
             "text_color": "#39ff9c", "accent": "#ff3df5",
             "font_scale": 1.05, "justify": "center"},               # 霓虹
}


def render_card_png(text: str, dest_png: Path, *, width: int, height: int,
                    template: str = "caption", chromium: Path | None = None,
                    preset: str = "") -> Path:
    """Screenshot a styled HTML card at exact WxH. Raises MediaError when no
    Chromium exists — callers own the drawtext fallback.

    ``preset`` (round X, agent XG) picks a named look from
    :data:`CARD_STYLE_PRESETS`; the default ``""`` renders the template's
    historical hard-coded colours (:data:`_TEMPLATE_DEFAULTS`) — byte-identical
    to before this parameter existed."""
    chromium = chromium or find_chromium()
    if chromium is None:
        raise MediaError("no headless Chromium found (CHROME_BIN / PATH / "
                         "PLAYWRIGHT_BROWSERS_PATH) — use the drawtext fallback")
    tpl = CARD_TEMPLATES.get(template, CARD_TEMPLATES["caption"])
    base = _TEMPLATE_DEFAULTS.get(template, _TEMPLATE_DEFAULTS["caption"])
    style = CARD_STYLE_PRESETS.get(preset) if preset else None
    bg = (style or {}).get("bg", base["bg"])
    # bg_edge must come from the SAME row that provided bg (a preset's edge
    # tone against the base's gradient would be a mismatched seam). A row
    # without bg_edge degrades to bg itself — right for solids, and for an
    # undeclared gradient no worse than the pre-fix canvas behavior.
    bg_edge = (style or {}).get("bg_edge", bg) if (style and "bg" in style) \
        else base.get("bg_edge", bg)
    text_color = (style or {}).get("text_color", base["text_color"])
    accent = (style or {}).get("accent", base["accent"])
    justify = (style or {}).get("justify", base["justify"])
    font_scale = (style or {}).get("font_scale", base["font_scale"])
    safe = html.escape(text).replace("\n", "<br>")
    # Short-edge basis: identical on portrait, proportionate on landscape.
    short = min(width, height)
    doc = tpl.format(width=width, height=height, text=safe,
                     font_px=max(24, int(short // 11 * font_scale)),
                     kicker_px=max(14, short // 34),
                     bg=bg, bg_edge=bg_edge, text_color=text_color,
                     accent=accent, justify=justify)
    with tempfile.TemporaryDirectory(prefix="manju_html_") as tmp:
        page = Path(tmp) / "card.html"
        page.write_text(doc, encoding="utf-8")
        proc = subprocess.run(
            [str(chromium), "--headless", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             f"--window-size={width},{height}", f"--screenshot={dest_png}",
             page.as_uri()],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        if proc.returncode != 0 or not Path(dest_png).exists():
            raise MediaError(
                f"chromium screenshot failed (exit {proc.returncode}): "
                + (proc.stderr or "")[-300:]
            )
    return Path(dest_png)


def html_card_video(text: str, dest: Path, *, width: int, height: int, fps: int,
                    duration_ms: int, template: str = "caption", preset: str = "",
                    log: Callable[[str], None] | None = None) -> Path:
    """HTML card → MP4 with silent audio (same output contract as
    media.card.caption_card, so providers can swap renderers freely).
    ``preset`` (round X, agent XG) forwards to :func:`render_card_png`."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="manju_html_") as tmp:
        png = render_card_png(text, Path(tmp) / "card.png",
                              width=width, height=height, template=template,
                              preset=preset)
        duration_s = max(0.05, duration_ms / 1000.0)
        run_ffmpeg(
            ["-loop", "1", "-framerate", str(fps), "-i", str(png),
             "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
             "-t", f"{duration_s:.3f}",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
             "-shortest", str(dest)],
            log=log,
        )
    return dest
