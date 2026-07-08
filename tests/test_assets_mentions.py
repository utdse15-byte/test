"""Round U (UA) — asset matrix (goal 5) + @mention system (goal 6).

The matrix is a read model over the EXISTING bible files; @mentions are a
registration aid, never a hidden runtime binding. These tests pin: matrix
reading with the additive aliases/relations fields (and byte-stability of a
plain project), mention parsing edge cases (CJK, punctuation, collisions,
chains, email-guard), the --check/--apply flows (apply respects locks), the QC
advisories, and the reuse of the ``manju appearances`` walk.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from manju.cli import app
from manju.core.appearances import appearances
from manju.core.assets import (
    AssetLookup,
    asset_matrix,
    build_lookup,
    find_asset,
)
from manju.core.check import run_check
from manju.core.mentions import (
    Mention,
    apply_mentions,
    apply_to_shot,
    find_mentions,
    mention_report,
    resolve_mentions,
    shot_mention_text,
)
from manju.core.models import AssetEntry
from manju.core.spec import compute_spec_hash, spec_payload
from manju.core.yamlio import read_yaml, write_yaml
from manju.qc.checks import run_qc

runner = CliRunner()


# ------------------------------------------------------------- helper matrix


def _rich_bible(project) -> None:
    """Give ``tmp_project``'s linxia/convenience_store the round-U fields, plus a
    prop, a voice, a style (and a reserved style ``look:`` config to filter)."""
    write_yaml(project.root / "bible" / "characters.yaml", {
        "linxia": {
            "name": "林夏",
            "appearance": "短发,黑色风衣",
            "aliases": ["阿夏", "夏"],
            "relations": {"located_in": "convenience_store", "uses": ["future_coin"]},
            "default_position": "frame-left",
            "locked_fields": ["appearance"],
            "ref_image": "media/refs/linxia.png",
            "ref_images": ["media/refs/linxia_2.png"],
        },
    })
    write_yaml(project.root / "bible" / "scenes.yaml", {
        "convenience_store": {"name": "便利店", "aliases": ["小卖部"]},
    })
    write_yaml(project.root / "bible" / "props.yaml", {
        "future_coin": {"name": "未来硬币"},
    })
    write_yaml(project.root / "bible" / "voices.yaml", {
        "vo_narrator": {"name": "旁白"},
    })
    write_yaml(project.root / "bible" / "style.yaml", {
        "look": {"preset": "warm", "intensity": 0.5},   # reserved config, NOT an asset
        "noir": {"name": "黑色电影风"},
    })


# =============================================================== asset matrix


def test_matrix_has_all_five_kinds(tmp_project):
    _rich_bible(tmp_project)
    m = asset_matrix(tmp_project)
    assert set(m["kinds"]) == {"character", "scene", "prop", "voice", "style"}
    assert [r["id"] for r in m["kinds"]["character"]] == ["linxia"]
    assert [r["id"] for r in m["kinds"]["voice"]] == ["vo_narrator"]


def test_matrix_row_carries_additive_fields(tmp_project):
    _rich_bible(tmp_project)
    row = find_asset(asset_matrix(tmp_project), "linxia")
    assert row["name"] == "林夏"
    assert row["aliases"] == ["阿夏", "夏"]
    assert row["relations"] == {"located_in": "convenience_store", "uses": ["future_coin"]}
    assert row["default_position"] == "frame-left"
    assert row["locked_fields"] == ["appearance"]
    # refs collect ref_image + ref_images (bible tier, verbatim)
    assert row["refs"]["images"] == ["media/refs/linxia.png", "media/refs/linxia_2.png"]
    assert row["refs"]["videos"] == []


def test_style_look_reserved_key_is_not_an_asset(tmp_project):
    _rich_bible(tmp_project)
    styles = asset_matrix(tmp_project)["kinds"]["style"]
    ids = [r["id"] for r in styles]
    assert "look" not in ids and ids == ["noir"]


def test_plain_entry_defaults_are_empty_and_byte_stable(tmp_project):
    """A bible entry WITHOUT round-U keys reads back with empty defaults, and the
    matrix never writes to the bible file (pure read model)."""
    bible_path = tmp_project.root / "bible" / "characters.yaml"
    before = bible_path.read_bytes()
    row = find_asset(asset_matrix(tmp_project), "linxia")
    assert row["aliases"] == []
    assert row["relations"] == {}
    assert row["default_position"] is None
    assert row["locked_fields"] == []
    # the round-U read model must not touch the truth file
    assert bible_path.read_bytes() == before


def test_round_u_defaults_do_not_leak_into_spec_hash(tmp_project, add_shot):
    """Byte-identical rule: the round-U schema additions must never fold into an
    existing project's picture hash. spec_payload reads the RAW bible dict, so a
    plain entry's excerpt carries none of the new default keys."""
    add_shot(tmp_project, "S001")
    shot = tmp_project.load_shot("S001")
    bible = tmp_project.load_bible()
    excerpt = spec_payload(shot, bible)["character_bible"]["linxia"]
    for key in ("aliases", "relations", "default_position", "locked_fields"):
        assert key not in excerpt
    # and the hash is stable across repeated matrix reads (no mutation)
    h1 = compute_spec_hash(shot, bible)
    asset_matrix(tmp_project)
    assert compute_spec_hash(tmp_project.load_shot("S001"), tmp_project.load_bible()) == h1


