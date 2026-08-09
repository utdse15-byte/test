from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from manju.cli import app
from manju.core.hashing import hash_value
from manju.core.models import ProbeInfo
from manju.core.yamlio import write_yaml
from manju.exporters.provider_handoff import (
    LEGACY_MANIFEST_SCHEMA,
    LEGACY_SCHEMA,
    ProviderHandoffError,
    SCHEMA,
    verify_handoff_bundle,
    write_handoff_bundle,
)
from manju.providers.minimax_h3_prompt import project_h3_plan, reference_plan_digest
from manju.providers.portable_video_prompt import build_portable_projection
from manju.providers.prompt_profiles import (
    PORTABLE_VIDEO_PROFILE,
    video_authoring_profile_registry,
)
from manju.providers.refs import RefItem
from manju.providers.video_authoring import build_video_authoring_plan


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _rewrite_manifest_checksums(directory: Path, manifest: dict) -> None:
    raw = _json_bytes(manifest)
    (directory / "MANIFEST.json").write_bytes(raw)
    rows = {row["path"]: row for row in manifest["members"]}
    rows["MANIFEST.json"] = {
        "sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
    }
    text = "".join(
        f"{row['sha256'].removeprefix('sha256:')}  {name}\n"
        for name, row in sorted(rows.items())
    )
    (directory / "SHA256SUMS").write_bytes(text.encode("ascii"))


def _replace_declared_member(directory: Path, name: str, data: bytes) -> None:
    _reseal_bundle(directory, {name: data})


