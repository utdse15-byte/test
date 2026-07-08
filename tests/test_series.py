"""Series layer (round V, goal item 3) — the multi-episode UMBRELLA.

Every test here proves the umbrella is *scaffolding + read-mostly aggregation +
explicit sync* over ORDINARY episode projects: the single-project engine is never
touched. The byte-identity test at the bottom pins that a plain project is
completely unaffected by this module's presence.

Fake media only; no ffmpeg. Chinese names throughout (§14).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.check import run_check
from manju.core.container import Project
from manju.core.events import tail_events
from manju.core.models import PackagingSpec, ShotSpec
from manju.core.series import (
    Series,
    SeriesError,
    new_episode,
    series_characters,
    series_continuity,
    series_status,
    split_script,
    sync_bible,
)
from manju.core.yamlio import read_yaml, write_yaml

runner = CliRunner()

SERIES_NAME = "深夜信号剧集"


# --------------------------------------------------------------------- helpers


def _seed_series_bible(series: Series) -> None:
    write_yaml(series.bible_dir / "characters.yaml",
               {"linxia": {"name": "林夏", "voice": "冷静、克制、略带沙哑"}})
    write_yaml(series.bible_dir / "scenes.yaml",
               {"store": {"name": "便利店", "lighting": "冷白灯管"}})


@pytest.fixture
def tmp_series(tmp_path: Path) -> Series:
    """A scaffolded series with a two-entry global bible (linxia + store)."""
    series = Series.create(tmp_path / SERIES_NAME, name=SERIES_NAME, git_init=False)
    _seed_series_bible(series)
    return series


def _add_shot(project: Project, shot_id: str, **overrides: Any) -> ShotSpec:
    data: dict[str, Any] = {
        "id": shot_id,
        "scene": "store",
        "characters": ["linxia"],
        "dialogue": {"speaker": "linxia", "text": "这不可能。"},
        "duration": "auto",
    }
    data.update(overrides)
    shot = ShotSpec.model_validate(data)
    project.save_shot(shot)
    index = project.load_index()
    if shot_id not in index.order:
        index.order.append(shot_id)
        project.save_index(index)
    return shot


# --------------------------------------------------------------- create / find


def test_create_scaffolds_umbrella(tmp_path: Path):
    series = Series.create(tmp_path / SERIES_NAME, name=SERIES_NAME, git_init=False)
    assert (series.root / "series.yaml").exists()
    for sub in ("bible", "episodes", "script", "reports"):
        assert (series.root / sub).is_dir()
    # bible carries the SAME file set as a project bible (empty scaffolds).
    for kind in ("characters", "scenes", "props", "style", "voices"):
        assert (series.bible_dir / f"{kind}.yaml").exists()
    cfg = series.load_config()
    assert cfg.name == SERIES_NAME
    assert cfg.episodes == []
    assert cfg.created_at  # stamped at creation


def test_create_refuses_duplicate(tmp_path: Path):
    Series.create(tmp_path / "s", name="s", git_init=False)
    with pytest.raises(SeriesError):
        Series.create(tmp_path / "s", name="s", git_init=False)


def test_config_is_extra_tolerant(tmp_series: Series):
    # a human annotates series.yaml with an unknown key — it must round-trip.
    raw = read_yaml(tmp_series.root / "series.yaml")
    raw["season"] = 2
    write_yaml(tmp_series.root / "series.yaml", raw)
    cfg = tmp_series.load_config()  # no ValidationError
    assert cfg.model_dump().get("season") == 2


def test_find_from_root_and_subdir(tmp_series: Series):
    assert Series.find(tmp_series.root).root == tmp_series.root
    assert Series.find(tmp_series.script_dir).root == tmp_series.root


def test_find_and_project_find_coexist_inside_episode(tmp_series: Series):
    """The crux: an episode nested at episodes/<eid>.manju resolves BOTH markers.

    Project.find stops at the episode (project.yaml); Series.find walks PAST it to
    the series root (series.yaml). series.yaml and project.yaml never collide."""
    project = new_episode(tmp_series, "E01", title="第一集")
    inside = project.root / "shots"

    # Project.find from inside the episode → the episode itself.
    assert Project.find(inside).root == project.root
    # Series.find from the same spot → the umbrella, NOT the episode.
    assert Series.find(inside).root == tmp_series.root
    # distinct markers
    assert (project.root / "project.yaml").exists()
    assert not (project.root / "series.yaml").exists()
    assert (tmp_series.root / "series.yaml").exists()
    assert not (tmp_series.root / "project.yaml").exists()


# ----------------------------------------------------------------- new_episode


def test_new_episode_seeds_bible_and_registers(tmp_series: Series):
    project = new_episode(tmp_series, "E01", title="第一集")
    # registered in series.yaml
    cfg = tmp_series.load_config()
    assert [(e.id, e.title) for e in cfg.episodes] == [("E01", "第一集")]
    # bible seeded as a plain copy of the series bible
    ep_chars = read_yaml(project.root / "bible" / "characters.yaml")
    assert ep_chars["linxia"]["name"] == "林夏"
    ep_scenes = read_yaml(project.root / "bible" / "scenes.yaml")
    assert "store" in ep_scenes
    # series-level event
    events = tail_events(tmp_series.root, 10)
    assert any(e["action"] == "series_new_episode" and e["detail"]["episode"] == "E01"
               for e in events)


def test_new_episode_is_a_fully_working_normal_project(tmp_series: Series):
    """An episode works standalone with the EXISTING engine — check passes and
    the shot resolves against the seeded bible."""
    project = new_episode(tmp_series, "E01")
    _add_shot(project, "S001")
    report = run_check(project)
    assert report.ok, report.errors
    # (chdir-based CLI coverage lives in test_cli_check_inside_episode)


def test_new_episode_owns_its_copy_after_creation(tmp_series: Series):
    """After the seeding copy, the episode owns its bible: editing the series
    bible does NOT retroactively change the episode (that is sync-bible's job)."""
    project = new_episode(tmp_series, "E01")
    write_yaml(tmp_series.bible_dir / "characters.yaml", {"linxia": {"name": "改名了"}})
    assert read_yaml(project.root / "bible" / "characters.yaml")["linxia"]["name"] == "林夏"


def test_new_episode_refuses_duplicate(tmp_series: Series):
    new_episode(tmp_series, "E01")
    with pytest.raises(SeriesError):
        new_episode(tmp_series, "E01")


@pytest.mark.parametrize("bad", ["E01/x", "Bad Slug", "../evil", "E01.manju", "", "-lead"])
def test_new_episode_rejects_bad_ids(tmp_series: Series, bad: str):
    with pytest.raises(SeriesError):
        new_episode(tmp_series, bad)


def test_new_episode_accepts_slug(tmp_series: Series):
    project = new_episode(tmp_series, "pilot", title="试播集")
    assert project.root.name == "pilot.manju"
    assert any(e.id == "pilot" for e in tmp_series.load_config().episodes)


def test_new_episode_with_preset(tmp_series: Series):
    project = new_episode(tmp_series, "E01", preset="horizontal_ai_video")
    cfg = project.load_config()
    assert cfg.preset == "horizontal_ai_video"
    # horizontal preset flips the frame to 16:9 — the engine's own behavior, reused.
    assert cfg.width > cfg.height


# --------------------------------------------------------------- series_status


def test_status_aggregates_episodes_and_totals(tmp_series: Series):
    e1 = new_episode(tmp_series, "E01", title="一")
    _add_shot(e1, "S001")
    _add_shot(e1, "S002")
    new_episode(tmp_series, "E02", title="二")

    info = series_status(tmp_series)
    assert info["series"] == SERIES_NAME
    ids = [e["id"] for e in info["episodes"]]
    assert ids == ["E01", "E02"]
    e1_row = next(e for e in info["episodes"] if e["id"] == "E01")
    assert e1_row["shots_total"] == 2
    assert e1_row["shots_by_state"].get("missing") == 2  # no takes yet
    assert info["totals"]["episodes"] == 2
    assert info["totals"]["ok"] == 2
    assert info["totals"]["shots"] == 2
    assert info["totals"]["finals"] == 0


def test_status_degrades_on_one_broken_episode(tmp_series: Series):
    new_episode(tmp_series, "E01", title="好的")
    broken = new_episode(tmp_series, "E02", title="坏的")
    # corrupt E02: remove its project.yaml so open_episode raises ProjectError.
    (broken.root / "project.yaml").unlink()

    info = series_status(tmp_series)
    rows = {e["id"]: e for e in info["episodes"]}
    assert "error" in rows["E02"]  # degraded per-episode
    assert "error" not in rows["E01"]  # the readable one still reports
    assert info["totals"]["ok"] == 1
    assert info["totals"]["errors"] == 1


# ------------------------------------------------------------------ sync_bible


def test_sync_reports_missing_add_without_apply(tmp_series: Series):
    project = new_episode(tmp_series, "E01")
    # add a NEW character to the series bible only.
    write_yaml(tmp_series.bible_dir / "characters.yaml",
               {"linxia": {"name": "林夏", "voice": "冷静、克制、略带沙哑"},
                "akun": {"name": "阿坤"}})

    report = sync_bible(tmp_series, apply=False)
    ep = report["episodes"][0]
    assert ep["added"] == ["characters:akun"]
    assert report["totals"]["added"] == 1
    # apply=False writes nothing.
    assert "akun" not in read_yaml(project.root / "bible" / "characters.yaml")


def test_sync_apply_adds_missing_and_logs_event(tmp_series: Series):
    project = new_episode(tmp_series, "E01")
    write_yaml(tmp_series.bible_dir / "characters.yaml",
               {"linxia": {"name": "林夏", "voice": "冷静、克制、略带沙哑"},
                "akun": {"name": "阿坤"}})

    report = sync_bible(tmp_series, apply=True)
    assert report["totals"]["added"] == 1
    assert read_yaml(project.root / "bible" / "characters.yaml")["akun"]["name"] == "阿坤"
    # every applied change lands an event
    events = tail_events(tmp_series.root, 20)
    assert any(e["action"] == "series_sync_bible" and e["detail"]["action"] == "add"
               and e["detail"]["entry"] == "characters:akun" for e in events)


def test_sync_diverged_is_report_only_never_overwritten(tmp_series: Series):
    project = new_episode(tmp_series, "E01")
    # the episode diverges its own copy; the series entry is different.
    write_yaml(project.root / "bible" / "characters.yaml", {"linxia": {"name": "林夏(分集改)"}})
    write_yaml(tmp_series.bible_dir / "characters.yaml", {"linxia": {"name": "林夏(总纲)"}})

    report = sync_bible(tmp_series, apply=True)  # apply, but NO force
    ep = report["episodes"][0]
    assert ep["diverged"] == ["characters:linxia"]
    assert ep["overwritten"] == []
    # divergence is NEVER auto-overwritten, even with apply=True.
    assert read_yaml(project.root / "bible" / "characters.yaml")["linxia"]["name"] == "林夏(分集改)"


def test_sync_force_overwrites_a_divergence(tmp_series: Series):
    project = new_episode(tmp_series, "E01")
    write_yaml(project.root / "bible" / "characters.yaml", {"linxia": {"name": "旧"}})
    write_yaml(tmp_series.bible_dir / "characters.yaml", {"linxia": {"name": "新"}})

    # plural (report) form
    report = sync_bible(tmp_series, apply=True, force=["characters:linxia"])
    assert report["episodes"][0]["overwritten"] == ["characters:linxia"]
    assert read_yaml(project.root / "bible" / "characters.yaml")["linxia"]["name"] == "新"
    events = tail_events(tmp_series.root, 20)
    assert any(e["action"] == "series_sync_bible" and e["detail"]["action"] == "overwrite"
               for e in events)


def test_sync_force_accepts_singular_kind(tmp_series: Series):
    project = new_episode(tmp_series, "E01")
    write_yaml(project.root / "bible" / "characters.yaml", {"linxia": {"name": "旧"}})
    write_yaml(tmp_series.bible_dir / "characters.yaml", {"linxia": {"name": "新"}})
    # singular asset-matrix form also resolves.
    sync_bible(tmp_series, apply=True, force=["character:linxia"])
    assert read_yaml(project.root / "bible" / "characters.yaml")["linxia"]["name"] == "新"


def test_sync_force_refused_when_locked_field_would_change(tmp_series: Series):
    from manju.core.locks import seal_lock

    project = new_episode(tmp_series, "E01")
    ep_entry = {"name": "旧名"}
    ep_entry["locked"] = {"name": seal_lock(ep_entry, "name")}
    write_yaml(project.root / "bible" / "characters.yaml", {"linxia": ep_entry})
    # series changes the very field the episode value-locked.
    write_yaml(tmp_series.bible_dir / "characters.yaml", {"linxia": {"name": "新名"}})

    report = sync_bible(tmp_series, apply=True, force=["characters:linxia"])
    refused = report["episodes"][0]["refused"]
    assert refused and refused[0]["entry"] == "characters:linxia"
    assert "name" in refused[0]["locked"]
    # the seal is honored: the value is untouched.
    assert read_yaml(project.root / "bible" / "characters.yaml")["linxia"]["name"] == "旧名"
    assert report["totals"]["refused"] == 1


def test_sync_in_sync_entry_counted(tmp_series: Series):
    new_episode(tmp_series, "E01")  # bible seeded identical
    report = sync_bible(tmp_series, apply=False)
    # linxia + store are identical copies → in_sync, nothing to add/diverge.
    assert report["totals"]["added"] == 0
    assert report["totals"]["diverged"] == 0
    assert report["totals"]["in_sync"] >= 2


def test_sync_reports_unused_force(tmp_series: Series):
    new_episode(tmp_series, "E01")
    report = sync_bible(tmp_series, apply=False, force=["character:nobody"])
    assert "characters:nobody" in report["unused_force"]


# -------------------------------------------------------------- series_characters


def test_characters_global_view(tmp_series: Series):
    e1 = new_episode(tmp_series, "E01", title="一")
    e2 = new_episode(tmp_series, "E02", title="二")
    _add_shot(e1, "S001")  # linxia appears in E01/S001
    # E02 diverges linxia and never uses it in a shot.
    write_yaml(e2.root / "bible" / "characters.yaml", {"linxia": {"name": "林夏(E2)"}})

    view = series_characters(tmp_series)
    assert view["episodes"] == ["E01", "E02"]
    linxia = next(c for c in view["characters"] if c["id"] == "linxia")
    cells = {c["id"]: c for c in linxia["episodes"]}
    assert cells["E01"]["present"] is True
    assert cells["E01"]["diverged"] is False
    assert cells["E01"]["appearances"] == ["S001"]
    assert cells["E02"]["present"] is True
    assert cells["E02"]["diverged"] is True
    assert cells["E02"]["appearances"] == []


def test_characters_episode_only(tmp_series: Series):
    e1 = new_episode(tmp_series, "E01")
    # a character only the episode bible knows about.
    chars = read_yaml(e1.root / "bible" / "characters.yaml")
    chars["guest"] = {"name": "客串"}
    write_yaml(e1.root / "bible" / "characters.yaml", chars)

    view = series_characters(tmp_series)
    eo = {x["id"]: x["episodes"] for x in view["episode_only"]}
    assert eo.get("guest") == ["E01"]


def test_characters_degrades_on_broken_episode(tmp_series: Series):
    new_episode(tmp_series, "E01")
    broken = new_episode(tmp_series, "E02")
    (broken.root / "project.yaml").unlink()

    view = series_characters(tmp_series)
    linxia = next(c for c in view["characters"] if c["id"] == "linxia")
    cells = {c["id"]: c for c in linxia["episodes"]}
    assert "error" in cells["E02"]
    assert cells["E01"].get("present") is True


# -------------------------------------------------------------- series_continuity


def _select_manual_take(project: Project, shot_id: str, make_take) -> None:
    """Give ``shot_id`` a selected MANUAL take (state MANUAL — usable/"built")
    without needing real media or a matching spec_hash (mirrors
    test_savings.py's ``_select_fresh_take`` sibling, MANUAL_HASH variant)."""
    take = make_take(project, shot_id, "manual")
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name),
    )


