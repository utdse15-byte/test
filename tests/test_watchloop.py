"""`manju watch` dev-loop generator (src/manju/build/watchloop.py).

Fast and hermetic: `interval_s=0.05`, and every edit changes the *size* of a
truth file so the stat-based fingerprint is guaranteed to move — no test leans
on mtime resolution, and no `next()` can block waiting for a change that never
lands. Drives the generator with explicit `next()` so an edit can be slipped in
between ticks, exactly as a human typing in the other terminal would.
"""

from __future__ import annotations

from manju.build.watchloop import WatchTick, watch_ticks


def test_first_tick_is_immediate_and_ok(tmp_project, add_shot):
    """A valid project reports at once: the initial tick fires before any
    sleep, marked changed, with a green check."""
    add_shot(tmp_project, "S001")

    gen = watch_ticks(tmp_project, interval_s=0.05)
    tick = next(gen)
    gen.close()

    assert isinstance(tick, WatchTick)
    assert tick.changed is True
    assert tick.check_ok is True
    assert tick.errors == []
    assert tick.fingerprint            # non-empty sha1 hex
    assert tick.ts                     # iso utc stamp


def test_editing_a_shot_between_ticks_yields_a_changed_tick(tmp_project, add_shot):
    """Edit a shot after the first tick -> the next tick reports the change
    with a moved fingerprint (and the edit is still valid, so still green)."""
    add_shot(tmp_project, "S001")

    gen = watch_ticks(tmp_project, interval_s=0.05)
    first = next(gen)

    # A human edits the dialogue in their editor. The longer text changes the
    # file size, so the fingerprint is guaranteed to move.
    tmp_project.update_shot_raw(
        "S001",
        lambda d: d.setdefault("dialogue", {}).__setitem__(
            "text", "改了一句明显更长的台词,用来确保 shots/S001.yaml 的大小发生变化。"
        ),
    )

    second = next(gen)
    gen.close()

    assert second.changed is True
    assert second.fingerprint != first.fingerprint
    assert second.check_ok is True


def test_broken_edit_reports_the_reference_error_then_recovers(tmp_project, add_shot):
    """A shot pointing at a non-existent scene fails check with the exact
    referential-integrity error; fixing it flips the next tick back to green."""
    add_shot(tmp_project, "S001")

    gen = watch_ticks(tmp_project, interval_s=0.05)
    next(gen)  # initial, green

    # BROKEN edit: scene points nowhere in the bible.
    tmp_project.update_shot_raw("S001", lambda d: d.__setitem__("scene", "nowhere"))
    broken = next(gen)

    assert broken.check_ok is False
    assert "shots/S001.yaml: scene 'nowhere' not found in bible" in broken.errors

    # FIX it back to a scene the bible actually defines.
    tmp_project.update_shot_raw(
        "S001", lambda d: d.__setitem__("scene", "convenience_store")
    )
    fixed = next(gen)
    gen.close()

    assert fixed.check_ok is True
    assert fixed.errors == []


def test_max_ticks_bounds_the_generator(tmp_project, add_shot):
    """max_ticks caps yielded ticks so a plain list() terminates (the CLI
    --once path). With max_ticks=1 the loop never even reaches a sleep."""
    add_shot(tmp_project, "S001")

    ticks = list(watch_ticks(tmp_project, interval_s=0.05, max_ticks=1))

    assert len(ticks) == 1
    assert ticks[0].changed is True
    assert ticks[0].check_ok is True


def test_torn_write_is_caught_and_the_generator_survives(tmp_project, add_shot):
    """A half-written (invalid YAML) shot file must not kill the watcher: the
    tick reports check_ok False (a finding) or None (a caught crash), and the
    generator lives on to report the next, valid save as green again."""
    add_shot(tmp_project, "S001")

    gen = watch_ticks(tmp_project, interval_s=0.05)
    next(gen)  # initial, green

    # A torn mid-editor save: invalid YAML bytes land in the shot file (an
    # unterminated flow sequence — yaml.safe_load raises).
    tmp_project.shot_path("S001").write_bytes(b"scene: [convenience_store, linxia\n")
    torn = next(gen)

    assert torn.check_ok in (False, None)   # a finding or a caught crash — never a raise

    # SURVIVAL: the editor finishes the save with valid content; the watcher,
    # still running, reports the recovered state on the next tick.
    add_shot(tmp_project, "S001", dialogue={"speaker": "linxia", "text": "修好了。"})
    healed = next(gen)
    gen.close()

    assert healed.check_ok is True
