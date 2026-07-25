"""Ledger P0 regressions — group `exchange` (relink / pullsheet / seriespack /
bridge).

Every test here is red-first against the fixed baseline: it drives the SAME
module-level writers/intake the CLI/services use and proves the safe-write and
safe-intake disciplines the audit demanded. Nothing depends on a real provider.

Covered ids: RELINK-P0-001/002, PULLSHEET-P0-001, REFPACK-P0-001,
TEMPLATE-P0-001/002, BRIDGE-P0-001/002.
"""

from __future__ import annotations

import base64
import json
import os
import zipfile
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.safeio import SafeOutError
from manju.core.yamlio import read_yaml, write_yaml

POSIX = os.name == "posix"

# a genuine 1x1 PNG — endpoint/image intake needs real magic bytes, not text.
_MIN_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _write_png(path: Path, tag: bytes = b"") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_MIN_PNG + tag)
    return path


# =========================================================== RELINK-P0-001

def _mk_plan_dict() -> dict:
    from manju.media.relink import RELINK_PLAN_SCHEMA
    return {"schema": RELINK_PLAN_SCHEMA, "rows": [], "summary": {}}


def test_relink_plan_out_refuses_project_truth(tmp_project):
    from manju.media.relink import write_relink_plan

    truth = tmp_project.root / "project.yaml"
    before = truth.read_bytes()
    with pytest.raises(SafeOutError):
        write_relink_plan(tmp_project, _mk_plan_dict(), truth)
    assert truth.read_bytes() == before  # 失败零写入 — truth untouched


def test_relink_plan_out_refuses_inside_non_publish_and_allows_exports(tmp_project):
    from manju.media.relink import write_relink_plan

    with pytest.raises(SafeOutError):
        write_relink_plan(tmp_project, _mk_plan_dict(),
                          tmp_project.shots_dir / "S001.yaml")
    out = tmp_project.root / "exports" / "relink" / "plan.json"
    written = write_relink_plan(tmp_project, _mk_plan_dict(), out)
    assert written == out
    assert json.loads(out.read_text(encoding="utf-8"))["schema"]


@pytest.mark.skipif(not POSIX, reason="symlink leaf")
def test_relink_plan_out_refuses_symlink_leaf(tmp_project, tmp_path):
    from manju.media.relink import write_relink_plan

    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n", encoding="utf-8")
    link = tmp_path / "out.json"
    link.symlink_to(victim)
    with pytest.raises(SafeOutError):
        write_relink_plan(tmp_project, _mk_plan_dict(), link)
    assert victim.read_text(encoding="utf-8") == "keep\n"


# =========================================================== RELINK-P0-002

def _handcrafted_plan(target: str, candidate: Path) -> dict:
    from manju.core.hashing import hash_file
    from manju.media.relink import RELINK_PLAN_SCHEMA
    return {
        "schema": RELINK_PLAN_SCHEMA,
        "rows": [{
            "missing": {"kind": "take", "id": "S001/take_01",
                        "last_known_relpath": target,
                        "expected_hash": hash_file(candidate)},
            "candidate": {"path": str(candidate), "size": candidate.stat().st_size,
                          "hash": hash_file(candidate)},
            "verified": True, "method": "content_hash", "action": "relink",
        }],
    }


@pytest.mark.skipif(not POSIX, reason="hardlink pre-plant")
def test_relink_apply_ignores_preplanted_hardlink_at_fixed_temp(tmp_project, tmp_path):
    """A pre-planted hardlink at the OLD predictable ``<name>.relink_tmp`` must
    no longer capture the copy and write candidate bytes into project truth —
    publish_tmp uses a random exclusive name (RELINK-P0-002)."""
    from manju.media.relink import apply_relink

    target_rel = "media/gen/S001/take_01.mp4"
    target = tmp_project.root / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    truth = tmp_project.root / "project.yaml"
    truth_before = truth.read_bytes()
    # the attacker's fixed-name temp aliasing project truth
    os.link(truth, target.parent / "take_01.mp4.relink_tmp")

    cand = tmp_path / "cand.mp4"
    cand.write_bytes(b"restored-candidate-bytes")
    result = apply_relink(tmp_project, _handcrafted_plan(target_rel, cand))

    assert truth.read_bytes() == truth_before          # truth NOT corrupted
    assert result["rows"][0]["status"] == "restored"   # restore still succeeds
    assert target.read_bytes() == b"restored-candidate-bytes"


