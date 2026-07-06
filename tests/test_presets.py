"""Preset kits (P3) — `manju new --preset` pre-fills a project skeleton and
then never binds it (DESIGN_v2.2 decision 6 / §12).

Covered here:
- the loader lists exactly 8 kits, deterministically ordered;
- every kit's merged rules / packaging / project overrides validate against the
  REAL engine models (core.models), not a preset-local copy;
- `manju new --preset <kit>` writes the overrides + records the preset +
  seeds the scaffold, and `manju check` passes on all 8 fresh projects;
- no --preset ⇒ byte-for-byte the generic scaffold of today (pinned);
- `manju presets [--json]` shape; unknown preset fails cleanly;
- one ffmpeg-gated e2e: a comic-preset project with two shots builds to a final.
"""

from __future__ import annotations

import json
import shutil

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.check import run_check
from manju.core.container import Project
from manju.core.models import PackagingSpec, ProjectConfig, TimelineRules
from manju.presets import (
    PRESET_ORDER,
    PresetError,
    apply_preset,
    list_presets,
    load_preset,
)

runner = CliRunner()

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

EXPECTED_NAMES = [
    "comic", "short_drama", "explainer", "novel", "trailer", "ad", "mv", "talking_head",
]

# The generic story scaffold as it exists today (core.container.Project.create).
# This is the PIN: if the default scaffold ever changes, this test fails on
# purpose — presets must not silently redefine "no preset ⇒ today's scaffold".
GENERIC_STORY = {
    "brief.md": "# 一句话创意\n\n<!-- 一句话说清:谁、在哪、发生什么、为什么抓人 -->\n",
    "outline.md": "# 大纲\n\n<!-- 三幕/起承转合;每行一个节拍,后续一节拍≈一镜头 -->\n",
    "script.md": "# 剧本\n\n<!-- 分场与对白;对白会成为 shots/*.yaml 的 dialogue.text -->\n",
}


# --------------------------------------------------------------------- loader


def test_loader_lists_exactly_eight_in_order():
    specs = list_presets()
    assert [s.name for s in specs] == EXPECTED_NAMES
    assert list(PRESET_ORDER) == EXPECTED_NAMES  # order tuple is the source of truth


def test_unknown_preset_raises_clean_error_listing_names():
    with pytest.raises(PresetError) as exc:
        load_preset("does_not_exist")
    msg = str(exc.value)
    assert "does_not_exist" in msg
    for name in EXPECTED_NAMES:
        assert name in msg  # the error lists every available kit


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_each_preset_validates_against_real_models(name):
    spec = load_preset(name)

    # merged rules validate as TimelineRules
    rules = spec.merged_rules()
    assert isinstance(rules, TimelineRules)

    # packaging (when present) validates as PackagingSpec
    pkg = spec.packaging_spec()
    assert pkg is None or isinstance(pkg, PackagingSpec)

    # project overrides produce a valid ProjectConfig
    ov = spec.project.model_dump(exclude_none=True)
    cfg = ProjectConfig.model_validate({"name": name, **ov})
    assert cfg.width > 0 and cfg.height > 0 and cfg.fps > 0

    # every kit carries thoughtful advisory focus + a story scaffold seed
    assert spec.qc_focus, f"{name} has no qc_focus"
    assert any(p.endswith(".md") for p in spec.scaffold), f"{name} has no md scaffold"
    assert "story/brief.md" in spec.scaffold


def test_first_class_vertical_and_landscape_kits():
    # Chinese short-video is first-class: the vertical kits are 1080x1920.
    for name in ("comic", "short_drama", "explainer", "novel", "ad", "talking_head"):
        assert load_preset(name).resolution() == (1080, 1920)
    # trailer and mv are the intentional 16:9 exceptions.
    for name in ("trailer", "mv"):
        assert load_preset(name).resolution() == (1920, 1080)


def test_key_choices_are_what_the_kits_advertise():
    # A few load-bearing choices, so a careless edit to a data file is caught.
    comic = load_preset("comic").merged_rules()
    assert comic.captions.style == "bold" and comic.captions.max_lines == 2
    assert comic.timing.max_shot_ms < TimelineRules().timing.max_shot_ms  # tighter

    explainer = load_preset("explainer").merged_rules()
    assert explainer.captions.max_chars_per_line > TimelineRules().captions.max_chars_per_line
    assert explainer.music.ducking is True

    novel = load_preset("novel").merged_rules()
    assert novel.captions.max_lines == 3  # dense captions
    assert novel.audio.ambient is not None  # ambient bed slot present

    mv = load_preset("mv").merged_rules()
    assert mv.captions.enabled is False  # captions off by default
    assert mv.music.ducking is False and mv.music.gain_db > -12  # music-forward

    th = load_preset("talking_head").merged_rules()
    assert th.audio.voice_gain_db > 0 and th.music.ducking is True  # voice-forward

    trailer = load_preset("trailer").packaging_spec()
    assert trailer.intro.enabled and trailer.outro.enabled and trailer.intro.text

    ad = load_preset("ad").packaging_spec()
    assert ad.outro.enabled and ad.outro.text  # CTA placeholder


# ----------------------------------------------------------- apply_preset unit