def _reseal_bundle(
    directory: Path,
    replacements: dict[str, bytes],
    *,
    manifest_updates: dict | None = None,
) -> None:
    manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
    rows = {row["path"]: row for row in manifest["members"]}
    for name, data in replacements.items():
        (directory / name).write_bytes(data)
        row = rows[name]
        row["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
        row["bytes"] = len(data)
    if manifest_updates:
        manifest.update(manifest_updates)
    manifest["manifest_digest"] = hash_value(manifest["members"])
    _rewrite_manifest_checksums(directory, manifest)


def _legacy_bundle(directory: Path) -> Path:
    directory.mkdir()
    handoff = {
        "schema": LEGACY_SCHEMA,
        "handoff_id": "minimax_h3:S001:legacy",
        "bundle_digest": "sha256:" + "1" * 64,
        "reference_plan_digest": "sha256:" + "2" * 64,
        "target": "minimax_h3",
        "shot": "S001",
    }
    files = {
        "handoff.json": _json_bytes(handoff),
        "prompt.txt": b"legacy prompt",
    }
    manifest = {
        "schema": LEGACY_MANIFEST_SCHEMA,
        "handoff_id": handoff["handoff_id"],
        "bundle_digest": handoff["bundle_digest"],
        "reference_plan_digest": handoff["reference_plan_digest"],
        "members": [
            {"path": name, "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
             "bytes": len(data)}
            for name, data in sorted(files.items())
        ],
    }
    for name, data in files.items():
        (directory / name).write_bytes(data)
    _rewrite_manifest_checksums(directory, manifest)
    return directory


def test_v1_is_permanently_readable_with_full_fixity(tmp_path):
    verified = verify_handoff_bundle(_legacy_bundle(tmp_path / "legacy"))
    assert verified.handoff["schema"] == LEGACY_SCHEMA
    assert verified.handoff["target"] == "minimax_h3"
    lineage = verified.lineage()
    assert lineage.profile_id == "minimax_h3"
    assert lineage.profile_revision is None
    assert lineage.manifest_digest is None
    assert lineage.bundle_format_revision is None
    assert lineage.renderer_revision is None


def test_canonical_graph_is_many_to_many_and_h3_counts_physical(
    tmp_project, add_shot
):
    shared = tmp_project.imports_dir / "shared.png"
    costume = tmp_project.imports_dir / "costume.png"
    shared.write_bytes(b"shared")
    costume.write_bytes(b"costume")
    add_shot(
        tmp_project,
        "S001",
        duration=6,
        generation={"params": {"images": [
            {"ref": "media/imports/shared.png", "controls": ["face"],
             "subject_ref": "character:linxia"},
            {"ref": "media/imports/shared.png", "controls": ["face"],
             "subject_ref": "character:mei"},
            {"ref": "media/imports/costume.png", "controls": ["costume"],
             "subject_ref": "character:linxia"},
            {"ref": "media/imports/costume.png", "ignore": ["lighting"]},
        ]}},
    )
    plan = build_video_authoring_plan(tmp_project, "S001")
    assert len(plan.reference_graph.physical) == 2
    assert len(plan.reference_graph.bindings) == 4
    assert len(plan.reference_graph.subjects) == 2
    linxia = next(
        row for row in plan.reference_graph.subjects
        if row.normalized_scope == "character:linxia"
    )
    assert linxia.source_physical_ids == ("Picture1", "Picture2")
    projection = project_h3_plan(plan)
    assert projection["reference_plan_digest"] == reference_plan_digest(
        projection["references"]
    )
    assert [row["label"] for row in projection["reference_labels"]] == [
        "Image1", "Image1", "Image2", "Image2"
    ]


def test_portable_prompt_sections_and_exact_override(tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6)
    plan = build_video_authoring_plan(tmp_project, "S001")
    projection = build_portable_projection(plan)
    for heading in (
        "SHOT GOAL", "VISUAL START", "VISUAL CHANGE", "VISUAL END",
        "SUBJECTS AND REFERENCES", "CAMERA", "ENVIRONMENT AND LIGHT",
        "DIALOGUE / VISIBLE TEXT", "AUDIO INTENT", "NEGATIVE CONSTRAINTS",
        "TECHNICAL TARGET",
    ):
        assert heading in projection["prompt"]
    assert "unverified_at_execution" in projection["prompt"]
    assert "MiniMax" not in projection["prompt"]

    exact = "Keep  leading spaces\r\n第二行\n"
    shot = tmp_project.load_shot("S001")
    shot.generation.prompt_override = exact
    tmp_project.save_shot(shot)
    plan = build_video_authoring_plan(tmp_project, "S001")
    assert build_portable_projection(plan)["prompt"] == exact
    directory, _ = write_handoff_bundle(
        tmp_project, "S001", target="portable_video", output="portable"
    )
    assert (directory / "prompt.txt").read_bytes() == exact.encode("utf-8")


def test_v2_bundle_is_verified_atomic_immutable_and_concurrently_reused(
    tmp_project, add_shot
):
    add_shot(tmp_project, "S001", duration=6)

    def create():
        return write_handoff_bundle(
            tmp_project, "S001", target="portable_video", output="handoffs"
        )[0]

    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _index: create(), range(4)))
    assert len(set(paths)) == 1
    directory = paths[0]
    verified = verify_handoff_bundle(directory)
    assert verified.handoff["schema"] == SCHEMA
    assert verified.manifest["manifest_digest"] == hash_value(
        [dict(row) for row in verified.manifest["members"]]
    )
    assert not list(directory.parent.glob(".*.tmp-*"))
    before = {p.relative_to(directory).as_posix(): p.read_bytes()
              for p in directory.rglob("*") if p.is_file()}
    assert create() == directory
    after = {p.relative_to(directory).as_posix(): p.read_bytes()
             for p in directory.rglob("*") if p.is_file()}
    assert before == after


