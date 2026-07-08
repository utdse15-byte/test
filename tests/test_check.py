"""Tests for manju.core.check — the agent's safety net (§4, §5, §8.2).

Schema + referential integrity + hard lock verification + secret scan. Each
error case is asserted by a stable substring of the reported message.
"""

from __future__ import annotations

import pytest

from manju.core.check import run_check
from manju.core.locks import seal_lock
from manju.core.yamlio import read_yaml, write_yaml


def _errors_text(project) -> str:
    return "\n".join(run_check(project).errors)


def _warnings_text(project) -> str:
    return "\n".join(run_check(project).warnings)


def test_healthy_project_passes(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)

    report = run_check(tmp_project)
    assert report.ok, report.errors
    assert report.errors == []


def test_scene_not_in_bible(tmp_project, add_shot):
    add_shot(tmp_project, "S001", scene="dark_alley")
    assert "scene 'dark_alley' not found in bible" in _errors_text(tmp_project)


def test_character_not_in_bible(tmp_project, add_shot):
    add_shot(tmp_project, "S001", characters=["ghost"])
    assert "character 'ghost' not found in bible" in _errors_text(tmp_project)


def test_selected_take_nonexistent(tmp_project, add_shot):
    add_shot(tmp_project, "S001", status={"selected_take": "take_99"})
    assert "selected_take 'take_99' does not exist" in _errors_text(tmp_project)


def test_lock_violation_after_tampering_yaml_on_disk(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    path = tmp_project.shot_path("S001")

    # seal a lock on dialogue.text with the real current-value hash...
    raw = read_yaml(path)
    raw["locked"] = {"dialogue.text": seal_lock(raw, "dialogue.text")}
    write_yaml(path, raw)

    # ...then tamper the value directly on disk (as a rogue edit would).
    raw = read_yaml(path)
    raw["dialogue"]["text"] = "被偷偷改掉的台词"
    write_yaml(path, raw)

    text = _errors_text(tmp_project)
    assert "locked field 'dialogue.text' changed" in text


def test_unsealed_lock_written_as_bare_list(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    path = tmp_project.shot_path("S001")
    raw = read_yaml(path)
    raw["locked"] = ["dialogue.text"]  # hand-written list, never sealed
    write_yaml(path, raw)

    text = _errors_text(tmp_project)
    assert "unsealed" in text
    assert "dialogue.text" in text


def test_index_references_missing_shot(tmp_project):
    index = tmp_project.load_index()
    index.order = ["S404"]
    tmp_project.save_index(index)
    assert "references missing shot 'S404'" in _errors_text(tmp_project)


def test_secret_pattern_in_story_markdown(tmp_project):
    brief = tmp_project.root / "story" / "brief.md"
    brief.write_text("# 创意\n\n临时把密钥贴这里:sk-" + "a" * 30 + "\n", encoding="utf-8")
    text = _errors_text(tmp_project)
    assert "API key" in text


def test_unregistered_gen_media_flagged(tmp_project, add_shot, make_take):
    """Toolbelt write-back rule (§2.5): media dropped into media/gen without a
    sidecar is flagged; registered takes and voice files are not."""
    from manju.core.check import run_check
    from manju.core.spec import compute_spec_hash

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )
    assert run_check(tmp_project).ok

    gen_dir = tmp_project.takes_dir("S001")
    (gen_dir / "voice_take_01.wav").write_bytes(b"fake")  # voice: exempt by name
    report = run_check(tmp_project)
    assert not any("unregistered" in w for w in report.warnings)

    (gen_dir / "sneaky_edit.mp4").write_bytes(b"fake")  # bypassed write-back
    report = run_check(tmp_project)
    hits = [w for w in report.warnings if "unregistered" in w and "sneaky_edit" in w]
    assert hits and "manju select" in hits[0]
    assert report.ok  # a warning, not a build-blocking error


# ====================================================== #1: bible dup ids


def test_bible_cross_file_duplicate_id_is_error(tmp_project):
    """review #1: the same id in two bible files silently overwrites (last
    BIBLE_FILES entry wins in Project.load_bible) — check must name BOTH
    files and the id, not just warn."""
    write_yaml(tmp_project.root / "bible" / "props.yaml",
              {"linxia": {"name": "误用同名的道具"}})  # collides with characters.linxia
    text = _errors_text(tmp_project)
    assert "id 'linxia'" in text
    assert "bible/characters.yaml" in text and "bible/props.yaml" in text


def test_bible_no_duplicate_ids_passes(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)
    write_yaml(tmp_project.root / "bible" / "props.yaml", {"future_coin": {"name": "硬币"}})
    assert run_check(tmp_project).ok


# =================================================== #5: unindexed shot warning


def test_unindexed_shot_warning_names_build_exclusion(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    # a second shot file dropped on disk WITHOUT going through index.yaml
    from manju.core.models import ShotSpec

    shot2 = ShotSpec.model_validate({"id": "S002", "scene": "convenience_store"})
    tmp_project.save_shot(shot2)

    warnings = _warnings_text(tmp_project)
    assert "S002' exists on disk but is not in index.yaml order" in warnings
    assert "EXCLUDE" in warnings and "--include-unindexed" in warnings


def test_build_excludes_unindexed_shot_from_timeline(tmp_project, add_shot, make_take):
    """The behavioural half of #5: an unindexed shot must not reach the
    compiled timeline even though it is check-clean and has a usable take.
    Compile-only (timeline.compiler.build_timeline with a stub probe) so the
    test needs no real playable media / ffmpeg render."""
    from manju.core.models import ShotSpec
    from manju.timeline.compiler import build_timeline

    shot1 = add_shot(tmp_project, "S001")
    take1 = make_take(tmp_project, "S001", "manual")
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take1.name))

    # S002 exists on disk, has a usable manual take, but is NOT in index order
    shot2 = ShotSpec.model_validate({
        "id": "S002", "scene": "convenience_store", "characters": ["linxia"],
    })
    tmp_project.save_shot(shot2)
    take2 = make_take(tmp_project, "S002", "manual")
    tmp_project.update_shot_raw(
        "S002", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take2.name))

    timeline, _, _ = build_timeline(tmp_project, lambda p: 3000)  # stub probe: 3s each
    assert [c.shot for c in timeline.tracks.video] == ["S001"]

    # --include-unindexed opts back in
    timeline2, _, _ = build_timeline(tmp_project, lambda p: 3000, include_unindexed=True)
    assert set(c.shot for c in timeline2.tracks.video) == {"S001", "S002"}


