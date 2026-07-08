"""Tests for build/ingest.py — 批量入库 batch ingest (round X, workflow
smoothness goal #1: "a batch of assets processed externally and imported
back into Manju feels cumbersome").

Coverage:
  * classification matrix — every filename convention (take/voice/shot_ref/
    bible_ref), ambiguity, and the unknown/unmatched fallback to a plain
    import (with a 中文 reason);
  * dedup rows — against existing project content AND within one batch;
  * plan_ingest is read-only (a dry run touches nothing on disk);
  * apply_ingest lands through the REAL registration paths — append-only
    proven (an existing take is untouched, a new one lands as the next
    numbered take), forced --shot/--role, per-row overrides, partial-failure
    stop with a structured failure record;
  * the CLI (`manju ingest`) — dry-run table, --json, --apply, --shot,
    --role, clean failures;
  * the GUI (/ingest, /api/ingest/upload|plan|apply) — a real HTTP round
    trip through the jobs runner, plus the token/readonly guards every other
    mutating surface in this GUI enforces.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from typer.testing import CliRunner

from manju.build.ingest import IngestError, apply_ingest, plan_ingest
from manju.cli import app
from manju.core.events import tail_events
from manju.core.yamlio import read_yaml, write_yaml
from manju.gui.server import create_server

runner = CliRunner()


def _drop(dirpath, name, content=b"x"):
    p = dirpath / name
    p.write_bytes(content)
    return p


def _snapshot(root):
    """(relpath, size) for every file under root — used to prove a dry run
    touches nothing."""
    return sorted(
        (str(p.relative_to(root)), p.stat().st_size)
        for p in root.rglob("*") if p.is_file()
    )


# ======================================================================
# classification matrix
# ======================================================================


def test_take_bare_filename(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    plan = plan_ingest(tmp_project, [batch])
    assert len(plan.rows) == 1
    row = plan.rows[0]
    assert row.action == "take"
    assert row.shot_id == "S001"
    assert row.hash.startswith("sha256:")


def test_take_explicit_suffix(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_take.mp4", b"v1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "take" and row.shot_id == "S001"


def test_take_arbitrary_trailing_token_still_matches_leading_id(tmp_project, add_shot, tmp_path):
    """`S001*.mov` in the spec — any trailing token after the id, as long as
    the LEADING token (before the first underscore) is a real shot id."""
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_v2_final.mov", b"v1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "take" and row.shot_id == "S001"


def test_voice_bare_filename(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.wav", b"a1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "voice" and row.shot_id == "S001"


def test_voice_explicit_suffix(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_voice.mp3", b"a1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "voice" and row.shot_id == "S001"


def test_shot_ref_suffix(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_ref.png", b"i1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "shot_ref" and row.shot_id == "S001"


def test_shot_ref_numbered_suffix(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_ref2.png", b"i1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "shot_ref" and row.shot_id == "S001"


def test_bible_ref_character(tmp_project, tmp_path):
    # tmp_project's bible already has the "linxia" character (conftest).
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "linxia_ref.png", b"i1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "bible_ref"
    assert row.asset_id == "linxia"
    assert row.asset_kind == "characters"


def test_bible_ref_scene(tmp_project, tmp_path):
    # tmp_project's bible already has the "convenience_store" scene.
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "convenience_store_ref.jpg", b"i1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "bible_ref"
    assert row.asset_id == "convenience_store"
    assert row.asset_kind == "scenes"


def test_unknown_id_falls_back_to_import_with_reason(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "nobody_ref.png", b"i1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "import"
    assert row.target == "media/imports"
    assert "nobody" in row.reason or "未找到" in row.reason


def test_unknown_extension_falls_back_to_import(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "notes.txt", b"hello")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "import"


def test_video_with_unmatched_id_falls_back_to_import(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "randomfootage.mp4", b"v1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "import"
    assert "randomfootage" in row.reason


def test_ambiguous_different_ids_falls_back_to_import(tmp_project, add_shot, tmp_path):
    """A filename whose candidates match a DIFFERENT shot id and a
    DIFFERENT bible id at once is genuinely ambiguous — refuse to guess."""
    add_shot(tmp_project, "foo")
    write_yaml(
        tmp_project.root / "bible" / "characters.yaml",
        {**(read_yaml(tmp_project.root / "bible" / "characters.yaml") or {}),
         "foo_bar": {"name": "道具"}},
    )
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "foo_bar_ref.png", b"i1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "import"
    assert "foo" in row.reason and "foo_bar" in row.reason


def test_same_id_matches_shot_and_bible_prefers_shot(tmp_project, add_shot, tmp_path):
    """When the SAME token is both a shot id and a bible id, the shot ref
    wins (deterministic tie-break), noted in the reason."""
    add_shot(tmp_project, "linxia")  # reuse the bible character id as a shot id too
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "linxia_ref.png", b"i1")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "shot_ref" and row.shot_id == "linxia"


def test_role_forced_take_rejects_non_video_extension(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.png", b"i1")
    row = plan_ingest(tmp_project, [batch], role="take").rows[0]
    assert row.action == "import"


def test_role_forced_ref_treats_image_as_ref_even_without_ref_suffix(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    # role=ref still needs the classifier's id-matching; a bare "S001.png"
    # (no _ref suffix) is tried as a whole-stem / bible candidate too.
    _drop(batch, "S001.png", b"i1")
    row = plan_ingest(tmp_project, [batch], role="ref").rows[0]
    assert row.action == "shot_ref" and row.shot_id == "S001"


def test_shot_forced_overrides_filename_entirely(tmp_project, add_shot, tmp_path):
    """The `--shot` "I regenerated three candidates externally" case: every
    file lands on the SAME shot regardless of its own name."""
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "candidate_a.mp4", b"v1")
    _drop(batch, "totally_unrelated_name.mov", b"v2")
    plan = plan_ingest(tmp_project, [batch], shot="S001")
    assert all(r.action == "take" and r.shot_id == "S001" for r in plan.rows)


def test_shot_forced_but_unknown_shot_raises_ingest_error(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "clip.mp4", b"v1")
    with pytest.raises(IngestError):
        plan_ingest(tmp_project, [batch], shot="S999")


def test_unsafe_role_raises(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    with pytest.raises(IngestError):
        plan_ingest(tmp_project, [batch], role="bogus")


def test_unsafe_shot_id_raises(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    with pytest.raises(IngestError):
        plan_ingest(tmp_project, [batch], shot="../evil")


def test_missing_path_raises_cleanly(tmp_project, tmp_path):
    with pytest.raises(IngestError):
        plan_ingest(tmp_project, [tmp_path / "does_not_exist"])


# ======================================================================
# dedup
# ======================================================================


def test_dedup_against_existing_import(tmp_project, tmp_path):
    tmp_project.imports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.imports_dir / "already.mp4").write_bytes(b"same-bytes")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "incoming.mp4", b"same-bytes")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "skip_duplicate"
    assert row.target == "media/imports/already.mp4"


def test_dedup_against_existing_take(tmp_project, add_shot, make_take, tmp_path):
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:whatever")
    content = take.media_path.read_bytes()
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_take.mp4", content)
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "skip_duplicate"
    assert "take_01" in row.target


def test_dedup_within_batch(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "a_S001.mp4", b"same")  # unmatched name -> import, claims "same"
    _drop(batch, "b_S001.mp4", b"same")  # identical bytes -> dup of the first
    plan = plan_ingest(tmp_project, [batch])
    actions = {r.name: r.action for r in plan.rows}
    assert actions["a_S001.mp4"] == "import"
    assert actions["b_S001.mp4"] == "skip_duplicate"


# ======================================================================
# LIBRARY dedup (round X agent XF) — --on-duplicate skip|import|link
# ======================================================================


@pytest.fixture
def lib_env(tmp_path, monkeypatch):
    """Isolate the private library at a tmp dir for these tests."""
    monkeypatch.setenv("MANJU_LIBRARY", str(tmp_path / "_library"))
    from manju.core.library import Library

    return Library()


def test_library_hit_default_skips_and_hints(tmp_project, tmp_path, lib_env):
    seed = tmp_path / "seed.mp4"
    seed.write_bytes(b"library-bytes")
    lib_env.add(seed, tags=["broll", "night"])

    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "unmatched.mp4", b"library-bytes")  # same content as the library asset
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "skip_duplicate"
    assert "素材库" in row.target
    assert row.library_hint and "broll" in row.library_hint and "night" in row.library_hint


def test_library_hit_import_still_classifies_and_attaches_hint(tmp_project, add_shot, tmp_path, lib_env):
    add_shot(tmp_project, "S001")
    seed = tmp_path / "seed.mp4"
    seed.write_bytes(b"library-bytes-2")
    lib_env.add(seed, tags=["hero"])

    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_take.mp4", b"library-bytes-2")
    row = plan_ingest(tmp_project, [batch], on_duplicate="import").rows[0]
    assert row.action == "take" and row.shot_id == "S001"       # classified normally
    assert row.library_hint and "hero" in row.library_hint       # advisory still attached


def test_library_hit_link_sources_copy_from_library_blob(tmp_project, tmp_path, lib_env):
    seed = tmp_path / "seed.mp4"
    seed.write_bytes(b"library-bytes-3")
    added = lib_env.add(seed, tags=["logo"])
    blob = lib_env.blob_path(added["entry"])

    batch = tmp_path / "batch"
    batch.mkdir()
    dropped = _drop(batch, "unmatched2.mp4", b"library-bytes-3")
    row = plan_ingest(tmp_project, [batch], on_duplicate="link").rows[0]
    assert row.action == "import"
    assert row.file == str(blob) and row.file != str(dropped)
    assert row.library_hint and "provenance: library" in row.library_hint


def test_library_hit_link_apply_lands_correct_bytes(tmp_project, tmp_path, lib_env):
    """apply_ingest, not just plan_ingest: link mode's row.file swap must
    actually land the library's bytes in the project on --apply."""
    seed = tmp_path / "seed4.mp4"
    seed.write_bytes(b"library-bytes-4")
    lib_env.add(seed, tags=["hero"])

    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "unmatched4.mp4", b"library-bytes-4")
    plan = plan_ingest(tmp_project, [batch], on_duplicate="link")
    result = apply_ingest(tmp_project, plan, actor="test")
    assert result.stopped_at is None and result.results[0].ok
    landed_rel = result.results[0].detail["imported"]
    landed = tmp_project.resolve(landed_rel)
    assert landed.exists() and landed.read_bytes() == b"library-bytes-4"


