"""TRISURFACE round 4 — the last two deferrals, decided and landed.

F-11: `locale add` scaffolded a line for EVERY shot — five hash-of-empty
placeholder rows on a 7-shot/2-dialogue project — and `locale status` then
reported ``missing=5`` forever: green unreachable, and the reader cannot tell
"you owe a translation" from "there is nothing to translate". The decision
(DECISIONS TRISURFACE-FIX #13): dialogue-less shots are not debt. `add_locale`
skips them; `line_status` answers ``not_needed`` (无台词) for them (including
legacy empty scaffold rows already on disk); a shot that GAINS dialogue later
becomes ``missing`` exactly then — the debt appears when it is real.

F-20 (minimal): 标记已人工确认 wrote a permanent human-verification record on
a single click — my own automation misclicked it in round 1. The record's
meaning ("a HUMAN opened this draft in the desktop app") deserves one native
confirm() before an append-only write; retraction semantics stay deferred.
"""

from __future__ import annotations

from manju.core.locale import add_locale, line_status, locale_status


def _dialogue_pair(project, add_shot):
    add_shot(project, "S001")  # conftest default carries dialogue
    add_shot(project, "S002", dialogue={"speaker": "", "text": ""})
    add_shot(project, "S003", dialogue={"speaker": "linxia", "text": "还给我。"})


# ------------------------------------------------------- F-11 scaffold


def test_add_locale_scaffolds_only_dialogue_shots(tmp_project, add_shot):
    _dialogue_pair(tmp_project, add_shot)
    result = add_locale(tmp_project, "en")
    assert result["added"] == ["S001", "S003"], result
    assert result["total"] == 2


def test_dialogueless_shot_is_not_needed_not_missing(tmp_project, add_shot):
    _dialogue_pair(tmp_project, add_shot)
    add_locale(tmp_project, "en")
    st = line_status(tmp_project, "en", "S002")
    assert st["state"] == "not_needed"


def test_legacy_empty_scaffold_row_reads_not_needed(tmp_project, add_shot):
    """Projects scaffolded before this decision carry hash-of-empty rows on
    disk — they must read not_needed too, without a migration."""
    from manju.core.locale import base_text_hash, locale_dir
    from manju.core.yamlio import write_yaml

    _dialogue_pair(tmp_project, add_shot)
    d = locale_dir(tmp_project, "en")
    d.mkdir(parents=True, exist_ok=True)
    write_yaml(d / "lines.yaml",
               {"S002": {"text": "", "base_hash": base_text_hash("")}})
    st = line_status(tmp_project, "en", "S002")
    assert st["state"] == "not_needed"


def test_green_is_reachable_once_real_lines_are_translated(tmp_project, add_shot):
    """The recorded trap: translate everything translatable and missing=5
    persisted. Now missing reaches 0."""
    from manju.core.locale import locale_dir
    from manju.core.yamlio import read_yaml, write_yaml

    _dialogue_pair(tmp_project, add_shot)
    add_locale(tmp_project, "en")
    p = locale_dir(tmp_project, "en") / "lines.yaml"
    lines = read_yaml(p)
    for row in lines.values():
        row["text"] = "translated."
    write_yaml(p, lines)

    body = locale_status(tmp_project, "en")["locales"]["en"]
    assert body["counts"]["missing"] == 0
    assert body["counts"]["ok"] == 2
    assert body["counts"].get("not_needed", 0) == 1
    # …and the voice column knows nothing is owed for the silent shot.
    assert body["voice"]["S002"] == "not_needed"


def test_gaining_dialogue_turns_not_needed_into_missing(tmp_project, add_shot):
    """The debt appears exactly when it becomes real — never earlier."""
    _dialogue_pair(tmp_project, add_shot)
    add_locale(tmp_project, "en")
    tmp_project.update_shot_raw(
        "S002",
        lambda d: d.__setitem__("dialogue", {"speaker": "linxia", "text": "新台词。"}))
    st = line_status(tmp_project, "en", "S002")
    assert st["state"] == "missing"


def test_cli_locale_status_says_wutai_ci_not_missing(tmp_project, add_shot,
                                                     monkeypatch):
    from typer.testing import CliRunner

    from manju.cli import app

    _dialogue_pair(tmp_project, add_shot)
    add_locale(tmp_project, "en")
    monkeypatch.chdir(tmp_project.root)
    res = CliRunner().invoke(app, ["locale", "status", "en"])
    assert res.exit_code == 0, res.output
    assert "missing=2" in res.output  # the two REAL dialogue lines
    assert "无台词=1" in res.output
    # non-actionable rows stay off the per-line listing
    assert "S002: 译=not_needed" not in res.output


# --------------------------------------------------- F-20 accident guard


def test_verify_button_asks_before_writing_the_permanent_record():
    """One stray click recorded "human verified in the desktop app" into the
    append-only log (my own automation did exactly that in round 1). The page
    JS must confirm() before POSTing — same native-dialog pattern the
    subtitles takeover uses."""
    from manju.gui.exports_page import render_exports_js

    js = render_exports_js()
    verify_part = js[js.index("verify"):]
    assert "confirm(" in verify_part
    assert "已在" in js or "打开过" in js  # the question states what is being sworn