@pytest.mark.skipif(not POSIX, reason="symlink pre-plant")
def test_relink_apply_ignores_preplanted_symlink_at_fixed_temp(tmp_project, tmp_path):
    from manju.media.relink import apply_relink

    target_rel = "media/gen/S001/take_02.mp4"
    target = tmp_project.root / target_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    victim = tmp_path / "outside_victim.bin"
    victim.write_bytes(b"outside-keep-me")
    (target.parent / "take_02.mp4.relink_tmp").symlink_to(victim)

    cand = tmp_path / "cand2.mp4"
    cand.write_bytes(b"good-bytes")
    result = apply_relink(tmp_project, _handcrafted_plan(target_rel, cand))

    assert victim.read_bytes() == b"outside-keep-me"   # external victim untouched
    assert result["rows"][0]["status"] == "restored"
    assert target.read_bytes() == b"good-bytes"


# =========================================================== PULLSHEET-P0-001

def _set_name(project: Project, name: str) -> None:
    data = read_yaml(project.root / "project.yaml") or {}
    data["name"] = name
    write_yaml(project.root / "project.yaml", data)


def test_pullsheet_absolute_project_name_cannot_escape(tmp_project, add_shot, tmp_path):
    from manju.build.pullsheet import export_pull_sheet

    add_shot(tmp_project, "S001")
    outside = tmp_path / "outside"
    outside.mkdir()
    victim_csv = outside / "victim.csv"
    victim_md = outside / "victim.md"
    victim_csv.write_text("KEEP CSV\n", encoding="utf-8")
    victim_md.write_text("KEEP MD\n", encoding="utf-8")

    _set_name(tmp_project, str(outside / "victim"))  # absolute path as "name"
    out = export_pull_sheet(tmp_project)

    assert victim_csv.read_text(encoding="utf-8") == "KEEP CSV\n"  # not overwritten
    assert victim_md.read_text(encoding="utf-8") == "KEEP MD\n"
    # output stayed inside exports/pullsheet
    assert out["csv"].is_relative_to(tmp_project.exports_dir / "pullsheet")
    assert out["md"].is_relative_to(tmp_project.exports_dir / "pullsheet")


def test_pullsheet_separator_and_reserved_name_sanitized(tmp_project, add_shot):
    from manju.build.pullsheet import _safe_sheet_stem, export_pull_sheet

    assert _safe_sheet_stem("../../etc/passwd") == "passwd" or \
        "/" not in _safe_sheet_stem("../../etc/passwd")
    assert _safe_sheet_stem("CON") == "pullsheet"   # Windows reserved → fallback
    assert _safe_sheet_stem("") == "pullsheet"
    assert _safe_sheet_stem("雨夜便利店") == "雨夜便利店"  # CJK kept

    add_shot(tmp_project, "S001")
    _set_name(tmp_project, "a/b\\c:d")
    out = export_pull_sheet(tmp_project)
    assert out["csv"].is_relative_to(tmp_project.exports_dir / "pullsheet")
    assert out["csv"].exists()


# =========================================================== REFPACK-P0-001

def _make_pack(pack_dir: Path, pack_id: str) -> Path:
    from manju.build.seriespack import REFPACK_SCHEMA
    pack_dir.mkdir(parents=True, exist_ok=True)
    write_yaml(pack_dir / "pack.yaml", {
        "schema": REFPACK_SCHEMA, "pack_id": pack_id,
        "subject": "x", "items": [], "pack_digest": "sha256:0"})
    return pack_dir