def test_matrix_appearances_reuse_appearances_walk(tmp_project, add_shot):
    add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")
    m = asset_matrix(tmp_project)
    app = appearances(tmp_project)
    row = find_asset(m, "linxia")
    assert row["appearances"] == app["characters"]["linxia"]["shots"] == ["S001", "S002"]
    # scene too
    scene_row = find_asset(m, "convenience_store")
    assert scene_row["appearances"] == app["scenes"]["convenience_store"]["shots"]


def test_voice_and_style_appearances_always_empty(tmp_project, add_shot):
    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001")
    m = asset_matrix(tmp_project)
    assert find_asset(m, "vo_narrator")["appearances"] == []
    assert find_asset(m, "noir")["appearances"] == []


def test_find_asset_priority_and_missing(tmp_project):
    # same id in two kinds → character wins (priority)
    write_yaml(tmp_project.root / "bible" / "characters.yaml", {"dup": {"name": "角色"}})
    write_yaml(tmp_project.root / "bible" / "props.yaml", {"dup": {"name": "道具"}})
    m = asset_matrix(tmp_project)
    assert find_asset(m, "dup")["kind"] == "character"
    assert find_asset(m, "nope") is None


def test_matrix_is_json_serializable(tmp_project):
    _rich_bible(tmp_project)
    # must round-trip through json without a custom encoder (GUI read model)
    json.dumps(asset_matrix(tmp_project))


# ---------------------------------------------------- AssetEntry validation


def test_asset_entry_relations_shape_validated():
    e = AssetEntry.model_validate({"relations": {"owner": "linxia", "uses": ["a", "b"]}})
    assert e.relations == {"owner": "linxia", "uses": ["a", "b"]}
    # a non-mapping relations is a hard schema error (shape validated)
    with pytest.raises(ValidationError):
        AssetEntry.model_validate({"relations": "not-a-dict"})


def test_asset_entry_aliases_coerce_scalar():
    assert AssetEntry.model_validate({"aliases": "solo"}).aliases == ["solo"]
    assert AssetEntry.model_validate({"aliases": None}).aliases == []


def test_bible_with_round_u_fields_still_passes_check(tmp_project, add_shot):
    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001")
    report = run_check(tmp_project)
    assert report.ok, report.errors


# =============================================================== parsing


def _raws(text):
    return [m.raw for m in find_mentions(text)]


def test_parse_basic_and_cjk():
    assert _raws("@林夏 走进 @convenience_store,拿起 @future-coin。") == \
        ["林夏", "convenience_store", "future-coin"]


def test_parse_punctuation_boundaries():
    assert _raws("@林夏,你好;@S001。完") == ["林夏", "S001"]
    assert _raws("(@a) [@b]!@c?") == ["a", "b", "c"]