def test_existing_mismatch_fails_closed_and_unknown_sibling_survives(
    tmp_project, add_shot
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="immutable")
    unknown = directory.parent / "owner-notes"
    unknown.mkdir()
    (unknown / "keep.txt").write_text("keep", encoding="utf-8")
    (directory / "prompt.txt").write_bytes(b"changed")
    with pytest.raises(ProviderHandoffError):
        write_handoff_bundle(tmp_project, "S001", output="immutable")
    assert (unknown / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_failed_publish_cleans_only_owned_temp(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001", duration=6)
    import manju.exporters.provider_handoff as module

    monkeypatch.setattr(module.os, "rename", lambda _source, _dest: (_ for _ in ()).throw(OSError("stop")))
    with pytest.raises(ProviderHandoffError) as raised:
        write_handoff_bundle(tmp_project, "S001", output="publish-failure")
    assert raised.value.code == "handoff_publish_failed"
    parent = tmp_project.root / "publish-failure" / "minimax_h3" / "S001"
    assert not list(parent.glob(".*.tmp-*"))
    assert not [path for path in parent.iterdir() if path.is_dir()]


def test_verifier_rejects_file_input_and_missing_manifest(tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="shape")
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory / "handoff.json")
    assert raised.value.code == "handoff_directory_required"
    (directory / "MANIFEST.json").unlink()
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_manifest_missing"


def test_verifier_rejects_link_and_identity_drift(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="linked")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    link = directory / "linked.txt"
    try:
        os.symlink(outside, link)
    except OSError:
        pytest.skip("host does not permit test symlink creation")
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_unsafe_member"
    link.unlink()

    handoff = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
    handoff["shot"] = "S999"
    _replace_declared_member(directory, "handoff.json", _json_bytes(handoff))
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_cross_document_mismatch"


def test_resealed_prompt_with_stale_semantic_digest_is_rejected(
    tmp_project, add_shot
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="semantic-prompt")
    _replace_declared_member(directory, "prompt.txt", b"resealed prompt")
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_semantic_digest_mismatch"


def test_resealed_refs_with_stale_semantic_digest_is_rejected(
    tmp_project, add_shot
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="semantic-refs")
    refs = json.loads((directory / "refs.json").read_text(encoding="utf-8"))
    refs["review_note"] = "content changed but byte envelope was resealed"
    _replace_declared_member(directory, "refs.json", _json_bytes(refs))
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_semantic_digest_mismatch"


def test_resealed_asset_with_stale_semantic_digest_is_rejected(
    tmp_project, add_shot
):
    reference = tmp_project.imports_dir / "reference.png"
    reference.write_bytes(b"original-reference")
    add_shot(tmp_project, "S001", duration=6, generation={"params": {
        "images": ["media/imports/reference.png"],
    }})
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="semantic-asset")
    asset = next(path for path in directory.rglob("*") if path.is_file()
                 and path.relative_to(directory).as_posix().startswith("assets/"))
    name = asset.relative_to(directory).as_posix()
    _replace_declared_member(directory, name, b"resealed-reference")
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_semantic_digest_mismatch"


def test_resealed_canonical_with_stale_semantic_digest_is_rejected(
    tmp_project, add_shot
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="semantic-canonical")
    handoff = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
    handoff["canonical"]["duration_ms"] += 1
    _replace_declared_member(directory, "handoff.json", _json_bytes(handoff))
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_semantic_digest_mismatch"


