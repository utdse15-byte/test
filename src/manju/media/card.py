"""§8.4 last-resort fallback: a text card (title / dialogue) rendered locally.

A lavfi color source + drawtext, centered and wrapped. The text is written to a
UTF-8 textfile and passed via ``textfile=`` rather than inlined, which sidesteps
the notorious drawtext escaping bugs around colons, quotes and Chinese
punctuation. A CJK-capable font is located via fontconfig; if none exists we
fall back to drawtext's default font (boxes are acceptable, we must not crash).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from .ffmpeg import run_ffmpeg
from .normalize import ANULLSRC

Log = Callable[[str], None] | None

FONT_ROOTS = (Path("/usr/share/fonts"),)


def _escape(path: Path | str) -> str:
    """Escape a path for a filtergraph option value (drawtext textfile/fontfile)."""
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _fc_match(query: str) -> Path | None:
    """Best-match font file for a fontconfig query, or None."""
    try:
        proc = subprocess.run(
            ["fc-match", "-f", "%{file}", query],
            capture_output=True, text=True, encoding="utf-8",
        )
    except FileNotFoundError:
        return None
    out = (proc.stdout or "").strip()
    if proc.returncode == 0 and out:
        p = Path(out)
        if p.exists():
            return p
    return None


def _fc_match_family(family: str) -> Path | None:
    """Match a named family, but only accept it if fontconfig actually resolved
    that family (it silently returns a fallback like DejaVu Sans otherwise, so a
    plain file match is not enough to prove the requested CJK family exists)."""
    try:
        proc = subprocess.run(
            ["fc-match", "-f", "%{family}|%{file}", family],
            capture_output=True, text=True, encoding="utf-8",
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    resolved_family, _, file = (proc.stdout or "").partition("|")
    key = family.split()[0].lower()  # distinctive leading token: noto / wenquanyi
    file = file.strip()
    if key and key in resolved_family.lower() and file and Path(file).exists():
        return Path(file)
    return None


def find_font() -> Path | None:
    """Locate a CJK-capable font, or None.

    1. Named CJK families (Noto Sans CJK, WenQuanYi), verified so a fontconfig
       fallback to a non-CJK family is not mistaken for a real hit.
    2. Any font that actually covers a common CJK ideograph (U+4E00 一) — the
       most reliable "is this CJK-capable" signal on systems where :lang=zh
       resolves to a non-CJK font.
    3. Filesystem glob for Noto/DejaVu ttf/otf/ttc under /usr/share/fonts.
    """
    for family in (
        "Noto Sans CJK SC",
        "Noto Sans CJK",
        "WenQuanYi Zen Hei",
        "WenQuanYi Zen Hei Sharp",
    ):
        found = _fc_match_family(family)
        if found is not None:
            return found

    found = _fc_match(":charset=4e00")  # font covering 一 → genuinely CJK-capable
    if found is not None:
        return found

    for root in FONT_ROOTS:
        if not root.exists():
            continue
        for stem in ("Noto", "DejaVu"):
            for ext in ("ttf", "otf", "ttc"):
                matches = sorted(root.rglob(f"{stem}*.{ext}"))
                if matches:
                    return matches[0]
    return None


def _wrap(text: str, chars_per_line: int) -> list[str]:
    """Width-based wrap (CJK has no spaces), honouring explicit newlines."""
    lines: list[str] = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        cur = ""
        for ch in para:
            cur += ch
            if len(cur) >= chars_per_line:
                lines.append(cur)
                cur = ""
        if cur:
            lines.append(cur)
    return lines or [""]


def caption_card(
    text: str,
    dest: Path,
    *,
    width: int,
    height: int,
    fps: int,
    duration_ms: int,
    bg: str = "black",
    log: Log = None,
) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    dur_s = duration_ms / 1000.0
    fontsize = max(12, width // 12)
    chars_per_line = max(1, width // fontsize - 1)
    wrapped = "\n".join(_wrap(text, chars_per_line))

    font = find_font()

    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".card_", suffix=".txt")
    tmp_txt = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(wrapped)

        draw_opts = [
            f"textfile={_escape(tmp_txt)}",
            "fontcolor=white",
            f"fontsize={fontsize}",
            "x=(w-text_w)/2",
            "y=(h-text_h)/2",
            "line_spacing=10",
        ]
        if font is not None:
            draw_opts.append(f"fontfile={_escape(font)}")
        drawtext = "drawtext=" + ":".join(draw_opts)

        run_ffmpeg(
            [
                "-f", "lavfi", "-t", f"{dur_s:.3f}",
                "-i", f"color=c={bg}:s={width}x{height}:r={fps}",
                "-f", "lavfi", "-t", f"{dur_s:.3f}", "-i", ANULLSRC,
                "-filter_complex", f"[0:v]{drawtext},format=yuv420p[v]",
                "-map", "[v]", "-map", "1:a",
                "-t", f"{dur_s:.3f}",
                "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                str(dest),
            ],
            log=log,
        )
    finally:
        try:
            tmp_txt.unlink()
        except OSError:
            pass
    return dest