@pytest.fixture
def three_episodes(tmp_series: Series, make_take):
    """One complete episode (built + in-sync bible), one missing assets
    (a shot with no take at all), one diverged bible (a voice field changed) —
    the round-AA7 fixture for series_continuity's verdict precedence."""
    e1 = new_episode(tmp_series, "E01", title="完整集")
    _add_shot(e1, "S001")
    _select_manual_take(e1, "S001", make_take)

    e2 = new_episode(tmp_series, "E02", title="缺素材集")
    _add_shot(e2, "S001")  # no take selected at all -> shots_by_state.missing

    e3 = new_episode(tmp_series, "E03", title="待同步集")
    # diverge a VOICE-shaping field specifically (name stays the same).
    write_yaml(e3.root / "bible" / "characters.yaml",
               {"linxia": {"name": "林夏", "voice": "换了配音"}})

    # a packaging outlier: E02 turns on a distinct intro; E01/E03 keep default.
    pkg = PackagingSpec()
    pkg.intro.enabled = True
    pkg.intro.style_preset = "neon"
    e2.save_packaging(pkg)

    return tmp_series, e1, e2, e3


def test_continuity_verdicts_and_totals(three_episodes):
    series, e1, e2, e3 = three_episodes
    view = series_continuity(series)

    assert view["series"] == SERIES_NAME
    verdicts = {e["id"]: e["verdict"] for e in view["episodes"]}
    assert verdicts == {"E01": "完整", "E02": "缺素材", "E03": "待同步"}

    t = view["totals"]
    assert t["episodes"] == 3
    assert t["ok"] == 3
    assert t["errors"] == 0
    assert t["complete"] == 1
    assert t["missing_assets"] == 1
    assert t["needs_sync"] == 1
    assert t["problem"] == 0
    assert view["needs_sync"] == ["E03"]

    e1_row = next(e for e in view["episodes"] if e["id"] == "E01")
    assert e1_row["shots_built"] == 1
    assert e1_row["shots_selected"] == 1
    e2_row = next(e for e in view["episodes"] if e["id"] == "E02")
    assert e2_row["shots_by_state"].get("missing") == 1
    assert e2_row["shots_built"] == 0