def test_apply_preset_writes_overrides_and_records_kit(tmp_path):
    project = Project.create(tmp_path / "t", name="t", git_init=False)
    spec = load_preset("trailer")
    apply_preset(project, spec)

    cfg = project.load_config()
    assert cfg.preset == "trailer"
    assert (cfg.width, cfg.height) == (1920, 1080)
    assert cfg.export_profiles == ["srt", "otio"]
    # qc_focus recorded as a plain extra field on project.yaml
    assert cfg.model_dump().get("qc_focus") == spec.qc_focus

    # rules.yaml is the merged TimelineRules
    assert project.load_rules() == spec.merged_rules()
    # packaging.yaml written because trailer carries one
    assert project.packaging_path.exists()
    assert project.load_packaging() == spec.packaging_spec()
    # scaffold seed present
    assert (project.root / "story" / "brief.md").read_text(encoding="utf-8") \
        == spec.scaffold["story/brief.md"]


def test_apply_preset_without_packaging_keeps_scaffold_default(tmp_path):
    project = Project.create(tmp_path / "t", name="t", git_init=False)
    apply_preset(project, load_preset("comic"))
    # comic ships no packaging: the all-disabled scaffold `manju new` wrote
    # stays untouched (round-N: packaging.yaml is always scaffolded)
    assert project.packaging_path.exists()
    assert project.load_packaging() == PackagingSpec()


# --------------------------------------------------------------- `manju new`


@pytest.mark.parametrize("name", EXPECTED_NAMES)
def test_new_with_preset_scaffolds_and_passes_check(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", f"proj_{name}", "--preset", name])
    assert result.exit_code == 0, result.output

    project = Project(tmp_path / f"proj_{name}.manju")
    spec = load_preset(name)

    cfg = project.load_config()
    assert cfg.preset == name
    assert (cfg.width, cfg.height) == spec.resolution()
    assert project.load_rules() == spec.merged_rules()
    # scaffold seed landed
    assert (project.root / "story" / "brief.md").read_text(encoding="utf-8") \
        == spec.scaffold["story/brief.md"]
    # a fresh preset project passes the safety net
    assert run_check(project).ok, run_check(project).errors


def test_new_comic_has_expected_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["new", "阿漫", "--preset", "comic"]).exit_code == 0

    project = Project(tmp_path / "阿漫.manju")
    cfg = project.load_config()
    assert (cfg.width, cfg.height) == (1080, 1920)  # vertical, first-class
    assert cfg.preset == "comic"

    rules = project.load_rules()
    assert rules.captions.style == "bold"
    assert rules.captions.max_chars_per_line == 14
    assert rules.timing.default_shot_ms == 2200  # tight pacing


def test_new_without_preset_is_todays_generic_scaffold(tmp_path, monkeypatch):
    """No --preset ⇒ byte-for-byte today's generic scaffold (the pin)."""
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["new", "plain"]).exit_code == 0

    project = Project(tmp_path / "plain.manju")
    cfg = project.load_config()
    assert cfg.preset == "generic"
    assert cfg.model_dump().get("qc_focus") is None  # no advisory recorded
    # the generic scaffold ships packaging.yaml all-disabled (round-N)
    assert project.load_packaging() == PackagingSpec()
    # rules are the untouched defaults
    assert project.load_rules() == TimelineRules()
    # story scaffolds are exactly the frozen defaults
    for fname, text in GENERIC_STORY.items():
        assert (project.root / "story" / fname).read_text(encoding="utf-8") == text


# --------------------------------------------------------------- `manju presets`


def test_presets_json_lists_all_eight_with_required_keys():
    result = runner.invoke(app, ["presets", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [d["name"] for d in data] == EXPECTED_NAMES
    required = {"name", "title", "description", "aspect", "resolution",
                "project", "rules", "packaging", "qc_focus", "scaffold"}
    for entry in data:
        assert required <= set(entry), f"{entry['name']} missing {required - set(entry)}"
        assert entry["qc_focus"]  # non-empty advisory list


def test_presets_human_table_lists_all_names():
    result = runner.invoke(app, ["presets"])
    assert result.exit_code == 0, result.output
    for name in EXPECTED_NAMES:
        assert name in result.output


def test_new_unknown_preset_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "--preset", "nope"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr if result.stderr else "")
    assert "nope" in combined
    assert "comic" in combined  # lists available kits
    # nothing was created
    assert not (tmp_path / "x.manju").exists()


# ------------------------------------------------------- status advisory line


def test_status_shows_qc_focus_advisory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["new", "adproj", "--preset", "ad"])
    monkeypatch.chdir(tmp_path / "adproj.manju")

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "质检重点" in result.output  # one advisory line
    assert "行动号召卡(outro CTA)" in result.output

    # and it rides in the --json payload too
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert data["preset"] == "ad"
    assert data["qc_focus"] == load_preset("ad").qc_focus


# ---------------------------------------------------------------- e2e (ffmpeg)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="e2e builds a real final via ffmpeg")
def test_comic_preset_project_builds_to_final(tmp_path):
    """A comic-preset project with two shots builds all the way to a final."""
    from manju.build.graph import run_build
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(
        tmp_path / "漫剧样片", shots=2, clip_seconds=0.5, with_bgm=False
    )
    project = Project(root)
    apply_preset(project, load_preset("comic"))

    assert project.load_config().preset == "comic"
    assert run_check(project).ok, run_check(project).errors

    result = run_build(project, target="final")
    assert result.ok, f"errors={result.errors} warnings={result.warnings}"
    assert result.render_path
    final = project.resolve(result.render_path)
    assert final.exists() and final.stat().st_size > 0

    timeline = project.load_timeline()
    assert len(timeline.tracks.video) == 2
    assert timeline.width == 1080 and timeline.height == 1920
