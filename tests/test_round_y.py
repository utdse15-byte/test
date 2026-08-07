"""Round Y: close the round-W accounting gap the external review caught —
media-ext determinism (#4), director state fingerprint routing/budget (#45),
unpack symlink/traversal hardening (#20). (Board CSRF #12 lives in
test_board_serve.py.)"""

from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest

from manju.core.container import MEDIA_EXTS


# ---------------------------------------------------- #4 media-ext determinism


def test_media_exts_is_ordered_priority_tuple():
    # a tuple (ordered), not a set (nondeterministic iteration)
    assert isinstance(MEDIA_EXTS, tuple)
    # video before image before audio; mp4 is the top-priority pick
    assert MEDIA_EXTS[0] == ".mp4"
    assert MEDIA_EXTS.index(".mp4") < MEDIA_EXTS.index(".png") < MEDIA_EXTS.index(".wav")


def test_take_with_two_media_files_is_deterministic_and_flagged(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    tdir = tmp_project.takes_dir("S001")
    tdir.mkdir(parents=True, exist_ok=True)
    # a valid sidecar plus TWO media files for the same stem
    (tdir / "take_01.yaml").write_text(
        "provider: manual_import\nspec_hash: manual\n", encoding="utf-8")
    (tdir / "take_01.mov").write_bytes(b"mov")
    (tdir / "take_01.mp4").write_bytes(b"mp4")

    takes = tmp_project.takes("S001")
    assert len(takes) == 1
    t = takes[0]
    # deterministic: .mp4 wins by priority regardless of set/dir order
    assert t.media_path is not None and t.media_path.name == "take_01.mp4"
    # and the ambiguity is surfaced for `manju check` / staleness to report
    assert t.error and "多个媒体文件" in t.error


# ------------------------------------------- #45 director fingerprint coverage


def _seed_shot(project, add_shot, make_take):
    add_shot(project, "S001")
    make_take(project, "S001", "manual")
    project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", "take_01"))


def test_state_fingerprint_covers_routing_and_config(tmp_project, add_shot, make_take):
    from manju.build.director import state_fingerprint

    _seed_shot(tmp_project, add_shot, make_take)
    base = state_fingerprint(tmp_project)

    # a routing.yaml change (which provider fires / at what price) must move it
    routing = tmp_project.root / "timeline" / "routing.yaml"
    routing.parent.mkdir(parents=True, exist_ok=True)
    routing.write_text("strategy: cheapest\n", encoding="utf-8")
    after_routing = state_fingerprint(tmp_project)
    assert after_routing != base

    # a project.yaml change (budget/mode) must move it too
    cfg = tmp_project.root / "project.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8") + "\nbudget:\n  limit: 5.0\n",
                   encoding="utf-8")
    assert state_fingerprint(tmp_project) != after_routing


def test_confirmed_proposal_expires_when_routing_changes(tmp_project, add_shot, make_take):
    from manju.build.director import _is_current, propose

    _seed_shot(tmp_project, add_shot, make_take)
    p = propose(tmp_project, [{"type": "snapshot"}], why="t", actor="human")
    assert _is_current(tmp_project, p)

    routing = tmp_project.root / "timeline" / "routing.yaml"
    routing.parent.mkdir(parents=True, exist_ok=True)
    routing.write_text("strategy: quality_first\n", encoding="utf-8")
    assert not _is_current(tmp_project, p)  # 待更新: re-propose against new routing


# ------------------------------------------------ #20 unpack member hardening


def _run_unpack(archive: Path, dest: Path):
    from typer.testing import CliRunner

    from manju.cli import app

    return CliRunner().invoke(app, ["unpack", str(archive), "--dest", str(dest)])


def test_unpack_rejects_symlink_member(tmp_path):
    archive = tmp_path / "evil.manjupkg"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("project.yaml", "name: x\n")
        # a symlink member pointing outside — external_attr marks it S_IFLNK
        info = zipfile.ZipInfo("link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, "/etc/passwd")
    res = _run_unpack(archive, tmp_path / "out")
    assert res.exit_code != 0
    assert not (tmp_path / "out").exists() or not (tmp_path / "out" / "link").exists()


def test_unpack_rejects_traversal_member(tmp_path):
    archive = tmp_path / "evil2.manjupkg"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("project.yaml", "name: x\n")
        zf.writestr("../escape.txt", "pwned")
    res = _run_unpack(archive, tmp_path / "out2")
    assert res.exit_code != 0


def test_unpack_accepts_a_clean_archive(tmp_path):
    archive = tmp_path / "clean.manjupkg"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("project.yaml", "name: x\n")
        zf.writestr("shots/index.yaml", "order: []\n")
    res = _run_unpack(archive, tmp_path / "out3")
    assert res.exit_code == 0, res.output
    assert (tmp_path / "out3" / "project.yaml").exists()
