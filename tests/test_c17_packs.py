"""AI_IDE_17 — identity reference packs, episode outline packages, template
packs (WP1/WP4/WP5). Contract §10 rows 6-7, 10, 12-13 + the voice-rights gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.series import Series, new_episode
from manju.core.yamlio import read_yaml, write_yaml


@pytest.fixture
def tmp_series(tmp_path: Path) -> Series:
    series = Series.create(tmp_path / "S", git_init=False)
    new_episode(series, "E01", title="初雪")
    return series


def _media(project: Project, name: str, data: bytes = b"ref-bytes-1") -> str:
    p = project.root / "media" / "refs" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return f"media/refs/{name}"


# ================================================ §10.6 pack path/license/hash


def test_reference_pack_records_role_controls_provenance_hash(tmp_project):
    from manju.build import seriespack as P

    rel = _media(tmp_project, "front.png")
    out = P.build_reference_pack(tmp_project, "linxia_pack", "linxia", [
        {"path": rel, "role": "front", "controls": ["character_identity", "face"],
         "ignore": ["background"],
         "provenance": {"source": "commissioned artist 2026-05",
                        "license_or_consent": "contract #77"}},
    ])
    item = out["index"]["items"][0]
    assert item["role"] == "front"
    assert item["controls"] == ["character_identity", "face"]
    assert item["ignore"] == ["background"]                    # must_not_transfer
    assert item["sha256"].startswith("sha256:")
    assert item["rights_missing"] is False
    assert out["index"]["pack_digest"].startswith("sha256:")
    assert (Path(out["pack_dir"]) / item["file"]).exists()     # bytes copied


def test_reference_pack_refuses_unknown_vocab_and_conflicts(tmp_project):
    from manju.build import seriespack as P

    rel = _media(tmp_project, "x.png")
    out = P.build_reference_pack(tmp_project, "bad_pack", "linxia", [
        {"path": rel, "role": "front", "controls": ["not_a_dimension"]},
        {"path": rel, "role": "front", "controls": ["face"], "ignore": ["face"]},
    ])
    assert len(out["refused"]) == 2 and not out["index"]["items"]
    assert any("未知 transfer 维度" in p for r in out["refused"] for p in r["problems"])
    assert any("冲突" in p for r in out["refused"] for p in r["problems"])


def test_reference_pack_missing_rights_flagged(tmp_project):
    from manju.build import seriespack as P

    rel = _media(tmp_project, "y.png")
    out = P.build_reference_pack(tmp_project, "norights", "linxia",
                                 [{"path": rel, "role": "profile"}])
    assert out["index"]["items"][0]["rights_missing"] is True


# =============================================== §10.7 copy-on-import immunity


def test_copy_on_import_external_mutation_is_inert(tmp_project, tmp_path):
    """PIN: after import, mutating (or deleting) the external pack changes
    nothing in the project — bytes were copied content-addressed."""
    from manju.build import seriespack as P
    from manju.core.hashing import hash_file

    rel = _media(tmp_project, "id.png", b"identity-v1")
    out = P.build_reference_pack(tmp_project, "linxia_pack", "linxia", [
        {"path": rel, "role": "front", "controls": ["face"],
         "provenance": {"source": "s", "license_or_consent": "c"}}],
        out_dir=tmp_path / "external_pack")

    dest = Project.create(tmp_path / "other_project", git_init=False)
    imp = P.import_reference_pack(dest, out["pack_dir"])
    assert imp["imported"] == 1 and not imp["problems"]
    local_file = dest.root / imp["index"]["items"][0]["file"]
    sha_before = hash_file(local_file)

    # sabotage the EXTERNAL pack after import
    for p in (tmp_path / "external_pack" / "media").iterdir():
        p.write_bytes(b"HACKED")
    (tmp_path / "external_pack" / "pack.yaml").write_text("schema: junk", encoding="utf-8")

    assert hash_file(local_file) == sha_before                # project unaffected
    assert read_yaml(dest.root / "bible" / "refpacks" / "linxia_pack.yaml")


def test_import_refuses_tampered_pack(tmp_project, tmp_path):
    from manju.build import seriespack as P

    rel = _media(tmp_project, "id2.png", b"identity-v2")
    out = P.build_reference_pack(tmp_project, "p2", "linxia",
                                 [{"path": rel, "role": "front"}],
                                 out_dir=tmp_path / "pack2")
    # tamper the media AFTER the index recorded its hash
    for p in (tmp_path / "pack2" / "media").iterdir():
        p.write_bytes(b"tampered")
    dest = Project.create(tmp_path / "dst2", git_init=False)
    imp = P.import_reference_pack(dest, tmp_path / "pack2")
    assert imp["imported"] == 0
    assert any("hash 不符" in p["problem"] for p in imp["problems"])


# ===================================== §10.12 cross-project provenance chain


def test_cross_project_import_keeps_identity_and_voice_provenance(tmp_project, tmp_path):
    from manju.build import seriespack as P

    rel = _media(tmp_project, "face.png", b"face-bytes")
    out = P.build_reference_pack(tmp_project, "lin_id", "linxia", [
        {"path": rel, "role": "front", "controls": ["character_identity"],
         "provenance": {"source": "actor shoot 2026", "license_or_consent": "release #9"}},
    ], out_dir=tmp_path / "pk")
    dest = Project.create(tmp_path / "second", git_init=False)
    imp = P.import_reference_pack(dest, tmp_path / "pk")
    item = imp["index"]["items"][0]
    assert item["provenance"]["source"] == "actor shoot 2026"          # rides along
    assert item["provenance"]["license_or_consent"] == "release #9"
    assert item["imported_from"]["pack_id"] == "lin_id"
    assert item["imported_from"]["pack_digest"] == out["index"]["pack_digest"]
    assert imp["index"]["origin_pack_digest"] == out["index"]["pack_digest"]


# ======================================= §10.10 outline zero-write / apply


def _outline_pkg(series: Series) -> dict:
    (series.script_dir / "novel.md").write_text(
        "\n".join(f"line {i}" for i in range(1, 21)), encoding="utf-8")
    return {
        "schema": "manju.episode-outline-package/v1",
        "source_script": "script/novel.md",
        "episodes": [
            {"id": "E01", "title": "初雪", "target_duration_s": 90,
             "source_span": {"start_line": 1, "end_line": 10},
             "cliffhanger": "她看见了柜台后的影子", "characters": ["linxia"]},
            {"id": "E02", "title": "临界", "target_duration_s": 90,
             "source_span": {"start_line": 11, "end_line": 20}},
        ],
    }


def test_outline_inspect_is_zero_write(tmp_series, tmp_path):
    from manju.build import seriespack as P

    pkg = _outline_pkg(tmp_series)
    pkg_path = tmp_path / "outline.yaml"
    write_yaml(pkg_path, pkg)
    snapshot = sorted(str(p.relative_to(tmp_series.root))
                      for p in tmp_series.root.rglob("*") if p.is_file())
    report = P.inspect_outline(tmp_series, P.load_outline_package(pkg_path))
    assert report["ok"] is True
    assert report["episodes"][0]["exists"] is True             # E01 already there
    assert report["episodes"][1]["would_create"] is True
    after = sorted(str(p.relative_to(tmp_series.root))
                   for p in tmp_series.root.rglob("*") if p.is_file())
    assert after == snapshot                                    # ZERO writes


def test_outline_apply_creates_episodes_via_existing_path(tmp_series):
    from manju.build import seriespack as P

    pkg = _outline_pkg(tmp_series)
    result = P.apply_outline(tmp_series, pkg, actor="human")
    created = {r["id"]: r for r in result["applied"]}
    assert created["E02"]["created"] is True
    assert created["E01"]["created"] is False                   # existed, reused
    # registered through the EXISTING series.yaml path
    assert "E02" in [e.id for e in tmp_series.load_config().episodes]
    script = (tmp_series.episode_project_dir("E02") / "story" / "script.md").read_text(encoding="utf-8")
    assert "line 11" in script and "line 20" in script


def test_outline_source_script_path_escape_refused(tmp_series, tmp_path):
    from manju.build import seriespack as P

    pkg = _outline_pkg(tmp_series)
    for evil in ("/etc/passwd", "../outside.md"):
        pkg["source_script"] = evil
        bad = tmp_path / "evil.yaml"
        write_yaml(bad, pkg)
        with pytest.raises(P.SeriesPackError):
            P.load_outline_package(bad)


def test_outline_span_diagnostics_refuse_apply(tmp_series, tmp_path):
    from manju.build import seriespack as P

    pkg = _outline_pkg(tmp_series)
    pkg["episodes"][1]["source_span"] = {"start_line": 5, "end_line": 25}  # overlap+range
    diags = P.inspect_outline(tmp_series, pkg)["diagnostics"]
    codes = {d["code"] for d in diags}
    assert "SPAN_OVERLAP_OR_DISORDER" in codes and "SPAN_OUT_OF_RANGE" in codes
    with pytest.raises(P.SeriesPackError):
        P.apply_outline(tmp_series, pkg)


# =============================== §10.13 cliffhanger/hook are proposals only


def test_cliffhanger_hook_carried_verbatim_never_invented(tmp_series):
    from manju.build import seriespack as P

    pkg = _outline_pkg(tmp_series)
    report = P.inspect_outline(tmp_series, pkg)
    assert report["episodes"][0]["cliffhanger"] == "她看见了柜台后的影子"  # verbatim
    assert report["episodes"][1]["cliffhanger"] is None        # absent stays absent
    assert report["episodes"][1]["hook"] is None               # engine invents nothing


# ============================================= WP5 template pack + voice gate


def _rights_char(project: Project, *, with_rights: bool) -> None:
    chars = read_yaml(project.root / "bible" / "characters.yaml") or {}
    entry = {"name": "林夏", "voice_id": "vx-1", "voice_locked": True}
    if with_rights:
        entry["voice_provenance"] = {"source": "actor session",
                                     "license_or_consent": "signed #A17"}
    chars["linxia"] = entry
    write_yaml(project.root / "bible" / "characters.yaml", chars)


def test_template_pack_voice_rights_gate_blocks_unlicensed(tmp_project, tmp_path):
    """Ruling 6 PIN: the pack MUST consult AI_IDE_18's template_export_gate —
    a voice profile without rights is refused (listed, not packed)."""
    from manju.build import seriespack as P

    _rights_char(tmp_project, with_rights=False)
    out = P.export_template_pack(tmp_project, tmp_path / "t.zip",
                                 bible_entries=["linxia"],
                                 voice_profiles=["linxia"])
    assert out["refused_voice_profiles"] and \
           out["refused_voice_profiles"][0]["character"] == "linxia"
    assert "voice/profiles.yaml" not in out["members"]         # NOT packed
    assert any("provenance" in r for r in out["refused_voice_profiles"][0]["reasons"])


def test_template_pack_licensed_voice_profile_is_packed(tmp_project, tmp_path):
    from manju.build import seriespack as P

    _rights_char(tmp_project, with_rights=True)
    out = P.export_template_pack(tmp_project, tmp_path / "t2.zip",
                                 bible_entries=["linxia"],
                                 voice_profiles=["linxia"],
                                 include_style=True, include_rules=True)
    assert not out["refused_voice_profiles"]
    assert "voice/profiles.yaml" in out["members"]
    assert "manifest.yaml" in out["members"]

    info = P.inspect_template_pack(tmp_path / "t2.zip")
    assert info["ok"] is True                                  # SHA256SUMS verify
    assert info["manifest"]["selections"]["voice_profiles"] == ["linxia"]
    assert "SHA256SUMS" in info["members"]


def test_template_pack_import_conflict_handling_never_silent(tmp_project, tmp_path):
    from manju.build import seriespack as P

    _rights_char(tmp_project, with_rights=True)
    P.export_template_pack(tmp_project, tmp_path / "t3.zip", bible_entries=["linxia"])

    dest = Project.create(tmp_path / "dst3", git_init=False)
    write_yaml(dest.root / "bible" / "characters.yaml",
               {"linxia": {"name": "本地林夏", "note": "pre-existing"}})

    # abort (default): conflict → refuse, local untouched
    with pytest.raises(P.SeriesPackError):
        P.import_template_pack(dest, tmp_path / "t3.zip")
    assert read_yaml(dest.root / "bible" / "characters.yaml")["linxia"]["name"] == "本地林夏"

    # skip: local kept, conflict recorded
    r = P.import_template_pack(dest, tmp_path / "t3.zip", on_conflict="skip")
    assert r["conflicts"][0]["action"] == "skipped_local_kept"
    assert read_yaml(dest.root / "bible" / "characters.yaml")["linxia"]["name"] == "本地林夏"

    # rename: imported id lands beside, provenance recorded — never overwrite
    r2 = P.import_template_pack(dest, tmp_path / "t3.zip", on_conflict="rename")
    chars = read_yaml(dest.root / "bible" / "characters.yaml")
    assert chars["linxia"]["name"] == "本地林夏"
    assert chars["linxia_imported"]["name"] == "林夏"
    assert chars["linxia_imported"]["imported_from"]["pack_digest"].startswith("sha256:")
    assert r2["conflicts"][0]["action"] == "renamed"


def test_template_pack_tamper_refused_and_no_marketplace(tmp_project, tmp_path):
    import zipfile

    from manju.build import seriespack as P

    _rights_char(tmp_project, with_rights=True)
    P.export_template_pack(tmp_project, tmp_path / "t4.zip", bible_entries=["linxia"])
    # tamper a member inside the zip
    with zipfile.ZipFile(tmp_path / "t4.zip") as zf:
        payload = {n: zf.read(n) for n in zf.namelist()}
    payload["bible/entries.yaml"] = b"linxia: {name: hacked}\n"
    with zipfile.ZipFile(tmp_path / "t4.zip", "w") as zf:
        for n, data in payload.items():
            zf.writestr(n, data)
    info = P.inspect_template_pack(tmp_path / "t4.zip")
    assert info["ok"] is False and "bible/entries.yaml" in info["checksum_mismatches"]
    dest = Project.create(tmp_path / "dst4", git_init=False)
    with pytest.raises(P.SeriesPackError):
        P.import_template_pack(dest, tmp_path / "t4.zip")

    # no marketplace/rating/social API surface: the module exposes only the
    # export/inspect/import + refpack + outline functions (contract §9 "不建
    # 评分、收藏、社交市场" — checked on the PUBLIC surface, not prose).
    public = {n for n in dir(P) if not n.startswith("_") and callable(getattr(P, n))}
    for forbidden in ("rate", "rating", "favorite", "star", "publish_to_market",
                      "share_social"):
        assert not any(forbidden in n.lower() for n in public), public
