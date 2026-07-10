"""OpenClap adapter — read-only import-plan.

Covers WP test matrix items 5 (locator safety: traversal / absolute / symlink
escape classified and diagnosed, never resolved outside root), 9 (import-plan
never writes; lists conflicts against a populated target) and 10 (remote URL
never fetched / no digest, data URI digested in-memory only).
"""

from __future__ import annotations

import gzip
import hashlib

import pytest
import yaml

from manju.exporters.openclap import build_import_plan, read_clap


def _clap(tmp_path, segments, *, name="p.clap", entities=(), scenes=()):
    items = [
        {"format": "clap-0", "numberOfWorkflows": 0, "numberOfEntities": len(entities),
         "numberOfScenes": len(scenes), "numberOfSegments": len(segments)},
        {"id": "m", "title": "T"},
        *entities, *scenes, *segments,
    ]
    path = tmp_path / name
    path.write_bytes(gzip.compress(
        yaml.safe_dump(items, allow_unicode=True, sort_keys=False).encode("utf-8"),
        mtime=0))
    return read_clap(path)


# ------------------------------------------- (5) locator safety


def test_absolute_and_traversal_locators_diagnosed_never_resolved(tmp_path, tmp_project):
    doc = _clap(tmp_path, [
        {"id": "seg-abs", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 1,
         "assetUrl": "/etc/passwd"},
        {"id": "seg-esc", "category": "VIDEO", "startTimeInMs": 1, "endTimeInMs": 2,
         "assetUrl": "../../secret.mp4"},
    ])
    plan = build_import_plan(doc, source_sha256="sha256:x", target_project=tmp_project)

    codes = {d["code"] for d in plan["diagnostics"]}
    assert "locator_absolute" in codes
    assert "locator_escapes_root" in codes

    reg = {op["from_segment"]: op for op in plan["operations"] if op["op"] == "register_media"}
    # the absolute + escaping locators are recorded but explicitly NOT resolved,
    # and never carry a digest (no filesystem read)
    assert "not resolved" in reg["seg-abs"]["locator_detail"]["note"].lower()
    assert "sha256" not in reg["seg-abs"]["locator_detail"]
    assert "sha256" not in reg["seg-esc"]["locator_detail"]


def test_symlink_escape_locator_is_diagnosed(tmp_path, tmp_project):
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"x")
    link = tmp_project.imports_dir / "link.mp4"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unsupported in this environment")

    doc = _clap(tmp_path, [
        {"id": "seg-link", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 1,
         "assetUrl": "media/imports/link.mp4"},
    ])
    plan = build_import_plan(doc, source_sha256="sha256:x", target_project=tmp_project)
    assert any(d["code"] == "locator_uncontained" for d in plan["diagnostics"])
    reg = next(op for op in plan["operations"] if op["op"] == "register_media")
    assert "not resolved" in reg["locator_detail"]["note"].lower()


def test_semantic_segments_not_planned_as_clips(tmp_path):
    doc = _clap(tmp_path, [
        {"id": "cam1", "category": "CAMERA", "startTimeInMs": 0, "endTimeInMs": 1},
        {"id": "sty1", "category": "STYLE", "startTimeInMs": 0, "endTimeInMs": 1},
        {"id": "vid1", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 1,
         "assetUrl": "media/gen/S001/take_01.mp4"},
    ])
    plan = build_import_plan(doc, source_sha256="sha256:x")
    unmapped_ids = {u["item"] for u in plan["unmapped"]}
    assert "cam1" in unmapped_ids and "sty1" in unmapped_ids
    # the semantic ones never become create_shot / register_media
    op_segments = {op.get("from_segment") for op in plan["operations"]}
    assert "cam1" not in op_segments and "sty1" not in op_segments
    assert "vid1" in op_segments


