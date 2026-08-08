from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from manju.build.ingest import apply_ingest, plan_ingest
from manju.build.graph import run_build
from manju.cli import app
from manju.core.models import ProbeInfo
from manju.core.yamlio import write_yaml
from manju.exporters.provider_handoff import (
    build_handoff,
    read_handoff_metadata,
    write_handoff_bundle,
)
from manju.providers.minimax_h3_prompt import build_h3_projection
from manju.providers.prompt_profiles import (
    MINIMAX_H3_PROFILE,
    PORTABLE_VIDEO_PROFILE,
    prompt_profile_registry,
)
from manju.providers.refs import resolve_refs
from manju.providers.registry import available_providers


def _projection(project, shot_id):
    shot = project.load_shot(shot_id)
    bible = project.load_bible()
    return build_h3_projection(
        shot,
        bible,
        resolve_refs(project, shot, bible),
        project_root=project.root,
    )


def test_h3_profile_is_authoring_only_and_not_a_provider():
    registry = prompt_profile_registry()
    assert registry == {
        "portable_video": PORTABLE_VIDEO_PROFILE,
        "minimax_h3": MINIMAX_H3_PROFILE,
    }
    assert registry["minimax_h3"].kind == "authoring_only"
    assert registry["minimax_h3"].network == "forbidden"
    assert registry["minimax_h3"].execution == "unavailable"
    assert registry["minimax_h3"].requires_api_key is False
    assert "minimax_h3" not in available_providers()


def test_h3_mode_projection_and_override_verbatim(tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6)
    assert _projection(tmp_project, "S001")["mode"] == "T2VA"

    add_shot(
        tmp_project,
        "S002",
        duration=6,
        keyframes=[{"position": "start", "prompt": "start"}],
    )
    assert _projection(tmp_project, "S002")["mode"] == "I2VA"

    add_shot(
        tmp_project,
        "S003",
        duration=6,
        keyframes=[{"position": "end", "prompt": "end"}],
    )
    assert _projection(tmp_project, "S003")["mode"] == "L2VA"

    add_shot(
        tmp_project,
        "S004",
        duration=6,
        keyframes=[
            {"position": "start", "prompt": "start"},
            {"position": "end", "prompt": "end"},
        ],
    )
    assert _projection(tmp_project, "S004")["mode"] == "FL2VA"

    override = "  不改写对白。\n保留尾部空格  "
    add_shot(
        tmp_project,
        "S005",
        duration=6,
        generation={
            "prompt_override": override,
            "params": {"images": ["media/refs/ordinary.png"]},
        },
    )
    result = _projection(tmp_project, "S005")
    assert result["mode"] == "REF2VA"
    assert result["prompt"] == override
    assert result["prompt_origin"] == "prompt_override"


def test_h3_keyframe_and_ordinary_ref_is_blocked(tmp_project, add_shot):
    add_shot(
        tmp_project,
        "S001",
        duration=6,
        keyframes=[{"position": "start", "prompt": "start"}],
        generation={"params": {"images": ["media/refs/ordinary.png"]}},
    )
    result = _projection(tmp_project, "S001")
    assert result["mode"] == "I2VA"
    assert any(
        row["code"] == "h3_mode_conflict" and row["level"] == "blocker"
        for row in result["findings"]
    )


