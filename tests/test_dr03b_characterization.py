"""DR03B — WP0 characterization: pin Manju's CURRENT build behavior.

These are the contract's four questions translated to Manju's ACTUAL shapes.
They are REAL behavioral pins, not import smoke — c1 drives a genuine
final-target build with a genuinely-failed shot and observes the outcome; its
verdict decides whether WP2 (execution fixes) touches anything.

Recorded verdict (see REPORTS/AI_IDE_03B_BASELINE.md):
  * c1  CORRECT-BY-DESIGN — a final build with a shot that has no usable take
        REFUSES at compile (ok=False, render_path=None, zero finals written);
        it never silently renders while omitting the failed shot. The audition
        target's slate tolerance is the DELIBERATE optional-edge analog.
        => the truth table already holds; WP2 = no production change.
  * c2  NOT-APPLICABLE — Manju models no multi-parent content-handoff concept
        (grep-zero); the handoff semantics live in the new pure module + its
        synthetic-graph tests (test_dr03b_graphdiag.py test 9).
  * c3/c4 (input mutation / dict-order) live in the new module (see graphdiag
        tests 10/11/12).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg required (real build)")

SRC = Path(__file__).resolve().parents[1] / "src" / "manju"


def _real_clip(project) -> Path:
    clip = project.runtime_dir / "c.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-shortest", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    return clip


# ----------------------------------------------------------------------- c1


@needs_ffmpeg
def test_c1_final_build_refuses_when_a_required_shot_has_no_usable_take(
    tmp_project, add_shot, monkeypatch
):
    """A shot whose generation FAILS on every provider (no usable take) makes a
    final-target build stop honestly: the compile refuses (ok=False), NO final
    is rendered, and the shot's required content is never silently omitted.

    This is the truth-table pin: a FAILED required predecessor BLOCKS the
    successor. Manju enforces it structurally — gather_compile_input lists the
    unresolved shot and raises CompileError, which _run_build_phases turns into
    ok=False before the render step ever runs."""
    from manju.build.graph import run_build
    from manju.core.failures import read_failures
    from manju.providers.base import FailureKind, ProviderFailure
    from manju.providers.manual import register_manual_take

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    # S001 gets a real, usable, human-imported take (MANUAL → usable).
    take = register_manual_take(tmp_project, "S001", _real_clip(tmp_project))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )

    # S002: every provider (including the caption_card terminal) fails — a
    # genuine "produced nothing" shot, exercised through the real generate path.
    def _boom(req, chain=None):
        raise ProviderFailure(FailureKind.provider_error, "all providers failed (c1 pin)")

    monkeypatch.setattr("manju.providers.registry.generate_with_fallback", _boom)

    finals_before = sorted(tmp_project.final_dir.glob("final_v*.mp4")) \
        if tmp_project.final_dir.exists() else []
    result = run_build(tmp_project, target="final", assume_yes=True)

    # honest block, no silent omission
    assert result.ok is False
    assert result.render_path is None, "no final may be rendered while S002 is unresolved"
    finals_after = sorted(tmp_project.final_dir.glob("final_v*.mp4")) \
        if tmp_project.final_dir.exists() else []
    assert finals_after == finals_before, "a refused build writes no final"
    assert any("S002" in e for e in result.errors), result.errors

    # the failure is debuggable at BOTH the generate step (the shot produced
    # nothing) and the compile step (the timeline could not be assembled)
    recs = read_failures(tmp_project, 20, level="error")
    steps = {(r["step"], r["subject"]) for r in recs}
    assert ("generate", "S002") in steps
    assert any(s == "compile" for s, _ in steps)


@needs_ffmpeg
def test_c1_audition_target_tolerates_missing_take_via_slate_by_design(
    tmp_project, add_shot
):
    """The DELIBERATE optional-edge analog: the SAME missing-take situation that
    blocks a final build is TOLERATED by the audition target, which composes an
    in-memory timeline with a slate placeholder for the missing picture and
    renders it — never touching the real timeline.json. This is explicit design
    (§WP2 audition), not a truth-table violation, so it is pinned too."""
    from manju.build.graph import run_build
    from manju.providers.manual import register_manual_take

    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")  # stays MISSING (gen off below)
    take = register_manual_take(tmp_project, "S001", _real_clip(tmp_project))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )

    result = run_build(tmp_project, target="audition", gen="off", assume_yes=True)
    assert result.ok is True, result.errors
    assert result.render_path is not None and "audition" in result.render_path
    # the real timeline was NOT written by the audition path
    assert result.timeline_path is None
    assert any("slate" in w for w in result.warnings), result.warnings


# ----------------------------------------------------------------------- c2


def test_c2_no_multiparent_handoff_concept_in_manju():
    """Manju models NO explicit multi-parent content-handoff / GenerationUnit /
    task-dependency concept — so c2 (multi-parent handoff) is NOT-APPLICABLE to
    Manju's own graph and lives entirely in the new pure module. Pinned with a
    grep over src/manju (the same evidence the baseline records verbatim)."""
    import re

    text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in SRC.rglob("*.py")
        if p.name != "graphdiag.py"  # the new module deliberately INTRODUCES the concept
    )
    # a genuine handoff/edge-graph vocabulary is absent from the modeled build
    for token in ("handoff_from", "GenerationUnit", "depends_on", "predecessors["):
        assert token not in text, f"unexpected {token!r} — Manju would model handoff after all"
    # the words that DO appear are unrelated (prompt fragments, BGM trim handoff);
    # none denotes an inter-node dependency edge
    assert not re.search(r"\bindegree\b|\btopological_sort\b|networkx", text)


def test_c2_no_explicit_dependency_edge_graph():
    """The orchestrator's calibration, re-verified locally: zero explicit
    edge-based dependency graph anywhere in src/manju (outside the new module)."""
    hits = []
    for p in SRC.rglob("*.py"):
        if p.name == "graphdiag.py":
            continue
        body = p.read_text(encoding="utf-8", errors="ignore")
        for token in ("depends_on", "def topological", "networkx", "in_degree", "indegree"):
            if token in body:
                hits.append((p.name, token))
    assert hits == [], f"expected zero explicit-edge-graph tokens, found {hits}"
