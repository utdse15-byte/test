"""`manju watch` — the dev-loop watch mode (§10 human⇄AI edit loop).

Every mature build system ships one: `watchexec`, `jest --watch`, `cargo
watch`, `tsc --watch`, `vite`. The pattern is always the same takeover: a human
keeps their editor open on `shots/*.yaml`, a *second* terminal runs the
watcher, and every time the truth on disk moves the watcher re-runs the cheap
verifier and prints a one-line verdict. It collapses the "edit → alt-tab → run
`manju check` → read → alt-tab back" loop into "edit → glance at the other
pane". The AI half of §5 gets the same benefit: an agent editing shots sees its
own check verdicts stream by without shelling out after every patch.

Why fingerprint-poll and NOT inotify/watchdog:

* **stdlib-only (§1-④).** A filesystem watcher means either `watchdog` (a
  third-party dep with C-extension build surface) or hand-rolled
  `inotify`/`FSEvents`/`ReadDirectoryChangesW` bindings. Polling a hash needs
  nothing but `time`, `hashlib` and `os` — all stdlib.
* **portability.** inotify is Linux-only; kqueue is BSD/macOS; the Windows API
  is a third thing. Manju targets Chinese/Windows desktops as first-class (§14).
  A stat-based fingerprint behaves identically on every platform and over the
  network filesystems where inotify silently misses events.

The fingerprint mostly stats files (name, mtime_ns, size) and never probes
media, so polling stays strictly cheaper than the `run_check` it gates —
watching the project must never be more expensive than looking at it. The file
set mirrors OUR CompileInput's truth (shots + bible + timeline/rules.yaml +
timeline/packaging.yaml — packaging and the audio rules both live under
`timeline/`), plus the finals/proxy/proposals/events another actor touches.

Round-W (§59/§77) widened the file set to close two real gaps:

* EVERY file inside every take dir, RECURSIVELY (not just the take dir's own
  mtime) — a media file or sidecar overwritten IN PLACE (same name, new bytes)
  moves only the FILE's own mtime, never the containing directory's, so the
  old dir-entry-only scan silently missed an external in-place replace (§77).
* `media/refs/**`, recursively — reference images/videos a shot's cloud prompt
  or a keyframe can point at; an externally-replaced ref file moves no truth
  TEXT, so it needs its own coverage (§59). Keyframe image paths beyond the
  refs/ convention are covered too, by the ONE place this fingerprint reads
  shot TEXT (not just stats): a bounded, cheap raw-YAML read of each shot's
  `keyframes:` list (no pydantic validation) — there is no other way to know
  which extra files a shot's keyframes touch, and the read is still strictly
  cheaper than `run_check`, which fully validates every shot.

Interaction with the board server and any build lock: the watcher is strictly
READ-ONLY — it only stats files and calls the read-only `run_check`. It never
acquires a lock, never renders, and never mutates truth, so a `manju board
--serve` (or a concurrent build holding `.manju/build.lock`) and this watcher
cannot contend. A running build merely produces new files, which the watcher
observes as ordinary change ticks.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..core.check import run_check

if TYPE_CHECKING:
    from ..core.container import Project

__all__ = ["WatchTick", "watch_ticks", "project_fingerprint"]


@dataclass
class WatchTick:
    """One emitted change verdict. Every yielded tick is a *change*
    (`changed=True`) — the loop stays silent on unchanged polls."""

    fingerprint: str
    changed: bool
    check_ok: bool | None      # None when run_check raised (torn mid-editor write)
    errors: list[str]          # run_check errors (empty when ok)
    warnings: list[str]
    ts: str                    # iso utc, seconds precision


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _feed_keyframe_images(project: "Project", feed_file) -> None:
    """Local files referenced by every shot's ``keyframes:`` list (round-W
    #59). A RAW-YAML read per shot (``load_shot_raw`` — dict, no pydantic
    model construction/validation) is the one place this fingerprint reads
    shot TEXT rather than just stats: there is no other way to know which
    extra files a shot's keyframes point at, and this read is still far
    cheaper than `run_check`'s full per-shot model validation. A bible-asset-
    id or ``http(s)://`` keyframe has no local file to stat (an asset id's own
    ref_image already rides bible/*.yaml's mtime; a URL is never local, and by
    convention ref images live under media/refs/, already covered above) — a
    torn/unreadable shot file is skipped, never raised, matching the
    watcher's total-function contract."""
    try:
        shot_ids = project.shot_ids()
    except OSError:
        return
    for sid in shot_ids:
        try:
            raw = project.load_shot_raw(sid)
        except Exception:
            continue
        keyframes = raw.get("keyframes") if isinstance(raw, dict) else None
        if not isinstance(keyframes, list):
            continue
        for kf in keyframes:
            if not isinstance(kf, dict):
                continue
            image = kf.get("image")
            if not isinstance(image, str) or not image:
                continue
            if image.startswith(("http://", "https://")):
                continue
            try:
                path = Path(image)
                if not path.is_absolute():
                    path = project.resolve(image)
            except Exception:
                continue
            feed_file(path)


def project_fingerprint(project: "Project") -> str:
    """A cheap change signal: sha1 over the (name, mtime_ns, size) of the files
    another actor plausibly touches — truth text (shots + bible), the timeline
    inputs (rules.yaml/packaging.yaml/timeline.json = OUR CompileInput), QC,
    finals, proposals, events, reference media (media/refs/**), every take
    dir's contents RECURSIVELY, and keyframe-referenced local images (see
    :func:`_feed_keyframe_images`). Never probes media; every stat/glob is
    OSError-guarded so a half-written file never raises here."""
    h = hashlib.sha1()

    def feed_file(p) -> None:
        try:
            st = p.stat()
            h.update(f"{p.name}:{st.st_mtime_ns}:{st.st_size};".encode())
        except OSError:
            pass

    def feed_dir(d, pattern: str = "*", *, recursive: bool = False) -> None:
        try:
            glob = d.rglob(pattern) if recursive else d.glob(pattern)
            for p in sorted(glob):
                if p.is_file():
                    feed_file(p)
        except OSError:
            pass

    feed_file(project.root / "project.yaml")
    feed_file(project.root / "events.jsonl")
    feed_dir(project.shots_dir, "*.yaml")
    feed_dir(project.root / "bible", "*.yaml")
    # OUR CompileInput truth: rules.yaml (incl. audio rules) + packaging.yaml +
    # the compiled/generated timeline all live under timeline/.
    feed_dir(project.root / "timeline")
    feed_dir(project.captions_dir)  # captions.srt / captions.ass / .generated.srt
    feed_file(project.reports_dir / "qc.json")
    feed_dir(project.final_dir, "final_v*.mp4")
    feed_dir(project.proxy_dir)
    feed_dir(project.proposals_dir, "*.md")
    # round-W #59: reference media a shot's cloud prompt/keyframe can point
    # at — recursive, so an externally-replaced ref image/video (same path,
    # new bytes) moves the fingerprint even though no truth TEXT changed.
    feed_dir(project.refs_dir, "*", recursive=True)
    # informational: a build lock is a file another actor may hold; observing it
    # move is harmless (the watcher never takes it — see the module docstring).
    feed_file(project.runtime_dir / "build.lock")
    # round-W #77: EVERY file inside every take dir, recursively — a media
    # file or sidecar overwritten IN PLACE (same name, new bytes) moves only
    # the FILE's own mtime, never the containing directory's, so the old
    # dir-entry-only scan (mtime of the take dir itself) silently missed it.
    feed_dir(project.gen_dir, "*", recursive=True)
    # round-W #59: keyframe-referenced local images — see the helper's
    # docstring for why this is the one place a raw-YAML read happens here.
    _feed_keyframe_images(project, feed_file)
    return h.hexdigest()


def _run_check(project: "Project") -> tuple[bool | None, list[str], list[str]]:
    """run_check, but a raised exception becomes (None, [message], []).

    A watcher must never die on a torn mid-editor write. `run_check` already
    turns most malformed truth into *findings* rather than tracebacks, but the
    contract here is total: whatever it does, the loop keeps polling.
    """
    try:
        report = run_check(project)
    except Exception as exc:  # noqa: BLE001 — the whole point is to never die
        return None, [" ".join(f"check crashed: {exc}".split())], []
    return report.ok, list(report.errors), list(report.warnings)


def watch_ticks(
    project: "Project",
    *,
    interval_s: float = 0.8,
    max_ticks: int | None = None,
) -> Iterator[WatchTick]:
    """Yield a `WatchTick` on the initial state and on every subsequent change.

    The first tick is always emitted (`changed=True`) with a full `run_check`,
    so a watcher started against an already-broken project reports it at once
    instead of waiting for the next edit. Thereafter the loop sleeps
    `interval_s`, recomputes the fingerprint, and yields — re-running
    `run_check` — *only* when the fingerprint moved. Unchanged polls are silent.

    `max_ticks` bounds the total number of *yielded* ticks (drives `--once` and
    keeps tests finite); `None` watches forever. `run_check` exceptions are
    caught into `check_ok=None` with the message in `errors`.
    """
    last_fp = project_fingerprint(project)
    check_ok, errors, warnings = _run_check(project)
    yield WatchTick(last_fp, True, check_ok, errors, warnings, _now())
    yielded = 1
    if max_ticks is not None and yielded >= max_ticks:
        return

    while True:
        time.sleep(interval_s)
        fp = project_fingerprint(project)
        if fp == last_fp:
            continue
        last_fp = fp
        check_ok, errors, warnings = _run_check(project)
        yield WatchTick(fp, True, check_ok, errors, warnings, _now())
        yielded += 1
        if max_ticks is not None and yielded >= max_ticks:
            return