def test_cli_build_include_unindexed_flag_parses(tmp_project, add_shot, monkeypatch):
    """The CLI surface for #5: `manju build --include-unindexed` is a real,
    parseable flag (dry-run so it never spends/renders)."""
    from typer.testing import CliRunner

    from manju.cli import app

    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()
    result = runner.invoke(app, ["build", "--include-unindexed", "--dry-run"])
    assert result.exit_code == 0, result.output


# ============================================================== #18: assets


def test_music_source_missing_is_error(tmp_project):
    rules = tmp_project.load_rules()
    rules.music.source = "media/refs/bgm.mp3"  # never written to disk
    tmp_project.save_rules(rules)
    text = _errors_text(tmp_project)
    assert "music.source" in text and "media/refs/bgm.mp3" in text


def test_sfx_source_missing_is_error(tmp_project):
    rules = tmp_project.load_rules()
    rules.audio.sfx = [{"source": "media/refs/no_such_sfx.wav", "at": ""}]
    tmp_project.save_rules(rules)
    assert "audio.sfx[0].source" in _errors_text(tmp_project)


def test_ambient_and_transition_sound_missing_are_errors(tmp_project):
    rules = tmp_project.load_rules()
    rules.audio.ambient.source = "media/refs/no_ambient.wav"
    rules.audio.transition_sound = "media/refs/no_whoosh.wav"
    tmp_project.save_rules(rules)
    text = _errors_text(tmp_project)
    assert "audio.ambient.source" in text
    assert "audio.transition_sound" in text


def test_asset_present_on_disk_passes(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)

    bgm = tmp_project.root / "media" / "refs" / "bgm.mp3"
    bgm.parent.mkdir(parents=True, exist_ok=True)
    bgm.write_bytes(b"fake-audio")
    rules = tmp_project.load_rules()
    rules.music.source = "media/refs/bgm.mp3"
    tmp_project.save_rules(rules)
    assert run_check(tmp_project).ok


def test_logo_missing_is_error_when_enabled_warning_when_disabled(tmp_project):
    packaging = tmp_project.load_packaging()
    packaging.logo.image = "media/refs/logo.png"
    packaging.logo.enabled = True
    tmp_project.save_packaging(packaging)
    report = run_check(tmp_project)
    assert any("logo.image" in e for e in report.errors)

    packaging.logo.enabled = False
    tmp_project.save_packaging(packaging)
    report = run_check(tmp_project)
    assert not any("logo.image" in e for e in report.errors)
    assert any("logo.image" in w for w in report.warnings)


def test_watermark_missing_is_error_when_enabled(tmp_project):
    packaging = tmp_project.load_packaging()
    packaging.watermark.image = "media/refs/wm.png"
    packaging.watermark.enabled = True
    tmp_project.save_packaging(packaging)
    assert any("watermark.image" in e for e in _errors_text(tmp_project).splitlines()) \
        or "watermark.image" in _errors_text(tmp_project)