def test_continuity_characters_matrix_and_voice_divergence(three_episodes):
    series, e1, e2, e3 = three_episodes
    view = series_continuity(series)

    linxia = next(c for c in view["characters"] if c["id"] == "linxia")
    cells = {c["id"]: c for c in linxia["episodes"]}
    assert cells["E01"]["present"] is True and cells["E01"]["diverged"] is False
    assert cells["E03"]["diverged"] is True
    assert cells["E03"]["voice_diverged"] == ["voice"]
    assert "voice_diverged" not in cells["E01"]

    assert any(v["id"] == "linxia" and v["episode"] == "E03" and v["fields"] == ["voice"]
               for v in view["voice_divergences"])
    # scenes/props matrices generalize the same engine (present, even if empty).
    assert "scenes" in view and "props" in view
    store = next((s for s in view["scenes"] if s["id"] == "store"), None)
    assert store is not None
    assert {c["id"] for c in store["episodes"]} == {"E01", "E02", "E03"}


def test_continuity_packaging_outliers_are_referential(three_episodes):
    series, e1, e2, e3 = three_episodes
    view = series_continuity(series)

    outliers = {o["field"]: o for o in view["packaging_outliers"]}
    assert "intro.enabled" in outliers
    assert {x["episode"] for x in outliers["intro.enabled"]["outliers"]} == {"E02"}
    assert "intro.style_preset" in outliers
    assert {x["episode"] for x in outliers["intro.style_preset"]["outliers"]} == {"E02"}
    # never a verdict input — E02's verdict is driven by its missing shot,
    # not by the packaging outlier.
    e2_row = next(e for e in view["episodes"] if e["id"] == "E02")
    assert e2_row["verdict"] == "缺素材"