def test_handoff_bundle_is_deterministic_and_keeps_one_frozen_ref_plan(
    tmp_project, add_shot
):
    ref = tmp_project.refs_dir / "人物 image.png"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_bytes(b"same-reference-bytes")
    add_shot(
        tmp_project,
        "S001",
        duration=6,
        generation={
            "params": {
                "images": [
                    {
                        "ref": "media/refs/人物 image.png",
                        "controls": ["character_identity"],
                        "subject_ref": "character:linxia",
                    },
                    {
                        "ref": "media/refs/人物 image.png",
                        "controls": ["costume"],
                        "subject_ref": "character:linxia",
                    },
                ]
            }
        },
    )

    first_dir, first_handoff = write_handoff_bundle(tmp_project, "S001")
    before = {
        path.relative_to(first_dir).as_posix(): path.read_bytes()
        for path in sorted(first_dir.rglob("*"))
        if path.is_file()
    }
    second_dir, second_handoff = write_handoff_bundle(tmp_project, "S001")
    after = {
        path.relative_to(second_dir).as_posix(): path.read_bytes()
        for path in sorted(second_dir.rglob("*"))
        if path.is_file()
    }
    assert first_dir == second_dir
    assert first_handoff == second_handoff
    assert before == after

    handoff = json.loads((first_dir / "handoff.json").read_text(encoding="utf-8"))
    refs = json.loads((first_dir / "refs.json").read_text(encoding="utf-8"))
    manifest = json.loads((first_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    built = build_handoff(tmp_project, "S001")
    digest = handoff["reference_plan_digest"]
    assert refs["reference_plan_digest"] == digest
    assert manifest["reference_plan_digest"] == digest
    assert built["projection"]["reference_plan_digest"] == digest
    assert len(refs["logical_bindings"]) == 2
    assert len(refs["physical_assets"]) == 1
    assert refs["logical_bindings"][0]["asset"] == refs["logical_bindings"][1]["asset"]

    checksums = (first_dir / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    for line in checksums:
        expected, name = line.split("  ", 1)
        assert hashlib.sha256((first_dir / name).read_bytes()).hexdigest() == expected
    all_text = b"\n".join(before.values()).decode("utf-8", errors="ignore")
    assert str(tmp_project.root) not in all_text
    assert ".manju/state.sqlite" not in all_text


def test_remote_reference_query_is_redacted_and_not_downloaded(tmp_project, add_shot):
    add_shot(
        tmp_project,
        "S001",
        duration=6,
        generation={
            "params": {
                "images": ["https://example.test/ref.png?token=secret#fragment"]
            }
        },
    )
    directory, _ = write_handoff_bundle(tmp_project, "S001")
    text = (directory / "refs.json").read_text(encoding="utf-8")
    assert "https://example.test/ref.png" in text
    assert "token=secret" not in text
    assert "fragment" not in text
    assert not list((directory / "assets").glob("*"))


def test_manual_handoff_roundtrip_preserves_lineage_without_selecting(
    tmp_project, add_shot, tmp_path, monkeypatch
):
    add_shot(tmp_project, "S001", duration=6)
    directory, handoff = write_handoff_bundle(tmp_project, "S001")
    assert read_handoff_metadata(directory)["handoff_id"] == handoff["handoff_id"]

    returned = tmp_path / "S001_h3_v1.mp4"
    returned.write_bytes(b"returned-media")
    monkeypatch.setattr(
        "manju.providers.manual.probe_media",
        lambda _path: ProbeInfo(duration_ms=6000, width=1920, height=1080),
    )
    plan = plan_ingest(tmp_project, [returned], shot="S001")
    result = apply_ingest(
        tmp_project,
        plan,
        actor="human",
        auto_select=False,
        handoff=handoff,
    )
    assert len(result.results) == 1
    assert result.results[0].ok is True
    assert result.results[0].detail["roundtrip_findings"][0]["code"] == (
        "external_return_aspect_mismatch"
    )
    assert tmp_project.load_shot("S001").status.selected_take is None
    take = tmp_project.takes("S001", skip_ghosts=True)[0]
    assert take.sidecar.provider == "manual_import"
    assert take.sidecar.params["source"] == "external_manual_roundtrip"
    assert take.sidecar.params["handoff_id"] == handoff["handoff_id"]
    assert take.sidecar.params["bundle_digest"] == handoff["bundle_digest"]
    assert take.sidecar.params["claimed_generator"] == "unverified"
    assert take.sidecar.params["returned_media_sha256"].startswith("sha256:")


def test_source_ledger_can_describe_h3_without_runtime_dependency():
    ledger = Path("docs/SOURCE_LEDGER.yaml").read_text(encoding="utf-8")
    assert "runtime_dependencies: []" in ledger


def test_h3_recipes_and_five_offline_eval_fixtures_are_structured():
    recipes = Path("skills/creation-funnel/references/recipes")
    for name in ("product-ad.md", "brand-promo.md", "music-video.md"):
        text = (recipes / name).read_text(encoding="utf-8")
        assert "H3" in text
        assert "select" in text.lower()
    fixtures = sorted(Path("skills/evals/fixtures/minimax_h3").glob("*.yaml"))
    assert len(fixtures) == 5
    for path in fixtures:
        row = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert row["schema"] == "manju.skill-eval/v1"
        assert row["skill"] == "prompt-craft"
        assert row["expected"]["must"]
        assert row["expected"]["must_not"]
        assert row["review"].strip()


def test_cli_h3_projection_check_and_bundle(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001", duration=6)
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()

    projected = runner.invoke(
        app, ["prompt", "S001", "--target", "minimax_h3", "--json"]
    )
    assert projected.exit_code == 0, projected.stdout
    projection = json.loads(projected.stdout)
    assert projection["profile"]["kind"] == "authoring_only"
    assert projection["profile"]["network"] == "forbidden"

    checked = runner.invoke(
        app, ["prompt", "S001", "--target", "minimax_h3", "--check", "--json"]
    )
    assert checked.exit_code == 0, checked.stdout
    assert json.loads(checked.stdout)["ok"] is True

    exported = runner.invoke(
        app,
        [
            "prompt",
            "S001",
            "--target",
            "minimax_h3",
            "--bundle",
            "--output",
            "handoffs",
            "--json",
        ],
    )
    assert exported.exit_code == 0, exported.stdout
    result = json.loads(exported.stdout)
    assert result["generated_media"] == "absent"
    assert result["picture_lock"] == "not_eligible"
    assert Path(result["bundle"]).is_dir()


def test_three_shot_zero_budget_dogfood_reuses_animatic_and_reference_core(
    tmp_project, add_shot
):
    keyframe = tmp_project.imports_dir / "S002_start.png"
    ref_a = tmp_project.imports_dir / "shared_subjects.png"
    ref_b = tmp_project.imports_dir / "linxia_costume.png"
    for path, data in (
        (keyframe, b"keyframe"),
        (ref_a, b"shared-reference"),
        (ref_b, b"second-reference"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    write_yaml(
        tmp_project.root / "bible" / "characters.yaml",
        {
            "linxia": {"name": "林夏"},
            "mei": {"name": "梅"},
        },
    )
    add_shot(tmp_project, "S001", duration=6, characters=[])
    add_shot(
        tmp_project,
        "S002",
        duration=6,
        characters=[],
        keyframes=[{"position": "start", "image": "media/imports/S002_start.png"}],
    )
    add_shot(
        tmp_project,
        "S003",
        duration=6,
        characters=["linxia", "mei"],
        generation={
            "params": {
                "images": [
                    {
                        "ref": "media/imports/shared_subjects.png",
                        "controls": ["character_identity"],
                        "subject_ref": "character:linxia",
                    },
                    {
                        "ref": "media/imports/shared_subjects.png",
                        "controls": ["character_identity"],
                        "subject_ref": "character:mei",
                    },
                    {
                        "ref": "media/imports/linxia_costume.png",
                        "controls": ["costume"],
                        "subject_ref": "character:linxia",
                    },
                ]
            }
        },
    )

    # Existing animatic planning stays the only proxy path and prices no video.
    animatic = run_build(tmp_project, target="animatic", dry_run=True, actor="ai")
    assert animatic.ok is True
    assert all(row.get("kind") == "voice" for row in animatic.plan)

    handoffs = {
        sid: build_handoff(tmp_project, sid) for sid in ("S001", "S002", "S003")
    }
    assert handoffs["S001"]["projection"]["mode"] == "T2VA"
    assert handoffs["S002"]["projection"]["mode"] == "I2VA"
    assert handoffs["S003"]["projection"]["mode"] == "REF2VA"
    assert len(handoffs["S003"]["refs"]["logical_bindings"]) == 3
    assert len(handoffs["S003"]["refs"]["physical_assets"]) == 2
    labels = [row["label"] for row in handoffs["S003"]["refs"]["logical_bindings"]]
    assert labels == ["Image1", "Image1", "Image2"]
    for built in handoffs.values():
        assert built["handoff"]["eligibility"]["generated_media"] == "absent"
        assert any(
            warning["code"] == "h3_handoff_before_animatic_approval"
            for warning in built["handoff"]["warnings"]
        )