def test_asset_path_escaping_project_is_error(tmp_project):
    rules = tmp_project.load_rules()
    rules.music.source = "../outside.mp3"
    tmp_project.save_rules(rules)
    assert "越界" in _errors_text(tmp_project)


# ============================================================ #26: routing


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    monkeypatch.delenv("MANJU_ROUTING", raising=False)
    yield root


def _write_provider(root, pid, *, disabled=False):
    write_yaml(root / pid / "provider.yaml", {
        "id": pid, "type": "video", "adapter": "generic_cloud",
        "capabilities": ["image_to_video"], "disabled": disabled,
        "auth": {"key_env": f"{pid.upper()}_KEY"},
        "submit": {"url": "https://api.x/v", "body_template": {}, "job_id_path": "$.id"},
        "poll": {"url": "https://api.x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "cost": {"per_second": 0.1, "currency": "CNY"},
    })


def test_routing_unknown_active_strategy_is_error(tmp_project, providers_dir):
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {"strategy": "made_up_strategy"})
    text = _errors_text(tmp_project)
    assert "made_up_strategy" in text and "routing" in text


def test_routing_rule_unknown_provider_is_error(tmp_project, providers_dir):
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "my_rules",
        "strategies": {"my_rules": {
            "rules": [{"match": {"shot_size": "close_up"}, "use": "no_such_provider"}],
            "else": "fallback",
        }},
    })
    text = _errors_text(tmp_project)
    assert "no_such_provider" in text


def test_routing_valid_file_passes(tmp_project, providers_dir, add_shot, make_take):
    _write_provider(providers_dir, "runway")
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "my_rules",
        "strategies": {"my_rules": {
            "rules": [{"match": {"shot_size": "close_up"}, "use": "runway"}],
            "else": "fallback",
        }},
    })
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)
    assert run_check(tmp_project).ok


def test_shot_explicit_disabled_provider_is_error(tmp_project, providers_dir, add_shot):
    _write_provider(providers_dir, "runway", disabled=True)
    add_shot(tmp_project, "S001", generation={"provider": "runway", "candidates": 1})
    text = _errors_text(tmp_project)
    assert "runway" in text and "已被禁用" in text


def test_shot_explicit_unregistered_provider_is_not_a_check_error(tmp_project, add_shot):
    """§8.4 graceful degradation stays intentional (test_failures.py pins the
    build-time behaviour): naming a provider with NO manifest at all falls
    through the fallback chain to caption_card — that is not a check error,
    only a disabled provider explicitly named is."""
    add_shot(tmp_project, "S001", generation={"provider": "totally_made_up", "candidates": 1})
    assert "totally_made_up" not in _errors_text(tmp_project)


# ========================================================== #34: style look


def test_style_look_bad_shape_is_warning_not_error(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)

    write_yaml(tmp_project.root / "bible" / "style.yaml",
              {"look": {"preset": "not_a_real_preset", "intensity": 1.0}})
    report = run_check(tmp_project)
    assert report.ok  # a WARNING, never a build-blocking error
    assert any("look" in w and "style.yaml" in w for w in report.warnings)


def test_style_look_valid_passes_with_no_warning(tmp_project, add_shot, make_take):
    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "manual")
    shot.status.selected_take = take.name
    tmp_project.save_shot(shot)

    write_yaml(tmp_project.root / "bible" / "style.yaml",
              {"look": {"preset": "warm", "intensity": 0.5}})
    report = run_check(tmp_project)
    assert report.ok
    assert not any("look" in w for w in report.warnings)


# ================================================ #57: continuity prop refs


def test_continuity_prop_ref_missing_is_error(tmp_project):
    from manju.core.yamlio import write_yaml as _wy

    _wy(tmp_project.shots_dir / "S001.yaml", {
        "id": "S001", "scene": "convenience_store", "characters": ["linxia"],
        "continuity": {"locks": ["character:linxia", "prop:future_coin"]},
    })
    index = tmp_project.load_index()
    index.order.append("S001")
    tmp_project.save_index(index)

    text = _errors_text(tmp_project)
    assert "prop:future_coin" in text


def test_continuity_prop_ref_present_passes(tmp_project):
    write_yaml(tmp_project.root / "bible" / "props.yaml", {"future_coin": {"name": "硬币"}})
    write_yaml(tmp_project.shots_dir / "S001.yaml", {
        "id": "S001", "scene": "convenience_store", "characters": ["linxia"],
        "continuity": {"locks": ["prop:future_coin"]},
    })
    index = tmp_project.load_index()
    index.order.append("S001")
    tmp_project.save_index(index)
    assert "prop:future_coin" not in _errors_text(tmp_project)
