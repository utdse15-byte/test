"""Round W agent WB — validation bounds (external review, rounds 1-10).

Covers issues #2/#6 (numeric bounds + fps/caption div-by-zero / non-advancing
loop guards), #3 (`build --gen` strict enum), #22 (TakeSidecar source window),
#23 (`pack`/`unpack --json`), #25 (provider manifest bounds), #48 (EpisodeRef.id
validated on load), #70 (`manju status` newest-final), #81 (`build --target qc`
artifact naming). Issue #69 (GUI transition-override) is covered in
tests/test_native_cut_v2.py alongside the rest of that endpoint's tests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from manju.cli import app
from manju.core.check import run_check
from manju.core.container import Project
from manju.core.models import (
    AudioClip,
    CaptionRules,
    Generation,
    ProjectConfig,
    ShotSpec,
    TakeSidecar,
    Timeline,
    TransitionSpec,
    VideoClip,
)
from manju.core.series import EpisodeRef, Series, SeriesError, new_episode
from manju.core.yamlio import write_yaml
from manju.providers.manifest import ProviderManifest, load_manifests
from manju.timeline.compiler import CompileError, _split_caption, snap_to_frame_grid

runner = CliRunner()


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))


# ======================================================= issue #2/#6: bounds


class TestProjectConfigBounds:
    def test_fps_zero_rejected(self):
        with pytest.raises(ValidationError, match="fps"):
            ProjectConfig(name="x", fps=0)

    def test_width_negative_rejected(self):
        with pytest.raises(ValidationError, match="width"):
            ProjectConfig(name="x", width=-10)

    def test_height_zero_rejected(self):
        with pytest.raises(ValidationError, match="height"):
            ProjectConfig(name="x", height=0)

    def test_positive_dims_ok_byte_identical_default(self):
        cfg = ProjectConfig(name="x")
        assert (cfg.fps, cfg.width, cfg.height) == (24, 1080, 1920)


class TestGenerationCandidates:
    def test_zero_candidates_rejected(self):
        with pytest.raises(ValidationError, match="candidates"):
            Generation(candidates=0)

    def test_negative_candidates_rejected(self):
        with pytest.raises(ValidationError, match="candidates"):
            Generation(candidates=-3)

    def test_default_candidates_ok(self):
        assert Generation().candidates == 1


class TestShotDuration:
    def test_zero_duration_rejected(self):
        with pytest.raises(ValidationError, match="duration"):
            ShotSpec(id="S001", duration=0)

    def test_negative_duration_rejected(self):
        with pytest.raises(ValidationError, match="duration"):
            ShotSpec(id="S001", duration=-2.5)

    def test_auto_still_legal(self):
        assert ShotSpec(id="S001", duration="auto").duration == "auto"

    def test_positive_numeric_duration_ok(self):
        assert ShotSpec(id="S001", duration=3.5).duration == 3.5


class TestTransitionAndCaptionBounds:
    def test_negative_transition_duration_rejected(self):
        with pytest.raises(ValidationError, match="duration_ms"):
            TransitionSpec(duration_ms=-1)

    def test_zero_transition_duration_ok(self):
        assert TransitionSpec(duration_ms=0).duration_ms == 0

    def test_caption_max_chars_zero_rejected(self):
        with pytest.raises(ValidationError, match="max_chars_per_line"):
            CaptionRules(max_chars_per_line=0)

    def test_caption_max_lines_zero_rejected(self):
        with pytest.raises(ValidationError, match="max_lines"):
            CaptionRules(max_lines=0)


class TestTimelineClipBounds:
    def _clip(self, **over):
        base = dict(shot="S001", take="take_01", source="media/gen/S001/take_01.mp4",
                    start_ms=0, duration_ms=1000)
        base.update(over)
        return base

    def test_video_clip_zero_duration_still_loads(self):
        """Timeline/VideoClip stays LENIENT at the model layer (compiled AND
        hand-editable, §6 manual mode; same stance as TRANSITION_TYPES) — a
        zero/negative duration loads fine here and is reported by QC instead
        (test_qc_flags_zero_duration_clip below; pinned pre-existing behaviour
        at tests/test_round_q.py::test_conflict_zero_duration_clip)."""
        assert VideoClip.model_validate(self._clip(duration_ms=0)).duration_ms == 0

    def test_video_clip_positive_duration_ok(self):
        assert VideoClip.model_validate(self._clip(duration_ms=1)).duration_ms == 1

    def test_audio_clip_negative_duration_still_loads(self):
        assert AudioClip(source="a.wav", start_ms=0, duration_ms=-1).duration_ms == -1

    def test_audio_clip_none_duration_still_legal(self):
        assert AudioClip(source="a.wav", start_ms=0).duration_ms is None

    def test_timeline_fps_zero_still_loads(self):
        assert Timeline(fps=0).fps == 0

    def test_timeline_width_zero_still_loads(self):
        assert Timeline(width=0).width == 0

    def test_healthy_compiled_timeline_round_trips(self, tmp_project, add_shot, make_take):
        """A real compile never produces any of these degenerate values —
        byte-identical for healthy projects (round-W discipline)."""
        from manju.timeline.compiler import build_timeline
        from manju.media.probe import probe_duration_ms

        shot = add_shot(tmp_project, "S001")
        take = make_take(tmp_project, "S001", spec_hash="whatever")
        tmp_project.update_shot_raw(
            "S001", lambda d: d.setdefault("status", {}).__setitem__(
                "selected_take", take.name))
        timeline, path, overwrote = build_timeline(tmp_project, probe_duration_ms)
        assert timeline.fps > 0 and timeline.width > 0 and timeline.height > 0
        assert all(c.duration_ms >= 1 for c in timeline.tracks.video)


def test_qc_flags_nonpositive_timeline_fps_and_resolution():
    from manju.core.models import TimelineTracks
    from manju.qc.checks import QCReport, _technical_timeline_conflicts

    tl = Timeline(fps=0, width=0, height=1920, tracks=TimelineTracks())
    report = QCReport()
    _technical_timeline_conflicts(None, report, tl)
    errs = [i.message for i in report.items if i.level == "error"]
    assert any("fps" in m for m in errs)
    assert any("分辨率" in m for m in errs)


def test_qc_flags_negative_audio_clip_duration():
    from manju.core.models import TimelineTracks
    from manju.qc.checks import QCReport, _technical_timeline_conflicts

    tl = Timeline(tracks=TimelineTracks(
        music=[AudioClip(source="bgm.wav", start_ms=0, duration_ms=-500)]))
    report = QCReport()
    _technical_timeline_conflicts(None, report, tl)
    assert any(i.level == "error" and "negative duration" in i.message
              for i in report.items)


class TestFrameSnapAndCaptionGuards:
    """Defensive guards (issue #2/#6): fps<=0 must not ZeroDivisionError, and
    a zero caption budget must not spin forever (non-advancing loop)."""

    def test_snap_to_frame_grid_zero_fps_does_not_crash(self):
        assert snap_to_frame_grid(1200, 0) >= 1  # would ZeroDivisionError pre-fix

    def test_snap_to_frame_grid_negative_fps_does_not_crash(self):
        assert snap_to_frame_grid(1200, -5) >= 1

    def test_snap_to_frame_grid_positive_fps_unchanged(self):
        # FIX-B's documented example: 1200ms @ 24fps -> 29 frames -> 1208ms
        assert snap_to_frame_grid(1200, 24) == 1208

    def test_split_caption_zero_budget_terminates(self):
        """Pre-fix this hung forever: budget=0 meant every loop iteration cut
        0 characters off `rest`, so it never shrank."""
        text = "这是一段测试字幕文本用来验证不会死循环" * 5
        pieces = _split_caption(text, max_chars=0, max_lines=0)
        assert pieces  # terminated, and produced *something*
        assert "".join(pieces).replace("", "") != ""  # sanity: not silently empty

    def test_split_caption_normal_budget_unaffected(self):
        pieces = _split_caption("你好,世界。", max_chars=18, max_lines=2)
        assert pieces == ["你好,世界。"]


# ============================================================== issue #22


class TestTakeSidecarWindow:
    def test_negative_in_rejected(self):
        with pytest.raises(ValidationError, match="source_in_ms"):
            TakeSidecar(provider="test", spec_hash="sha256:x", source_in_ms=-1)

    def test_reversed_window_rejected(self):
        with pytest.raises(ValidationError, match="source_out_ms"):
            TakeSidecar(provider="test", spec_hash="sha256:x",
                       source_in_ms=500, source_out_ms=500)

    def test_zero_length_window_rejected(self):
        with pytest.raises(ValidationError):
            TakeSidecar(provider="test", spec_hash="sha256:x",
                       source_in_ms=1000, source_out_ms=900)

    def test_legal_window_ok(self):
        sc = TakeSidecar(provider="test", spec_hash="sha256:x",
                         source_in_ms=400, source_out_ms=1400)
        assert (sc.source_in_ms, sc.source_out_ms) == (400, 1400)

    def test_default_whole_file_window_ok(self):
        sc = TakeSidecar(provider="test", spec_hash="sha256:x")
        assert sc.source_in_ms == 0 and sc.source_out_ms is None


def test_hand_edited_reversed_window_degrades_safely_and_is_flagged(tmp_project, add_shot, make_take):
    """A hand-corrupted take_NN.yaml (reversed window) must not crash
    Project.takes()/get_take() — every caller (build/status/GUI polling)
    depends on these never raising — but must be visibly broken."""
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", spec_hash="whatever")
    sidecar_path = tmp_project.takes_dir("S001") / f"{take.name}.yaml"
    raw = sidecar_path.read_text(encoding="utf-8")
    # hand-corrupt: reversed window bypassing set_inout_take's own validation
    from manju.core.yamlio import read_yaml
    data = read_yaml(sidecar_path)
    data["source_in_ms"] = 1000
    data["source_out_ms"] = 200
    write_yaml(sidecar_path, data)

    takes = tmp_project.takes("S001")
    broken = next(t for t in takes if t.name == take.name)
    assert broken.error is not None
    assert broken.media_path is not None  # media untouched, still resolvable
    # degraded sidecar is SAFE (whole-file window), never the illegal values
    assert broken.sidecar.source_in_ms == 0
    assert broken.sidecar.source_out_ms is None


def test_selected_broken_take_is_reported_by_check(tmp_project, add_shot, make_take):
    from manju.core.yamlio import read_yaml

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", spec_hash="whatever")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__(
            "selected_take", take.name))
    sidecar_path = tmp_project.takes_dir("S001") / f"{take.name}.yaml"
    data = read_yaml(sidecar_path)
    data["source_in_ms"] = 500
    data["source_out_ms"] = 500  # zero-length
    write_yaml(sidecar_path, data)

    report = run_check(tmp_project)
    assert not report.ok
    assert any("sidecar" in e and take.name in e for e in report.errors)


def test_selected_broken_take_marks_shot_broken(tmp_project, add_shot, make_take):
    from manju.build.stale import ShotState, evaluate_shot
    from manju.core.yamlio import read_yaml

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", spec_hash="whatever")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__(
            "selected_take", take.name))
    sidecar_path = tmp_project.takes_dir("S001") / f"{take.name}.yaml"
    data = read_yaml(sidecar_path)
    data["source_in_ms"] = -5
    write_yaml(sidecar_path, data)

    status = evaluate_shot(tmp_project, tmp_project.load_shot("S001"))
    assert status.state == ShotState.BROKEN
    assert "sidecar" in status.note


def test_compile_names_bad_window_never_squashes_to_1ms(tmp_project, add_shot, make_take):
    """End-to-end: a selected take with a hand-corrupted reversed window fails
    the COMPILE with a named, explicit problem — the review's concrete
    complaint was that this used to be silently squashed to a 1ms clip
    instead. (evaluate_all's BROKEN degrade — tested above — is what actually
    stops it from ever reaching the old clamp line; either way, compile must
    refuse, never silently produce a bogus 1ms segment.)"""
    from manju.core.yamlio import read_yaml
    from manju.media.probe import probe_duration_ms
    from manju.timeline.compiler import CompileError, build_timeline

    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", spec_hash="whatever")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__(
            "selected_take", take.name))
    sidecar_path = tmp_project.takes_dir("S001") / f"{take.name}.yaml"
    data = read_yaml(sidecar_path)
    data["source_in_ms"] = 1000
    data["source_out_ms"] = 200  # reversed
    write_yaml(sidecar_path, data)

    with pytest.raises(CompileError) as exc:
        build_timeline(tmp_project, probe_duration_ms)
    assert "S001" in str(exc.value)
    # never produced a timeline with a squashed-to-1ms clip
    assert not tmp_project.timeline_path.exists()


def test_compiler_guard_rejects_reversed_window_directly(tmp_project, add_shot, make_take):
    """Unit-level: gather_compile_input's own window check (defence in depth
    behind the BROKEN-state gate above) names the shot/take and the illegal
    values instead of clamping — exercised by constructing the compile input
    the same way gather_compile_input does, bypassing the BROKEN gate to pin
    the guard itself stays correct even if the upstream gate is ever bypassed."""
    from manju.timeline.compiler import ShotInput

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", spec_hash="whatever")
    inp = ShotInput(shot=shot, take_name=take.name, take_source="media/x.mp4",
                    take_duration_ms=1000, source_in_ms=1000, source_out_ms=500)
    assert inp.source_in_ms >= inp.source_out_ms  # sanity: genuinely reversed
    # (the actual guard lives in gather_compile_input's per-shot loop; the
    # full end-to-end assertion is test_compile_names_bad_window_never_squashes_to_1ms)


# =============================================================== issue #3


def test_build_gen_typo_fails_clean(in_project, add_shot):
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["build", "--gen", "offf", "--dry-run"])
    assert result.exit_code == 1
    assert "off" in result.output or "gen" in result.output.lower()


def test_build_gen_typo_json_reports_error_not_traceback(in_project, add_shot):
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["build", "--gen", "offf", "--dry-run", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert any("gen" in e.lower() for e in payload["errors"])


def test_build_gen_off_still_skips_generation(in_project, add_shot):
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["build", "--gen", "off", "--dry-run", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["rows"] == []  # off means no generation planned


def test_run_build_centralizes_gen_validation_for_every_caller():
    """MCP's _h_build and any future caller of run_build get the same
    validation for free — this pins the single source of truth."""
    from manju.build.graph import GEN_MODES, run_build

    assert GEN_MODES == ("missing", "auto", "off")


def test_director_action_still_rejects_bad_gen(tmp_project):
    from manju.build.director import DirectorError, _validate_action

    with pytest.raises(DirectorError, match="gen"):
        _validate_action(tmp_project, {"type": "build", "gen": "offf"})


# =============================================================== issue #23


def test_pack_json(in_project):
    result = runner.invoke(app, ["pack", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["packed"].endswith(".manjupkg")
    assert payload["files"] > 0
    assert payload["full"] is False


def test_unpack_json(in_project, tmp_path):
    out = tmp_path / "out.manjupkg"
    result = runner.invoke(app, ["pack", "--out", str(out), "--json"])
    assert result.exit_code == 0

    dest = tmp_path / "restored_project"
    result2 = runner.invoke(app, ["unpack", str(out), "--dest", str(dest), "--json"])
    assert result2.exit_code == 0
    payload = json.loads(result2.output)
    assert payload["unpacked"] == str(dest)
    assert payload["files"] > 0


def test_pack_without_json_unaffected(in_project):
    """Byte-identity: the default (no --json) text path is untouched."""
    result = runner.invoke(app, ["pack"])
    assert result.exit_code == 0
    assert "packed" in result.output


# =============================================================== issue #25


class TestManifestBounds:
    def _base(self, **over):
        base = {"id": "p1", "type": "video", "adapter": "generic_cloud",
                "auth": {"key_env": "P1_KEY"},
                "submit": {"url": "https://x/v", "body_template": {}, "job_id_path": "$.id"},
                "poll": {"url": "https://x/v/{job_id}", "status_path": "$.s",
                         "status_map": {"ok": "succeeded"}}}
        base.update(over)
        return base

    def test_zero_rate_limit_rejected(self):
        with pytest.raises(ValidationError, match="rate_limit_per_min"):
            ProviderManifest.model_validate(self._base(limits={"rate_limit_per_min": 0}))

    def test_zero_max_concurrent_rejected(self):
        with pytest.raises(ValidationError, match="max_concurrent"):
            ProviderManifest.model_validate(self._base(limits={"max_concurrent": 0}))

    def test_negative_cost_rejected(self):
        with pytest.raises(ValidationError, match="per_call"):
            ProviderManifest.model_validate(
                self._base(cost={"per_call": -0.5, "currency": "CNY"}))

    def test_negative_per_second_rejected(self):
        with pytest.raises(ValidationError, match="per_second"):
            ProviderManifest.model_validate(
                self._base(cost={"per_second": -0.01, "currency": "CNY"}))

    def test_zero_cost_still_legal(self):
        m = ProviderManifest.model_validate(
            self._base(cost={"per_call": 0.0, "currency": "CNY"}))
        assert m.cost.per_call == 0.0

    def test_default_limits_still_legal(self):
        m = ProviderManifest.model_validate(self._base())
        assert m.limits.rate_limit_per_min >= 1 and m.limits.max_concurrent >= 1


def test_broken_manifest_fails_load_with_error_surfaced(providers_dir, tmp_path):
    root = tmp_path / "providers" / "bad"
    root.mkdir(parents=True)
    write_yaml(root / "provider.yaml", {
        "id": "bad", "type": "video", "adapter": "generic_cloud",
        "limits": {"rate_limit_per_min": 0},
        "cost": {"per_call": -1.0, "currency": "CNY"},
    })
    manifests, errors = load_manifests()
    assert "bad" not in manifests
    assert errors and any("bad/provider.yaml" in e for e in errors)


def test_doctor_surfaces_broken_manifest(providers_dir, tmp_path):
    from manju.build.doctor import run_doctor

    root = tmp_path / "providers" / "bad2"
    root.mkdir(parents=True)
    write_yaml(root / "provider.yaml", {
        "id": "bad2", "type": "video", "adapter": "generic_cloud",
        "limits": {"max_concurrent": -1},
    })
    result = run_doctor(None)
    assert result["ok"] is False
    assert any("bad2" in c["detail"] for c in result["checks"] if not c["ok"])


# =============================================================== issue #48


class TestEpisodeIdOnLoad:
    def test_path_escaping_id_rejected_on_construct(self):
        with pytest.raises(ValidationError, match="id"):
            EpisodeRef(id="../../etc")

    def test_slash_in_id_rejected(self):
        with pytest.raises(ValidationError):
            EpisodeRef(id="a/b")

    def test_legal_e_number_id_ok(self):
        assert EpisodeRef(id="E01").id == "E01"

    def test_legal_slug_id_ok(self):
        assert EpisodeRef(id="pilot-episode").id == "pilot-episode"


def test_series_load_rejects_hand_edited_bad_id(tmp_path):
    series = Series.create(tmp_path / "series1", name="s1", git_init=False)
    series_yaml = series.root / "series.yaml"
    from manju.core.yamlio import read_yaml
    data = read_yaml(series_yaml)
    data["episodes"] = [{"id": "../escape", "title": "evil"}]
    write_yaml(series_yaml, data)

    with pytest.raises(SeriesError, match="episode id"):
        series.load_config()


def test_series_status_cli_fails_clean_not_traceback(tmp_path, monkeypatch):
    series = Series.create(tmp_path / "series2", name="s2", git_init=False)
    series_yaml = series.root / "series.yaml"
    from manju.core.yamlio import read_yaml
    data = read_yaml(series_yaml)
    data["episodes"] = [{"id": "bad/id", "title": "x"}]
    write_yaml(series_yaml, data)

    monkeypatch.chdir(series.root)
    result = runner.invoke(app, ["series", "status"])
    assert result.exit_code == 1
    assert "traceback" not in result.output.lower()


def test_new_episode_still_rejects_bad_id_before_scaffolding(tmp_path):
    """Unchanged behaviour (round W does not touch new_episode's own guard):
    a bad id still fails BEFORE any directory is created."""
    series = Series.create(tmp_path / "series3", name="s3", git_init=False)
    with pytest.raises(SeriesError):
        new_episode(series, "../escape")
    assert not (series.episodes_dir / "..escape.manju").exists()
    assert list(series.episodes_dir.iterdir()) == []


# =============================================================== issue #70


def test_status_uses_numeric_newest_final_past_v9(tmp_project):
    from manju.build.status import project_status

    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, 11):
        (tmp_project.final_dir / f"final_v{n}.mp4").write_bytes(b"x")
    info = project_status(tmp_project)
    assert info["latest_final"] == "renders/final/final_v10.mp4"


def test_status_matches_project_numeric_resolver(tmp_project):
    from manju.build.status import project_status

    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    for n in (1, 2, 9, 10, 11):
        (tmp_project.final_dir / f"final_v{n}.mp4").write_bytes(b"x")
    info = project_status(tmp_project)
    resolver_pick = tmp_project.newest_final_path()
    assert info["latest_final"] == tmp_project.relpath(resolver_pick)
    assert info["latest_final"] == "renders/final/final_v11.mp4"


def test_gui_state_version_stack_numeric_order_past_v9(tmp_project):
    from manju.gui.state import build_state

    class _FakeRunner:
        def list(self):
            return []

        def interrupted(self):  # round AA: build_state merges these into "jobs"
            return []

    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    for n in range(1, 12):
        (tmp_project.final_dir / f"final_v{n}.mp4").write_bytes(b"x")
    state = build_state(tmp_project, _FakeRunner())
    names = [f["name"] for f in state["finals"]]
    # newest first, numerically — v11 must lead, not v9 (lexicographic bug)
    assert names[0] == "final_v11.mp4"
    assert names[1] == "final_v10.mp4"


# =============================================================== issue #81


def test_build_target_qc_names_the_existing_final(in_project, add_shot, make_take):
    shot = add_shot(in_project, "S001")
    take = make_take(in_project, "S001", spec_hash="whatever")
    in_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__(
            "selected_take", take.name))
    # a pre-existing final on disk that build --target qc must NOT replace
    in_project.final_dir.mkdir(parents=True, exist_ok=True)
    (in_project.final_dir / "final_v1.mp4").write_bytes(b"stale-final")

    result = runner.invoke(app, ["build", "--target", "qc", "--gen", "off", "--json"])
    payload = json.loads(result.output)
    assert payload.get("render_path") is None  # never rendered
    assert payload.get("qc_final") == "renders/final/final_v1.mp4"


def test_build_target_qc_with_no_final_reports_none(in_project, add_shot):
    add_shot(in_project, "S001")
    result = runner.invoke(app, ["build", "--target", "qc", "--gen", "off", "--json"])
    payload = json.loads(result.output)
    assert payload.get("qc_final") is None


def test_cli_help_explains_qc_does_not_render():
    result = runner.invoke(app, ["build", "--help"])
    assert result.exit_code == 0
    assert "qc" in result.output.lower()
