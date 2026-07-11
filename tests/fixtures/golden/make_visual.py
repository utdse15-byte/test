"""Deterministic synthetic VISUAL calibration set (AI_IDE_20A, contract §3
"Visual continuity" + §4 human annotation).

Everything here is SYNTHESIZED with PIL — no private or copyrighted material
(contract §4/§9 stop-condition: 不得复制受版权限制的大型媒体). Two synthetic
"characters" (colored geometric figures with distinguishing features) plus scene
/style/visibility fixtures exercise the AI_IDE_15 visual-continuity dimensions:
identity match/mismatch, wardrobe/prop drift, scene/lighting change, axis/
screen-direction flip, action phase, style/palette shift, not-visible/occluded
and a deliberately low-signal ambiguous case.

Determinism: fixed geometry, fixed palette, NO randomness and NO wall-clock, so
re-running reproduces the same pixels. The COMMITTED PNG bytes are the pinned
authority (manifest.json records each sha256); this module is the regenerable
recipe (contract §8: 同一 fixture 重复结果稳定). Images are 96x96 (<=128px) and a
few KB each so the committed corpus stays tiny (<300 KB total).

Run standalone to (re)write the PNGs into ``visual/`` next to this file::

    python tests/fixtures/golden/make_visual.py
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 96  # square, <=128px per the addendum

# Two synthetic characters, each a fixed geometric figure with DISTINGUISHING
# features (palette + badge shape) so "identity" is a real, checkable property.
CHAR_A = {
    "head": (46, 170, 165),    # teal head
    "body": (34, 54, 92),      # navy coat
    "badge": (240, 200, 40),   # yellow triangular badge
    "badge_shape": "triangle",
}
CHAR_B = {
    "head": (214, 120, 40),    # orange head
    "body": (120, 30, 44),     # maroon coat
    "badge": (60, 150, 70),    # green square badge
    "badge_shape": "square",
}

_BG_COOL = (36, 44, 60)        # cool "night convenience store" backdrop
_BG_WARM = (92, 70, 46)        # warm relight of the same scene
_BLACK = (0, 0, 0)


def _bg(draw: ImageDraw.ImageDraw, color) -> None:
    draw.rectangle([0, 0, SIZE, SIZE], fill=color)


def _badge(draw: ImageDraw.ImageDraw, char: dict, x: int, y: int) -> None:
    c = char["badge"]
    if char["badge_shape"] == "triangle":
        draw.polygon([(x, y + 8), (x + 8, y + 8), (x + 4, y)], fill=c)
    else:
        draw.rectangle([x, y, x + 8, y + 8], fill=c)


def draw_character(img: Image.Image, char: dict, *, facing: str = "right",
                   arm: str = "down", body_color=None, occluded: bool = False,
                   style: str = "flat") -> None:
    """Draw one synthetic character. ``body_color`` overrides the coat (wardrobe
    drift); ``facing`` flips the direction marker (axis / screen direction);
    ``arm`` raises or lowers the arm (action phase); ``style`` swaps flat fill
    for a vertical gradient coat (style/palette shift); ``occluded`` drops a
    black bar over most of the figure (not-visible)."""
    draw = ImageDraw.Draw(img)
    body = body_color or char["body"]

    # coat (body): flat rectangle, or a vertical gradient in "gradient" style.
    if style == "gradient":
        top, bot = body, tuple(min(255, v + 90) for v in body)
        for i in range(48, 86):
            t = (i - 48) / 38.0
            row = tuple(int(top[k] * (1 - t) + bot[k] * t) for k in range(3))
            draw.line([(34, i), (62, i)], fill=row)
    else:
        draw.rectangle([34, 48, 62, 86], fill=body)

    # head
    draw.ellipse([36, 22, 60, 46], fill=char["head"])

    # direction marker (a "nose" nub) — which way the subject faces.
    if facing == "right":
        draw.rectangle([60, 32, 66, 36], fill=char["head"])
    else:
        draw.rectangle([30, 32, 36, 36], fill=char["head"])

    # arm — raised or lowered (action phase).
    if arm == "up":
        draw.line([(48, 54), (74, 30)], fill=body, width=5)
    else:
        draw.line([(48, 54), (72, 78)], fill=body, width=5)

    # fixed identity badge on the coat.
    _badge(draw, char, 44, 58)

    if occluded:
        # a heavy black bar hides most of the figure — identity NOT visible.
        draw.rectangle([0, 30, SIZE, 74], fill=_BLACK)


def _scene(img: Image.Image, warm: bool) -> None:
    """A backdrop 'store window': a big lit rectangle on a graded wall. The warm
    variant is the same geometry under a different light (lighting change)."""
    draw = ImageDraw.Draw(img)
    base = _BG_WARM if warm else _BG_COOL
    _bg(draw, base)
    for i in range(SIZE):  # subtle vertical grade so it reads as a lit wall
        t = i / float(SIZE)
        row = tuple(min(255, int(base[k] + (40 if warm else 24) * (1 - t))) for k in range(3))
        draw.line([(0, i), (SIZE, i)], fill=row)
    window = (250, 232, 180) if warm else (180, 210, 245)
    draw.rectangle([18, 20, 78, 60], outline=window, width=3)
    draw.line([(48, 20), (48, 60)], fill=window, width=2)


def _umbrella(img: Image.Image, color) -> None:
    draw = ImageDraw.Draw(img)
    draw.pieslice([20, 24, 60, 64], 180, 360, fill=color)
    draw.line([(40, 44), (40, 74)], fill=(30, 30, 30), width=2)


# Each entry: (case_id, filename, builder). The case_id matches manifest.json.
def _img(bg=_BG_COOL) -> Image.Image:
    im = Image.new("RGB", (SIZE, SIZE))
    ImageDraw.Draw(im).rectangle([0, 0, SIZE, SIZE], fill=bg)
    return im


def _identity_ref():
    im = _img(); draw_character(im, CHAR_A, facing="right", arm="down"); return im


def _identity_match():
    # same character A, a different but on-model pose (arm mid) — identity holds.
    im = _img()
    draw_character(im, CHAR_A, facing="right", arm="down")
    ImageDraw.Draw(im).ellipse([38, 24, 58, 44], outline=(255, 255, 255))  # tiny highlight only
    return im


def _identity_mismatch():
    im = _img(); draw_character(im, CHAR_B, facing="right", arm="down"); return im


def _wardrobe_base():
    im = _img(); draw_character(im, CHAR_A, body_color=CHAR_A["body"]); return im


def _wardrobe_drift():
    im = _img(); draw_character(im, CHAR_A, body_color=(150, 40, 40)); return im  # coat recolored


def _prop_base():
    im = _img(); draw_character(im, CHAR_A); _umbrella(im, (200, 40, 40)); return im  # red umbrella


def _prop_drift():
    im = _img(); draw_character(im, CHAR_A); _umbrella(im, (40, 90, 200)); return im  # blue umbrella


def _scene_base():
    im = _img(); _scene(im, warm=False); return im


def _scene_drift():
    im = _img(); _scene(im, warm=True); return im  # same geometry, warm relight


def _axis_left():
    im = _img(); draw_character(im, CHAR_A, facing="left"); return im


def _axis_right():
    im = _img(); draw_character(im, CHAR_A, facing="right"); return im


def _action_arm_up():
    im = _img(); draw_character(im, CHAR_A, arm="up"); return im


def _action_arm_down():
    im = _img(); draw_character(im, CHAR_A, arm="down"); return im


def _style_flat():
    im = _img(); draw_character(im, CHAR_A, style="flat"); return im


def _style_gradient():
    im = _img(); draw_character(im, CHAR_A, style="gradient"); return im


def _visibility_occluded():
    im = _img(); draw_character(im, CHAR_A, occluded=True); return im


def _ambiguous_lowsignal():
    # near-uniform low-contrast field: no reviewable subject (deliberately low
    # signal so a reviewer should honestly answer UNCERTAIN).
    im = _img((40, 42, 45))
    d = ImageDraw.Draw(im)
    for i in range(0, SIZE, 6):
        d.line([(0, i), (SIZE, i)], fill=(43, 45, 48))
    return im


def _ambiguous_tinyfigure():
    # a very small figure in a big empty frame — identity present but unresolvable.
    im = _img((40, 42, 45))
    d = ImageDraw.Draw(im)
    d.ellipse([46, 46, 52, 52], fill=CHAR_A["head"])
    d.rectangle([47, 52, 51, 62], fill=CHAR_A["body"])
    return im


BUILDERS = {
    "visual.identity.ref.charA": ("identity_ref_charA.png", _identity_ref),
    "visual.identity.match.charA": ("identity_match_charA.png", _identity_match),
    "visual.identity.mismatch.charB": ("identity_mismatch_charB.png", _identity_mismatch),
    "visual.wardrobe.base.charA": ("wardrobe_base_charA.png", _wardrobe_base),
    "visual.wardrobe.drift.charA": ("wardrobe_drift_charA.png", _wardrobe_drift),
    "visual.prop.base.charA": ("prop_base_charA.png", _prop_base),
    "visual.prop.drift.charA": ("prop_drift_charA.png", _prop_drift),
    "visual.scene.base": ("scene_base.png", _scene_base),
    "visual.scene.drift.lighting": ("scene_drift_lighting.png", _scene_drift),
    "visual.axis.facing_left.charA": ("axis_facing_left_charA.png", _axis_left),
    "visual.axis.facing_right.charA": ("axis_facing_right_charA.png", _axis_right),
    "visual.action.arm_up.charA": ("action_arm_up_charA.png", _action_arm_up),
    "visual.action.arm_down.charA": ("action_arm_down_charA.png", _action_arm_down),
    "visual.style.flat.charA": ("style_flat_charA.png", _style_flat),
    "visual.style.gradient.charA": ("style_gradient_charA.png", _style_gradient),
    "visual.visibility.occluded.charA": ("visibility_occluded_charA.png", _visibility_occluded),
    "visual.ambiguous.lowsignal": ("ambiguous_lowsignal.png", _ambiguous_lowsignal),
    "visual.ambiguous.tinyfigure": ("ambiguous_tinyfigure.png", _ambiguous_tinyfigure),
}


def visual_dir() -> Path:
    return Path(__file__).resolve().parent / "visual"


def _slate(img: Image.Image, case_id: str) -> None:
    """Stamp a deterministic 4x4 corner 'slate' whose color derives from the
    case id. Several cases share a canonical charA pose (the reference, the
    wardrobe base, the arm-down action phase, the right-facing axis, the flat
    style) — semantically each is a DISTINCT capture, and the slate makes that
    literal so every committed frame has its own sha256 (no two cases collide on
    one hash, which a sha256-keyed reviewer table requires). Fully deterministic
    (hash of the id), never wall-clock or random."""
    h = hashlib.sha256(case_id.encode("utf-8")).digest()
    color = (h[0], h[1], h[2])
    ImageDraw.Draw(img).rectangle([0, 0, 3, 3], fill=color)


def build_all(dest: Path | None = None) -> dict[str, Path]:
    """(Re)generate every committed visual PNG. Returns {case_id: path}.
    Deterministic: same pixels every run, saved with a fixed PNG encoding."""
    dest = dest or visual_dir()
    dest.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for case_id, (fname, builder) in BUILDERS.items():
        path = dest / fname
        img = builder()
        _slate(img, case_id)  # per-case uniqueness (distinct sha256)
        # optimize=True + fixed params => deterministic, small bytes.
        img.save(path, format="PNG", optimize=True)
        out[case_id] = path
    return out


if __name__ == "__main__":
    made = build_all()
    for cid, p in made.items():
        print(f"{cid:42s} {p.name:34s} {p.stat().st_size:5d}B")