def test_no_library_hit_leaves_hint_none(tmp_project, tmp_path, lib_env):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "fresh.mp4", b"never-seen-bytes")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "import"
    assert row.library_hint is None


def test_project_dedup_wins_over_library_hit(tmp_project, tmp_path, lib_env):
    """A project-internal hit is always a plain skip — the library hint is
    only computed for files NOT already found inside the project."""
    tmp_project.imports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.imports_dir / "already.mp4").write_bytes(b"shared-bytes")
    seed = tmp_path / "seed.mp4"
    seed.write_bytes(b"shared-bytes")
    lib_env.add(seed, tags=["also-in-library"])

    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "incoming.mp4", b"shared-bytes")
    row = plan_ingest(tmp_project, [batch]).rows[0]
    assert row.action == "skip_duplicate"
    assert row.target == "media/imports/already.mp4"   # project hit, not library
    assert row.library_hint is None


def test_bad_on_duplicate_raises(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "x.mp4", b"x")
    with pytest.raises(IngestError, match="on-duplicate"):
        plan_ingest(tmp_project, [batch], on_duplicate="bogus")


def test_cli_ingest_on_duplicate_flag_skips_by_default(in_project, tmp_path, lib_env):
    seed = tmp_path / "seed.mp4"
    seed.write_bytes(b"cli-library-bytes")
    lib_env.add(seed, tags=["cli-tag"])
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "cli_drop.mp4", b"cli-library-bytes")

    result = runner.invoke(app, ["ingest", str(batch), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["rows"][0]["action"] == "skip_duplicate"
    assert "cli-tag" in data["rows"][0]["library_hint"]


# ======================================================================
# plan_ingest is read-only
# ======================================================================


def test_plan_ingest_touches_nothing_on_disk(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    _drop(batch, "linxia_ref.png", b"i1")
    _drop(batch, "unknown.bin", b"x")

    before_project = _snapshot(tmp_project.root)
    before_events = tail_events(tmp_project.root, 1000)

    plan_ingest(tmp_project, [batch])

    assert _snapshot(tmp_project.root) == before_project
    assert tail_events(tmp_project.root, 1000) == before_events


# ======================================================================
# apply_ingest
# ======================================================================


def test_apply_take_is_append_only(tmp_project, add_shot, make_take, tmp_path):
    add_shot(tmp_project, "S001")
    existing = make_take(tmp_project, "S001", "sha256:whatever")
    existing_bytes = existing.media_path.read_bytes()

    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_take.mp4", b"brand-new-bytes")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="test")

    assert result.stopped_at is None
    assert result.results[0].ok
    assert result.results[0].detail["take"] == "take_02"
    # the pre-existing take is untouched — byte-identical, still there
    assert existing.media_path.exists()
    assert existing.media_path.read_bytes() == existing_bytes
    new_take = tmp_project.get_take("S001", "take_02")
    assert new_take is not None and new_take.media_path.read_bytes() == b"brand-new-bytes"
    assert new_take.sidecar.provider == "manual_import"
    assert new_take.sidecar.spec_hash == "manual"


def test_apply_voice_lands_manual_no_sidecar(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.wav", b"audio-bytes")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="test")

    assert result.stopped_at is None
    voices = tmp_project.voice_takes("S001")
    assert len(voices) == 1
    media, sidecar = voices[0]
    assert media.read_bytes() == b"audio-bytes"
    assert sidecar is None  # hand-dropped convention: no sidecar => MANUAL (§4.3)


def test_apply_shot_ref_collision_safe_copy(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_ref.png", b"img-a")
    _drop(batch, "S001_ref2.png", b"img-b")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="test")

    assert result.stopped_at is None
    refs = sorted(p.name for p in tmp_project.refs_dir.iterdir())
    assert len(refs) == 2
    # neither ref touched the shot's own spec — shot_ref is copy + hint only
    raw = tmp_project.load_shot_raw("S001")
    assert "generation" not in raw or "refs" not in (raw.get("generation") or {}).get("params", {})


def test_apply_bible_ref_sets_ref_image_and_never_overwrites(tmp_project, tmp_path):
    batch1 = tmp_path / "batch1"
    batch1.mkdir()
    _drop(batch1, "linxia_ref.png", b"img-a")
    result1 = apply_ingest(tmp_project, plan_ingest(tmp_project, [batch1]), actor="test")
    assert result1.stopped_at is None
    chars = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    assert chars["linxia"]["ref_image"] == "media/refs/linxia_ref.png"

    # a SECOND, different ref for the same asset appends rather than clobbers
    batch2 = tmp_path / "batch2"
    batch2.mkdir()
    _drop(batch2, "linxia_ref2.png", b"img-b")
    result2 = apply_ingest(tmp_project, plan_ingest(tmp_project, [batch2]), actor="test")
    assert result2.stopped_at is None
    chars = read_yaml(tmp_project.root / "bible" / "characters.yaml")
    assert chars["linxia"]["ref_image"] == [
        "media/refs/linxia_ref.png", "media/refs/linxia_ref_2.png",
    ]
    # name/appearance/voice fields from conftest's fixture are untouched
    assert chars["linxia"]["name"] == "林夏"


def test_apply_import_fallback_makes_a_plain_import(tmp_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "mystery.mp4", b"v1")
    plan = plan_ingest(tmp_project, [batch])
    result = apply_ingest(tmp_project, plan, actor="test")
    assert result.stopped_at is None
    assert (tmp_project.imports_dir / "mystery.mp4").read_bytes() == b"v1"


def test_apply_skip_duplicate_is_a_pure_noop(tmp_project, tmp_path):
    """A skip_duplicate row moves no media/truth file — it only ever adds the
    audit-trail events (§10) every apply row gets, skip or not (round AA:
    the persisted reports/ingest_batches/<id>.yaml record is part of that
    same audit trail now, so it's excluded from the diff exactly like
    events.jsonl already was)."""
    tmp_project.imports_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.imports_dir / "existing.mp4").write_bytes(b"same-bytes")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "incoming.mp4", b"same-bytes")
    plan = plan_ingest(tmp_project, [batch])
    before = _snapshot(tmp_project.root)
    result = apply_ingest(tmp_project, plan, actor="test")
    assert result.results[0].ok and result.results[0].detail == {"skipped": True}

    def _is_audit_trail(relpath: str) -> bool:
        return relpath == "events.jsonl" or relpath.startswith("reports/ingest_batches/")

    after = {p: sz for p, sz in _snapshot(tmp_project.root) if not _is_audit_trail(p)}
    before = {p: sz for p, sz in before if not _is_audit_trail(p)}
    assert after == before


def test_apply_overrides_change_action_and_target(tmp_project, add_shot, tmp_path):
    """The GUI per-row dropdown case: an auto-classified `import` row is
    redirected to a real shot's take via an override, with no filename
    convention backing it at all."""
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "totally_unnamed.mp4", b"v1")
    plan = plan_ingest(tmp_project, [batch])
    assert plan.rows[0].action == "import"  # auto-classification, unmatched

    result = apply_ingest(
        tmp_project, plan, actor="test",
        overrides={0: {"action": "take", "shot_id": "S001"}},
    )
    assert result.stopped_at is None
    assert result.results[0].detail["shot"] == "S001"
    assert tmp_project.get_take("S001", "take_01") is not None


def test_apply_partial_failure_stops_and_reports(tmp_project, add_shot, tmp_path):
    """Three files that would ALL auto-classify as S001 takes (alphabetical
    order a/b/c controls apply order); the middle one's target is overridden
    to a nonexistent shot (a reviewer typo). Row 0 must land for real, the
    typo'd row must fail with a structured error, and row 2 — which on its
    own would succeed just fine — must never even be attempted."""
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001_a.mp4", b"v1")
    _drop(batch, "S001_b_broken.mp4", b"v2")
    _drop(batch, "S001_c.mp4", b"v3")
    plan = plan_ingest(tmp_project, [batch])
    assert [r.action for r in plan.rows] == ["take", "take", "take"]

    result = apply_ingest(
        tmp_project, plan, actor="test",
        overrides={1: {"shot_id": "S999"}},  # reviewer typo'd the target shot
    )

    assert result.stopped_at == 1
    assert len(result.results) == 2  # row 2 was never attempted at all
    assert result.results[0].ok is True
    assert result.results[0].detail["take"] == "take_01"
    assert result.results[1].ok is False
    assert "S999" in result.results[1].error

    # exactly what landed: row 0's take is there, nothing else registered
    assert tmp_project.get_take("S001", "take_01") is not None
    assert len(tmp_project.takes("S001")) == 1


def test_apply_events_summary_and_per_row(tmp_project, add_shot, tmp_path):
    add_shot(tmp_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    plan = plan_ingest(tmp_project, [batch])
    apply_ingest(tmp_project, plan, actor="tester")

    events = tail_events(tmp_project.root, 100)
    actions = [e["action"] for e in events]
    assert "ingest_row" in actions
    assert "ingest" in actions
    summary = [e for e in events if e["action"] == "ingest"][-1]
    assert summary["detail"]["rows"] == 1
    assert summary["detail"]["landed"] == 1
    assert summary["actor"] == "tester"


# ======================================================================
# CLI
# ======================================================================


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


def test_cli_dry_run_prints_table_and_touches_nothing(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    before = _snapshot(in_project.root)

    result = runner.invoke(app, ["ingest", str(batch)])
    assert result.exit_code == 0, result.output
    assert "S001" in result.output
    assert "dry-run" in result.output
    assert _snapshot(in_project.root) == before


def test_cli_json_dry_run_shape(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    result = runner.invoke(app, ["ingest", str(batch), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["rows"][0]["action"] == "take"
    assert data["rows"][0]["shot_id"] == "S001"


def test_cli_apply_flag_executes_and_reports(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    result = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["stopped_at"] is None
    assert in_project.get_take("S001", "take_01") is not None


def test_cli_shot_flag_forces_target(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "candidate1.mp4", b"v1")
    _drop(batch, "candidate2.mp4", b"v2")
    result = runner.invoke(app, ["ingest", str(batch), "--shot", "S001", "--apply", "--json"])
    assert result.exit_code == 0, result.output
    assert len(in_project.takes("S001")) == 2


def test_cli_role_flag_forces_classification(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.png", b"i1")  # bare image, no _ref suffix
    result = runner.invoke(app, ["ingest", str(batch), "--role", "ref", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["rows"][0]["action"] == "shot_ref"


def test_cli_bad_shot_fails_cleanly(in_project, tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "clip.mp4", b"v1")
    result = runner.invoke(app, ["ingest", str(batch), "--shot", "S999"])
    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_cli_missing_path_fails_cleanly(in_project, tmp_path):
    result = runner.invoke(app, ["ingest", str(tmp_path / "nope")])
    assert result.exit_code != 0


def test_cli_apply_is_idempotent_via_dedup_on_rerun(in_project, add_shot, tmp_path):
    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")
    result = runner.invoke(app, ["ingest", str(batch), "--apply"])
    assert result.exit_code == 0, result.output
    # re-running against the SAME batch now dedups (no failure) — confirms
    # the append-only path is safe to re-run, not a second copy.
    result2 = runner.invoke(app, ["ingest", str(batch), "--apply", "--json"])
    assert result2.exit_code == 0
    assert json.loads(result2.output)["rows"][0]["action"] == "skip_duplicate"


def test_cli_partial_failure_reports_nonzero_exit(in_project, add_shot, tmp_path, monkeypatch):
    """CLI-level unit test of the failure-reporting path: when apply_ingest
    reports a stopped_at row, `manju ingest --apply` exits nonzero and names
    the failing row/error — exercised via a monkeypatched apply_ingest so the
    test does not depend on an OS-level way to force a real registration
    failure (the engine-level partial-failure semantics are covered directly
    in test_apply_partial_failure_stops_and_reports above)."""
    import manju.build.ingest as ingest_mod
    from manju.build.ingest import IngestApplyResult, IngestRowResult

    add_shot(in_project, "S001")
    batch = tmp_path / "batch"
    batch.mkdir()
    _drop(batch, "S001.mp4", b"v1")

    def fake_apply(project, plan, *, actor, overrides=None, **_kwargs):
        # **_kwargs swallows round-AA's new batch_id/clock/source — this
        # fake only needs to prove the CLI's stopped_at handling, not
        # exercise batch persistence.
        row = plan.rows[0]
        return IngestApplyResult(
            results=[IngestRowResult(row=row, ok=False, error="模拟失败 simulated failure")],
            stopped_at=0,
        )

    monkeypatch.setattr(ingest_mod, "apply_ingest", fake_apply)
    result = runner.invoke(app, ["ingest", str(batch), "--apply"])
    assert result.exit_code != 0
    assert "模拟失败" in result.output
    assert "第 1" in result.output


# ======================================================================
# GUI
# ======================================================================


@pytest.fixture
def gui(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


@pytest.fixture
def gui_readonly(tmp_project):
    server = create_server(tmp_project, host="127.0.0.1", port=0, actor="human", readonly=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.close()


def _req(server, path, *, method="GET", body=None, headers=None, raw=False):
    url = f"http://127.0.0.1:{server.port}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else json.loads(payload or b"{}"))
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            parsed = payload if raw else json.loads(payload or b"{}")
        except json.JSONDecodeError:
            parsed = payload
        return exc.code, parsed


def _post(server, path, body, token="__use__"):
    tok = server.token if token == "__use__" else token
    headers = {"X-Manju-Token": tok} if tok is not None else {}
    return _req(server, path, method="POST", body=body, headers=headers)


def _raw_upload(server, batch, name, content, headers):
    url = f"http://127.0.0.1:{server.port}/api/ingest/upload?batch={batch}&name={name}"
    req = urllib.request.Request(url, data=content, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _poll_job(server, job_id, tries=200):
    for _ in range(tries):
        status, data = _req(server, "/api/jobs")
        job = next((j for j in data["jobs"] if j["id"] == job_id), None)
        if job and job["state"] in ("done", "failed"):
            return job
        import time

        time.sleep(0.02)
    raise TimeoutError("job did not finish")


def test_gui_page_serves_with_csp(gui):
    status, body = _req(gui, "/ingest", raw=True)
    assert status == 200
    assert b"<title>" in body
    status, _ = _req(gui, "/ingest.css", raw=True)
    assert status == 200
    status, _ = _req(gui, "/ingest.js", raw=True)
    assert status == 200


def test_gui_upload_plan_apply_roundtrip(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    status, data = _raw_upload(gui, "b1", "S001.mp4", b"vid-bytes",
                                {"X-Manju-Token": gui.token})
    assert status == 200 and data["ok"] is True

    status, plan = _post(gui, "/api/ingest/plan", {"batch": "b1"})
    assert status == 200
    assert plan["rows"][0]["action"] == "take" and plan["rows"][0]["shot_id"] == "S001"

    status, applied = _post(gui, "/api/ingest/apply", {"batch": "b1"})
    assert status == 202
    job = _poll_job(gui, applied["job"]["id"])
    assert job["state"] == "done"
    assert job["result"]["stopped_at"] is None
    assert tmp_project.get_take("S001", "take_01") is not None
    # staging dir is cleaned up after apply
    assert not (tmp_project.runtime_dir / "ingest-tmp" / "b1").exists()


def test_gui_apply_respects_overrides(gui, tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    _raw_upload(gui, "b2", "unnamed.mp4", b"vid-bytes", {"X-Manju-Token": gui.token})
    status, plan = _post(gui, "/api/ingest/plan", {"batch": "b2"})
    assert plan["rows"][0]["action"] == "import"

    status, applied = _post(gui, "/api/ingest/apply", {
        "batch": "b2", "overrides": {"0": {"action": "take", "id": "S001"}},
    })
    assert status == 202
    job = _poll_job(gui, applied["job"]["id"])
    assert job["result"]["results"][0]["detail"]["shot"] == "S001"


def test_gui_upload_requires_token(gui):
    status, data = _raw_upload(gui, "b3", "clip.mp4", b"x", {})
    assert status == 403


def test_gui_plan_requires_token(gui):
    status, data = _req(gui, "/api/ingest/plan", method="POST",
                        body={"batch": "b3"}, headers={})
    assert status == 403


def test_gui_readonly_blocks_upload_and_apply(gui_readonly):
    status, _ = _raw_upload(gui_readonly, "b4", "clip.mp4", b"x",
                            {"X-Manju-Token": gui_readonly.token})
    assert status == 403
    status, _ = _post(gui_readonly, "/api/ingest/apply", {"batch": "b4"})
    assert status == 403


def test_gui_plan_rejects_bad_batch_id(gui):
    status, data = _post(gui, "/api/ingest/plan", {"batch": "../evil"})
    assert status == 400


def test_gui_plan_empty_batch_is_a_clean_400(gui):
    status, data = _post(gui, "/api/ingest/plan", {"batch": "nosuchbatch"})
    assert status == 400
