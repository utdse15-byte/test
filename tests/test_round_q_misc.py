"""Round-Q misc gaps: text import, bible completeness (props/voices),
`manju appearances`, and `manju tasks` (the run-ledger view).

Everything read-only here drives the real Typer app through ``CliRunner``
against the conftest fixtures; the ledger cases fabricate rows through the same
``RuntimeState`` helpers ``test_runtime.py`` uses.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from manju.cli import app
from manju.core.check import run_check
from manju.core.container import BIBLE_FILES, Project
from manju.core.locks import seal_lock
from manju.core.models import ShotIndex, ShotSpec
from manju.core.yamlio import read_yaml, write_yaml
from manju.runtime.state import RuntimeState

runner = CliRunner()


# ============================================================ 1. text import


def test_import_txt_lands_in_story_imports_as_md(tmp_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_project.root)
    novel = tmp_path / "novel.txt"
    novel.write_text("第一章 雨夜。", encoding="utf-8")

    result = runner.invoke(app, ["import", str(novel), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["imported"] == ["story/imports/novel.md"]
    assert data["story_imports"] == ["story/imports/novel.md"]
    assert (tmp_project.story_imports_dir / "novel.md").exists()
    # a text drop is not media: it never lands under media/imports
    assert not (tmp_project.imports_dir / "novel.txt").exists()


def test_import_md_lands_in_story_imports(tmp_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_project.root)
    draft = tmp_path / "draft.md"
    draft.write_text("# 剧本\n", encoding="utf-8")

    data = json.loads(runner.invoke(app, ["import", str(draft), "--json"]).output)
    assert data["imported"] == ["story/imports/draft.md"]
    assert (tmp_project.story_imports_dir / "draft.md").exists()


def test_import_media_still_lands_in_media_imports(tmp_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_project.root)
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake-media-bytes")

    data = json.loads(runner.invoke(app, ["import", str(clip), "--json"]).output)
    assert data["imported"] == ["media/imports/clip.mp4"]
    assert data["story_imports"] == []
    assert (tmp_project.imports_dir / "clip.mp4").exists()


def test_import_mixed_batch_routes_each_file(tmp_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_project.root)
    novel = tmp_path / "novel.txt"; novel.write_text("小说", encoding="utf-8")
    clip = tmp_path / "clip.mp4"; clip.write_bytes(b"media")
    draft = tmp_path / "draft.md"; draft.write_text("# 稿", encoding="utf-8")

    data = json.loads(
        runner.invoke(app, ["import", str(novel), str(clip), str(draft), "--json"]).output
    )
    assert data["imported"] == [
        "story/imports/novel.md",
        "media/imports/clip.mp4",
        "story/imports/draft.md",
    ]
    assert data["story_imports"] == ["story/imports/novel.md", "story/imports/draft.md"]


def test_import_text_no_overwrite_suffixing(tmp_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_project.root)
    novel = tmp_path / "novel.txt"
    novel.write_text("v1", encoding="utf-8")

    first = json.loads(runner.invoke(app, ["import", str(novel), "--json"]).output)
    assert first["imported"] == ["story/imports/novel.md"]
    # a second drop of the same name is never overwritten — it gets a _2 suffix
    second = json.loads(runner.invoke(app, ["import", str(novel), "--json"]).output)
    assert second["imported"] == ["story/imports/novel_2.md"]
    assert (tmp_project.story_imports_dir / "novel.md").exists()
    assert (tmp_project.story_imports_dir / "novel_2.md").exists()


def test_import_text_human_output_prints_adapt_hint(tmp_project, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_project.root)
    novel = tmp_path / "novel.txt"
    novel.write_text("小说", encoding="utf-8")
    result = runner.invoke(app, ["import", str(novel)])
    assert result.exit_code == 0, result.output
    assert "story/imports/novel.md" in result.output
    assert "novel→script" in result.output  # the adapt-me hint


# ================================================ 2a. bible completeness


def test_create_scaffolds_props_and_voices(tmp_path):
    project = Project.create(tmp_path / "bcheck", git_init=False)
    for fname in ("props", "voices"):
        assert (project.root / "bible" / f"{fname}.yaml").exists(), fname
    # props + voices are members of the single first-class bible-file list
    assert "props" in BIBLE_FILES and "voices" in BIBLE_FILES


def test_load_bible_merges_props_and_voices(tmp_project):
    # props ALREADY worked (it was in the enumerated tuple) — prove it, and
    # prove voices now merges too (the code change).
    write_yaml(tmp_project.root / "bible" / "props.yaml",
               {"future_coin": {"name": "未来硬币", "detail": "刻着 2036"}})
    write_yaml(tmp_project.root / "bible" / "voices.yaml",
               {"narrator": {"name": "旁白音色", "voice_id": "vox-01"}})
    bible = tmp_project.load_bible()
    assert bible["future_coin"]["name"] == "未来硬币"
    assert bible["narrator"]["voice_id"] == "vox-01"


def test_check_verifies_props_bible_locks(tmp_project):
    """props.yaml lock tampering is caught (props already first-class)."""
    props_path = tmp_project.root / "bible" / "props.yaml"
    entry = {"name": "旧手表", "color": "黄铜"}
    entry["locked"] = {"color": seal_lock(entry, "color")}
    write_yaml(props_path, {"watch": entry})
    assert run_check(tmp_project).ok

    data = read_yaml(props_path)
    data["watch"]["color"] = "银色"  # tamper the sealed field on disk
    write_yaml(props_path, data)
    errors = "\n".join(run_check(tmp_project).errors)
    assert "bible/props.yaml:watch: locked field 'color' changed" in errors


def test_check_verifies_voices_bible_locks(tmp_project):
    """voices.yaml is now first-class in the lock machinery too."""
    voices_path = tmp_project.root / "bible" / "voices.yaml"
    entry = {"name": "林夏音色", "tone": "沙哑"}
    entry["locked"] = {"tone": seal_lock(entry, "tone")}
    write_yaml(voices_path, {"linxia_voice": entry})
    assert run_check(tmp_project).ok

    data = read_yaml(voices_path)
    data["linxia_voice"]["tone"] = "清亮"
    write_yaml(voices_path, data)
    errors = "\n".join(run_check(tmp_project).errors)
    assert "bible/voices.yaml:linxia_voice: locked field 'tone' changed" in errors


# ==================================================== 2b. appearances


def _appearances_fixture(tmp_project):
    """A small project: linxia+villain chars, one scene, coin+umbrella props;
    S001 uses linxia/scene/prop:coin, S002 adds an unknown char + prop:knife."""
    write_yaml(tmp_project.root / "bible" / "characters.yaml",
               {"linxia": {"name": "林夏"}, "villain": {"name": "反派"}})
    write_yaml(tmp_project.root / "bible" / "scenes.yaml",
               {"convenience_store": {"name": "便利店"}})
    write_yaml(tmp_project.root / "bible" / "props.yaml",
               {"coin": {"name": "硬币"}, "umbrella": {"name": "伞"}})
    tmp_project.save_shot(ShotSpec(
        id="S001", scene="convenience_store", characters=["linxia"],
        continuity={"prev": None, "locks": ["prop:coin", "character:linxia"]}))
    tmp_project.save_shot(ShotSpec(
        id="S002", scene="convenience_store", characters=["linxia", "ghost"],
        continuity={"prev": "S001", "locks": ["prop:knife"]}))
    tmp_project.save_index(ShotIndex(order=["S001", "S002"]))


def test_appearances_json_lists_refs_orphans_missing(tmp_project, monkeypatch):
    _appearances_fixture(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["appearances", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert data["shots_total"] == 2
    # character/scene/prop → ordered shot lists
    assert data["characters"]["linxia"]["shots"] == ["S001", "S002"]
    assert data["characters"]["linxia"]["name"] == "林夏"
    assert data["scenes"]["convenience_store"]["shots"] == ["S001", "S002"]
    assert data["props"]["coin"]["shots"] == ["S001"]

    # orphans: bible entries no shot references
    assert data["orphans"]["characters"] == ["villain"]
    assert data["orphans"]["props"] == ["umbrella"]

    # missing: shot references absent from the bible
    assert data["missing"]["characters"] == [{"id": "ghost", "shots": ["S002"]}]
    assert data["missing"]["props"] == [{"id": "knife", "shots": ["S002"]}]


def test_appearances_missing_char_is_also_a_check_error(tmp_project, monkeypatch):
    """appearances links to check rather than duplicating it: the same missing
    character it reports is independently a `manju check` error."""
    _appearances_fixture(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    data = json.loads(runner.invoke(app, ["appearances", "--json"]).output)
    assert data["missing"]["characters"][0]["id"] == "ghost"
    assert "character 'ghost' not found in bible" in "\n".join(run_check(tmp_project).errors)


def test_appearances_human_table(tmp_project, monkeypatch):
    _appearances_fixture(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    out = runner.invoke(app, ["appearances"]).output
    assert "出场表 / appearances" in out
    assert "linxia" in out and "villain" in out  # referenced + orphan both shown
    assert "ghost" in out  # a missing ref surfaces


# ========================================================== 3. tasks


def _seed_ledger(project):
    with RuntimeState(project.root) as st:
        st.record_run(shot="S001", provider="cloud_x", status="succeeded",
                      cost=0.32, currency="CNY", take="take_01", remote_job_id="j1")
        st.record_run(shot="S002", provider="cloud_x", status="failed",
                      failure_kind="content_rejected",
                      error="审核拒绝:悬疑题材内容不合规,请改写提示词")
        st.record_run(shot="S001", provider="kenburns", status="succeeded",
                      cost=0.0, take="take_02")
        st.open_job("j9", provider="cloud_y", shot="S003")


def test_tasks_json_shape_and_status_mapping(tmp_project, monkeypatch):
    _seed_ledger(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    result = runner.invoke(app, ["tasks", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)

    assert {"tasks", "pending", "spend", "shown"} <= set(data)
    # newest first (run_log order); content_rejected → moderation-rejected bucket
    statuses = [t["status"] for t in data["tasks"]]
    assert "moderation-rejected" in statuses
    assert "succeeded" in statuses
    rejected = next(t for t in data["tasks"] if t["status"] == "moderation-rejected")
    assert rejected["shot"] == "S002"
    assert "审核拒绝" in rejected["reason"]

    # a still-in-flight job shows up as polling
    assert data["pending"][0]["remote_job_id"] == "j9"
    assert data["pending"][0]["status"] == "polling"


def test_tasks_spend_by_provider_sums_to_project_total(tmp_project, monkeypatch):
    _seed_ledger(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    data = json.loads(runner.invoke(app, ["tasks", "--json"]).output)

    spend = data["spend"]
    assert spend["total"] == 0.32 and spend["currency"] == "CNY"
    by_provider = {r["provider"]: r for r in spend["by_provider"]}
    assert by_provider["cloud_x"]["cost"] == 0.32
    assert by_provider["cloud_x"]["runs"] == 2
    assert by_provider["kenburns"]["cost"] == 0.0
    # the breakdown reconciles with total_cost() — the number status also shows
    assert round(sum(r["cost"] for r in spend["by_provider"]), 6) == spend["total"]
    with RuntimeState(tmp_project.root) as st:
        assert st.total_cost()[0] == spend["total"]


def test_tasks_respects_limit_n(tmp_project, monkeypatch):
    _seed_ledger(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    data = json.loads(runner.invoke(app, ["tasks", "-n", "1", "--json"]).output)
    assert data["shown"] == 1
    assert len(data["tasks"]) == 1
    # the footer spend still reflects the WHOLE ledger, not just the shown row
    assert data["spend"]["total"] == 0.32


def test_tasks_empty_ledger_is_clean(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    data = json.loads(runner.invoke(app, ["tasks", "--json"]).output)
    assert data["tasks"] == []
    assert data["spend"]["total"] == 0.0
    assert data["shown"] == 0


def test_tasks_human_output_has_footer(tmp_project, monkeypatch):
    _seed_ledger(tmp_project)
    monkeypatch.chdir(tmp_project.root)
    out = runner.invoke(app, ["tasks"]).output
    assert "任务 / tasks" in out
    assert "moderation-rejected" in out
    assert "合计 / total" in out
    assert "cloud_x" in out