def test_parse_email_is_not_a_mention():
    assert _raws("寄给 utdse15@gmail.com 一封信") == []
    assert _raws("foo@bar baz @real") == ["real"]


def test_parse_chain_double_at():
    assert _raws("@a@b@c") == ["a", "b", "c"]


def test_parse_bare_at_and_empty():
    assert _raws("给我 @ 空间 @") == []
    assert find_mentions("") == []


def test_find_mentions_spans_are_accurate():
    text = "看 @林夏 走"
    (m,) = find_mentions(text)
    assert isinstance(m, Mention)
    assert text[m.span[0]:m.span[1]] == "@林夏"
    assert m.at == m.span[0]


# =============================================================== resolution


def _matrix_for_resolution():
    return {"kinds": {
        "character": [{"id": "linxia", "aliases": ["阿夏"]}],
        "scene": [{"id": "night_market", "aliases": ["夜市"]}],
        "prop": [{"id": "linxia", "aliases": []}],   # collides with character id
        "voice": [],
        "style": [],
    }}


def test_resolve_by_id_and_alias():
    m = _matrix_for_resolution()
    resolved, unresolved = resolve_mentions("@阿夏 在 @夜市 找 @ghost", m)
    assert [(x.raw, k, a) for x, k, a in resolved] == \
        [("阿夏", "character", "linxia"), ("夜市", "scene", "night_market")]
    assert [x.raw for x in unresolved] == ["ghost"]


def test_resolve_collision_priority():
    m = _matrix_for_resolution()
    resolved, _ = resolve_mentions("@linxia", m)
    assert [(k, a) for _, k, a in resolved] == [("character", "linxia")]


def test_build_lookup_reports_collision():
    lk: AssetLookup = build_lookup(_matrix_for_resolution())
    assert lk.is_collision("linxia") is True
    assert lk.is_collision("阿夏") is False
    assert lk.resolve("阿夏") == ("character", "linxia")
    assert lk.resolve("nobody") is None


def test_resolve_is_pure_and_deterministic():
    m = _matrix_for_resolution()
    a = resolve_mentions("@阿夏 @夜市 @阿夏", m)
    b = resolve_mentions("@阿夏 @夜市 @阿夏", m)
    assert [(x.raw, k, i) for x, k, i in a[0]] == [(x.raw, k, i) for x, k, i in b[0]]


# =============================================================== --check


def test_shot_mention_text_covers_expected_fields(tmp_project, add_shot):
    add_shot(tmp_project, "S001",
             action={"main": "@a 动作", "emotion": "@b"},
             dialogue={"speaker": "linxia", "text": "@c 台词"},
             quality={"must_show": ["@d"], "avoid": ["@e"]})
    text = shot_mention_text(tmp_project.load_shot("S001"))
    assert _raws(text) == ["a", "b", "c", "d", "e"]


def test_mention_report_flags_registered_and_unresolved(tmp_project, add_shot):
    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001", characters=["linxia"],
             action={"main": "@阿夏 拿起 @future_coin 遇到 @typo"})
    rep = mention_report(tmp_project, asset_matrix(tmp_project), "S001")
    entry = rep["shots"][0]
    by_raw = {r["raw"]: r for r in entry["resolved"]}
    assert by_raw["阿夏"]["registered"] is True         # linxia already in characters
    assert by_raw["future_coin"]["registered"] is None  # prop: no field to register
    assert entry["unresolved"][0]["raw"] == "typo"
    assert "convenience_store" not in entry["unresolved"][0]["nearest"]


def test_mention_report_scans_story_files(tmp_project):
    _rich_bible(tmp_project)
    (tmp_project.root / "story" / "script.md").write_text(
        "@阿夏 走进 @convenience_store。@nobody 出现。", encoding="utf-8")
    rep = mention_report(tmp_project, asset_matrix(tmp_project))
    story = {s["file"]: s for s in rep["story"]}
    assert "story/script.md" in story
    resolved_assets = {r["asset"] for r in story["story/script.md"]["resolved"]}
    assert resolved_assets == {"linxia", "convenience_store"}
    assert story["story/script.md"]["unresolved"][0]["raw"] == "nobody"


