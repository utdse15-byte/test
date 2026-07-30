"""HTML 卡片两处渲染缺陷 — 视觉 QC 闭环第一枪抓到折行,复现时又量出白带。

Both defects are PIXEL facts, so the tests assert on rendered pixels (chromium
+ ffmpeg gated, same precedent as test_round5):

1. **短台词折行**: `.card{max-width:82%}` sat on a block INSIDE the anonymous
   shrink-to-fit flex wrapper. During intrinsic sizing the cyclic percentage
   is ignored (wrapper = the text's one-line width W), then layout re-applies
   max-width = 0.82·W — so EVERY one-line caption broke its tail onto a second
   line (「谢谢。」→「谢/谢。」, both orientations). The width cap belongs on
   the flex item, where the percentage resolves against the definite body.

2. **底部白带**: this headless Chromium truncates gradient canvas backgrounds
   (html/body propagation) 87px short of the window, and skips them entirely
   at auto size — solid colours paint fully. Measured, not theorised: 87 white
   rows at 1080x1920 AND 1920x1080. The gradient therefore also lives on
   body::before{position:fixed;inset:0}, which paints like any element.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from manju.media.html_card import html_available

has_ffmpeg = shutil.which("ffmpeg") is not None
has_chromium = html_available()

pytestmark = pytest.mark.skipif(
    not (has_ffmpeg and has_chromium), reason="chromium + ffmpeg required")


def _gray_rows(png, width: int, height: int) -> list[bytes]:
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(png),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    assert len(raw) == width * height, "png decoded at unexpected size"
    return [raw[y * width:(y + 1) * width] for y in range(height)]


def _glyph_bands(rows: list[bytes], *, bright: int = 200, gap: int = 25) -> list[tuple[int, int]]:
    """Contiguous runs of rows containing bright (text) pixels; runs closer
    than ``gap`` merge, so intra-glyph stroke gaps never split a line but the
    line-height:1.65 inter-line gap (~60px at card sizes) always does."""
    ys = [y for y, row in enumerate(rows) if max(row) > bright]
    bands: list[list[int]] = []
    for y in ys:
        if bands and y - bands[-1][1] <= gap:
            bands[-1][1] = y
        else:
            bands.append([y, y])
    return [(a, b) for a, b in bands]


@pytest.mark.ffmpeg
@pytest.mark.parametrize("width,height", [(1080, 1920), (1920, 1080)])
def test_short_dialogue_stays_on_one_line(tmp_path, width, height):
    """「谢谢。」 fits one line with room to spare at every project size —
    the card must never break it (the vision-QC finding, both orientations).
    mono_black keeps the pixel analysis trivial: white glyphs, black frame."""
    from manju.media.html_card import render_card_png

    png = tmp_path / "card.png"
    render_card_png("谢谢。", png, width=width, height=height,
                    preset="mono_black")
    bands = _glyph_bands(_gray_rows(png, width, height))
    assert len(bands) == 1, f"short dialogue wrapped: glyph bands {bands}"


@pytest.mark.ffmpeg
def test_long_dialogue_still_wraps(tmp_path):
    """The one-line fix must not have removed wrapping altogether: text wider
    than the card measure still breaks into multiple lines inside the frame."""
    from manju.media.html_card import render_card_png

    png = tmp_path / "card.png"
    render_card_png("这句台词足够长,一行放不下,必须折行。", png,
                    width=1080, height=1920, preset="mono_black")
    bands = _glyph_bands(_gray_rows(png, 1080, 1920))
    assert len(bands) >= 2, "long dialogue no longer wraps at all"


@pytest.mark.ffmpeg
@pytest.mark.parametrize("template", ["caption", "chapter"])
@pytest.mark.parametrize("width,height", [(1080, 1920), (1920, 1080)])
def test_card_paints_the_full_frame(tmp_path, template, width, height):
    """No row of the card may be near-white: the default gradient must reach
    every edge (the 87px canvas-truncation band was pure white, so a dark
    gradient with white TEXT still keeps every row mean far below this)."""
    from manju.media.html_card import render_card_png

    png = tmp_path / "card.png"
    render_card_png("画面必须涂满", png, width=width, height=height,
                    template=template)
    rows = _gray_rows(png, width, height)
    white = [y for y, row in enumerate(rows) if sum(row) // width > 200]
    assert not white, f"unpainted near-white rows: {white[:5]}… ({len(white)} total)"