def test_refpack_absolute_pack_id_cannot_overwrite_external(tmp_project, tmp_path):
    from manju.build.seriespack import SeriesPackError, import_reference_pack

    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.yaml"
    victim.write_text("keep: true\n", encoding="utf-8")

    pack = _make_pack(tmp_path / "pack", str(outside / "victim"))
    with pytest.raises(SeriesPackError):
        import_reference_pack(tmp_project, pack)
    assert victim.read_text(encoding="utf-8") == "keep: true\n"


def test_refpack_traversal_and_reserved_pack_id_refused(tmp_project, tmp_path):
    from manju.build.seriespack import SeriesPackError, import_reference_pack

    for bad in ("../evil", "a/b", "CON", "sub\\pack"):
        pack = _make_pack(tmp_path / f"p_{abs(hash(bad))}", bad)
        with pytest.raises(SeriesPackError):
            import_reference_pack(tmp_project, pack)


def test_refpack_valid_pack_id_still_imports(tmp_project, tmp_path):
    from manju.build.seriespack import import_reference_pack

    pack = _make_pack(tmp_path / "good", "linxia_pack")
    res = import_reference_pack(tmp_project, pack)
    assert res["index_path"].endswith("linxia_pack.yaml")
    assert (tmp_project.root / "bible" / "refpacks" / "linxia_pack.yaml").exists()


# =========================================================== TEMPLATE-P0-001

@pytest.mark.skipif(not POSIX, reason="symlink pre-plant")
def test_template_export_predictable_tmp_symlink_cannot_capture(tmp_project, tmp_path):
    from manju.build.seriespack import export_template_pack

    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"OUTSIDE-KEEP")
    out_zip = tmp_path / "template.zip"
    (tmp_path / "template.zip.tmp").symlink_to(victim)  # the old fixed temp name

    export_template_pack(tmp_project, out_zip, bible_entries=["linxia"])

    assert victim.read_bytes() == b"OUTSIDE-KEEP"       # external victim untouched
    assert out_zip.exists() and zipfile.is_zipfile(out_zip)


# =========================================================== TEMPLATE-P0-002

@pytest.mark.skipif(not POSIX, reason="symlink source")
def test_template_export_refuses_symlinked_style_source(tmp_project, tmp_path):
    from manju.build.seriespack import SeriesPackError, export_template_pack

    secret = tmp_path / "private_client_style.yaml"
    secret.write_text("secret_client: do-not-share\n", encoding="utf-8")
    style_path = tmp_project.root / "bible" / "style.yaml"
    if style_path.exists():
        style_path.unlink()
    style_path.parent.mkdir(parents=True, exist_ok=True)
    style_path.symlink_to(secret)

    out_zip = tmp_path / "t.zip"
    with pytest.raises(SeriesPackError):
        export_template_pack(tmp_project, out_zip, include_style=True)
    # nothing external leaked into a pack (export refused before finalizing)
    if out_zip.exists():
        with zipfile.ZipFile(out_zip) as zf:
            assert "bible/style.yaml" not in zf.namelist()


def test_template_export_reads_regular_style_source(tmp_project, tmp_path):
    from manju.build.seriespack import export_template_pack

    style_path = tmp_project.root / "bible" / "style.yaml"
    style_path.parent.mkdir(parents=True, exist_ok=True)
    style_path.write_text("palette: cool\n", encoding="utf-8")
    out_zip = tmp_path / "ok.zip"
    out = export_template_pack(tmp_project, out_zip, include_style=True)
    assert "bible/style.yaml" in out["members"]


# =========================================================== BRIDGE-P0-001

