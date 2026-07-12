"""FP rational-edit-rate migration, STAGE 1 (R1): the OPTIONAL ``edit_rate``
truth field on ``ProjectConfig`` + the single ``Project.edit_rate`` accessor.

The registry-declared migration (CONTRACTS.yaml ``planned_migrations``:
``project.fps`` int -> rational ``edit_rate``) begins here, STAGED. R1 lands the
truth field and the one accessor with **ZERO behavior change**: the engine's
output for every existing (edit_rate-less) project must be BYTE-IDENTICAL. This
suite is the proof — it pins each surface the addendum enumerated:

  * ProjectConfig serialization drops a None edit_rate entirely (the ONLY
    surface R1 actually touches — presets/supportbundle/status dump config with
    a plain ``model_dump()``, so the drop must not depend on ``exclude_none``);
  * the compiled timeline, ``spec_hash``, ``_segment_cache_key`` and
    ``snap_to_frame_grid`` are value-identical (expected values pinned as
    LITERALS, never derived from old code) — these read an int ``fps`` /
    ``ShotSpec``, never ``ProjectConfig``, so R1 is structurally immune;
  * the compat corpus project.yaml still loads without a rewrite;
  * an edit_rate-bearing project round-trips its exact ``num/den``;
  * a contradictory edit_rate/fps pair, and garbage rates, are STRUCTURED
    errors (also through ``manju check``);
  * the accessor returns the exact rational truth, else the promoted int fps;
  * the only-two-files grep pin against premature plumbing.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from manju.core.check import run_check
from manju.core.container import Project
from manju.core.hashing import cache_key, hash_file
from manju.core.models import (
    Dialogue,
    EditRate,
    ProjectConfig,
    ShotSpec,
    TimelineRules,
    VideoClip,
)
from manju.core.spec import compute_spec_hash, spec_payload
from manju.core.timebase import Rate
from manju.core.yamlio import read_yaml, write_yaml
from manju.media.render import _segment_cache_key
from manju.timeline.compiler import (
    CompileInput,
    ShotInput,
    compile_timeline,
    snap_to_frame_grid,
)

COMPAT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "compat" / "project.yaml"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# =====================================================================
# 1. BYTE-IDENTITY — the serialization surface R1 actually touches
# =====================================================================

# The pre-R1 shape of a default ProjectConfig, captured as a LITERAL. R1 adds an
# OPTIONAL edit_rate field that, when absent (the default), must NOT appear in
# EITHER dump form — else presets/supportbundle/status (plain model_dump) and
# save_config (exclude_none) would emit a spurious `edit_rate: null`.
_EXCLUDE_NONE_LITERAL = {
    "name": "t",
    "width": 1080,
    "height": 1920,
    "fps": 24,
    "mode": "copilot",
    "ask_before": ["expensive_generation", "final_export", "lock_change"],
    "budget": {"currency": "CNY"},
    "export_profiles": ["srt"],
    "preset": "generic",
}
_PLAIN_LITERAL = {
    "name": "t",
    "width": 1080,
    "height": 1920,
    "fps": 24,
    "mode": "copilot",
    "build": None,
    "ask_before": ["expensive_generation", "final_export", "lock_change"],
    "budget": {"limit": None, "currency": "CNY"},
    "export_profiles": ["srt"],
    "preset": "generic",
    "agent": None,
}


def test_no_edit_rate_config_dumps_are_byte_identical_to_pre_r1():
    """A config that never sets edit_rate serializes exactly as before — no
    edit_rate key in exclude_none OR plain model_dump()."""
    c = ProjectConfig(name="t")
    assert c.edit_rate is None
    assert c.model_dump(exclude_none=True) == _EXCLUDE_NONE_LITERAL
    assert c.model_dump() == _PLAIN_LITERAL
    assert "edit_rate" not in c.model_dump(exclude_none=True)
    assert "edit_rate" not in c.model_dump()  # the drop-None serializer, not exclude_none


def test_save_config_writes_no_edit_rate_line(tmp_project):
    """save_config on an edit_rate-less config writes bytes with no such key,
    and reloads byte-stably."""
    project = tmp_project
    config = project.load_config()
    assert config.edit_rate is None
    project.save_config(config)
    text = (project.root / "project.yaml").read_text(encoding="utf-8")
    assert "edit_rate" not in text
    # a second save is byte-stable (no churn introduced by the new field)
    before = _sha256(project.root / "project.yaml")
    project.save_config(project.load_config())
    assert _sha256(project.root / "project.yaml") == before


def test_compat_corpus_project_loads_hash_identical(tmp_path):
    """The declared-migration compat fixture (old int-fps shape) still loads
    read-only, with edit_rate absent — mirrors test_fp_compat, never touches it."""
    dest = tmp_path / "old.manju"
    dest.mkdir()
    (dest / "project.yaml").write_bytes(COMPAT_FIXTURE.read_bytes())
    before = _sha256(dest / "project.yaml")
    config = Project(dest).load_config()
    assert config.name == "旧项目雨夜"
    assert config.fps == 24
    assert config.edit_rate is None  # pre-migration shape: no rational field
    assert _sha256(dest / "project.yaml") == before, "load_config rewrote the old project.yaml"


# =====================================================================
# 2. BYTE-IDENTITY — surfaces that read int fps / ShotSpec, never the config
#    (pinned as literals; structurally immune to the additive field)
# =====================================================================

# LITERAL spec_hash of a fixed shot (picture-only payload; fps/edit_rate never
# participate). These are the invariant pre/post-R1 values — spec.py and the
# picture-relevant ShotSpec fields are untouched by R1.
_SPEC_HASH_V1 = "sha256:27880f11b4ada44bd760502aaee59404239debe0686693eea2c05e1bc2825b3c"
_SPEC_HASH_V2 = "sha256:574ff2f86db208f1dbc8c3e4893cb7eba74bd66ec1323f40e11253f95e9e7d68"


def _fixed_shot() -> ShotSpec:
    return ShotSpec(id="S001", scene="convenience_store",
                    dialogue=Dialogue(speaker="linxia", text="hi"))


def test_spec_hash_is_unchanged_and_rate_free():
    shot = _fixed_shot()
    payload = spec_payload(shot)
    assert "fps" not in payload and "edit_rate" not in payload  # rate never restages picture
    assert compute_spec_hash(shot) == _SPEC_HASH_V1
    assert compute_spec_hash(shot, version=2) == _SPEC_HASH_V2


def test_snap_to_frame_grid_is_unchanged():
    """The frame-grid snap still keys off the int fps — pinned literals."""
    assert snap_to_frame_grid(1200, 24) == 1208
    assert snap_to_frame_grid(1000, 24) == 1000
    assert snap_to_frame_grid(3000, 25) == 3000
    assert snap_to_frame_grid(4000, 24) == 4000


def test_segment_cache_key_folds_int_fps_and_nothing_rational(tmp_project):
    """The segment key is composed of exactly (source hash, w, h, INT fps,
    duration, target, fades) for a default clip — R1 folds NOTHING rate-rational
    into it. Re-derived from primitives so it never hardcodes a media hash."""
    project = tmp_project
    src = project.root / "seg_src.bin"
    src.write_bytes(b"deterministic-segment-source-bytes")
    clip = VideoClip(shot="S001", take="take_01", source="seg_src.bin",
                     start_ms=0, duration_ms=4000)
    key = _segment_cache_key(project, clip, width=1080, height=1920, fps=24,
                             target="final", fade_in_ms=0, fade_out_ms=0)
    expected = cache_key(hash_file(src), 1080, 1920, 24, 4000, "final", 0, 0)
    assert key == expected


def _compile_one_shot(config: ProjectConfig):
    shot = ShotInput(
        shot=ShotSpec(id="S001", duration="auto",
                      dialogue=Dialogue(speaker="linxia", text="雨夜")),
        take_name="take_01", take_source="gen/S001/take_01.mp4",
        take_duration_ms=4000, voice_source=None, voice_duration_ms=None,
    )
    return compile_timeline(CompileInput(config=config, rules=TimelineRules(), shots=[shot]))


def test_compiled_timeline_has_no_edit_rate_and_pinned_fps():
    """Compiling with an edit_rate-less config yields the same timeline as before:
    int fps 24, no edit_rate anywhere, deterministic bytes."""
    config = ProjectConfig(name="t", fps=24)
    tl = _compile_one_shot(config)
    j = tl.model_dump_json()
    assert "edit_rate" not in j
    assert tl.fps == 24 and tl.width == 1080 and tl.height == 1920
    assert tl.duration_ms == 4000
    vc = tl.tracks.video[0]
    assert (vc.shot, vc.take, vc.start_ms, vc.duration_ms) == ("S001", "take_01", 0, 4000)
    # determinism: same input -> byte-identical json
    assert _compile_one_shot(ProjectConfig(name="t", fps=24)).model_dump_json() == j


# =====================================================================
# 3. THE ACCESSOR — Project.edit_rate -> timebase.Rate
# =====================================================================

def test_accessor_default_is_promoted_int_fps(tmp_project):
    """fps=24, no edit_rate => Rate(24/1), exact_int == 24, nominal_int == 24."""
    project = tmp_project
    rate = project.edit_rate()
    assert isinstance(rate, Rate)
    assert rate == Rate.from_fraction(24, 1)
    assert rate.exact_int == 24
    assert rate.nominal_int == 24
    assert rate.is_ntsc is False


def test_accessor_reads_rational_field_when_present(tmp_project):
    """A declared edit_rate {24000,1001} (with fps 24) => the EXACT Rate, NTSC."""
    project = tmp_project
    config = ProjectConfig(name="t", fps=24, edit_rate={"num": 24000, "den": 1001})
    rate = project.edit_rate(config)
    assert rate == Rate.from_fraction(24000, 1001)
    assert (rate.numerator, rate.denominator) == (24000, 1001)
    assert rate.is_ntsc is True
    assert rate.nominal_int == 24  # the legacy fps mirror
    assert rate.exact_int is None  # not a whole number


def test_accessor_loads_fresh_when_no_config_passed(tmp_project):
    """Omitting config reads project.yaml fresh (mirrors load_config().fps)."""
    project = tmp_project
    project.save_config(ProjectConfig(name="t", fps=25))
    assert project.edit_rate() == Rate.from_fraction(25, 1)


# =====================================================================
# 4. VALIDATION — structured errors, never a silent split truth
# =====================================================================

def test_edit_rate_ntsc_with_matching_fps_loads():
    c = ProjectConfig(name="t", fps=24, edit_rate={"num": 24000, "den": 1001})
    assert c.edit_rate == EditRate(num=24000, den=1001)
    assert c.edit_rate.rate.nominal_int == c.fps


def test_edit_rate_mismatched_fps_is_structured_error():
    """edit_rate 24000/1001 (nominal 24) with fps 25 => error naming both."""
    with pytest.raises(ValidationError) as exc:
        ProjectConfig(name="t", fps=25, edit_rate={"num": 24000, "den": 1001})
    msg = str(exc.value)
    assert "25" in msg and "24" in msg  # names the offending fps and the nominal it must be


@pytest.mark.parametrize("bad", [
    {"num": 0, "den": 1},        # zero numerator
    {"num": 24, "den": 0},       # zero denominator
    {"num": -24, "den": 1},      # negative numerator
    {"num": 24, "den": -1},      # negative denominator
    {"num": 2000, "den": 1},     # absurd: 2000 fps > sanity bound
    {"num": 1, "den": 2},        # absurd: 0.5 fps < sanity bound
    {"num": True, "den": 1},     # bool is not a frame-rate term
])
def test_garbage_edit_rate_is_rejected(bad):
    with pytest.raises(ValidationError):
        EditRate(**bad)


def test_manju_check_surfaces_mismatched_edit_rate(tmp_project):
    """An invalid edit_rate/fps pair surfaces as a structured `manju check`
    error (free via the pydantic config-validation path — round-W precedent)."""
    project = tmp_project
    # hand-write a contradictory project.yaml (fps 25 vs 24000/1001 -> nominal 24)
    write_yaml(project.root / "project.yaml", {
        "name": "t", "width": 1080, "height": 1920, "fps": 25,
        "edit_rate": {"num": 24000, "den": 1001}, "mode": "copilot",
    })
    report = run_check(project)
    assert not report.ok
    assert any("project.yaml" in e and "schema invalid" in e for e in report.errors)
    assert any("edit_rate" in e or "24000/1001" in e for e in report.errors)


# =====================================================================
# 5. YAML ROUND-TRIP — an edit_rate-bearing project keeps its exact num/den
# =====================================================================

def test_edit_rate_yaml_round_trips_exactly(tmp_project):
    project = tmp_project
    write_yaml(project.root / "project.yaml", {
        "name": "ntsc片", "width": 1080, "height": 1920, "fps": 24,
        "edit_rate": {"num": 24000, "den": 1001}, "mode": "copilot",
    })
    config = project.load_config()
    assert config.edit_rate == EditRate(num=24000, den=1001)
    assert (config.edit_rate.num, config.edit_rate.den) == (24000, 1001)

    # save + reload preserves the exact rational and the fps mirror
    project.save_config(config)
    reloaded = project.load_config()
    assert (reloaded.edit_rate.num, reloaded.edit_rate.den) == (24000, 1001)
    assert reloaded.fps == 24
    raw = read_yaml(project.root / "project.yaml")
    assert raw["edit_rate"] == {"num": 24000, "den": 1001}  # present + exact on disk


# =====================================================================
# 6. GREP PIN — the only-two-files guard against premature plumbing
# =====================================================================

def test_edit_rate_only_referenced_in_models_and_container():
    """R1 landed the ``edit_rate`` field + accessor in core/models.py +
    core/container.py and NOTHING else — the guard against premature plumbing
    "ahead of its staged loop (R2+)". That staged loop has now landed, so the
    guard is updated (as R1 anticipated) to permit ONLY the sanctioned R2+
    consumers while keeping full teeth everywhere else:

      * the field is still DECLARED in the R1 surface (core/models.py +
        core/container.py);
      * the R2 rational BUILD SPINE stays token-free — timeline/compiler,
        media/render, media/normalize, media/ffmpeg and build/graph dispatch on
        an exact :class:`~manju.core.timebase.Rate` via the typed resolvers
        ``ProjectConfig.frame_rate`` / ``Timeline.frame_rate`` and NEVER touch
        the raw field, so a regression that pokes ``edit_rate`` from the
        compile/render/graph path is caught HERE (those files are not
        sanctioned below → they land in ``unexpected``);
      * the only OTHER sanctioned consumers are the interchange/CLI surface —
        the ``exporters/`` package and ``cli.py`` (the parallel export loop's
        rational-rate OTIO/EDL/TTML export);
      * it still leaks into NOTHING else (gui/qc/providers/board/runtime/…)."""
    src_root = Path(__file__).resolve().parent.parent / "src" / "manju"
    hits = sorted(
        p.relative_to(src_root).as_posix()
        for p in src_root.rglob("*.py")
        if "edit_rate" in p.read_text(encoding="utf-8")
    )
    # R1 surface still owns the declaration.
    r1_surface = ["core/container.py", "core/models.py"]
    assert all(f in hits for f in r1_surface), (
        f"edit_rate vanished from the R1 surface: {hits}"
    )
    # Every other reference must be a SANCTIONED R2+ consumer: the exporters
    # package or the CLI. Anything else — crucially the R2 build spine
    # (timeline/compiler.py, media/render.py, build/graph.py, …), or any
    # gui/qc/providers/board/runtime module — is a leak and fails here.
    sanctioned_prefixes = ("exporters/", "cli.py")
    unexpected = [
        h for h in hits
        if h not in r1_surface and not h.startswith(sanctioned_prefixes)
    ]
    assert unexpected == [], (
        f"edit_rate leaked into unsanctioned src files (the R2 build spine reads "
        f"the frame_rate resolver, never the raw field): {unexpected}"
    )
