"""AI_IDE_17 — series world state, identity variants, season health (WP1-3).
Contract §10 rows 1-5, 8-9, 11 live here; packs (6-7, 10, 12-13) in
tests/test_c17_packs.py.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from manju.core.series import Series, new_episode
from manju.core.yamlio import read_yaml, write_yaml


@pytest.fixture
def tmp_series(tmp_path: Path) -> Series:
    series = Series.create(tmp_path / "雨夜系列", git_init=False)
    for eid, title in (("E01", "初雪"), ("E02", "临界"), ("E03", "归途")):
        new_episode(series, eid, title=title)
    return series


def _bible_digest(series: Series) -> str:
    h = hashlib.sha256()
    for p in sorted(series.bible_dir.glob("*.yaml")):
        h.update(p.name.encode() + p.read_bytes())
    return h.hexdigest()


def _set_char(series: Series, entry: dict) -> None:
    write_yaml(series.bible_dir / "characters.yaml", {"linxia": entry})


# ------------------------------------------------- §10.1 basic series unchanged


def test_basic_series_behaviour_unchanged(tmp_series):
    """§10: basic series 旧行为不变 — series_status keeps its classic shape;
    nothing AI_IDE_17 added leaks into it unless asked for."""
    from manju.core.series import series_status

    info = series_status(tmp_series)
    assert set(info) == {"series", "episodes", "totals"}   # no new keys
    assert [e["id"] for e in info["episodes"]] == ["E01", "E02", "E03"]
    assert "season_health" not in info


# ---------------------------------------------------- §10.2 variant resolution


def test_variant_episode_range_resolution(tmp_series):
    from manju.core import series_state as S

    _set_char(tmp_series, {
        "name": "林夏", "appearance": "短发黑风衣",
        "variants": [
            {"variant_id": "lin_injured", "valid_from_episode": "E02",
             "valid_to_episode": "E03",
             "changes": {"injury": "左臂包扎", "wardrobe": "破损风衣"}},
        ],
    })
    order = S.episode_order(tmp_series)
    entry = read_yaml(tmp_series.bible_dir / "characters.yaml")["linxia"]

    assert S.active_variant(entry, "E01", order) is None          # before range
    v2 = S.active_variant(entry, "E02", order)
    assert v2 and v2["variant_id"] == "lin_injured"               # in range
    assert S.active_variant(entry, "E03", order) is v2 or \
           S.active_variant(entry, "E03", order)["variant_id"] == "lin_injured"

    view = S.resolve_entry_for_episode(entry, "E02", order)
    assert view["layer"] == "variant"
    assert view["effective"]["injury"] == "左臂包扎"
    assert view["canonical"].get("injury") is None                # canonical untouched
    base = S.resolve_entry_for_episode(entry, "E01", order)
    assert base["layer"] == "canonical" and "injury" not in base["effective"]


def test_variant_overlap_merges_or_conflicts_never_single_pick(tmp_series):
    """CLOSEOUT C4 ruling 1 (PIN FLIPPED): per-episode resolution is a stable
    MERGE of ALL live variants, never the forbidden 'latest valid_from wins'
    single pick (contract §5 bans that runtime behaviour). Where only one variant
    is live the merge is clean; where two set the SAME field it is a BLOCKING
    conflict, never a silent winner. (Was test_variant_latest_from_wins_
    deterministically — same fixture, assertions flipped to the merge model.)"""
    from manju.core import series_state as S

    entry = {
        "variants": [
            {"variant_id": "a_base", "valid_from_episode": "E01",
             "changes": {"hair": "长发"}},
            {"variant_id": "b_later", "valid_from_episode": "E02",
             "changes": {"hair": "短发"}},
        ],
    }
    order = S.episode_order(tmp_series)
    # E01: only a_base is live → clean single-source merge, no conflict
    r1 = S.resolve_variants(entry, "E01", order)
    assert r1["merged_changes"] == {"hair": "长发"}
    assert r1["field_sources"]["hair"] == "a_base" and r1["conflicts"] == []
    # E03: both live on the SAME field → blocking conflict, never a silent winner
    r3 = S.resolve_variants(entry, "E03", order)
    assert r3["merged_changes"] == {}
    assert any(c["field"] == "hair" for c in r3["conflicts"])
    assert set(r3["active_variant_ids"]) == {"a_base", "b_later"}


# ----------------------------------------------- §10.3 overlap / contradiction


def test_variant_overlap_and_contradiction_diagnostics(tmp_series):
    from manju.core import series_state as S

    _set_char(tmp_series, {
        "name": "林夏",
        "variants": [
            {"variant_id": "v_a", "valid_from_episode": "E01",
             "valid_to_episode": "E02", "changes": {"hair": "长发"}},
            {"variant_id": "v_b", "valid_from_episode": "E02",
             "valid_to_episode": "E03", "changes": {"hair": "短发"}},
            {"variant_id": "v_c", "valid_from_episode": "E02",
             "valid_to_episode": "E02", "changes": {"wardrobe": "校服"}},
        ],
    })
    diags = S.variant_diagnostics(tmp_series)
    codes = {d["code"] for d in diags}
    assert "VARIANT_CONTRADICTION" in codes      # v_a vs v_b both set hair on E02
    assert "VARIANT_OVERLAP" in codes            # v_c overlaps but disjoint field
    contra = next(d for d in diags if d["code"] == "VARIANT_CONTRADICTION")
    assert contra["fields"] == ["hair"]


def test_variant_range_and_reference_diagnostics(tmp_series):
    from manju.core import series_state as S

    _set_char(tmp_series, {
        "variants": [
            {"variant_id": "bad_range", "valid_from_episode": "E03",
             "valid_to_episode": "E01", "changes": {}},
            {"variant_id": "bad_ep", "valid_from_episode": "E99", "changes": {}},
            {"variant_id": "bad_voice", "valid_from_episode": "E01",
             "voice_profile_ref": "no_such_voice", "changes": {}},
            {"valid_from_episode": "E01"},           # missing id
        ],
    })
    codes = [d["code"] for d in S.variant_diagnostics(tmp_series)]
    assert "RANGE_INVALID" in codes
    assert "UNKNOWN_EPISODE" in codes
    assert "UNKNOWN_VOICE_PROFILE_REF" in codes
    assert "VARIANT_ID_MISSING" in codes


# ------------------------------------- §10.4 canonical vs transient separation


def test_canonical_and_transient_never_confused(tmp_series):
    """resolve_entry_for_episode is derived-only (canonical file untouched) and
    carries NO transient observation; the continuity packet is labelled
    transient and never appears in a bible."""
    from manju.core import series_state as S

    _set_char(tmp_series, {"name": "林夏", "appearance": "短发",
                           "variants": [{"variant_id": "v", "valid_from_episode": "E01",
                                         "changes": {"appearance": "长发"}}]})
    before = _bible_digest(tmp_series)
    order = S.episode_order(tmp_series)
    entry = read_yaml(tmp_series.bible_dir / "characters.yaml")["linxia"]
    view = S.resolve_entry_for_episode(entry, "E01", order)
    packet = S.continuity_packet(tmp_series, "E01")

    assert view["layer"] in ("canonical", "variant")
    assert "observed_endpoint" not in view                 # no transient here
    assert packet["layer"] == "transient" and packet["advisory"] is True
    assert _bible_digest(tmp_series) == before             # nothing written


# ------------------------------------ §10.5 accepted observation never writes


def test_accepted_observation_never_writes_global_bible(tmp_series):
    """PIN: deriving the continuity packet (accepted observed endings + world
    state) mutates no series bible file and no episode bible file."""
    from manju.core import series_state as S

    write_yaml(tmp_series.root / "world_state.yaml", {"entries": [
        {"id": "ws1", "subject": "linxia", "change": {"knows_secret": True},
         "effective": {"episode": "E01"}, "known": True},
    ]})
    _set_char(tmp_series, {"name": "林夏"})
    before_series = _bible_digest(tmp_series)
    ep = tmp_series.open_episode("E01")
    ep_bible_before = {p.name: p.read_bytes() for p in (ep.root / "bible").glob("*.yaml")}

    packet = S.continuity_packet(tmp_series, "E01")
    assert packet["schema"] == "manju.continuity-packet/v1"
    assert packet["world_state"]["state"].get("linxia", {}).get("knows_secret")

    assert _bible_digest(tmp_series) == before_series
    ep_bible_after = {p.name: p.read_bytes() for p in (ep.root / "bible").glob("*.yaml")}
    assert ep_bible_after == ep_bible_before


# ----------------------------------------------------------- world-state checks


def test_world_state_validation_and_derived_view(tmp_series):
    from manju.core import series_state as S

    _set_char(tmp_series, {"name": "林夏"})
    write_yaml(tmp_series.root / "world_state.yaml", {"entries": [
        {"id": "w1", "subject": "linxia", "change": {"loc": "北城"},
         "effective": {"episode": "E01"}},
        {"id": "w1", "subject": "linxia", "change": {"loc": "南城"},
         "effective": {"episode": "E01"}},                    # duplicate id
        {"id": "w2", "subject": "ghost", "change": {"x": 1},
         "effective": {"episode": "E02"}},                    # unknown subject
        {"id": "w3", "subject": "linxia", "change": {"loc": "东城"},
         "effective": {"episode": "E99"}},                    # unknown episode
        {"id": "w4", "subject": "linxia", "change": {"loc": "西城"},
         "effective": {"episode": "E01"}},                    # conflict with w1
    ]})
    codes = [d["code"] for d in S.world_state_diagnostics(tmp_series)]
    assert "ENTRY_ID_DUPLICATE" in codes
    assert "UNKNOWN_SUBJECT" in codes
    assert "UNKNOWN_EPISODE" in codes
    assert "STATE_CONFLICT" in codes

    view = S.world_state_at(tmp_series, "E01")
    # entries apply in (episode order, file order): the LAST declared patch for
    # a field wins the derived view — the conflict itself is the checker's diag.
    assert view["state"]["linxia"]["loc"]["value"] == "西城"
    assert view["applied_entries"][0] == "w1"                 # order preserved
    assert S.world_state_at(tmp_series, "E99")["error"]


# --------------------------------------------------- §10.8/10.9 season health


def test_season_health_aggregates_current_evidence(tmp_series):
    from manju.core.series_state import season_health

    health = season_health(tmp_series)
    assert health["schema"] == "manju.season-health/v1"
    assert health["episode_count"] == 3
    for row in health["episodes"]:
        # each evidence block present (verbatim owners), never re-derived
        assert "release" in row and "ready" in row["release"]
        assert "budget" in row
        assert "locales" in row
        assert "refs_missing" in row
    assert isinstance(health["variant_diagnostics"], list)
    assert isinstance(health["world_state_diagnostics"], list)


def test_stale_episode_never_masked_by_season_ready(tmp_series):
    """PIN: an unreadable/blocked episode always forces season ready=False and
    is NAMED in not_ready."""
    from manju.core.series_state import season_health

    # break E02: remove its project.yaml so it cannot even open
    (tmp_series.episode_project_dir("E02") / "project.yaml").unlink()
    health = season_health(tmp_series)
    assert health["ready"] is False
    reasons = {b["id"]: b["reason"] for b in health["not_ready"]}
    assert "E02" in reasons and "unreadable" in reasons["E02"]
    # fresh episodes with no final are honestly not release-ready either
    assert any(b["id"] == "E01" for b in health["not_ready"])


# --------------------------------------------------- §10.11 SQLite explainable


def test_deleted_sqlite_is_explainable_not_fabricated(tmp_series):
    """§10: 删除 SQLite 可解释 — an absent runtime DB reads as None + a note
    (rebuildable projection), never fabricated as zero."""
    from manju.core.series_state import _episode_health

    ep = tmp_series.open_episode("E01")
    db = ep.root / ".manju" / "state.sqlite"
    assert not db.exists()
    row = _episode_health(ep)
    assert row["unresolved_submissions"] is None
    assert "可重建" in row["unresolved_note"]


def test_season_health_folds_into_series_status_cli(tmp_series, monkeypatch):
    """Ruling 4: extend `manju series status --json` additively — the classic
    keys stay; --health adds the season_health section."""
    import json

    from typer.testing import CliRunner

    from manju.cli import app

    monkeypatch.chdir(tmp_series.root)
    r = CliRunner().invoke(app, ["series", "status", "--json", "--health"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert {"series", "episodes", "totals"} <= set(data)
    assert data["season_health"]["schema"] == "manju.season-health/v1"

    r2 = CliRunner().invoke(app, ["series", "status", "--json"])
    assert "season_health" not in json.loads(r2.stdout)      # opt-in only
