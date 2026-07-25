"""Three workflow traps found by actually using the tool (2026-07-25).

Not correctness defects — the engine did the right thing each time. They are the
places where using it normally cost the owner real work or real time:

1. ``manju import`` was a ONE-WAY DOOR for footage. Import a clip (which
   ``import``'s own help invites: "Real footage/audio → media/imports"), then try
   to route it to a shot: ingest skipped it as a duplicate forever. Not via
   ``--shot``, not via ``--on-duplicate import``, not by handing ingest an
   identically-named copy from outside. There was no escape hatch and no message
   pointing at one, because none existed.
2. New material arrived in SILENCE. An ingested take for an already-selected
   shot never auto-selects (correct, append-only) — but ``build`` then said
   "final up-to-date", the film did not change, and ``status``/``explain`` went
   on naming the old take. The only trace was a counter in ``ingest-batches``.
3. The headline next step recommended a command that fails 100% of the time: a
   locale with translations but no voice is refused on purpose, yet
   ``manju build --lang X --target final`` was what status (and the GUI's most
   prominent slot) told you to run.
"""

from __future__ import annotations

from pathlib import Path

from manju.build.ingest import plan_ingest
from manju.build.status import shot_next_action
from manju.core.container import Project


def _clip(path: Path, payload: bytes = b"\x00\x00\x00\x18ftypmp42REALFOOTAGE") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _already_imported(project: Project, src: Path) -> Path:
    """What `manju import` leaves behind: the bytes sitting in media/imports/.

    The CLI does this inline (cli.py's `import` command), so the test reproduces
    the RESULT rather than reaching for a Project method that does not exist.
    That result is all the dedup index looks at."""
    project.imports_dir.mkdir(parents=True, exist_ok=True)
    dest = project.imports_dir / src.name
    dest.write_bytes(src.read_bytes())
    return dest


# ------------------------------------------------- 1. import is not a dead end


def test_already_imported_footage_can_still_become_a_take(
        tmp_project: Project, add_shot, tmp_path: Path) -> None:
    add_shot(tmp_project, "S003")
    src = _clip(tmp_path / "drop" / "我的素材 01.mp4")
    landed = _already_imported(tmp_project, src)   # the one-way door

    rows = plan_ingest(tmp_project, [landed], shot="S003").rows
    assert len(rows) == 1
    assert rows[0].action == "take", rows[0].reason
    assert "S003" in rows[0].target
    # sourced from the copy already in the project, not a second raw copy
    assert "media/imports" in rows[0].reason.replace("\\", "/")


def test_a_plain_duplicate_import_is_still_skipped(
        tmp_project: Project, tmp_path: Path) -> None:
    """The dedup still defends what it was written for: no second RAW copy."""
    src = _clip(tmp_path / "drop" / "footage.mp4")
    landed = _already_imported(tmp_project, src)

    rows = plan_ingest(tmp_project, [landed]).rows   # no role → a plain import
    assert rows[0].action == "skip_duplicate"
    assert "素材只增不改" in rows[0].reason


def test_content_already_registered_as_a_take_is_still_skipped(
        tmp_project: Project, add_shot, make_take, tmp_path: Path) -> None:
    """"already in the project" vs "already IS a take" — only the second is a
    no-op, and it must stay one."""
    add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", "sha256:spec")
    rows = plan_ingest(tmp_project, [take.media_path], shot="S001").rows
    assert rows[0].action == "skip_duplicate"


# ----------------------------------------------- 2. new material is not silent


def test_a_take_newer_than_the_selection_is_surfaced(
        tmp_project: Project, add_shot, make_take) -> None:
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:spec")
    make_take(tmp_project, "S001", "sha256:spec")      # ingested later
    act = shot_next_action(tmp_project, "S001", state="fresh",
                           selected_take="take_01")
    assert act["key"] == "newtake"
    assert "manju select S001 2" in act["action"]
    assert "现选依然有效" in act["action"]   # a decision, not a defect


def test_no_new_take_nag_when_the_newest_is_selected(
        tmp_project: Project, add_shot, make_take) -> None:
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:spec")
    make_take(tmp_project, "S001", "sha256:spec")
    act = shot_next_action(tmp_project, "S001", state="fresh",
                           selected_take="take_02")
    assert act["key"] != "newtake"


def test_new_take_outranks_the_voice_todo(
        tmp_project: Project, add_shot, make_take) -> None:
    """A missing voice already has many surfaces; an undecided take had none."""
    add_shot(tmp_project, "S001")
    make_take(tmp_project, "S001", "sha256:spec")
    make_take(tmp_project, "S001", "sha256:spec")
    act = shot_next_action(tmp_project, "S001", state="fresh",
                           selected_take="take_01", voice_state="missing")
    assert act["key"] == "newtake"


# --------------------------------------- 3. never recommend an unrunnable build


def test_locale_next_step_names_the_unblocking_step_not_a_doomed_build(
        tmp_project: Project, add_shot) -> None:
    from manju.build.status import _locale_voice_blocker
    from manju.core.locale import add_locale

    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "台词"})
    add_locale(tmp_project, "en")
    lines = tmp_project.root / "locales" / "en" / "lines.yaml"
    lines.write_text("S001:\n  text: 'A line.'\n  base_hash: x\n", encoding="utf-8")

    blocker = _locale_voice_blocker(tmp_project, "en")
    assert blocker is not None
    assert "有译文无配音" in blocker
    # it must NOT be the build command that would be refused
    assert "manju build --lang" not in blocker


def test_locale_blocker_is_none_when_nothing_needs_voice(
        tmp_project: Project) -> None:
    """No lines needing voice → the ordinary build recommendation stands."""
    from manju.build.status import _locale_voice_blocker
    from manju.core.locale import add_locale

    add_locale(tmp_project, "en")
    assert _locale_voice_blocker(tmp_project, "en") is None


def test_locale_blocker_never_raises_on_a_broken_locale(
        tmp_project: Project) -> None:
    from manju.build.status import _locale_voice_blocker

    assert _locale_voice_blocker(tmp_project, "nope-not-a-locale") is None