# =============================================================== --apply


def test_apply_registers_character_via_alias(tmp_project, add_shot):
    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001", characters=[], action={"main": "@阿夏 独白"})
    res = apply_to_shot(tmp_project, "S001", asset_matrix(tmp_project))
    assert res["added_characters"] == ["linxia"]
    assert tmp_project.load_shot("S001").characters == ["linxia"]


def test_apply_sets_scene_when_empty(tmp_project, add_shot):
    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001", scene=None, action={"main": "@convenience_store 场景"})
    res = apply_to_shot(tmp_project, "S001", asset_matrix(tmp_project))
    assert res["set_scene"] == "convenience_store"
    assert tmp_project.load_shot("S001").scene == "convenience_store"


def test_apply_does_not_overwrite_existing_scene(tmp_project, add_shot):
    _rich_bible(tmp_project)
    write_yaml(tmp_project.root / "bible" / "scenes.yaml", {
        "convenience_store": {"name": "便利店"},
        "night_market": {"name": "夜市", "aliases": ["夜市"]},
    })
    add_shot(tmp_project, "S001", scene="convenience_store",
             action={"main": "@夜市 也出现"})
    res = apply_to_shot(tmp_project, "S001", asset_matrix(tmp_project))
    assert res["set_scene"] is None
    assert any("不覆盖" in s["reason"] for s in res["skipped"])
    assert tmp_project.load_shot("S001").scene == "convenience_store"


def test_apply_respects_locked_characters(tmp_project, add_shot):
    from manju.core.locks import seal_lock

    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001", characters=[], action={"main": "@阿夏 独白"})
    raw = tmp_project.load_shot_raw("S001")
    digest = seal_lock(raw, "characters")   # seal the (empty) list
    tmp_project.update_shot_raw("S001", lambda d: d.__setitem__("locked", {"characters": digest}))

    res = apply_to_shot(tmp_project, "S001", asset_matrix(tmp_project))
    assert res["added_characters"] == []
    assert res["changed"] is False
    assert any("锁定" in s["reason"] for s in res["skipped"])
    # the locked field was NOT written
    assert tmp_project.load_shot("S001").characters == []


def test_apply_to_shot_guard_blocks_lock_added_between_read_and_write(
    tmp_project, add_shot, monkeypatch
):
    """Round W (#41): mentions --apply used to have only the TOP-level
    pre-filter (locked_paths from ONE snapshot of shot.locked) protecting it —
    no write-time re-check. checked_shot_write's guard_paths re-reads the raw
    file immediately before writing, so a lock sealed in the race window
    AFTER apply_to_shot's initial read (another actor's `manju lock` landing
    concurrently) is still refused, loudly, instead of writing straight over
    it — exactly the gap #41 named ("只做有限的顶层 lock 检查")."""
    from manju.core.container import Project
    from manju.core.locks import seal_lock
    from manju.core.writes import WriteRejected

    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001", characters=[], action={"main": "@阿夏 独白"})

    orig_load_shot = Project.load_shot

    def racy_load_shot(self, shot_id):
        shot = orig_load_shot(self, shot_id)
        if shot_id == "S001":
            # Simulate a concurrent `manju lock` landing on disk RIGHT as
            # apply_to_shot finishes reading (its own `shot` snapshot is
            # already unlocked — matches the pre-fix single-read pre-filter).
            raw = self.load_shot_raw(shot_id)
            digest = seal_lock(raw, "characters")
            self.update_shot_raw(
                shot_id, lambda d: d.__setitem__("locked", {"characters": digest})
            )
        return shot

    monkeypatch.setattr(Project, "load_shot", racy_load_shot)
    try:
        with pytest.raises(WriteRejected):
            apply_to_shot(tmp_project, "S001", asset_matrix(tmp_project))
    finally:
        monkeypatch.undo()

    # the race-sealed lock held: characters was NOT written despite the
    # pre-filter (working from a stale snapshot) having seen it as open
    assert tmp_project.load_shot("S001").characters == []


