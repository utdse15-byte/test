"""AI_IDE_14_21_CLOSEOUT Track C — C4 Series State red tests (S01–S06).

Contract §5 + CLOSEOUT_C_ADDENDUM C4 rulings 1–3:

- variant resolution is a stable MERGE of every live variant (disjoint fields
  coexist, same field+value dedups, same field+different value is a BLOCKING
  conflict — never a silent single pick); diagnostics and runtime agree;
- world state carries episode/scene/shot scope with precedence shot>scene>
  episode; narrow-scope state never leaks episode-wide; scene changes in
  different scenes never conflict;
- season health's unresolved-submission verdict is tri-state; an UNAVAILABLE
  runtime projection (missing/unreadable) forces season ready=false.

Red-first: every test below fails on the pre-CLOSEOUT tree (single-pick
resolution, episode-only world state, None+note season health).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from manju.core import series_state as S
from manju.core.series import Series, new_episode
from manju.core.yamlio import read_yaml, write_yaml


@pytest.fixture
def tmp_series(tmp_path: Path) -> Series:
    series = Series.create(tmp_path / "闭环系列", git_init=False)
    for eid, title in (("E01", "初雪"), ("E02", "临界"), ("E03", "归途")):
        new_episode(series, eid, title=title)
    return series


def _set_char(series: Series, entry: dict[str, Any]) -> None:
    write_yaml(series.bible_dir / "characters.yaml", {"linxia": entry})


def _entry(series: Series) -> dict[str, Any]:
    return read_yaml(series.bible_dir / "characters.yaml")["linxia"]


# ============================================================ S01 disjoint merge


def test_s01_wardrobe_and_voice_overlapping_variants_both_active(tmp_series):
    """S01: two live variants touching DISJOINT fields (wardrobe + voice) at the
    same episode BOTH contribute to the resolved view — the runtime merges them
    instead of picking one (the model contract §5 mandates)."""
    _set_char(tmp_series, {
        "name": "林夏", "appearance": "短发黑风衣",
        "variants": [
            {"variant_id": "v_wardrobe", "valid_from_episode": "E02",
             "valid_to_episode": "E03", "changes": {"wardrobe": "破损风衣"}},
            {"variant_id": "v_voice", "valid_from_episode": "E02",
             "valid_to_episode": "E03", "changes": {"voice": "沙哑低语"}},
        ],
    })
    order = S.episode_order(tmp_series)
    view = S.resolve_entry_for_episode(_entry(tmp_series), "E02", order)

    assert set(view["active_variant_ids"]) == {"v_wardrobe", "v_voice"}
    assert view["effective"]["wardrobe"] == "破损风衣"     # both fields present
    assert view["effective"]["voice"] == "沙哑低语"
    assert view["conflicts"] == []                          # disjoint => no conflict
    assert view["field_sources"]["wardrobe"] == "v_wardrobe"
    assert view["field_sources"]["voice"] == "v_voice"
    assert view["merged_changes"] == {"wardrobe": "破损风衣", "voice": "沙哑低语"}


# ==================================================== S02 same-field contradiction


def test_s02_same_field_contradiction_blocks(tmp_series):
    """S02: two live variants setting the SAME field to DIFFERENT values are a
    BLOCKING conflict — the resolution never silently picks a winner, and the
    checker's VARIANT_CONTRADICTION agrees with the runtime conflict."""
    _set_char(tmp_series, {
        "name": "林夏",
        "variants": [
            {"variant_id": "v_long", "valid_from_episode": "E01",
             "valid_to_episode": "E02", "changes": {"hair": "长发"}},
            {"variant_id": "v_short", "valid_from_episode": "E02",
             "valid_to_episode": "E03", "changes": {"hair": "短发"}},
        ],
    })
    order = S.episode_order(tmp_series)
    view = S.resolve_entry_for_episode(_entry(tmp_series), "E02", order)

    conflicts = view["conflicts"]
    assert any(c["field"] == "hair" for c in conflicts)
    hair_conflict = next(c for c in conflicts if c["field"] == "hair")
    assert set(hair_conflict["variants"]) == {"v_long", "v_short"}
    assert set(hair_conflict["values"]) == {"长发", "短发"}
    # a contradicted field is NEVER auto-resolved into the clean merge / effective
    assert "hair" not in view["merged_changes"]
    assert "hair" not in view["effective"]

    # runtime and diagnostics agree (the split the contract forbids is closed)
    codes = {d["code"] for d in S.variant_diagnostics(tmp_series)}
    assert "VARIANT_CONTRADICTION" in codes


# ==================================================== S03 order-independent merge