def test_handoff_id_is_recomputed_from_semantic_digest(tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="semantic-id")
    handoff = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
    handoff["handoff_id"] = "portable_video:S001:000000000000"
    _reseal_bundle(
        directory,
        {"handoff.json": _json_bytes(handoff)},
        manifest_updates={"handoff_id": handoff["handoff_id"]},
    )
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_id_mismatch"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dialect_mode", "different"),
        ("quality_status", "different"),
        ("prompt_origin", "different"),
    ],
)
def test_projection_duplicate_fields_must_agree(
    tmp_project, add_shot, field, value
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(
        tmp_project, "S001", output=f"projection-drift-{field}"
    )
    handoff = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
    handoff["projection"][field] = value
    _replace_declared_member(directory, "handoff.json", _json_bytes(handoff))
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_cross_document_mismatch"


def test_refs_cross_document_identity_must_agree(tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="refs-drift")
    refs = json.loads((directory / "refs.json").read_text(encoding="utf-8"))
    refs["shot"] = "S999"
    _replace_declared_member(directory, "refs.json", _json_bytes(refs))
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_cross_document_mismatch"


@pytest.mark.parametrize(
    ("tamper", "code"),
    [
        ("hash", "handoff_member_hash_mismatch"),
        ("size", "handoff_member_size_mismatch"),
        ("missing", "handoff_member_missing"),
        ("checksum", "handoff_checksum_mismatch"),
        ("unexpected", "handoff_unexpected_member"),
        ("case", "handoff_case_collision"),
        ("unsafe", "handoff_unsafe_member"),
    ],
)
def test_verifier_rejects_every_tampering_class(
    tmp_project, add_shot, tamper, code
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output=tamper)
    if tamper == "hash":
        data = bytearray((directory / "prompt.txt").read_bytes())
        data[0] = (data[0] + 1) % 255
        (directory / "prompt.txt").write_bytes(data)
    elif tamper == "size":
        with (directory / "prompt.txt").open("ab") as stream:
            stream.write(b"x")
    elif tamper == "missing":
        (directory / "prompt.txt").unlink()
    elif tamper == "checksum":
        (directory / "SHA256SUMS").write_bytes(b"wrong\n")
    elif tamper == "unexpected":
        (directory / "stale.txt").write_bytes(b"stale")
    elif tamper == "case":
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
        prompt_row = next(row for row in manifest["members"] if row["path"] == "prompt.txt")
        manifest["members"].append({**prompt_row, "path": "PROMPT.txt"})
        _rewrite_manifest_checksums(directory, manifest)
    elif tamper == "unsafe":
        manifest = json.loads((directory / "MANIFEST.json").read_text(encoding="utf-8"))
        manifest["members"][0]["path"] = "../escape"
        _rewrite_manifest_checksums(directory, manifest)
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == code


def test_handoff_cli_is_profile_driven(tmp_project, add_shot, monkeypatch):
    add_shot(tmp_project, "S001", duration=6)
    monkeypatch.chdir(tmp_project.root)
    runner = CliRunner()
    profiles = runner.invoke(app, ["handoff", "profiles", "--json"])
    assert profiles.exit_code == 0, profiles.stdout
    assert [row["id"] for row in json.loads(profiles.stdout)["profiles"]] == [
        "portable_video", "minimax_h3"
    ]
    created = runner.invoke(app, [
        "handoff", "create", "S001", "--profile", "portable_video", "--json"
    ])
    assert created.exit_code == 0, created.stdout
    path = Path(json.loads(created.stdout)["bundle"])
    checked = runner.invoke(app, ["handoff", "verify", str(path), "--json"])
    assert checked.exit_code == 0, checked.stdout
    assert json.loads(checked.stdout)["ok"] is True
    alias = runner.invoke(app, [
        "prompt", "S001", "--target", "portable_video", "--bundle", "--json"
    ])
    assert alias.exit_code == 0, alias.stdout


def test_ingest_refuses_tampered_handoff_before_registering(
    tmp_project, add_shot, monkeypatch
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001")
    (directory / "prompt.txt").write_bytes(b"tampered")
    returned = tmp_project.root / "S001_return.mp4"
    returned.write_bytes(b"returned")
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, [
        "ingest", str(returned), "--shot", "S001", "--apply",
        "--no-auto-select", "--handoff", str(directory),
    ])
    assert result.exit_code != 0
    assert not tmp_project.takes("S001", skip_ghosts=True)


def test_v2_r1_bundle_remains_readable_after_current_profile_becomes_r2(
    tmp_project, add_shot, monkeypatch
):
    import manju.exporters.provider_handoff as handoff_module
    import manju.providers.prompt_profiles as profiles_module

    add_shot(tmp_project, "S001", duration=6)
    monkeypatch.setattr(handoff_module, "RENDERER_REVISION", "2026-08-08.r1")
    directory, _ = write_handoff_bundle(
        tmp_project, "S001", target="portable_video", output="historical-profile"
    )
    assert verify_handoff_bundle(directory).handoff["renderer_revision"].endswith(".r1")

    current_r2 = replace(PORTABLE_VIDEO_PROFILE, revision="2026-08-08.r2")
    monkeypatch.setitem(profiles_module._DESCRIPTORS, "portable_video", current_r2)
    assert video_authoring_profile_registry()["portable_video"].revision.endswith(".r2")
    assert verify_handoff_bundle(directory).handoff["profile"]["revision"].endswith(".r1")


def test_unknown_profile_revision_is_rejected(tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="unknown-profile-rev")
    handoff = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
    handoff["profile"]["revision"] = "2099-01-01.r999"
    _reseal_bundle(
        directory,
        {"handoff.json": _json_bytes(handoff)},
        manifest_updates={"profile": {
            "id": handoff["profile"]["id"],
            "revision": handoff["profile"]["revision"],
        }},
    )
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_profile_revision_unknown"


def test_unknown_renderer_revision_is_rejected(tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="unknown-renderer")
    handoff = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
    handoff["renderer_revision"] = "2099-01-01.r999"
    _reseal_bundle(
        directory,
        {"handoff.json": _json_bytes(handoff)},
        manifest_updates={"renderer_revision": handoff["renderer_revision"]},
    )
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_renderer_revision_unknown"


def test_known_profile_revision_descriptor_drift_is_rejected(
    tmp_project, add_shot
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(tmp_project, "S001", output="profile-drift")
    handoff = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
    handoff["profile"]["display_name"] = "tampered descriptor"
    _replace_declared_member(directory, "handoff.json", _json_bytes(handoff))
    with pytest.raises(ProviderHandoffError) as raised:
        verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_profile_descriptor_mismatch"


def test_writer_uses_current_profile_and_renderer_revisions(tmp_project, add_shot):
    import manju.exporters.provider_handoff as handoff_module

    add_shot(tmp_project, "S001", duration=6)
    _directory, handoff = write_handoff_bundle(
        tmp_project, "S001", target="portable_video", output="current-revisions"
    )
    assert handoff["renderer_revision"] == handoff_module.RENDERER_REVISION
    assert handoff["renderer_revision"].endswith(".r2")
    assert handoff["profile"] == (
        video_authoring_profile_registry()["portable_video"].to_dict()
    )


def test_verified_lineage_survives_cli_manual_roundtrip(
    tmp_project, add_shot, tmp_path, monkeypatch
):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = write_handoff_bundle(
        tmp_project, "S001", target="portable_video", output="lineage"
    )
    verified = verify_handoff_bundle(directory)
    returned = tmp_path / "S001_return.mp4"
    returned.write_bytes(b"returned-media")
    monkeypatch.setattr(
        "manju.providers.manual.probe_media",
        lambda _path: ProbeInfo(duration_ms=6000, width=1920, height=1080),
    )
    monkeypatch.chdir(tmp_project.root)
    result = CliRunner().invoke(app, [
        "ingest", str(returned), "--shot", "S001", "--apply",
        "--no-auto-select", "--handoff", str(directory),
    ])
    assert result.exit_code == 0, result.stdout

    take = tmp_project.takes("S001", skip_ghosts=True)[0]
    params = take.sidecar.params
    lineage = verified.lineage()
    assert params["handoff_id"] == lineage.handoff_id
    assert params["handoff_profile"] == lineage.profile_id
    assert params["handoff_profile_revision"] == lineage.profile_revision
    assert params["handoff_semantic_digest"] == lineage.semantic_digest
    assert params["bundle_digest"] == lineage.semantic_digest
    assert params["handoff_manifest_digest"] == lineage.manifest_digest
    assert params["handoff_reference_plan_digest"] == lineage.reference_plan_digest
    assert params["handoff_bundle_format_revision"] == lineage.bundle_format_revision
    assert params["handoff_renderer_revision"] == lineage.renderer_revision
    assert params["claimed_generator"] == "unverified"
    assert str(directory.resolve()) not in json.dumps(params, ensure_ascii=False)
    assert tmp_project.load_shot("S001").status.selected_take is None


def test_reference_graph_does_not_swallow_unknown_relpath_error(tmp_path):
    import manju.providers.video_authoring as video_authoring

    item = RefItem(
        ref="media/imports/reference.png",
        tier="params",
        kind="image",
        path=tmp_path / "reference.png",
        is_url=False,
        exists=False,
        root=tmp_path,
    )

    class BrokenProject:
        def relpath(self, _path):
            raise RuntimeError("programming invariant failed")

    with pytest.raises(RuntimeError, match="programming invariant failed"):
        video_authoring._reference_graph(
            BrokenProject(), SimpleNamespace(items=(item,))
        )