def test_continuity_tolerates_broken_episode(tmp_series: Series):
    new_episode(tmp_series, "E01", title="好的")
    broken = new_episode(tmp_series, "E02", title="坏的")
    (broken.root / "project.yaml").unlink()

    view = series_continuity(tmp_series)
    rows = {e["id"]: e for e in view["episodes"]}
    assert "error" in rows["E02"]
    assert "verdict" not in rows["E02"]
    assert rows["E01"]["verdict"] == "完整"  # freshly seeded, in sync, no shots
    assert view["totals"]["errors"] == 1
    assert view["totals"]["ok"] == 1

    # the cross-episode matrices surface the SAME error on E02's column,
    # never a silent gap.
    linxia = next(c for c in view["characters"] if c["id"] == "linxia")
    cells = {c["id"]: c for c in linxia["episodes"]}
    assert "error" in cells["E02"]
    assert cells["E01"]["present"] is True


def test_continuity_problem_verdict_beats_missing_and_sync(tmp_series: Series):
    """errors beat missing beats out-of-sync (the documented precedence): a
    check ERROR wins even when the SAME episode also has a missing shot and a
    diverged bible entry."""
    e1 = new_episode(tmp_series, "E01")
    _add_shot(e1, "S001")  # missing (no take) — would be 缺素材 on its own
    write_yaml(e1.root / "bible" / "characters.yaml", {"linxia": {"name": "改名了"}})  # diverged
    # a genuine check error: reference a character absent from the bible.
    _add_shot(e1, "S002", characters=["nobody"])

    view = series_continuity(tmp_series)
    e1_row = next(e for e in view["episodes"] if e["id"] == "E01")
    assert e1_row["check_errors"] >= 1
    assert e1_row["verdict"] == "有问题"


