"""Prompt Workbench data layer (goal 7) + single-action prompt checks (goal 8).

The bundle re-uses the build's own code paths — the prompt strings it reports
are byte-identical to what the build compiles; the provider trace is the routing
resolver's; the cost is the same estimator dry-run/spend use. The single-action
checks count clauses/motion robustly across CJK, English and mixed text, propose
deterministic splits, and drive `manju prompt --check`'s exit code.

Hermetic: MANJU_PROVIDERS_DIR points at a temp dir (never a developer's real
~/.manju) and the registry manifest cache is reset per test.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import manju.providers.registry as registry_mod
from manju.build.promptlab import shot_prompt_bundle
from manju.cli import app
from manju.core.yamlio import write_yaml
from manju.providers.prompt import (
    compile_director_prompt,
    compile_image_prompt,
    compile_negative_prompt,
    compile_prompt,
)
from manju.qc.prompt_checks import (
    CODE_EXCESSIVE_DURATION,
    CODE_TOO_MANY_ACTIONS,
    CODE_TOO_MANY_MOTION_PATHS,
    WARNING,
    check_all,
    check_shot,
    count_motion_paths,
    has_blocking,
    split_clauses,
)

runner = CliRunner()


# --------------------------------------------------------------------- fixtures


@pytest.fixture
def providers_dir(tmp_path, monkeypatch):
    """Hermetic ~/.manju/providers; resets the registry manifest cache."""
    root = tmp_path / "providers"
    root.mkdir()
    monkeypatch.setenv("MANJU_PROVIDERS_DIR", str(root))
    monkeypatch.delenv("MANJU_ROUTING", raising=False)
    registry_mod._manifest_cache = None
    yield root
    registry_mod._manifest_cache = None


def _reset():
    registry_mod._manifest_cache = None


def _cloud_manifest(pid, caps, *, per_second=0.05, disabled=False, max_duration_ms=None):
    data = {
        "id": pid, "type": "video", "adapter": "generic_cloud",
        "capabilities": caps, "disabled": disabled,
        "auth": {"key_env": f"{pid.upper()}_KEY"},
        "submit": {"url": "https://api.x/v", "body_template": {}, "job_id_path": "$.id"},
        "poll": {"url": "https://api.x/v/{job_id}", "status_path": "$.s",
                 "status_map": {"ok": "succeeded"}},
        "cost": {"per_second": per_second, "currency": "CNY"},
    }
    if max_duration_ms is not None:
        data["limits"] = {"max_duration_ms": max_duration_ms}
    return data


def _write(providers_dir, pid, data):
    write_yaml(providers_dir / pid / "provider.yaml", data)
    _reset()


# ===================================================== bundle == build-path prompts


def test_bundle_prompts_equal_build_path(providers_dir, tmp_project, add_shot):
    add_shot(tmp_project, "S001",
             action={"main": "林夏接过硬币", "emotion": "震惊"},
             quality={"must_show": ["硬币年份"], "avoid": ["黑屏", "多余手指"]})
    bundle = shot_prompt_bundle(tmp_project, "S001")

    shot = tmp_project.load_shot("S001")
    bible = tmp_project.load_bible()
    # video_prompt is byte-identical to what a cloud/comfyui/local provider
    # archives on its take sidecar as compiled_prompt (the build path).
    assert bundle["video_prompt"] == compile_prompt(shot, bible)
    assert bundle["image_prompt"] == compile_image_prompt(shot, bible)
    assert bundle["director_prompt"] == compile_director_prompt(shot, bible)
    assert bundle["negative_prompt"] == compile_negative_prompt(shot)


def test_image_prompt_drops_action_and_dialogue_keeps_picture(tmp_project, add_shot):
    add_shot(tmp_project, "S001",
             action={"main": "林夏接过硬币", "emotion": "震惊"},
             dialogue={"speaker": "linxia", "text": "这不可能。"})
    shot = tmp_project.load_shot("S001")
    bible = tmp_project.load_bible()
    img = compile_image_prompt(shot, bible)
    # picture stays…
    assert "便利店" in img and "林夏" in img
    # …the timed action and the dialogue line do not (a still cannot show them)
    assert "林夏接过硬币" not in img
    assert "这不可能。" not in img


def test_negative_prompt_is_the_avoid_list(tmp_project, add_shot):
    add_shot(tmp_project, "S001", quality={"avoid": ["黑屏", "多余手指"]})
    shot = tmp_project.load_shot("S001")
    assert compile_negative_prompt(shot) == "黑屏, 多余手指"
    # empty when nothing to avoid
    add_shot(tmp_project, "S002", quality={"avoid": []})
    assert compile_negative_prompt(tmp_project.load_shot("S002")) == ""


def test_bundle_is_json_serialisable_and_has_all_sections(providers_dir, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    bundle = shot_prompt_bundle(tmp_project, "S001")
    json.dumps(bundle, ensure_ascii=False)  # must not raise
    for key in ("shot", "shot_spec", "spec_hash", "image_prompt", "video_prompt",
                "director_prompt", "negative_prompt", "references", "provider",
                "cost", "checks"):
        assert key in bundle
    # (1) ShotSpec snapshot is the canonical spec_payload
    from manju.core.spec import spec_payload
    assert bundle["shot_spec"] == spec_payload(
        tmp_project.load_shot("S001"), tmp_project.load_bible())


# ============================================================ cost field present


def test_cost_field_present(providers_dir, tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=4.0)
    cost = shot_prompt_bundle(tmp_project, "S001")["cost"]
    assert isinstance(cost["estimated_cost"], float)
    assert "currency" in cost
    assert cost["duration_ms"] == 4000


# ============================================================ routing trace honesty


def test_routing_trace_fallback(providers_dir, tmp_project, add_shot):
    # no routing.yaml, no explicit provider → the §8.4 fallback chain drives it.
    add_shot(tmp_project, "S001")
    prov = shot_prompt_bundle(tmp_project, "S001")["provider"]
    assert prov["why"] == "fallback"
    assert prov["explicit_provider"] is None
    assert prov["order"][-1] == "caption_card"
    assert prov["fallback_chain"][-1] == "caption_card"


def test_routing_trace_explicit(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "explprov", _cloud_manifest("explprov", ["image_to_video"]))
    add_shot(tmp_project, "S001", generation={"provider": "explprov"})
    prov = shot_prompt_bundle(tmp_project, "S001")["provider"]
    assert prov["why"] == "explicit"
    assert prov["explicit_provider"] == "explprov"
    assert prov["chosen"] == "explprov"


def test_routing_trace_rule(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "ruleprov", _cloud_manifest("ruleprov", ["image_to_video"]))
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "mine",
        "strategies": {"mine": {"rules": [
            {"match": {"shot_size": "close_up"}, "use": "ruleprov"}], "else": "fallback"}}})
    _reset()
    add_shot(tmp_project, "S001", camera={"shot_size": "close_up"})
    prov = shot_prompt_bundle(tmp_project, "S001")["provider"]
    assert prov["why"] == "rule"
    assert prov["fired_rule"]["use"] == "ruleprov"
    assert prov["chosen"] == "ruleprov"


def test_routing_broken_file_is_honest_note_not_crash(providers_dir, tmp_project, add_shot):
    write_yaml(tmp_project.root / "timeline" / "routing.yaml", {
        "strategy": "bad",
        "strategies": {"bad": {"rules": [{"match": {"mood": "tense"}, "use": "x"}]}}})
    _reset()
    add_shot(tmp_project, "S001")
    prov = shot_prompt_bundle(tmp_project, "S001")["provider"]
    assert "routing_error" in prov  # surfaced, not raised
    assert prov["fallback_chain"][-1] == "caption_card"


# =============================================================== references lineage


def test_references_records_tier_lineage(tmp_project, add_shot):
    tmp_project.imports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.imports_dir / "ref.png").write_bytes(b"\x89PNG fake")
    add_shot(tmp_project, "S001",
             generation={"params": {"image": "media/imports/ref.png"}})
    refs = shot_prompt_bundle(tmp_project, "S001")["references"]
    items = refs["items"]
    assert any(it["kind"] == "image" and it["tier"] == "params" and it["exists"]
               for it in items)
    assert refs["primary_image_tier"] == "params"


# =================================================== clause counting: CJK/EN/mixed


def test_split_clauses_cjk():
    # 然后 / 再 are sequencing markers; 转身 is NOT a clause marker (it is motion).
    clauses = split_clauses("她走进房间然后转身再坐下")
    assert clauses == ["她走进房间", "转身", "坐下"]


def test_split_clauses_english():
    clauses = split_clauses("She enters and then turns while smiling and sits")
    assert clauses == ["She enters", "turns", "smiling", "sits"]


def test_split_clauses_mixed_and_leading_marker():
    # a leading 先 must not produce an empty clause; markers span both scripts.
    clauses = split_clauses("先关灯 then 她开门 and 走出去")
    assert clauses == ["关灯", "她开门", "走出去"]


def test_split_clauses_empty():
    assert split_clauses("") == []
    assert split_clauses("   ") == []
    assert split_clauses("单一动作") == ["单一动作"]  # no markers → one clause


def test_and_word_boundary_does_not_split_inside_words():
    # "grand" / "android" contain "and" but must not be split on.
    assert split_clauses("a grand android") == ["a grand android"]


# ==================================================== the single-action checks


def test_too_many_actions_warns_and_proposes_split(providers_dir, tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6.0,
             action={"main": "她走进房间然后转身再坐下"})
    findings = check_shot(tmp_project, tmp_project.load_shot("S001"))
    tma = [f for f in findings if f["code"] == CODE_TOO_MANY_ACTIONS]
    assert tma and tma[0]["level"] == WARNING
    split = tma[0]["split"]
    assert [s["text"] for s in split["sub_shots"]] == ["她走进房间", "转身", "坐下"]
    # durations distribute the whole shot and sum EXACTLY to it
    assert sum(s["duration_ms"] for s in split["sub_shots"]) == 6000


def test_two_actions_is_not_too_many(providers_dir, tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "她走进房间然后坐下"})
    findings = check_shot(tmp_project, tmp_project.load_shot("S001"))
    assert not any(f["code"] == CODE_TOO_MANY_ACTIONS for f in findings)


def test_check_reads_prompt_override_when_both_are_set(providers_dir, tmp_project, add_shot):
    """round-W #71: compile_prompt returns generation.prompt_override VERBATIM
    and UNCONDITIONALLY when non-empty — action.main is only its fallback.
    The single-action linter must scrutinize the SAME text the provider
    actually receives, not action.main, whenever an override is set."""
    add_shot(
        tmp_project, "S001", duration=6.0,
        action={"main": "一个安全的单一动作,完全不会触发任何检查"},
        generation={"prompt_override": "她走进房间然后转身再坐下"},  # the REAL sent prompt
    )
    shot = tmp_project.load_shot("S001")
    # the real, provider-bound text must be what compile_prompt sends...
    assert compile_prompt(shot, tmp_project.load_bible()) == "她走进房间然后转身再坐下"
    # ...and the SAME text the linter checks (not action.main).
    findings = check_shot(tmp_project, shot)
    tma = [f for f in findings if f["code"] == CODE_TOO_MANY_ACTIONS]
    assert tma, "the linter checked action.main instead of the real prompt_override"
    assert [s["text"] for s in tma[0]["split"]["sub_shots"]] == ["她走进房间", "转身", "坐下"]


def test_check_falls_back_to_action_main_when_no_override(providers_dir, tmp_project, add_shot):
    """No prompt_override set → action.main is the fallback the compiler
    itself uses, so the linter reads it exactly as before."""
    add_shot(tmp_project, "S001", duration=6.0,
             action={"main": "她走进房间然后转身再坐下"})
    shot = tmp_project.load_shot("S001")
    findings = check_shot(tmp_project, shot)
    tma = [f for f in findings if f["code"] == CODE_TOO_MANY_ACTIONS]
    assert tma


def test_check_ignores_whitespace_only_override(providers_dir, tmp_project, add_shot):
    """A whitespace-only prompt_override is not a real override (mirrors
    compile_prompt's ``override.strip()`` truthiness check) — the linter
    still falls back to action.main."""
    add_shot(tmp_project, "S001", duration=6.0,
             action={"main": "她走进房间然后转身再坐下"},
             generation={"prompt_override": "   "})
    shot = tmp_project.load_shot("S001")
    findings = check_shot(tmp_project, shot)
    tma = [f for f in findings if f["code"] == CODE_TOO_MANY_ACTIONS]
    assert tma  # still reads action.main, not the blank override


def test_motion_path_counting_and_warning(providers_dir, tmp_project, add_shot):
    # 从…到… (1) + 走向 (1) in text, plus a non-static camera movement (1) = 3.
    assert count_motion_paths("镜头从左到右移动,主体走向门口", "pan") == 3
    add_shot(tmp_project, "S001",
             camera={"shot_size": "medium", "movement": "pan"},
             action={"main": "镜头从左到右移动,主体走向门口"})
    findings = check_shot(tmp_project, tmp_project.load_shot("S001"))
    m = [f for f in findings if f["code"] == CODE_TOO_MANY_MOTION_PATHS]
    assert m and m[0]["level"] == WARNING


def test_excessive_duration_for_provider_warns_and_splits(providers_dir, tmp_project, add_shot):
    _write(providers_dir, "cc",
           _cloud_manifest("cc", ["image_to_video"], max_duration_ms=5000))
    add_shot(tmp_project, "S001", duration=12.0, action={"main": "长镜头缓慢推进"})
    findings = check_shot(tmp_project, tmp_project.load_shot("S001"))
    ed = [f for f in findings if f["code"] == CODE_EXCESSIVE_DURATION]
    assert ed and ed[0]["level"] == WARNING
    split = ed[0]["split"]
    # 12000ms over a 5000ms ceiling → ceil(12000/5000) = 3 sub-shots
    assert len(split["sub_shots"]) == 3
    assert sum(s["duration_ms"] for s in split["sub_shots"]) == 12000


def test_no_manifest_ceiling_skips_duration_check_honestly(providers_dir, tmp_project, add_shot):
    # no provider manifest declares a ceiling → the check is skipped, never a
    # false warning.
    add_shot(tmp_project, "S001", duration=99.0, action={"main": "单一动作"})
    findings = check_shot(tmp_project, tmp_project.load_shot("S001"))
    assert not any(f["code"] == CODE_EXCESSIVE_DURATION for f in findings)


# =============================================================== split determinism


def test_split_suggestion_is_deterministic(providers_dir, tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=7.0,
             action={"main": "她走进房间然后转身再坐下"})
    shot = tmp_project.load_shot("S001")
    a = check_shot(tmp_project, shot)
    b = check_shot(tmp_project, shot)
    assert a == b  # identical input → identical findings incl. split data


# ================================================================= --check exit codes


def test_prompt_check_clean_exits_zero(providers_dir, tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001", action={"main": "单一动作"})
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["prompt", "--check"])
    assert res.exit_code == 0, res.output


def test_prompt_check_warning_exits_nonzero(providers_dir, tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001", action={"main": "她走进房间然后转身再坐下"})
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["prompt", "--check", "--json"])
    assert res.exit_code == 1, res.output
    payload = json.loads(res.output)
    assert payload["ok"] is False
    assert any(f["code"] == CODE_TOO_MANY_ACTIONS for f in payload["findings"])


def test_check_all_tags_shot_ids(providers_dir, tmp_project, add_shot):
    add_shot(tmp_project, "S001", action={"main": "她走进房间然后转身再坐下"})
    add_shot(tmp_project, "S002", action={"main": "单一动作"})
    findings = check_all(tmp_project)
    assert all("shot" in f for f in findings)
    assert {f["shot"] for f in findings} == {"S001"}  # only the busy shot warns
    assert has_blocking(findings)


# ====================================================================== CLI bundle


def test_prompt_cli_json_and_human(providers_dir, tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001", action={"main": "林夏接过硬币"})
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["prompt", "S001", "--json"])
    assert res.exit_code == 0, res.output
    bundle = json.loads(res.output)
    assert bundle["shot"] == "S001"
    assert "video_prompt" in bundle

    human = runner.invoke(app, ["prompt", "S001"])
    assert human.exit_code == 0, human.output
    assert "供应商" in human.output  # 中文 sections rendered


def test_prompt_cli_unknown_shot_is_clean_failure(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001")
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["prompt", "S999"])
    assert res.exit_code == 1
    assert "not found" in res.output
