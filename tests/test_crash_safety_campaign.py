"""崩溃安全战役 (owner-funded hardening round): property invariants over the
pure cores + real SIGKILL injection over a real build.

Two families:

1. **Properties** (hypothesis, pure, fast) — the rational-timebase grid stays
   drift-free under round-trips, the event-detail brief is bounded and never
   leaks a Python repr, and the locale line-state machine answers exactly one
   honest state per (base, entry) world.

2. **Crash injection** (ffmpeg, slow lane) — a `manju build` subprocess is
   SIGKILLed at a random point, after which every §3 discipline must hold on
   disk: truth YAML parses whole (atomic writes — no torn files), pre-existing
   media bytes are untouched (append-only), the events tail still reads, the
   disposable runtime rebuilds, `manju check` passes, and a follow-up build
   completes green. Three kill points per run keep the suite honest without
   soaking CI; the loop is a real process kill, not a mock.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from manju.core.events import event_detail_brief
from manju.core.timebase import Rate, frames_to_ms, ms_to_frames

# ------------------------------------------------------- timebase properties

_rates = st.sampled_from([
    Rate.from_fraction(24), Rate.from_fraction(25), Rate.from_fraction(30),
    Rate.from_fraction(60), Rate.from_fraction(24000, 1001),
    Rate.from_fraction(30000, 1001), Rate.from_fraction(60000, 1001),
])


@given(frames=st.integers(0, 10**7), rate=_rates)
@settings(max_examples=200)
def test_frame_grid_roundtrip_is_identity(frames, rate):
    """R2's load-bearing promise: the ms projection of a frame index maps back
    to the SAME frame — no cumulative drift, at any distance from zero, for
    int and 1001-family rates alike."""
    assert ms_to_frames(frames_to_ms(frames, rate), rate) == frames


@given(frames=st.integers(0, 10**6), rate=_rates)
@settings(max_examples=200)
def test_frame_to_ms_error_stays_under_half_frame(frames, rate):
    """The int-ms projection may round, but never by half a frame or more —
    the bound that keeps long concats from sliding off the grid."""
    ms = frames_to_ms(frames, rate)
    exact_ms = Fraction(frames) * Fraction(1000) / rate.fraction
    half_frame_ms = Fraction(1000) / rate.fraction / 2
    assert abs(Fraction(ms) - exact_ms) < half_frame_ms


# ------------------------------------------------- event brief properties

_detail_values = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(-10**6, 10**6),
              st.text(max_size=120)),
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=12), children, max_size=5),
    ),
    max_leaves=25,
)


@given(detail=st.dictionaries(st.text(min_size=1, max_size=16), _detail_values,
                              max_size=10))
@settings(max_examples=150)
def test_event_brief_is_bounded_and_never_a_repr(detail):
    """The one-line digest must stay a one-line digest for ANY detail shape:
    no Python dict repr, no unbounded value, elision always announced."""
    brief = event_detail_brief(detail)
    assert "\n" not in brief
    assert "{'" not in brief and "': " not in brief
    shown = min(len(detail), 4)
    if len(detail) > shown:
        assert "→ --json" in brief
    for part in brief.split(", "):
        if "=" in part:
            assert len(part.split("=", 1)[1]) <= 60


# -------------------------------------------- locale line-state properties


@given(base=st.text(max_size=40), text=st.text(max_size=40),
       stale=st.booleans(), row_exists=st.booleans())
@settings(max_examples=150, deadline=None)
def test_line_state_machine_answers_exactly_one_honest_state(
        tmp_path_factory, base, text, stale, row_exists):
    """For every (base dialogue, translation row) world the state must be the
    documented one: not_needed iff nothing to translate and nothing written;
    missing iff base exists and no usable translation; 翻译过期 iff a written
    translation's stored hash no longer matches; ok otherwise."""
    from manju.core.container import Project
    from manju.core.locale import base_text_hash, line_status, locale_dir
    from manju.core.models import ShotSpec
    from manju.core.yamlio import write_yaml

    root = tmp_path_factory.mktemp("ls")
    project = Project.create(root / "p", git_init=False)
    shot = ShotSpec.model_validate({
        "id": "S001", "scene": "", "duration": "auto",
        "dialogue": {"speaker": "x", "text": base},
    })
    project.save_shot(shot)
    index = project.load_index()
    index.order.append("S001")
    project.save_index(index)

    d = locale_dir(project, "en")
    d.mkdir(parents=True, exist_ok=True)
    if row_exists:
        stored = "sha256:deadbeef" if stale else base_text_hash(base)
        write_yaml(d / "lines.yaml",
                   {"S001": {"text": text, "base_hash": stored}})
    else:
        write_yaml(d / "lines.yaml", {})

    state = line_status(project, "en", "S001")["state"]
    has_base = bool(base.strip())
    has_text = bool(text.strip()) and row_exists
    if not has_text:
        assert state == ("missing" if has_base else "not_needed")
    elif stale:
        assert state == "翻译过期"
    else:
        assert state == "ok"