def test_bridge_plan_out_refuses_truth_and_allows_exports(tmp_project):
    from manju.build.bridge import write_bridge_plan

    truth = tmp_project.root / "project.yaml"
    before = truth.read_bytes()
    with pytest.raises(SafeOutError):
        write_bridge_plan({"request_digest": "x"}, truth, tmp_project.root)
    assert truth.read_bytes() == before

    out = tmp_project.root / "exports" / "bridge" / "plan.json"
    written = write_bridge_plan({"request_digest": "x"}, out, tmp_project.root)
    assert written == out and json.loads(out.read_text(encoding="utf-8"))


@pytest.mark.skipif(not POSIX, reason="symlink leaf")
def test_bridge_plan_out_refuses_symlink_to_external(tmp_project, tmp_path):
    from manju.build.bridge import write_bridge_plan

    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"KEEP")
    link = tmp_path / "plan.json"
    link.symlink_to(victim)
    with pytest.raises(SafeOutError):
        write_bridge_plan({"request_digest": "x"}, link, tmp_project.root)
    assert victim.read_bytes() == b"KEEP"


# =========================================================== BRIDGE-P0-002

def test_bridge_rejects_non_image_endpoint_by_extension(tmp_project, tmp_path):
    from manju.build.bridge import BridgeError, plan_bridge

    env = tmp_project.root / ".env"
    env.write_text("BRIDGE_LOCAL_SECRET_30=sk-live-do-not-upload\n", encoding="utf-8")
    good = _write_png(tmp_project.root / "media" / "gen" / "S001" / "a.png", b"a")
    with pytest.raises(BridgeError):
        plan_bridge(prev_end_frame=env, next_start_frame=good,
                    duration_ms=600, project_root=tmp_project.root)


def test_bridge_rejects_non_image_bytes_disguised_as_png(tmp_project, tmp_path):
    from manju.build.bridge import BridgeError, plan_bridge

    fake = tmp_project.root / "media" / "gen" / "S001" / "secret.png"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text("SECRET=sk-live\n", encoding="utf-8")   # .png ext, non-image bytes
    good = _write_png(tmp_project.root / "media" / "gen" / "S001" / "b.png", b"b")
    with pytest.raises(BridgeError):
        plan_bridge(prev_end_frame=fake, next_start_frame=good,
                    duration_ms=600, project_root=tmp_project.root)


@pytest.mark.skipif(not POSIX, reason="symlink endpoint")
def test_bridge_rejects_symlink_endpoint(tmp_project, tmp_path):
    from manju.build.bridge import BridgeError, plan_bridge

    secret = tmp_path / "outside.png"
    secret.write_bytes(_MIN_PNG + b"outside")
    link = tmp_project.root / "media" / "gen" / "S001" / "link.png"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(secret)
    good = _write_png(tmp_project.root / "media" / "gen" / "S001" / "c.png", b"c")
    with pytest.raises(BridgeError):
        plan_bridge(prev_end_frame=link, next_start_frame=good,
                    duration_ms=600, project_root=tmp_project.root)


def test_bridge_rejects_endpoint_outside_project(tmp_project, tmp_path):
    from manju.build.bridge import BridgeError, plan_bridge

    outside = _write_png(tmp_path / "outside_real.png", b"o")
    good = _write_png(tmp_project.root / "media" / "gen" / "S001" / "d.png", b"d")
    with pytest.raises(BridgeError):
        plan_bridge(prev_end_frame=outside, next_start_frame=good,
                    duration_ms=600, project_root=tmp_project.root)


def test_bridge_accepts_real_in_project_image(tmp_project):
    from manju.build.bridge import plan_bridge

    prev = _write_png(tmp_project.root / "media" / "gen" / "S001" / "p.png", b"p")
    nxt = _write_png(tmp_project.root / "media" / "gen" / "S001" / "n.png", b"n")
    plan = plan_bridge(prev_end_frame=prev, next_start_frame=nxt,
                       duration_ms=600, project_root=tmp_project.root)
    assert plan["prev_end_frame_hash"] and plan["next_start_frame_hash"]
