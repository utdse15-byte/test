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

The fingerprint stats files (name, mtime_ns, size); it never parses YAML or
probes media, so polling stays strictly cheaper than the `run_check` it gates —
watching the project must never be more expensive than looking at it. The file
set mirrors OUR CompileInput's truth (shots + bible + timeline/rules.yaml +
timeline/packaging.yaml — packaging and the audio rules both live under
`timeline/`), plus the finals/proxy/proposals/events another actor touches.

Interaction with the board server and any build lock: the watcher is strictly
READ-ONLY — it only stats files and calls the read-only `run_check`. It never
acquires a lock, never renders, and never mutates truth, so a `manju board
--serve` (or a concurrent build holding `.manju/build.lock`) and this watcher
cannot contend. A running build merely produces new files, which the watcher
observes as ordinary change ticks.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
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


def project_fingerprint(project: "Project") -> str:
    """A cheap change signal: sha1 over the (name, mtime_ns, size) of the files
    another actor plausibly touches — truth text (shots + bible), the timeline
    inputs (rules.yaml/packaging.yaml/timeline.json = OUR CompileInput), QC,
    finals, proposals, events — plus per-shot take-dir mtimes (a new take
    changes its directory). Never probes media, never parses YAML; every
    stat/glob is OSError-guarded so a half-written file never raises here."""
    h = hashlib.sha1()

    def feed_file(p) -> None:
        try:
            st = p.stat()
            h.update(f"{p.name}:{st.st_mtime_ns}:{st.st_size};".encode())
        except OSError:
            pass

    def feed_dir(d, pattern: str = "*") -> None:
        try:
            for p in sorted(d.glob(pattern)):
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
    feed_dir(project.captions_dir)
    feed_file(project.reports_dir / "qc.json")
    feed_dir(project.final_dir, "final_v*.mp4")
    feed_dir(project.proxy_dir)
    feed_dir(project.proposals_dir, "*.md")
    # informational: a build lock is a file another actor may hold; observing it
    # move is harmless (the watcher never takes it — see the module docstring).
    feed_file(project.runtime_dir / "build.lock")
    try:  # take dirs: mtime moves on add/remove, which is all we need
        with os.scandir(project.gen_dir) as it:
            for entry in sorted(it, key=lambda e: e.name):
                h.update(f"{entry.name}:{entry.stat().st_mtime_ns};".encode())
    except OSError:
        pass
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