def test_s03_stable_merge_independent_of_yaml_ordering(tmp_series):
    """S03: the merged result is independent of the YAML declaration order of the
    variants wherever semantics permit (disjoint fields)."""
    order = S.episode_order(tmp_series)
    vw = {"variant_id": "v_wardrobe", "valid_from_episode": "E02",
          "valid_to_episode": "E03", "changes": {"wardrobe": "破损风衣"}}
    vv = {"variant_id": "v_voice", "valid_from_episode": "E02",
          "valid_to_episode": "E03", "changes": {"voice": "沙哑低语"}}

    a = S.resolve_entry_for_episode({"name": "林夏", "variants": [vw, vv]}, "E02", order)
    b = S.resolve_entry_for_episode({"name": "林夏", "variants": [vv, vw]}, "E02", order)

    assert a["effective"] == b["effective"]
    assert a["merged_changes"] == b["merged_changes"]
    assert a["field_sources"] == b["field_sources"]
    assert sorted(a["active_variant_ids"]) == sorted(b["active_variant_ids"])
    # the merge is a real merge, not a pick: BOTH disjoint fields survive
    assert a["merged_changes"] == {"wardrobe": "破损风衣", "voice": "沙哑低语"}


# ==================================================== S04 scene scope, no cross-conflict


def test_s04_scene_specific_changes_do_not_conflict_across_scenes(tmp_series):
    """S04: the SAME subject field set to different values in DIFFERENT scenes of
    one episode is NOT a conflict, and each scene view reads its own value."""
    _set_char(tmp_series, {"name": "林夏"})
    write_yaml(tmp_series.root / "world_state.yaml", {"entries": [
        {"id": "ws_a", "subject": "linxia", "change": {"mood": "紧张"},
         "effective": {"episode": "E01", "scene": "sc_alley"}},
        {"id": "ws_b", "subject": "linxia", "change": {"mood": "平静"},
         "effective": {"episode": "E01", "scene": "sc_store"}},
    ]})
    codes = [d["code"] for d in S.world_state_diagnostics(tmp_series)]
    assert "STATE_CONFLICT" not in codes                     # different scope => no clash

    va = S.world_state_at(tmp_series, "E01", scene="sc_alley")
    vb = S.world_state_at(tmp_series, "E01", scene="sc_store")
    assert va["state"]["linxia"]["mood"]["value"] == "紧张"
    assert vb["state"]["linxia"]["mood"]["value"] == "平静"


# ==================================================== S05 shot scope does not leak


def test_s05_shot_specific_state_does_not_leak_episode_wide(tmp_series):
    """S05: a shot-scoped change is invisible in the episode-wide view; it only
    appears when that exact shot is in scope. Broader (episode) state stays
    visible under the narrower query (precedence shot>scene>episode)."""
    _set_char(tmp_series, {"name": "林夏"})
    write_yaml(tmp_series.root / "world_state.yaml", {"entries": [
        {"id": "ws_ep", "subject": "linxia", "change": {"loc": "便利店"},
         "effective": {"episode": "E01"}},                   # episode scope
        {"id": "ws_shot", "subject": "linxia", "change": {"holding": "雨伞"},
         "effective": {"episode": "E01", "scene": "sc_store", "shot": "S001"}},
    ]})
    epview = S.world_state_at(tmp_series, "E01")              # no scene/shot in scope
    assert "holding" not in epview["state"].get("linxia", {})   # shot state never leaks
    assert epview["state"]["linxia"]["loc"]["value"] == "便利店"

    shotview = S.world_state_at(tmp_series, "E01", scene="sc_store", shot="S001")
    assert shotview["state"]["linxia"]["holding"]["value"] == "雨伞"
    assert shotview["state"]["linxia"]["loc"]["value"] == "便利店"   # broader still visible


# ==================================================== S06 season health tri-state


def test_s06_missing_runtime_projection_makes_season_not_ready(tmp_series):
    """S06: an UNAVAILABLE runtime projection (the SQLite unresolved-submission
    view missing/unreadable) is a tri-state verdict that forces season
    ready=false — while STILL being explainable (never fabricated as zero)."""
    ep = tmp_series.open_episode("E01")
    assert not (ep.root / ".manju" / "state.sqlite").exists()

    row = S._episode_health(ep)
    assert row["unresolved_status"] == "UNAVAILABLE"
    assert row["unresolved_submissions"] is None             # not fabricated as 0
    assert "可重建" in row["unresolved_note"]                # still explainable

    # a season whose episodes have an UNAVAILABLE runtime projection is forced
    # not-ready, and at least one such episode is NAMED with the unavailable
    # reason (fail-closed — an unverifiable projection cannot prove zero work).
    health = S.season_health(tmp_series)
    assert health["ready"] is False
    unavailable = [b for b in health["not_ready"]
                   if any(k in b["reason"]
                          for k in ("unresolved", "unavailable", "unverifiable"))]
    assert unavailable and all(
        r["unresolved_status"] in ("VERIFIED_NONE", "VERIFIED_UNRESOLVED", "UNAVAILABLE")
        for r in health["episodes"])
