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


# -------------------------------------------------- round-W #59/#77 coverage


def test_fingerprint_moves_when_caption_file_touched(tmp_project, add_shot):
    """§59: captions/*.srt|.ass are covered — a size-changing edit moves the
    fingerprint even though it is not one of the CompileInput truth files."""
    from manju.build.watchloop import project_fingerprint

    add_shot(tmp_project, "S001")
    tmp_project.captions_dir.mkdir(parents=True, exist_ok=True)
    srt = tmp_project.captions_dir / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n旧字幕\n\n", encoding="utf-8")
    before = project_fingerprint(tmp_project)

    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n换成一句明显更长的新字幕内容\n\n",
                   encoding="utf-8")
    after = project_fingerprint(tmp_project)
    assert after != before


def test_fingerprint_moves_when_take_media_replaced_in_place(
    tmp_project, add_shot, make_take
):
    """§77: replacing a take's media file IN PLACE (same path, new bytes) used
    to be invisible — the old scan only recorded the take DIRECTORY's own
    mtime, which does not move on an in-place file rewrite (only on add/
    remove). The fingerprint must now recurse into the take dir."""
    from manju.build.watchloop import project_fingerprint
    from manju.core.spec import compute_spec_hash

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    before = project_fingerprint(tmp_project)

    take.media_path.write_bytes(b"externally-replaced-media-bytes-different-size")
    after = project_fingerprint(tmp_project)
    assert after != before


def test_fingerprint_moves_when_take_sidecar_replaced_in_place(
    tmp_project, add_shot, make_take
):
    """§77, sidecar half of the same fix: an externally-edited take sidecar
    (e.g. a timing/virtual-trim field hand-patched) must move the fingerprint
    too, not just the media file."""
    from manju.build.watchloop import project_fingerprint
    from manju.core.spec import compute_spec_hash

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    before = project_fingerprint(tmp_project)

    take.sidecar_path.write_text(
        take.sidecar_path.read_text(encoding="utf-8") + "note: 手工改过的 sidecar\n",
        encoding="utf-8",
    )
    after = project_fingerprint(tmp_project)
    assert after != before


def test_fingerprint_moves_when_ref_media_replaced(tmp_project, add_shot):
    """§59: media/refs/** is covered recursively — a reference image a shot's
    cloud prompt/keyframe can point at moves the fingerprint on an in-place
    rewrite, even though no truth TEXT file changed."""
    from manju.build.watchloop import project_fingerprint

    add_shot(tmp_project, "S001")
    tmp_project.refs_dir.mkdir(parents=True, exist_ok=True)
    ref = tmp_project.refs_dir / "linxia_front.png"
    ref.write_bytes(b"old-ref-bytes")
    before = project_fingerprint(tmp_project)

    ref.write_bytes(b"new-ref-bytes-of-a-different-size")
    after = project_fingerprint(tmp_project)
    assert after != before


def test_fingerprint_moves_when_keyframe_referenced_image_replaced(tmp_project, add_shot):
    """§59: a keyframe's `image` path is resolved and stat'ed even OUTSIDE the
    media/refs/ convention — the fingerprint reads each shot's raw keyframes
    list specifically to find files like this one, under media/imports/."""
    from manju.build.watchloop import project_fingerprint

    kf_path = tmp_project.root / "media" / "imports" / "kf_custom.png"
    kf_path.parent.mkdir(parents=True, exist_ok=True)
    kf_path.write_bytes(b"old-keyframe-bytes")
    add_shot(tmp_project, "S001",
             keyframes=[{"position": "start", "image": "media/imports/kf_custom.png"}])
    before = project_fingerprint(tmp_project)

    kf_path.write_bytes(b"new-keyframe-bytes-of-a-different-size")
    after = project_fingerprint(tmp_project)
    assert after != before


def test_touching_take_media_in_place_triggers_a_watch_tick(
    tmp_project, add_shot, make_take
):
    """End-to-end (§77): the actual `watch_ticks` generator — not just the raw
    fingerprint — reports a change when a take's media is replaced in place."""
    from manju.core.spec import compute_spec_hash

    shot = add_shot(tmp_project, "S001")
    take = make_take(tmp_project, "S001", compute_spec_hash(shot, tmp_project.load_bible()))
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", take.name)
    )

    gen = watch_ticks(tmp_project, interval_s=0.05)
    first = next(gen)

    take.media_path.write_bytes(b"replaced-in-place-different-size-bytes")
    second = next(gen)
    gen.close()

    assert second.changed is True
    assert second.fingerprint != first.fingerprint


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
