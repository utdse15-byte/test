"""§8.4 last-resort fallback: a text card (title / dialogue) rendered locally.

A lavfi color source + drawtext, centered and wrapped. The text is written to a
UTF-8 textfile and passed via ``textfile=`` rather than inlined, which sidesteps
the notorious drawtext escaping bugs around colons, quotes and Chinese
punctuation. A CJK-capable font is located via fontconfig (POSIX) or the
standard font stores + registry Fonts lists (Windows, W3 §5.3); if none exists
we fall back to drawtext's default font (boxes are acceptable, we must not
crash).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .ffmpeg import run_ffmpeg
from .normalize import ANULLSRC

Log = Callable[[str], None] | None

FONT_ROOTS = (Path("/usr/share/fonts"),)

# W3 (§5.3): os.name is process-constant; a module flag keeps the Windows
# dispatch patchable in tests without touching the global ``os`` module (the
# W1/W2 _IS_WINDOWS precedent in buildlock/local_cmd/doctor/html_card).
_IS_WINDOWS = os.name == "nt"

# Curated, ORDERED CJK-capable fonts that ship with Windows — Microsoft YaHei
# first (the modern UI CJK font), then the legacy Simplified trio
# (SimHei/SimSun/KaiTi), the Win8+ DengXian family, and the Traditional
# MingLiU/JhengHei sets. A curated list keeps find_font CHEAP on Windows: a
# handful of exists() probes, never a directory-wide glob over the thousands
# of files a real Fonts dir holds.
_WINDOWS_CJK_CANDIDATES = (
    "msyh.ttc",     # Microsoft YaHei
    "msyhbd.ttc",   # Microsoft YaHei Bold
    "simhei.ttf",   # SimHei
    "simsun.ttc",   # SimSun
    "simkai.ttf",   # KaiTi
    "Deng.ttf",     # DengXian
    "Dengb.ttf",    # DengXian Bold
    "Dengl.ttf",    # DengXian Light
    "mingliu.ttc",  # MingLiU (Traditional)
    "msjh.ttc",     # Microsoft JhengHei (Traditional)
)

# Round X (agent XG): card style presets for the drawtext floor (§8.4) — the
# SAME preset names as media/html_card.py:CARD_STYLE_PRESETS (kept as two
# separate tables on purpose: drawtext only understands solid colours, never
# CSS gradients, so the "warm_gradient"/"neon" entries here are the closest
# solid-colour approximation of the html_card look). "" (classic/no preset,
# core/models.py:PACKAGING_CARD_PRESETS[0]) is intentionally ABSENT — callers
# treat a miss as "keep caption_card's own defaults" (bg="black",
# fontcolor="white", font_scale=1.0), which is exactly what caption_card
# already did before this table existed, so a card without a preset renders
# BYTE-IDENTICAL video bytes to before.
CARD_STYLE_PRESETS: dict[str, dict[str, Any]] = {
    "mono_black": {"bg": "black", "fontcolor": "white", "font_scale": 1.0},
    "white_big": {"bg": "white", "fontcolor": "0x14161b", "font_scale": 1.3},
    "warm_gradient": {"bg": "0xb5502e", "fontcolor": "white", "font_scale": 1.0},
    "neon": {"bg": "0x05010c", "fontcolor": "0x39ff9c", "font_scale": 1.05},
}


def _escape(path: Path | str) -> str:
    r"""Escape a path for a filtergraph option value (drawtext textfile/fontfile).

    Windows gate round 2: delegates to render's two-level form — the old
    single-level ``C\:`` escaping died in drawtext's OWN option parser on
    every drive-letter path (the same 'Unable to parse' failure the ass=
    filter had), which killed the caption-card fallback and with it the whole
    offline degradation chain on Windows. One owner, one escaping."""
    from .render import _escape_filter_path

    return _escape_filter_path(path)


def _fc_match(query: str) -> Path | None:
    """Best-match font file for a fontconfig query, or None."""
    try:
        proc = subprocess.run(
            ["fc-match", "-f", "%{file}", query],
            # errors="replace": a font path with non-UTF-8 bytes must degrade to
            # None (find_font contract), not raise UnicodeDecodeError out of the
            # decode (CLAUDE.md errors="replace" mandate on every subprocess decode).
            capture_output=True, text=True, encoding="utf-8", errors="replace",
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
            # errors="replace": see _fc_match — never raise out of find_font.
            capture_output=True, text=True, encoding="utf-8", errors="replace",
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


def _win_font_dirs() -> list[Path]:
    """The two standard Windows font stores: machine-wide %WINDIR%\\Fonts and
    the per-user store %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts (where Win10
    1809+ installs "for me" fonts). Env-derived so tests and unusual installs
    can redirect them; a missing variable simply drops that store."""
    dirs: list[Path] = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        dirs.append(Path(windir) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return dirs


def _win_registry_font_files() -> list[Path]:
    """Font files named by the registry Fonts lists (HKLM machine-wide + HKCU
    per-user ``SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Fonts``).
    Values are bare file names or absolute paths; relative ones resolve
    against %WINDIR%\\Fonts. Injectable + failure-safe by contract: ANY
    problem (no winreg off Windows, missing key, access denied) returns []
    so the caller can only ever degrade to None, never crash."""
    try:
        import winreg  # guarded: only exists on Windows

        windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
        base = Path(windir) / "Fonts" if windir else None
        subkey = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"
        files: list[Path] = []
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    for i in range(winreg.QueryInfoKey(key)[1]):
                        value = winreg.EnumValue(key, i)[1]
                        if not isinstance(value, str) or not value:
                            continue
                        p = Path(value)
                        if not p.is_absolute():
                            if base is None:
                                continue  # relative name, nowhere to resolve
                            p = base / p
                        files.append(p)
            except OSError:
                continue  # a hive without the key is a fact, not an error
        return files
    except Exception:
        return []


def _find_font_windows() -> Path | None:
    """Windows locator (§5.3) — no fontconfig exists here, so:

    1. Probe the curated candidates against the two standard font stores.
       Curated ORDER wins across stores (YaHei in the user store beats SimSun
       in the system store) — preference is about the font, not its home.
    2. Fall back to the registry Fonts lists for fonts installed to
       non-standard paths, accepting only curated names (same order).

    None = honestly not found: the §8.4 card degrades to drawtext's default
    font and glyph coverage stays UNKNOWN — never guessed.
    """
    dirs = [d for d in _win_font_dirs() if d.is_dir()]
    for name in _WINDOWS_CJK_CANDIDATES:
        for d in dirs:
            candidate = d / name
            if candidate.exists():
                return candidate
    try:
        registry = _win_registry_font_files()
    except Exception:
        return None  # a broken registry probe degrades, never crashes
    by_name: dict[str, Path] = {}
    for p in registry:
        by_name.setdefault(p.name.lower(), p)  # first registry entry wins
    for name in _WINDOWS_CJK_CANDIDATES:
        found = by_name.get(name.lower())
        if found is not None and found.exists():
            return found
    return None


def find_font() -> Path | None:
    """Locate a CJK-capable font, or None.

    0. Windows (no fontconfig): dispatch to the curated dir-scan + registry
       locator above — the POSIX chain below is never consulted there, and
       the Windows sources never on POSIX (the chain stays byte-identical).
    1. Named CJK families (Noto Sans CJK, WenQuanYi), verified so a fontconfig
       fallback to a non-CJK family is not mistaken for a real hit.
    2. Any font that actually covers a common CJK ideograph (U+4E00 一) — the
       most reliable "is this CJK-capable" signal on systems where :lang=zh
       resolves to a non-CJK font.
    3. Filesystem glob for Noto/DejaVu ttf/otf/ttc under /usr/share/fonts.
    """
    if _IS_WINDOWS:
        return _find_font_windows()

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
    fontcolor: str = "white",
    font_scale: float = 1.0,
    log: Log = None,
) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    dur_s = duration_ms / 1000.0
    # Base the size on the SHORT edge so landscape cards don't blow the text
    # up relative to frame height (identical to before on portrait frames).
    # font_scale defaults to 1.0 (round X, agent XG — CARD_STYLE_PRESETS), so
    # an untouched caller computes the EXACT same fontsize as before.
    fontsize = max(12, int(min(width, height) // 12 * font_scale))
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
            f"fontcolor={fontcolor}",
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
