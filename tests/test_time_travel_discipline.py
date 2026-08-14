"""战役④ git 时间旅行纪律钉(2026-07-31)。

Field origin: on a real film under a CJK+space root, truth was committed,
evolved (new text → redo → select → final_v3), then rolled back with
`git checkout <old> -- shots/`. The doctrine held perfectly: status was
instantly coherent (the v1 take is FRESH again — content keys reunite),
the newer take stayed offered as a non-blocking 待办 (§3 append-only),
and the next build minted final_v4 whose clip composition is EXACTLY
final_v1's — with zero take regeneration, zero spend.

This test pins that emergent property without git (restoring the yaml
bytes is what checkout does): text-is-truth + append-only media +
content-keyed builds ⇒ rolling truth back reproduces the old film from
media already on disk. If a future change breaks any leg (selection
stored outside truth, spec keys drifting, takes overwritten in place),
this goes red.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.ffmpeg


def _clip_identity(final_dir, version):
    doc = json.loads((final_dir / f"final_v{version}.timeline.json")
                     .read_text(encoding="utf-8"))
    clips = (doc.get("tracks") or {}).get("video") or []
    assert clips, f"final_v{version}.timeline.json carries no video track"
    return [(c.get("shot"), c.get("take"), c.get("source"),
             c.get("start_ms"), c.get("duration_ms")) for c in clips]


def test_rolling_truth_back_reproduces_the_old_film_without_regeneration(
        tmp_path):
    import subprocess
    import sys

    from manju.core.container import Project
    from manju.core.yamlio import write_yaml

    project = Project.create(tmp_path / "tt", git_init=False)
    write_yaml(project.root / "bible" / "scenes.yaml", {"s": {"name": "场"}})
    shot_path = project.shots_dir / "S001.yaml"
    write_yaml(shot_path, {
        "id": "S001", "scene": "s", "duration": 2.0,
        "action": {"main": "第一版画面。", "emotion": "静"},
    })
    write_yaml(project.shots_dir / "index.yaml",
               {"order": ["S001"], "defaults": {}})

    def run_cli(*args):
        return subprocess.run(
            [sys.executable, "-m", "manju.cli", *args],
            cwd=project.root, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=600,
        )

    def cli(*args):
        proc = run_cli(*args)
        assert proc.returncode == 0, f"{args}: {proc.stdout}\n{proc.stderr}"
        return proc.stdout

    first = run_cli("build")
    assert first.returncode == 1 and "manual selection required" in first.stdout
    cli("select", "S001", "1")
    cli("build", "--gen", "off")                  # v1 world → final_v1
    v1_truth = shot_path.read_bytes()              # what git would restore

    # evolve: new text → new take → SELECT it → the film really changes
    write_yaml(shot_path, {
        "id": "S001", "scene": "s", "duration": 2.0,
        "action": {"main": "第二版画面,完全不同。", "emotion": "动"},
    })
    cli("redo", "S001", "--yes")
    cli("select", "S001", "2")
    cli("build")                                   # v2 world → final_v2

    final_dir = project.root / "renders" / "final"
    assert (final_dir / "final_v2.mp4").exists()
    assert _clip_identity(final_dir, 1) != _clip_identity(final_dir, 2)

    take_dirs_before = sorted(p.name for p in (project.root / "media" / "gen")
                              .rglob("*") if p.is_file())

    # time travel: restore v1 truth bytes (== `git checkout v1 -- shots/S001.yaml`)
    shot_path.write_bytes(v1_truth)
    out = cli("build")                             # → final_v3, the v1 film again

    assert (final_dir / "final_v3.mp4").exists()   # append-only: v2 is not overwritten
    assert (final_dir / "final_v2.mp4").exists()
    assert _clip_identity(final_dir, 3) == _clip_identity(final_dir, 1)

    # zero regeneration: the rolled-back film was assembled from media
    # already on disk — no take was minted or rewritten by the revert build
    take_dirs_after = sorted(p.name for p in (project.root / "media" / "gen")
                             .rglob("*") if p.is_file())
    assert take_dirs_after == take_dirs_before
    assert "生成" not in out or "生成: " not in out
