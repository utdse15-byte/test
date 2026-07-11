"""AI_IDE_16 §6/§7 — board read-only ladder view fields + 2D blocking SVG.

Pinned: the 2D blocking SVG is a PURE derivation from Shot source camera/action
(no 3D, no canvas truth) — deterministic bytes; the ladder chips (stage /
keyframe approval / next-step cost) are read-only and serve-mode only, so the
static board.html stays byte-identical (its own pin lives in test_board_serve).
"""

from __future__ import annotations

from manju.board.board import _ladder_chips, blocking_svg, render_board


# ------------------------------------------------------------ blocking SVG


def test_blocking_svg_is_pure_and_deterministic(tmp_project, add_shot):
    add_shot(tmp_project, "S001", camera={"shot_size": "close_up",
                                          "movement": "pan_left", "angle": "low_angle"})
    shot = tmp_project.load_shot("S001")
    a = blocking_svg(shot)
    b = blocking_svg(shot)
    assert a == b                      # deterministic — same bytes
    assert a.startswith("<svg") and a.rstrip().endswith("</svg>")
    # derives from the source fields (labels present)
    assert "close_up" in a and "pan_left" in a and "low_angle" in a


def test_blocking_svg_reflects_shot_size(tmp_project, add_shot):
    add_shot(tmp_project, "CU", camera={"shot_size": "extreme_close_up"})
    add_shot(tmp_project, "WS", camera={"shot_size": "extreme_wide"})
    cu = blocking_svg(tmp_project.load_shot("CU"))
    ws = blocking_svg(tmp_project.load_shot("WS"))
    assert cu != ws                    # a close-up and a wide differ in the box
    assert "extreme_close_up" in cu and "extreme_wide" in ws


def test_blocking_svg_unknown_movement_draws_no_guessed_arrow(tmp_project, add_shot):
    add_shot(tmp_project, "S1", camera={"movement": "static"})
    svg = blocking_svg(tmp_project.load_shot("S1"))
    # static → no movement arrow marker line to the arrowhead
    assert 'marker-end="url(#ar)"' not in svg


# ------------------------------------------------------------ ladder chips


def test_ladder_chips_show_stage_and_pending_keyframe(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")  # unadopted keyframe
    chips = _ladder_chips(tmp_project, "S001")
    assert "ladder:" in chips
    assert "pending" in chips          # keyframe awaiting adoption


def test_ladder_chips_show_adopted(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    kf = make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    shot = tmp_project.load_shot("S001")
    shot.status.selected_take = kf.name
    tmp_project.save_shot(shot)
    chips = _ladder_chips(tmp_project, "S001")
    assert "adopted" in chips


def test_serve_board_carries_ladder_and_blocking(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    html = render_board(tmp_project, serve=True)
    assert "ladder-chips" in html
    assert "2D blocking" in html and "<svg" in html


def test_static_board_omits_the_serve_only_fields(tmp_project, add_shot, make_take):
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:k", suffix=".png")
    html = render_board(tmp_project, serve=False)
    assert "ladder-chips" not in html
    assert "2D blocking" not in html
