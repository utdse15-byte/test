"""Preset kits (P3) — `manju new --preset` pre-fills a project skeleton and
then never binds it (DESIGN_v2.2 decision 6 / §12).

Per the owner's round-q decision the shipped kits are deliberately
content-type-neutral: only `blank`, `vertical_ai_video`, `horizontal_ai_video`.
There is no comic / short-drama / ad / MV / talking-head / commentary kit — the
agent infers the genre from the input at creation time; a preset only fixes the
frame (orientation + generic technical defaults), never the content.

Covered here:
- the loader lists exactly 3 kits, deterministically ordered;
- every kit's merged rules / packaging / project overrides validate against the
  REAL engine models (core.models), not a preset-local copy;
- the kits carry no content-type flavor (a guard against genre creep);
- `manju new --preset <kit>` writes the overrides + records the preset +
  seeds the scaffold, and `manju check` passes on all 3 fresh projects;
- `--preset blank` ⇒ byte-for-byte today's generic scaffold, differing only by
  the recorded preset label (THE pin); no --preset ⇒ that same generic scaffold;
- `manju presets [--json]` shape; unknown preset fails cleanly; CJK pad helper;
- one ffmpeg-gated e2e: a vertical_ai_video project with two shots builds a final.
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
    display_width,
    list_presets,
    load_preset,
    pad,
)

runner = CliRunner()

_HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

EXPECTED_NAMES = ["blank", "vertical_ai_video", "horizontal_ai_video"]

# The generic story scaffold as it exists today (core.container.Project.create).
# This is the PIN: `--preset blank` must reproduce it byte-for-byte, so a preset
# never silently redefines "start from nothing / today's generic scaffold".
GENERIC_STORY = {
    "brief.md": "# 一句话创意\n\n<!-- 一句话说清:谁、在哪、发生什么、为什么抓人 -->\n",
    "outline.md": "# 大纲\n\n<!-- 三幕/起承转合;每行一个节拍,后续一节拍≈一镜头 -->\n",
    "script.md": "# 剧本\n\n<!-- 分场与对白;对白会成为 shots/*.yaml 的 dialogue.text -->\n",
}

# Distinctive tokens for content-type kits the owner explicitly ruled out. Kept
# specific (no bare "ad"/"mv") so the neutrality guard has no false positives.
BANNED_CONTENT_TOKENS = (
    "comic", "漫剧", "短剧", "预告", "trailer", "口播", "解说", "commentary",
    "music video", "talking head", "storyboard", "分镜", "虚拟人", "广告",
)


# --------------------------------------------------------------------- loader


def test_loader_lists_all_kits_in_order():
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

    # none of the three kits ship packaging; when present it must validate
    pkg = spec.packaging_spec()
    assert pkg is None or isinstance(pkg, PackagingSpec)

    # project overrides produce a valid ProjectConfig
    ov = spec.project.model_dump(exclude_none=True)
    cfg = ProjectConfig.model_validate({"name": name, **ov})
    assert cfg.width > 0 and cfg.height > 0 and cfg.fps > 0

    # qc_focus is a plain list of generic technical focuses (empty for blank)
    assert isinstance(spec.qc_focus, list)
    for focus in spec.qc_focus:
        assert isinstance(focus, str) and focus


def test_kits_are_content_type_neutral():
    # The round-q reversal's whole point: no kit names or advertises a content
    # type. Guard against a future edit sneaking genre flavor back in.
    for name in EXPECTED_NAMES:
        spec = load_preset(name)
        blob = " ".join(
            [spec.title, spec.description, *spec.qc_focus, *spec.scaffold.values()]
        ).lower()
        for token in BANNED_CONTENT_TOKENS:
            assert token.lower() not in blob, f"{name} leaks content type: {token!r}"


def test_blank_kit_is_pure_scaffold():
    # blank carries no overrides at all — it exists only as a named, explicit
    # "start from nothing" choice surfaced by `manju presets`.
    spec = load_preset("blank")
    assert spec.description  # still described so the table shows the choice
    assert spec.project.model_dump(exclude_none=True) == {}
    assert spec.rules == {}
    assert spec.packaging is None
    assert spec.qc_focus == []
    assert spec.scaffold == {}
    # merged rules are exactly the engine defaults
    assert spec.merged_rules() == TimelineRules()


def test_vertical_and_horizontal_frames():
    # The two framed kits differ only in orientation + a couple of generic
    # technical defaults; both are content-type-neutral.
    v = load_preset("vertical_ai_video")
    assert v.resolution() == (1080, 1920) and v.project.fps == 30  # phone-first
    assert v.aspect() == "9:16"
    assert v.project.export_profiles == ["srt"]
    vr = v.merged_rules()
    assert vr.captions.enabled is True   # captions on inside the safe area
    assert vr.music.ducking is True      # music ducks under voice
    assert v.qc_focus == ["字幕安全区", "素材完整"]  # generic technical focuses
    assert "story/brief.md" in v.scaffold

    h = load_preset("horizontal_ai_video")
    assert h.resolution() == (1920, 1080) and h.project.fps == 24  # 16:9 counterpart
    assert h.aspect() == "16:9"
    assert h.project.export_profiles == ["srt"]
    assert h.merged_rules().captions.enabled is True
    assert h.qc_focus == ["字幕安全区", "素材完整"]
    assert "story/brief.md" in h.scaffold


# ----------------------------------------------------------- apply_preset unit


def test_apply_blank_is_generic_scaffold_byte_for_byte(tmp_path):
    """THE pin: `--preset blank` reproduces today's generic scaffold
    byte-for-byte, differing only by the recorded preset label."""
    generic = Project.create(tmp_path / "g", name="p", git_init=False)
    blank = Project.create(tmp_path / "b", name="p", git_init=False)
    apply_preset(blank, load_preset("blank"))

    # every file the scaffold writes is identical
    for rel in ("timeline/rules.yaml", "timeline/packaging.yaml",
                "story/brief.md", "story/outline.md", "story/script.md"):
        assert (blank.root / rel).read_text(encoding="utf-8") \
            == (generic.root / rel).read_text(encoding="utf-8"), rel

    # rules / packaging are the untouched defaults
    assert blank.load_rules() == TimelineRules()
    assert blank.load_packaging() == PackagingSpec()

    # story scaffolds are exactly the frozen generic defaults
    for fname, text in GENERIC_STORY.items():
        assert (blank.root / "story" / fname).read_text(encoding="utf-8") == text

    # project.yaml differs from the generic project ONLY in the preset label
    gd = generic.load_config().model_dump()
    bd = blank.load_config().model_dump()
    assert gd["preset"] == "generic" and bd["preset"] == "blank"
    gd.pop("preset")
    bd.pop("preset")
    assert gd == bd
    # blank records no advisory
    assert bd.get("qc_focus") is None


def test_apply_preset_writes_overrides_and_records_kit(tmp_path):
    project = Project.create(tmp_path / "t", name="t", git_init=False)
    spec = load_preset("vertical_ai_video")
    apply_preset(project, spec)

    cfg = project.load_config()
    assert cfg.preset == "vertical_ai_video"
    assert (cfg.width, cfg.height) == (1080, 1920)
    assert cfg.fps == 30
    assert cfg.export_profiles == ["srt"]
    # qc_focus recorded as a plain extra field on project.yaml
    assert cfg.model_dump().get("qc_focus") == spec.qc_focus

    # rules.yaml is the merged TimelineRules
    assert project.load_rules() == spec.merged_rules()
    # the kit ships no packaging: the all-disabled scaffold `manju new` wrote stays
    assert project.packaging_path.exists()
    assert project.load_packaging() == PackagingSpec()
    # scaffold seed present verbatim
    assert (project.root / "story" / "brief.md").read_text(encoding="utf-8") \
        == spec.scaffold["story/brief.md"]


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
    # any scaffold seed the kit carries landed verbatim
    for rel, seed in spec.scaffold.items():
        assert (project.root / rel).read_text(encoding="utf-8") == seed
    # a fresh preset project passes the safety net
    assert run_check(project).ok, run_check(project).errors


def test_new_vertical_has_expected_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(
        app, ["new", "阿竖", "--preset", "vertical_ai_video"]
    ).exit_code == 0

    project = Project(tmp_path / "阿竖.manju")
    cfg = project.load_config()
    assert (cfg.width, cfg.height) == (1080, 1920)  # vertical, phone-first default
    assert cfg.fps == 30
    assert cfg.preset == "vertical_ai_video"

    rules = project.load_rules()
    assert rules.captions.enabled is True
    assert rules.music.ducking is True


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


def test_presets_json_lists_all_three_with_required_keys():
    result = runner.invoke(app, ["presets", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert [d["name"] for d in data] == EXPECTED_NAMES
    required = {"name", "title", "description", "aspect", "resolution",
                "project", "rules", "packaging", "qc_focus", "scaffold"}
    for entry in data:
        assert required <= set(entry), f"{entry['name']} missing {required - set(entry)}"
    # every kit is described so `manju presets` shows the choice explicitly
    assert all(d["description"] for d in data)
    # the framed kits carry advisory focuses; blank stays empty
    focus = {d["name"]: d["qc_focus"] for d in data}
    assert focus["blank"] == []
    assert focus["vertical_ai_video"] and focus["horizontal_ai_video"]


def test_presets_human_table_lists_all_names():
    result = runner.invoke(app, ["presets"])
    assert result.exit_code == 0, result.output
    for name in EXPECTED_NAMES:
        assert name in result.output


def test_cjk_pad_helper_counts_double_width():
    # '竖屏' is two East-Asian Wide chars → 4 display columns; padding to a
    # visual width of 8 appends 4 spaces (str.ljust would wrongly add 6).
    padded = pad("竖屏", 8)
    assert padded == "竖屏" + " " * 4  # 4 display cols of glyph + 4 spaces = 8
    assert display_width(padded) == 8
    assert len(padded) == 6  # 2 code points + 4 spaces (naive ljust would give 8)
    # ASCII behaves exactly like str.ljust.
    assert pad("blank", 8) == "blank".ljust(8)
    assert display_width("blank") == 5
    # already at/over the target width → returned unchanged, never truncated.
    assert pad("竖屏", 4) == "竖屏"
    assert pad("竖屏", 2) == "竖屏"


def test_new_unknown_preset_fails_cleanly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["new", "x", "--preset", "nope"])
    assert result.exit_code == 1
    combined = result.stdout + (result.stderr if result.stderr else "")
    assert "nope" in combined
    assert "blank" in combined  # lists available kits
    # nothing was created
    assert not (tmp_path / "x.manju").exists()


# ------------------------------------------------------- status advisory line


def test_status_shows_qc_focus_advisory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["new", "vproj", "--preset", "vertical_ai_video"])
    monkeypatch.chdir(tmp_path / "vproj.manju")

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    assert "质检重点" in result.output  # one advisory line
    assert "字幕安全区" in result.output

    # and it rides in the --json payload too
    data = json.loads(runner.invoke(app, ["status", "--json"]).output)
    assert data["preset"] == "vertical_ai_video"
    assert data["qc_focus"] == load_preset("vertical_ai_video").qc_focus


# ---------------------------------------------------------------- e2e (ffmpeg)


@pytest.mark.skipif(not _HAS_FFMPEG, reason="e2e builds a real final via ffmpeg")
def test_vertical_preset_project_builds_to_final(tmp_path):
    """A vertical_ai_video project with two shots builds all the way to a final."""
    from manju.build.graph import run_build
    from tests.fixtures.make_sample import make_sample_project

    root = make_sample_project(
        tmp_path / "竖屏样片", shots=2, clip_seconds=0.5, with_bgm=False
    )
    project = Project(root)
    apply_preset(project, load_preset("vertical_ai_video"))

    assert project.load_config().preset == "vertical_ai_video"
    assert run_check(project).ok, run_check(project).errors

    result = run_build(project, target="final")
    assert result.ok, f"errors={result.errors} warnings={result.warnings}"
    assert result.render_path
    final = project.resolve(result.render_path)
    assert final.exists() and final.stat().st_size > 0

    timeline = project.load_timeline()
    assert len(timeline.tracks.video) == 2
    assert timeline.width == 1080 and timeline.height == 1920