def test_unknown_category_segment_listed_unmapped(tmp_path):
    doc = _clap(tmp_path, [
        {"id": "weird", "category": "FOO_FUTURE", "startTimeInMs": 0, "endTimeInMs": 1},
    ])
    plan = build_import_plan(doc, source_sha256="sha256:x")
    assert any(u["item"] == "weird" and "unknown" in u["reason"].lower()
               for u in plan["unmapped"])
    assert not plan["operations"]


# ---------------------------------- (9) import-plan never writes


def _tree_snapshot(root):
    snap = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_mtime_ns, p.read_bytes())
    return snap


def test_import_plan_never_writes_and_lists_conflicts(tmp_path, tmp_project, add_shot, make_take):
    # populate the target project
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:a")

    doc = _clap(tmp_path, [
        {"id": "v1", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 1000,
         "assetUrl": "media/gen/S001/take_01.mp4"},
        {"id": "v2", "category": "VIDEO", "startTimeInMs": 1000, "endTimeInMs": 2000,
         "assetUrl": "https://example.com/two.mp4"},
    ])

    before = _tree_snapshot(tmp_project.root)
    plan = build_import_plan(doc, source_sha256="sha256:x", target_project=tmp_project)
    after = _tree_snapshot(tmp_project.root)

    assert before == after  # zero filesystem mutations (content AND mtime)
    assert plan["target_project"] == str(tmp_project.root)
    assert plan["conflicts"]  # target already has shots -> conflicts listed
    assert any(c["target"] == "shots/S001.yaml" for c in plan["conflicts"])


# ------------------------------ (10) URL & data-URI locators


def test_remote_url_never_fetched_data_uri_digested_in_memory(tmp_path):
    data_uri = "data:text/plain;base64,SGVsbG8="  # decodes to b"Hello"
    doc = _clap(tmp_path, [
        {"id": "remote", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 1,
         "assetUrl": "https://cdn.example.com/clip.mp4"},
        {"id": "inline", "category": "STORYBOARD", "startTimeInMs": 0, "endTimeInMs": 1,
         "assetUrl": data_uri},
    ])
    plan = build_import_plan(doc, source_sha256="sha256:x")
    reg = {op["from_segment"]: op for op in plan["operations"] if op["op"] == "register_media"}

    remote = reg["remote"]
    assert remote["locator"]["kind"] == "remote_url"
    assert "not downloaded" in remote["note"].lower()
    # a remote URL is NEVER fetched -> it can never claim a digest
    assert "sha256" not in remote["locator_detail"]
    assert "size_bytes" not in remote["locator_detail"]

    inline = reg["inline"]
    assert inline["locator"]["kind"] == "data_uri"
    detail = inline["locator_detail"]
    assert detail["size_bytes"] == 5
    assert detail["sha256"] == "sha256:" + hashlib.sha256(b"Hello").hexdigest()
    assert "in-memory" in detail["note"].lower()


def test_video_segment_plans_create_shot_and_register_media(tmp_path):
    doc = _clap(tmp_path, [
        {"id": "v1", "category": "VIDEO", "startTimeInMs": 0, "endTimeInMs": 1000,
         "prompt": "a rainy street", "assetUrl": "media/gen/S001/take_01.mp4"},
    ])
    plan = build_import_plan(doc, source_sha256="sha256:x")
    ops = {op["op"] for op in plan["operations"]}
    assert ops == {"create_shot", "register_media"}
    create = next(op for op in plan["operations"] if op["op"] == "create_shot")
    assert create["target"] == "shots/S001.yaml"
    assert create["from_segment"] == "v1"
    assert create["summary"] == "a rainy street"
    reg = next(op for op in plan["operations"] if op["op"] == "register_media")
    assert reg["media_role"] == "video"
    assert reg["target"].startswith("media/imports/")


def test_plan_schema_and_source_sha_echoed(tmp_path):
    doc = _clap(tmp_path, [])
    plan = build_import_plan(doc, source_sha256="sha256:deadbeef")
    assert plan["schema"] == "manju.openclap-import-plan/v1"
    assert plan["source_sha256"] == "sha256:deadbeef"
    assert plan["target_project"] is None
