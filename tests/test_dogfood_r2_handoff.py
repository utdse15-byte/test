"""Human upload guidance is derived from, and integrity-bound to, frozen assets."""
from __future__ import annotations

import json

import pytest

from manju.exporters import provider_handoff as handoff
from tests.test_video_authoring_generalization import _replace_declared_member


def _shot(project, add_shot):
    for name in ("人物.png", "结尾.png"):
        (project.imports_dir / name).write_bytes(name.encode())
    add_shot(project, "S001", duration=6, generation={"params": {"images": [
        {"ref": "media/imports/人物.png", "subject_ref": "character:linxia", "controls": ["face"], "ignore": ["lighting"]},
        {"ref": "media/imports/人物.png", "subject_ref": "character:mei", "controls": ["costume"]},
        "https://example.invalid/remote.png?token=DO_NOT_EXPORT",
    ]}}, keyframes=[
        {"position": "start", "image": "media/imports/人物.png"},
        {"position": "end", "image": "media/imports/结尾.png"},
    ])


def test_asset_map_matches_actual_frozen_files_and_keeps_bindings(tmp_project, add_shot):
    _shot(tmp_project, add_shot)
    directory, _ = handoff.write_handoff_bundle(tmp_project, "S001", target="portable_video", output="mapped")
    mapping = (directory / "ASSET_MAP.md").read_text(encoding="utf-8")
    refs = json.loads((directory / "refs.json").read_text(encoding="utf-8"))
    metadata = json.loads((directory / "handoff.json").read_text(encoding="utf-8"))
    for path in directory.glob("assets/*"):
        assert path.relative_to(directory).as_posix() in mapping
    for binding in refs["logical_bindings"]:
        assert binding["physical_id"] in mapping
    assert "character:linxia" in mapping and "character:mei" in mapping
    assert "face" in mapping and "costume" in mapping and "lighting" in mapping
    assert "start" in mapping and "end" in mapping
    assert "NOT_PACKAGED" in mapping
    assert "DO_NOT_EXPORT" not in mapping
    assert str(tmp_project.root) not in mapping
    assert metadata["claims"]["execution_verified"] is False
    assert metadata["return"]["auto_select"] is False
    assert handoff.verify_handoff_bundle(directory).handoff["renderer_revision"] == handoff.RENDERER_REVISION


def test_asset_map_is_deterministic_and_generated_from_one_frozen_snapshot(tmp_project, add_shot):
    _shot(tmp_project, add_shot)
    built = handoff.build_handoff(tmp_project, "S001", target="portable_video")
    first = handoff._bundle_files(built)
    (tmp_project.imports_dir / "人物.png").write_bytes(b"changed after freezing")
    assert handoff._bundle_files(built) == first
    assert "ASSET_MAP.md" in first


def test_resealed_misleading_asset_map_fails_semantic_verification(tmp_project, add_shot):
    add_shot(tmp_project, "S001", duration=6)
    directory, _ = handoff.write_handoff_bundle(tmp_project, "S001", target="portable_video", output="tamper")
    _replace_declared_member(directory, "ASSET_MAP.md", b"Upload the wrong file instead.")
    with pytest.raises(handoff.ProviderHandoffError) as raised:
        handoff.verify_handoff_bundle(directory)
    assert raised.value.code == "handoff_semantic_digest_mismatch"


@pytest.mark.parametrize("revision", ["2026-08-08.r1", "2026-08-08.r2"])
def test_pre_asset_map_bundles_remain_verifiable(tmp_project, add_shot, monkeypatch, revision):
    add_shot(tmp_project, "S001", duration=6)
    with monkeypatch.context() as old:
        old.setattr(handoff, "RENDERER_REVISION", revision)
        directory, _ = handoff.write_handoff_bundle(tmp_project, "S001", target="portable_video", output="old")
    assert "ASSET_MAP.md" not in {p.name for p in directory.iterdir()}
    assert handoff.verify_handoff_bundle(directory).handoff["renderer_revision"] == revision


@pytest.mark.parametrize('filename', [
    '首帧.png', '人物.JPG', 'ééé.webp', 'a' * 110 + '.png', '角色 ' + 'b' * 100 + '.jpeg',
])
def test_upload_asset_keeps_media_extension_for_unicode_and_long_names(
    tmp_project, add_shot, filename,
):
    """A safe stem must not erase the extension used by upload file pickers."""
    import mimetypes
    from pathlib import Path

    source = tmp_project.imports_dir / filename
    source.write_bytes(b'fixture bytes; this test concerns packaging, not media decoding')
    add_shot(tmp_project, 'S001', duration=6,
             keyframes=[{'position': 'start', 'image': 'media/imports/' + filename}])
    directory, _ = handoff.write_handoff_bundle(
        tmp_project, 'S001', target='portable_video', output='extension-check')
    [asset] = list((directory / 'assets').iterdir())
    assert asset.suffix == Path(filename).suffix
    assert (mimetypes.guess_type(str(asset))[0] or '').startswith('image/')
    assert asset.name.isascii()
    assert asset.read_bytes() == source.read_bytes()
    assert handoff.verify_handoff_bundle(directory).handoff['shot'] == 'S001'


@pytest.mark.parametrize('revision', ['2026-08-08.r1', '2026-08-08.r2'])
def test_historical_unicode_asset_names_still_verify(tmp_project, add_shot, monkeypatch, revision):
    (tmp_project.imports_dir / '首帧.png').write_bytes(b'historical fixture')
    add_shot(tmp_project, 'S001', duration=6,
             keyframes=[{'position': 'start', 'image': 'media/imports/首帧.png'}])
    with monkeypatch.context() as old:
        old.setattr(handoff, 'RENDERER_REVISION', revision)
        directory, _ = handoff.write_handoff_bundle(
            tmp_project, 'S001', target='portable_video', output='historical-extension')
    [asset] = list((directory / 'assets').iterdir())
    assert asset.suffix == ''  # historical broken naming is not rewritten in place
    assert handoff.verify_handoff_bundle(directory).handoff['renderer_revision'] == revision