def test_cli_continuity_json(cli_series: Series, make_take):
    e1 = new_episode(cli_series, "E01", title="一")
    _add_shot(e1, "S001")
    _select_manual_take(e1, "S001", make_take)

    res = runner.invoke(app, ["series", "continuity", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["series"] == SERIES_NAME
    assert data["episodes"][0]["id"] == "E01"
    assert data["episodes"][0]["verdict"] == "完整"
    assert "characters" in data and "totals" in data


def test_cli_continuity_table_output_shows_verdicts(three_episodes, monkeypatch):
    series, e1, e2, e3 = three_episodes
    monkeypatch.chdir(series.root)
    res = runner.invoke(app, ["series", "continuity"])
    assert res.exit_code == 0, res.output
    assert "完整" in res.output and "缺素材" in res.output and "待同步" in res.output
    assert "待同步分集" in res.output


# ---------------------------------------------------------------- split_script


def test_split_no_markers_errors(tmp_series: Series, tmp_path: Path):
    script = tmp_path / "flat.md"
    script.write_text("没有任何分集标记的一段长文。\n只是普通段落。\n", encoding="utf-8")
    with pytest.raises(SeriesError) as exc:
        split_script(tmp_series, script)
    # the error points at the convention + the skills library.
    assert "标记" in str(exc.value)


def test_split_report_lists_without_writing(tmp_series: Series, tmp_path: Path):
    script = tmp_path / "long.md"
    script.write_text("# E01 开场\n林夏走进便利店。\n## E02 追逐\n雨中追逐。\n", encoding="utf-8")
    report = split_script(tmp_series, script, apply=False)
    eids = [e["eid"] for e in report["episodes"]]
    assert eids == ["E01", "E02"]
    assert report["episodes"][0]["title"] == "开场"
    # nothing created
    assert not tmp_series.episode_project_dir("E01").exists()
    assert tmp_series.load_config().episodes == []


def test_split_apply_creates_episodes_and_writes_scripts(tmp_series: Series, tmp_path: Path):
    script = tmp_path / "long.md"
    script.write_text(
        "开头的预备文本(在第一个标记前,忽略)。\n"
        "# E01 开场\n林夏走进便利店。\n对白:这不可能。\n"
        "## E02 追逐\n雨中追逐,霓虹碎裂。\n",
        encoding="utf-8",
    )
    report = split_script(tmp_series, script, apply=True)
    assert [e["created"] for e in report["episodes"]] == [True, True]

    e1 = tmp_series.open_episode("E01")
    e2 = tmp_series.open_episode("E02")
    s1 = (e1.root / "story" / "script.md").read_text(encoding="utf-8")
    s2 = (e2.root / "story" / "script.md").read_text(encoding="utf-8")
    assert "# 开场" in s1 and "林夏走进便利店" in s1
    assert "预备文本" not in s1  # preamble is dropped
    assert "# 追逐" in s2 and "霓虹碎裂" in s2
    # both registered
    assert [e.id for e in tmp_series.load_config().episodes] == ["E01", "E02"]
    # event recorded
    events = tail_events(tmp_series.root, 20)
    assert any(e["action"] == "series_split_script" for e in events)


def test_split_apply_writes_into_existing_episode(tmp_series: Series, tmp_path: Path):
    new_episode(tmp_series, "E01", title="预先存在")
    script = tmp_path / "long.md"
    script.write_text("# E01 新内容\n这是新剧本。\n", encoding="utf-8")
    report = split_script(tmp_series, script, apply=True)
    assert report["episodes"][0]["created"] is False
    body = (tmp_series.open_episode("E01").root / "story" / "script.md").read_text(encoding="utf-8")
    assert "这是新剧本" in body


def test_split_apply_refuses_to_overwrite_edited_episode_script(
    tmp_series: Series, tmp_path: Path
):
    """round-W #75: an episode's story/script.md that already holds content
    DIFFERENT from this split's output (e.g. a human polished it after an
    earlier split) must not be silently overwritten."""
    new_episode(tmp_series, "E01", title="预先存在")
    script_path = tmp_series.open_episode("E01").root / "story" / "script.md"
    script_path.write_text("# 人工精修过的剧本\n\n这是编辑精心打磨的对白版本。\n",
                           encoding="utf-8")

    script = tmp_path / "long.md"
    script.write_text("# E01 新原稿\n这是一个不同版本的原稿内容。\n", encoding="utf-8")
    report = split_script(tmp_series, script, apply=True)

    ep = report["episodes"][0]
    assert ep["refused"] is True and ep["written"] is False
    assert "E01" in ep["note"] and "--force E01" in ep["note"]
    # the human's edit is UNTOUCHED
    assert "人工精修过的剧本" in script_path.read_text(encoding="utf-8")


def test_split_apply_force_overwrites_edited_episode_script(
    tmp_series: Series, tmp_path: Path
):
    """--force <eid> is the explicit escape hatch."""
    new_episode(tmp_series, "E01", title="预先存在")
    script_path = tmp_series.open_episode("E01").root / "story" / "script.md"
    script_path.write_text("# 人工精修过的剧本\n\n这是编辑精心打磨的对白版本。\n",
                           encoding="utf-8")

    script = tmp_path / "long.md"
    script.write_text("# E01 新原稿\n这是一个不同版本的原稿内容。\n", encoding="utf-8")
    report = split_script(tmp_series, script, apply=True, force=["E01"])

    ep = report["episodes"][0]
    assert ep["refused"] is False and ep["written"] is True
    assert report["unused_force"] == []
    assert "新原稿" in script_path.read_text(encoding="utf-8")
    events = tail_events(tmp_series.root, 20)
    forced_events = [e for e in events if e["action"] == "series_split_script"
                     and e["detail"].get("forced")]
    assert forced_events


def test_split_apply_never_refuses_a_scaffold_or_identical_repeat(
    tmp_series: Series, tmp_path: Path
):
    """The untouched `Project.create` scaffold is safe to overwrite with no
    --force (nobody split or edited it yet); re-running an UNCHANGED split
    (idempotent re-apply) is also safe — nothing to protect against itself."""
    new_episode(tmp_series, "E01")  # story/script.md is still the bare scaffold

    script = tmp_path / "long.md"
    script.write_text("# E01 首次拆分\n第一次的内容。\n", encoding="utf-8")

    first = split_script(tmp_series, script, apply=True)
    assert first["episodes"][0]["refused"] is False
    assert first["episodes"][0]["written"] is True

    # re-run the SAME split again — identical output, no --force needed.
    second = split_script(tmp_series, script, apply=True)
    assert second["episodes"][0]["refused"] is False
    assert second["episodes"][0]["written"] is True


def test_split_rejects_duplicate_marker(tmp_series: Series, tmp_path: Path):
    script = tmp_path / "dup.md"
    script.write_text("# E01 一\n甲\n# E01 又一\n乙\n", encoding="utf-8")
    with pytest.raises(SeriesError):
        split_script(tmp_series, script)


# ----------------------------------------------------------- CLI --json surfaces


@pytest.fixture
def cli_series(tmp_series: Series, monkeypatch):
    monkeypatch.chdir(tmp_series.root)
    return tmp_series


def test_cli_new_and_new_episode_json(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    res = runner.invoke(app, ["series", "new", str(tmp_path / "剧"), "--name", "剧", "--json"])
    assert res.exit_code == 0, res.output
    root = json.loads(res.output)["root"]
    monkeypatch.chdir(root)
    res2 = runner.invoke(app, ["series", "new-episode", "E01", "--title", "首集", "--json"])
    assert res2.exit_code == 0, res2.output
    assert json.loads(res2.output)["episode"] == "E01"


def test_cli_status_json(cli_series: Series):
    new_episode(cli_series, "E01", title="一")
    res = runner.invoke(app, ["series", "status", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.output)
    assert data["series"] == SERIES_NAME
    assert data["episodes"][0]["id"] == "E01"
    assert "totals" in data


def test_cli_episodes_json(cli_series: Series):
    new_episode(cli_series, "E01")
    res = runner.invoke(app, ["series", "episodes", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["episodes"][0]["id"] == "E01"


def test_cli_sync_bible_json(cli_series: Series):
    new_episode(cli_series, "E01")
    write_yaml(cli_series.bible_dir / "characters.yaml",
               {"linxia": {"name": "林夏"}, "akun": {"name": "阿坤"}})
    res = runner.invoke(app, ["series", "sync-bible", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["totals"]["added"] == 1


def test_cli_characters_json(cli_series: Series):
    new_episode(cli_series, "E01")
    res = runner.invoke(app, ["series", "characters", "--json"])
    assert res.exit_code == 0, res.output
    ids = [c["id"] for c in json.loads(res.output)["characters"]]
    assert "linxia" in ids


def test_cli_split_script_force_flag_overwrites_edited_script(
    cli_series: Series, tmp_path: Path
):
    """round-W #75: the CLI --force <eid> flag reaches split_script."""
    new_episode(cli_series, "E01", title="预先存在")
    script_path = cli_series.open_episode("E01").root / "story" / "script.md"
    script_path.write_text("# 人工精修\n\n编辑打磨过的对白。\n", encoding="utf-8")
    long_script = tmp_path / "long.md"
    long_script.write_text("# E01 新原稿\n不同的新内容。\n", encoding="utf-8")

    refused = runner.invoke(app, ["series", "split-script", str(long_script),
                                  "--apply", "--json"])
    assert refused.exit_code == 0, refused.output
    assert json.loads(refused.output)["episodes"][0]["refused"] is True
    assert "人工精修" in script_path.read_text(encoding="utf-8")  # untouched

    forced = runner.invoke(app, ["series", "split-script", str(long_script),
                                 "--apply", "--force", "E01", "--json"])
    assert forced.exit_code == 0, forced.output
    assert json.loads(forced.output)["episodes"][0]["written"] is True
    assert "新原稿" in script_path.read_text(encoding="utf-8")


def test_cli_split_script_json_and_error(cli_series: Series, tmp_path: Path):
    good = tmp_path / "g.md"
    good.write_text("# E01 甲\n内容\n", encoding="utf-8")
    res = runner.invoke(app, ["series", "split-script", str(good), "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["episodes"][0]["eid"] == "E01"

    bad = tmp_path / "b.md"
    bad.write_text("没有标记\n", encoding="utf-8")
    res2 = runner.invoke(app, ["series", "split-script", str(bad)])
    assert res2.exit_code == 1


def test_cli_check_inside_episode(tmp_series: Series, monkeypatch):
    """The episode is a plain project: `manju check` works from inside it."""
    project = new_episode(tmp_series, "E01")
    _add_shot(project, "S001")
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["check", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.output)["ok"] is True


# ---------------------------------------------- byte-identity: plain project safe


def test_plain_project_untouched_by_series_module(tmp_project: Project):
    """CRITICAL (round V hard rule 2): importing/using the series module changes
    NOTHING about an ordinary single project — no new files, no behavior change.

    Trivially true architecturally (the series layer only reads/writes under a
    series root); pinned anyway with an import + a Project.find that still works
    + a before/after file-tree snapshot."""
    import manju.core.series as _series_mod  # noqa: F401  (the import itself is the pin)

    def _tree(root: Path) -> set[str]:
        return {p.relative_to(root).as_posix() for p in root.rglob("*")}

    before = _tree(tmp_project.root)

    # Create + drive a whole series ELSEWHERE; the plain project must not move.
    other = Series.create(tmp_project.root.parent / "_umbrella", name="别处", git_init=False)
    ep = new_episode(other, "E01")
    _add_shot(ep, "S001")
    series_status(other)
    sync_bible(other, apply=True)

    after = _tree(tmp_project.root)
    assert after == before, "series activity leaked files into a plain project"
    # the plain project still resolves as its own project (no series.yaml above it
    # that could shadow it) and has none of the umbrella markers.
    assert Project.find(tmp_project.root).root == tmp_project.root
    assert not (tmp_project.root / "series.yaml").exists()
