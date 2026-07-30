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


@pytest.mark.ffmpeg
def test_gradient_actually_reaches_the_pixels(tmp_path):
    """涂满 ≠ 画对:the no-white-rows assertion is blind to a gradient that
    silently degrades to the flat bg_edge solid (paint order: canvas → the
    z-index:-1 ::before → body's own OPAQUE background covered it — every
    gradient card shipped flat). These probes pin the gradient's actual
    tonal travel on the pixels, per family:

    - caption default (165deg linear): top rows measurably darker than
      bottom rows;
    - chapter default (radial at 50% 20%): the radial center brighter than
      the far corner;
    - warm_gradient preset: warm (R>B) at top-left, cool (B>R) at
      bottom-right."""
    from manju.media.html_card import render_card_png

    W, H = 1920, 1080

    def rows_gray(png):
        raw = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(png),
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True, check=True).stdout
        return [sum(raw[y * W:(y + 1) * W]) // W for y in range(H)]

    p1 = tmp_path / "caption.png"
    render_card_png("空", p1, width=W, height=H)
    rows = rows_gray(p1)
    top, bottom = sum(rows[:80]) // 80, sum(rows[-80:]) // 80
    assert bottom - top >= 8, f"caption gradient flat: top={top} bottom={bottom}"

    p2 = tmp_path / "chapter.png"
    render_card_png("空", p2, width=W, height=H, template="chapter")
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(p2),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    center_top = raw[(H // 5) * W + W // 2]
    corner = raw[(H - 10) * W + 10]
    assert center_top - corner >= 8, \
        f"chapter radial flat: center_top={center_top} corner={corner}"

    p3 = tmp_path / "warm.png"
    render_card_png("空", p3, width=W, height=H, preset="warm_gradient")
    rgb = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(p3),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout

    def px(x, y):
        i = (y * W + x) * 3
        return rgb[i], rgb[i + 1], rgb[i + 2]

    tl, br = px(10, 10), px(W - 10, H - 10)
    assert tl[0] > tl[2] + 30, f"top-left not warm: {tl}"
    assert br[2] > br[0] + 30, f"bottom-right not cool: {br}"


@pytest.mark.ffmpeg
def test_edge_pushed_text_keeps_a_margin(tmp_path):
    """white_big justifies the stack to the frame edge — the text must still
    keep a real inset (≥3% of width), never sit flush against the border
    (measured 14px of 1920 before the fix)."""
    from manju.media.html_card import render_card_png

    W, H = 1920, 1080
    png = tmp_path / "wb.png"
    render_card_png("第三章 归途", png, width=W, height=H, preset="white_big")
    raw = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(png),
         "-f", "rawvideo", "-pix_fmt", "gray", "-"],
        capture_output=True, check=True).stdout
    rightmost = 0
    for y in range(H):
        row = raw[y * W:(y + 1) * W]
        for x in range(W - 1, rightmost, -1):
            if row[x] < 100:
                rightmost = max(rightmost, x)
                break
    assert rightmost <= W - int(0.03 * W), \
        f"text flush against the edge: rightmost dark pixel at {rightmost}/{W}"
