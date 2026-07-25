"""Ledger P0 group ``cli_out`` — validated-output regressions.

Red-first coverage for the "output overwrites input" P0 family owned by this
group: pack / transcribe / openclap export / delivery bundle+manifest all used
to truncate a caller-named truth/import/artifact file, and gc / tasks-manifest
followed a symlinked root out of the project. Every fix routes through the
shared ``core.safeio`` owner (or the delivery disjointness guard) so a mis-named
destination refuses BEFORE any byte is written and the prior file stays intact.

Entries: PACK-P0-001, WINCLI-P0-002, OPENCLAP-P0-001, DELIVERY-P0-001,
DELIVERY-P0-002, WINCLI-P0-001, GC-P0-001, TASKS-P0-001.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.build import delivery as D
from manju.cli import app
from manju.core.container import ProjectError
from manju.core.yamlio import read_yaml, write_yaml

runner = CliRunner()


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    """cwd-anchor a fresh project so ``_project()`` discovers it from cwd."""
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


def _yaml_bytes(project) -> bytes:
    return (project.root / "project.yaml").read_bytes()


# ============================================================ PACK-P0-001


def test_pack_out_refuses_project_truth_and_leaves_it_intact(in_project):
    before = _yaml_bytes(in_project)
    result = runner.invoke(app, ["pack", "--out", "project.yaml", "--json"])
    assert result.exit_code != 0, result.output
    assert _yaml_bytes(in_project) == before          # truth untouched
    assert read_yaml(in_project.root / "project.yaml") is not None  # still YAML


def test_pack_out_refuses_import_original(in_project):
    imp = in_project.root / "media" / "imports" / "original.mp4"
    imp.parent.mkdir(parents=True, exist_ok=True)
    imp.write_bytes(b"\x00\x01original-bytes")
    before = imp.read_bytes()
    result = runner.invoke(app, ["pack", "--out", "media/imports/original.mp4", "--json"])
    assert result.exit_code != 0, result.output
    assert imp.read_bytes() == before                 # sacred import untouched


def test_pack_default_outside_project_still_works(in_project):
    result = runner.invoke(app, ["pack", "--json"])
    assert result.exit_code == 0, result.output
    pkg = in_project.root.parent / (in_project.root.stem + ".manjupkg")
    assert pkg.exists() and zipfile.is_zipfile(pkg)


def test_pack_to_exports_subtree_is_allowed(in_project):
    result = runner.invoke(app, ["pack", "--out", "exports/backup.manjupkg", "--json"])
    assert result.exit_code == 0, result.output
    pkg = in_project.root / "exports" / "backup.manjupkg"
    assert pkg.exists() and zipfile.is_zipfile(pkg)


# ============================================================ WINCLI-P0-002


def _seed_media_and_srt(project) -> tuple[Path, Path]:
    media = project.root / "media" / "imports" / "align.wav"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"RIFFfake-wav-bytes")
    srt = project.root / "in.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8")
    return media, srt


def test_transcribe_out_refuses_project_yaml(in_project):
    _media, srt = _seed_media_and_srt(in_project)
    before = _yaml_bytes(in_project)
    result = runner.invoke(app, ["transcribe", "media/imports/align.wav",
                                 "--from-srt", str(srt), "--out", "project.yaml", "--json"])
    assert result.exit_code != 0, result.output
    assert _yaml_bytes(in_project) == before          # truth NOT replaced by SRT
    assert read_yaml(in_project.root / "project.yaml") is not None


def test_transcribe_out_default_captions_dir_ok(in_project):
    media, srt = _seed_media_and_srt(in_project)
    result = runner.invoke(app, ["transcribe", "media/imports/align.wav",
                                 "--from-srt", str(srt), "--json"])
    assert result.exit_code == 0, result.output
    out = in_project.captions_dir / "transcripts" / "align.srt"
    assert out.exists()


def test_transcribe_out_explicit_captions_path_ok(in_project):
    media, srt = _seed_media_and_srt(in_project)
    result = runner.invoke(app, ["transcribe", "media/imports/align.wav",
                                 "--from-srt", str(srt),
                                 "--out", "captions/manual/x.srt", "--json"])
    assert result.exit_code == 0, result.output
    assert (in_project.root / "captions" / "manual" / "x.srt").exists()


# ============================================================ OPENCLAP-P0-001


def test_openclap_resolve_output_refuses_truth(tmp_project):
    from manju.exporters.openclap.exporter import _resolve_output

    before = _yaml_bytes(tmp_project)
    with pytest.raises(ProjectError):
        _resolve_output(tmp_project, "project.yaml")
    assert _yaml_bytes(tmp_project) == before


def test_openclap_resolve_output_refuses_import(tmp_project):
    from manju.exporters.openclap.exporter import _resolve_output

    imp = tmp_project.root / "media" / "imports" / "src.mp4"
    imp.parent.mkdir(parents=True, exist_ok=True)
    imp.write_bytes(b"import")
    with pytest.raises(ProjectError):
        _resolve_output(tmp_project, "media/imports/src.mp4")


def test_openclap_resolve_output_allows_exports_and_default(tmp_project):
    from manju.exporters.openclap.exporter import _resolve_output

    default = _resolve_output(tmp_project, None)
    assert default.parts[-2:] == ("openclap", f"{tmp_project.load_config().name}.clap")
    ok = _resolve_output(tmp_project, "exports/openclap/custom.clap")
    assert ok.is_relative_to(tmp_project.exports_dir)


def test_openclap_resolve_output_refuses_external(tmp_project, tmp_path):
    from manju.exporters.openclap.exporter import _resolve_output

    with pytest.raises(ProjectError):
        _resolve_output(tmp_project, tmp_path / "outside.clap")


# ============================================================ DELIVERY-P0-001/002


def _master_manifest(project, add_shot):
    add_shot(project, "S001")
    return D.build_manifest(project, "master")


def test_delivery_bundle_output_refuses_project_truth(tmp_project, add_shot):
    man = _master_manifest(tmp_project, add_shot)
    before = _yaml_bytes(tmp_project)
    with pytest.raises(D.DeliveryManifestError):
        D.write_bundle(tmp_project, man, output=tmp_project.root / "project.yaml")
    assert _yaml_bytes(tmp_project) == before
    assert read_yaml(tmp_project.root / "project.yaml") is not None


def test_delivery_bundle_output_refuses_renders_artifact(tmp_project, add_shot):
    man = _master_manifest(tmp_project, add_shot)
    final = tmp_project.final_dir / "final_v1.mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"master-bytes")
    before = final.read_bytes()
    with pytest.raises(D.DeliveryManifestError):
        D.write_bundle(tmp_project, man, output=final)
    assert final.read_bytes() == before               # master never truncated to a ZIP


def test_delivery_bundle_neutral_and_exports_outputs_still_work(tmp_project, add_shot):
    man = _master_manifest(tmp_project, add_shot)
    out1, _ = D.write_bundle(tmp_project, man, output=tmp_project.root / "scratch.zip")
    assert zipfile.is_zipfile(out1)
    out2, _ = D.write_bundle(tmp_project, man,
                             output=tmp_project.exports_dir / "delivery" / "ok.zip")
    assert zipfile.is_zipfile(out2)


def test_delivery_manifest_output_refuses_project_truth(tmp_project, add_shot):
    man = _master_manifest(tmp_project, add_shot)
    before = _yaml_bytes(tmp_project)
    with pytest.raises(D.DeliveryManifestError):
        D.materialize_manifest(tmp_project, man, output=tmp_project.root / "project.yaml")
    assert _yaml_bytes(tmp_project) == before


def test_delivery_manifest_default_and_reports_output_ok(tmp_project, add_shot):
    man = _master_manifest(tmp_project, add_shot)
    p = D.materialize_manifest(tmp_project, man)       # default reports area
    assert p.exists()
    p2 = D.materialize_manifest(tmp_project, man,
                                output=tmp_project.reports_dir / "delivery" / "m.json")
    assert p2.exists() and json.loads(p2.read_text(encoding="utf-8"))["schema"]


# ============================================================ WINCLI-P0-001


def _set_profiles(project, profiles) -> None:
    data = read_yaml(project.root / "project.yaml") or {}
    data["delivery_profiles"] = profiles
    write_yaml(project.root / "project.yaml", data)


TOKEN = "sk-abcdefghijklmnopqrstuvwxyz012345"


def _credential_manifest(project, add_shot):
    add_shot(project, "S001")
    meta = project.exports_dir / "youtube-metadata-secret.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"Authorization": f"Bearer {TOKEN}"}), encoding="utf-8")
    _set_profiles(project, {"yt": {"variant_kind": "platform_package",
                                   "platform": "youtube",
                                   "metadata_file": "exports/youtube-metadata-secret.json"}})
    return D.build_manifest(project, "yt")


def test_bundle_fails_closed_on_blocking_credential_leak(tmp_project, add_shot):
    man = _credential_manifest(tmp_project, add_shot)
    assert man["platform_handoff"]["credentials_present"] is True
    dest = tmp_project.root / "deliver.zip"
    with pytest.raises(D.DeliveryManifestError, match="PLATFORM_CREDENTIAL_LEAK"):
        D.write_bundle(tmp_project, man, output=dest)
    assert not dest.exists()                           # no outward bundle produced


def test_credential_token_never_reaches_any_bundle(tmp_project, add_shot):
    man = _credential_manifest(tmp_project, add_shot)
    dest = tmp_project.root / "deliver.zip"
    with pytest.raises(D.DeliveryManifestError):
        D.write_bundle(tmp_project, man, output=dest)
    # the raw secret file is never packed because the bundle refuses outright
    assert not any(p.suffix == ".zip" and TOKEN.encode() in p.read_bytes()
                   for p in tmp_project.root.rglob("*.zip"))


# ============================================================ GC-P0-001


def test_gc_refuses_symlinked_segments_root(in_project, tmp_path):
    outside = tmp_path / "ext_segments"
    outside.mkdir()
    victim = outside / "victim.bin"
    victim.write_bytes(b"external-file")
    in_project.segments_dir.parent.mkdir(parents=True, exist_ok=True)  # renders/
    if in_project.segments_dir.exists():
        in_project.segments_dir.rmdir()               # scaffolded empty dir → replace
    in_project.segments_dir.symlink_to(outside, target_is_directory=True)

    result = runner.invoke(app, ["gc", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert victim.exists()                             # external file NOT deleted
    assert "renders/segments" in data["refused_roots"]


# ============================================================ TASKS-P0-001


def test_tasks_manifest_refuses_absolute_run_id(in_project, tmp_path):
    outside = tmp_path / "outside-cli"
    result = runner.invoke(app, ["tasks", "manifest", str(outside / "x"), "--json"])
    assert result.exit_code != 0, result.output
    assert not (outside / "x" / "run.json").exists()   # no external run.json


def test_tasks_manifest_refuses_symlinked_run_dir(in_project, tmp_path):
    outside = tmp_path / "outside-run"
    outside.mkdir()
    runs = in_project.reports_dir / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "link").symlink_to(outside, target_is_directory=True)

    result = runner.invoke(app, ["tasks", "manifest", "link", "--json"])
    assert result.exit_code != 0, result.output
    assert not (outside / "run.json").exists()         # link never followed


def test_tasks_manifest_refuses_overlong_run_id(in_project):
    result = runner.invoke(app, ["tasks", "manifest", "a" * 5000, "--json"])
    assert result.exit_code != 0, result.output


def test_tasks_manifest_valid_run_id_still_works(in_project):
    result = runner.invoke(app, ["tasks", "manifest", "run_00000001", "--json"])
    assert result.exit_code == 0, result.output
    assert (in_project.reports_dir / "runs" / "run_00000001" / "run.json").exists()
