"""``@周叔`` must resolve — the display name is the handle the user SEES.

`build_lookup` indexed only ``id`` and ``aliases``. But every surface that
shows an asset shows its ``name``: ``manju appearances`` prints
"old_zhou  周叔", ``bible/characters.yaml`` says ``name: 周叔``, the GUI
storyboard chips are labelled with it. So the handle a user naturally types
after ``@`` was the one handle that resolved to nothing.

It failed twice over: no resolution, AND no nearest-match hint, because
``all_names`` held only ASCII ids and difflib had nothing to compare a CJK
token against. A dead end with no signpost.

Indexing the name adds a handle to the SAME asset, so it can never manufacture
a collision with its own id — only with a genuinely different asset, which is
exactly what `is_collision` already reports.
"""

from __future__ import annotations

from typer.testing import CliRunner

from manju.cli import app
from manju.core.assets import asset_matrix, build_lookup
from manju.core.mentions import mention_report, nearest_ids, resolve_mentions

runner = CliRunner()


def _bible(project, characters: dict, scenes: dict | None = None) -> None:
    from manju.core.yamlio import write_yaml

    write_yaml(project.root / "bible" / "characters.yaml", characters)
    if scenes is not None:
        write_yaml(project.root / "bible" / "scenes.yaml", scenes)


CHARS = {
    "linxia": {"name": "林夏", "appearance": "短发"},
    "old_zhou": {"name": "周叔", "appearance": "花白头发"},
}
SCENES = {
    "convenience_store": {"name": "便利店", "description": "雨夜街角"},
    "rainy_street": {"name": "雨夜街道", "description": "窄街"},
}


# --------------------------------------------------------------- the lookup


def _matrix():
    return {"kinds": {
        "character": [{"id": "old_zhou", "name": "周叔", "aliases": ["阿周"]}],
        "scene": [{"id": "rainy_street", "name": "雨夜街道", "aliases": []}],
        "prop": [], "voice": [], "style": [],
    }}


def test_the_display_name_resolves_to_its_asset() -> None:
    lk = build_lookup(_matrix())
    assert lk.resolve("周叔") == ("character", "old_zhou")
    assert lk.resolve("雨夜街道") == ("scene", "rainy_street")


def test_id_and_alias_still_resolve() -> None:
    """The name is an ADDITION; nothing that worked may stop working."""
    lk = build_lookup(_matrix())
    assert lk.resolve("old_zhou") == ("character", "old_zhou")
    assert lk.resolve("阿周") == ("character", "old_zhou")
    assert lk.resolve("nobody") is None


def test_a_name_is_not_a_collision_with_its_own_id() -> None:
    """Two handles for ONE asset is not ambiguity — `distinct` is by (kind, id)."""
    lk = build_lookup(_matrix())
    assert lk.is_collision("周叔") is False
    assert lk.is_collision("old_zhou") is False


def test_a_name_that_names_a_different_asset_is_a_collision() -> None:
    m = {"kinds": {
        "character": [{"id": "zhou", "name": "灯", "aliases": []}],
        "prop": [{"id": "灯", "name": "台灯", "aliases": []}],
        "scene": [], "voice": [], "style": [],
    }}
    lk = build_lookup(m)
    assert lk.is_collision("灯") is True
    # kind priority still decides: character > prop.
    assert lk.resolve("灯") == ("character", "zhou")


def test_an_exact_id_outranks_another_assets_display_name() -> None:
    """Within one kind, id beats name beats alias."""
    m = {"kinds": {
        "character": [
            {"id": "a", "name": "b", "aliases": []},
            {"id": "b", "name": "z", "aliases": []},
        ],
        "scene": [], "prop": [], "voice": [], "style": [],
    }}
    lk = build_lookup(m)
    assert lk.resolve("b") == ("character", "b")   # the id, not a's name
    assert [c[2] for c in lk.candidates("b")] == ["id", "name"]


def test_a_name_equal_to_its_own_id_is_indexed_once() -> None:
    m = {"kinds": {"character": [{"id": "x", "name": "x", "aliases": []}],
                   "scene": [], "prop": [], "voice": [], "style": []}}
    lk = build_lookup(m)
    assert lk.by_key["x"] == [("character", "x", "id")]
    assert lk.all_names.count("x") == 1


def test_a_row_without_a_name_still_indexes() -> None:
    """Matrices built by tests and older callers carry no `name` key."""
    lk = build_lookup({"kinds": {"character": [{"id": "q", "aliases": []}],
                                 "scene": [], "prop": [], "voice": [], "style": []}})
    assert lk.resolve("q") == ("character", "q")


# ---------------------------------------------------------- nearest-match hint


def test_a_cjk_typo_now_has_something_to_be_near() -> None:
    """all_names was ASCII-only, so difflib could not hint at a CJK token."""
    m = _matrix()
    assert "周叔" in build_lookup(m).all_names
    assert nearest_ids("周叔叔", m)


# ------------------------------------------------------------------ end to end


def test_resolve_mentions_reads_a_name_from_free_text() -> None:
    resolved, unresolved = resolve_mentions("@周叔 在 @雨夜街道 等 @谁", _matrix())
    assert [(x.raw, k, a) for x, k, a in resolved] == [
        ("周叔", "character", "old_zhou"),
        ("雨夜街道", "scene", "rainy_street")]
    assert [x.raw for x in unresolved] == ["谁"]


def test_mention_report_marks_a_name_hit_unregistered(tmp_project, add_shot) -> None:
    _bible(tmp_project, CHARS, SCENES)
    add_shot(tmp_project, "S001", characters=["linxia"],
             scene="convenience_store", action={"main": "@周叔 擦杯子"})
    rep = mention_report(tmp_project, asset_matrix(tmp_project), "S001")
    (hit,) = rep["shots"][0]["resolved"]
    assert (hit["raw"], hit["kind"], hit["asset"]) == ("周叔", "character", "old_zhou")
    assert hit["registered"] is False


def test_apply_writes_the_name_hit_into_characters(
        tmp_project, add_shot, monkeypatch) -> None:
    _bible(tmp_project, CHARS, SCENES)
    add_shot(tmp_project, "S001", characters=["linxia"],
             scene="convenience_store", action={"main": "@周叔 擦杯子"})
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["mentions", "--apply", "S001"])
    assert res.exit_code == 0, res.stdout
    assert tmp_project.load_shot("S001").characters == ["linxia", "old_zhou"]