def test_apply_mentions_batch_isolates_and_reports(tmp_project, add_shot):
    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001", characters=[], action={"main": "@阿夏"})
    add_shot(tmp_project, "S002", characters=["linxia"], action={"main": "无提及"})
    results = apply_mentions(tmp_project, asset_matrix(tmp_project))
    by_shot = {r["shot"]: r for r in results}
    assert by_shot["S001"]["changed"] is True
    assert by_shot["S002"]["changed"] is False


# =============================================================== QC


def test_qc_flags_unresolved_and_unregistered(tmp_project, add_shot):
    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001", characters=[], scene=None,
             action={"main": "@阿夏 在 @convenience_store 遇到 @typo_here"})
    report = run_qc(tmp_project, timeline=None, extract_frames=False)
    msgs = [it.message for it in report.items if it.area == "content"]
    assert any("@typo_here" in m and "无法解析" in m for m in msgs)
    assert any("@阿夏" in m and "未登记进 shot.characters" in m for m in msgs)
    assert any("@convenience_store" in m and "shot.scene" in m for m in msgs)
    # all mention advisories are info-level (never fail QC)
    mention_items = [it for it in report.items
                     if it.area == "content" and "@" in it.message]
    assert mention_items and all(it.level == "info" for it in mention_items)


def test_qc_no_mentions_adds_no_mention_items(tmp_project, add_shot):
    """Byte-identical QC for a project that uses no @mentions."""
    add_shot(tmp_project, "S001", action={"main": "普通动作,没有 at 符号"})
    report = run_qc(tmp_project, timeline=None, extract_frames=False)
    assert not [it for it in report.items if "无法解析" in it.message
                or "未登记进 shot.characters" in it.message]


def test_qc_registered_mention_is_not_flagged(tmp_project, add_shot):
    _rich_bible(tmp_project)
    add_shot(tmp_project, "S001", characters=["linxia"],
             action={"main": "@阿夏 已登记"})
    report = run_qc(tmp_project, timeline=None, extract_frames=False)
    assert not [it for it in report.items if "未登记进 shot.characters" in it.message]


# =============================================================== CLI wiring


@pytest.fixture
def in_project(tmp_project, monkeypatch):
    monkeypatch.chdir(tmp_project.root)
    return tmp_project


def test_cli_assets_table_and_show(in_project):
    _rich_bible(in_project)
    table = runner.invoke(app, ["assets"])
    assert table.exit_code == 0
    assert "资产矩阵" in table.output and "linxia" in table.output

    js = runner.invoke(app, ["assets", "--json"])
    assert js.exit_code == 0
    data = json.loads(js.output)
    assert data["kinds"]["character"][0]["id"] == "linxia"

    show = runner.invoke(app, ["assets", "show", "linxia", "--json"])
    assert show.exit_code == 0
    assert json.loads(show.output)["aliases"] == ["阿夏", "夏"]

    missing = runner.invoke(app, ["assets", "show", "nope"])
    assert missing.exit_code == 1


def test_cli_mentions_check_and_apply(in_project, add_shot):
    _rich_bible(in_project)
    add_shot(in_project, "S001", characters=[], action={"main": "@阿夏 独白"})

    check = runner.invoke(app, ["mentions", "--json"])
    assert check.exit_code == 0
    rep = json.loads(check.output)
    assert rep["shots"][0]["resolved"][0]["asset"] == "linxia"

    apply_res = runner.invoke(app, ["mentions", "S001", "--apply", "--json"])
    assert apply_res.exit_code == 0
    applied = json.loads(apply_res.output)["applied"]
    assert applied[0]["added_characters"] == ["linxia"]
    assert in_project.load_shot("S001").characters == ["linxia"]

    # the apply landed an event on the collaboration log
    from manju.core.events import tail_events
    kinds = [e.get("action") for e in tail_events(in_project.root, 10)]
    assert "mentions_apply" in kinds
