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
from typing import Callable

from .ffmpeg import MediaError, run_ffmpeg

_CHROMIUM_CANDIDATES = (
    "chromium", "chromium-browser", "google-chrome", "headless_shell",
)


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
    # caption/dialogue card: dark gradient, centered text
    "caption": """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;
background:linear-gradient(165deg,#0d1117 0%,#161f2e 55%,#1d2a3a 100%);
display:flex;align-items:center;justify-content:center;
font-family:"Noto Sans CJK SC","WenQuanYi Zen Hei","PingFang SC",sans-serif}}
.card{{color:#f5f6f8;font-size:{font_px}px;font-weight:600;text-align:center;
max-width:82%;line-height:1.65;letter-spacing:.04em;
text-shadow:0 2px 14px rgba(0,0,0,.85)}}
.rule{{width:56px;height:3px;margin:28px auto 0;border-radius:2px;
background:#4a90d9;opacity:.85}}</style></head>
<body><div><div class="card">{text}</div><div class="rule"></div></div></body></html>""",
    # chapter/title card: bigger, with a kicker line
    "chapter": """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;width:{width}px;height:{height}px;overflow:hidden;
background:radial-gradient(120% 90% at 50% 20%,#1b2838 0%,#0b1017 70%);
display:flex;align-items:center;justify-content:center;
font-family:"Noto Sans CJK SC","WenQuanYi Zen Hei","PingFang SC",sans-serif}}
.wrap{{text-align:center;max-width:84%}}
.kicker{{color:#7fa6c9;font-size:{kicker_px}px;letter-spacing:.5em;
text-indent:.5em;margin-bottom:30px}}
.title{{color:#ffffff;font-size:{font_px}px;font-weight:700;line-height:1.5;
letter-spacing:.06em;text-shadow:0 3px 18px rgba(0,0,0,.9)}}</style></head>
<body><div class="wrap"><div class="kicker">CHAPTER</div>
<div class="title">{text}</div></div></body></html>""",
}


def render_card_png(text: str, dest_png: Path, *, width: int, height: int,
                    template: str = "caption", chromium: Path | None = None) -> Path:
    """Screenshot a styled HTML card at exact WxH. Raises MediaError when no
    Chromium exists — callers own the drawtext fallback."""
    chromium = chromium or find_chromium()
    if chromium is None:
        raise MediaError("no headless Chromium found (CHROME_BIN / PATH / "
                         "PLAYWRIGHT_BROWSERS_PATH) — use the drawtext fallback")
    tpl = CARD_TEMPLATES.get(template, CARD_TEMPLATES["caption"])
    safe = html.escape(text).replace("\n", "<br>")
    # Short-edge basis: identical on portrait, proportionate on landscape.
    short = min(width, height)
    doc = tpl.format(width=width, height=height, text=safe,
                     font_px=max(24, short // 11), kicker_px=max(14, short // 34))
    with tempfile.TemporaryDirectory(prefix="manju_html_") as tmp:
        page = Path(tmp) / "card.html"
        page.write_text(doc, encoding="utf-8")
        proc = subprocess.run(
            [str(chromium), "--headless", "--disable-gpu", "--no-sandbox",
             "--hide-scrollbars", "--force-device-scale-factor=1",
             f"--window-size={width},{height}", f"--screenshot={dest_png}",
             page.as_uri()],
            capture_output=True, text=True, timeout=60,
        )
        if proc.returncode != 0 or not Path(dest_png).exists():
            raise MediaError(
                f"chromium screenshot failed (exit {proc.returncode}): "
                + (proc.stderr or "")[-300:]
            )
    return Path(dest_png)


def html_card_video(text: str, dest: Path, *, width: int, height: int, fps: int,
                    duration_ms: int, template: str = "caption",
                    log: Callable[[str], None] | None = None) -> Path:
    """HTML card → MP4 with silent audio (same output contract as
    media.card.caption_card, so providers can swap renderers freely)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="manju_html_") as tmp:
        png = render_card_png(text, Path(tmp) / "card.png",
                              width=width, height=height, template=template)
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