# ------------------------------------------------------- crash injection


pytestmark_ffmpeg = pytest.mark.ffmpeg

KILL_ROUNDS = int(os.environ.get("MANJU_CRASH_ROUNDS", "3"))


def _make_project(root: Path):
    from manju.core.container import Project
    from manju.core.yamlio import write_yaml

    project = Project.create(root / "crash", git_init=False)
    write_yaml(project.root / "bible" / "scenes.yaml", {"s": {"name": "场"}})
    for i in (1, 2, 3):
        write_yaml(project.shots_dir / f"S00{i}.yaml", {
            "id": f"S00{i}", "scene": "s", "duration": 2.0,
            "action": {"main": f"第 {i} 段占位画面。", "emotion": "—"},
        })
    write_yaml(project.shots_dir / "index.yaml",
               {"order": ["S001", "S002", "S003"], "defaults": {}})
    return project


def _truth_files(root: Path):
    for sub in ("shots", "bible", "timeline", "story"):
        d = root / sub
        if d.is_dir():
            yield from (p for p in sorted(d.rglob("*.yaml")))
    if (root / "project.yaml").exists():
        yield root / "project.yaml"


def _assert_disciplines(project, media_before: dict[Path, bytes]) -> None:
    import yaml as _yaml

    from manju.core.check import run_check
    from manju.core.events import tail_events
    from manju.runtime.state import RuntimeState

    # 1) no torn truth: every YAML parses whole (atomic_write_text discipline)
    for p in _truth_files(project.root):
        _yaml.safe_load(p.read_text(encoding="utf-8"))
    # 2) append-only media: bytes that existed before the kill are untouched
    for path, blob in media_before.items():
        assert path.exists() and path.read_bytes() == blob, path
    # 3) the events tail still reads (a torn final line is skipped, never a crash)
    tail_events(project.root, 50)
    # 4) the disposable runtime opens and rebuilds from truth
    with RuntimeState(project.root) as state:
        state.rebuild(project)
    # 5) the safety net passes
    assert run_check(project).ok


@pytest.mark.ffmpeg
def test_sigkill_mid_build_never_breaks_the_disciplines(tmp_path):
    """Kill -9 a real `manju build` at staggered points; after every kill the
    §3 disciplines must hold and a follow-up build must complete green. This
    is the recovery story the owner actually lives (power loss, task-manager
    kill), executed for real — no mocks, no signal handlers to cushion it."""
    project = _make_project(tmp_path)
    env = {**os.environ, "PYTHONUTF8": "1"}

    delays = [0.9 + 0.8 * i for i in range(KILL_ROUNDS)]
    killed = 0
    for delay in delays:
        media_before = {p: p.read_bytes()
                        for p in sorted(project.root.rglob("media/gen/**/*.mp4"))}
        proc = subprocess.Popen(
            [sys.executable, "-m", "manju.cli", "build"],
            cwd=project.root, env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(delay)
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=30)
            killed += 1
        _assert_disciplines(project, media_before)

    # the loop must have actually murdered at least one in-flight build —
    # otherwise this test proved nothing and must say so, not pass quietly.
    assert killed >= 1, "every build finished before its kill fired — increase delays"

    # recovery: a clean follow-up build completes and the film exists
    done = subprocess.run(
        [sys.executable, "-m", "manju.cli", "build"],
        cwd=project.root, env=env, capture_output=True, text=True, timeout=600,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    finals = list((project.root / "renders" / "final").glob("final_v*.mp4"))
    assert finals, "no final after recovery build"
    _assert_disciplines(project, {})
