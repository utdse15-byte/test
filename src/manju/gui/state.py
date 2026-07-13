"""Assemble the consolidated /api/state payload for the GUI.

One GET drives the whole page: project meta, status summary, per-shot cards
(takes, voice takes, locks), QC, events tail and the job list. Everything here
is READ-ONLY and cheap by construction — no ffprobe, no timeline compile, no
content keys: take durations come from the sidecars' cached probe data, and
the expensive explainer (§ `manju explain`, which recompiles + hashes) stays
behind its own on-demand endpoint. This keeps a polling UI honest: watching
the project must never mutate it or make it slower.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..build.stale import evaluate_all
from ..build.status import project_status
from ..core.events import tail_events
from ..core.hashing import short_hash
from ..core.yamlio import read_json

if TYPE_CHECKING:
    from ..core.container import Project

    from .jobs import JobRunner

__all__ = ["build_state", "media_urls_for_rel", "project_fingerprint"]

_EVENTS_TAIL = 15


def project_fingerprint(project: "Project", runner: "JobRunner") -> str:
    """A cheap change signal for the /api/watch long-poll: sha1 over the
    (name, mtime_ns, size) of the files another actor plausibly touches —
    truth text, timeline, QC, finals, events — plus per-shot take-dir mtimes
    (a new take changes its directory) and the job-runner revision. Never
    probes media, never parses YAML: watching must stay cheaper than looking.
    """
    import hashlib
    import os

    h = hashlib.sha1()

    def feed_file(p: Any) -> None:
        try:
            st = p.stat()
            h.update(f"{p.name}:{st.st_mtime_ns}:{st.st_size};".encode())
        except OSError:
            pass

    def feed_dir(d: Any, pattern: str = "*") -> None:
        try:
            for p in sorted(d.glob(pattern)):
                feed_file(p)
        except OSError:
            pass

    feed_file(project.root / "project.yaml")
    feed_file(project.root / "events.jsonl")
    feed_dir(project.shots_dir, "*.yaml")
    feed_dir(project.root / "bible", "*.yaml")
    feed_dir(project.root / "timeline")
    feed_file(project.reports_dir / "qc.json")
    feed_file(project.reports_dir / "failures.jsonl")  # new failures poll live
    feed_dir(project.final_dir, "final_v*.mp4")
    feed_dir(project.proxy_dir)
    feed_dir(project.proposals_dir, "*.md")
    # deliberately NOT state.sqlite: merely OPENING the ledger (as build_state
    # does) touches it, which would make the fingerprint self-invalidating;
    # ledger spend only moves alongside events/renders changes, already fed
    feed_file(project.runtime_dir / "build.lock")
    try:  # take dirs: mtime moves on add/remove, which is all we need
        with os.scandir(project.gen_dir) as it:
            for entry in sorted(it, key=lambda e: e.name):
                h.update(f"{entry.name}:{entry.stat().st_mtime_ns};".encode())
    except OSError:
        pass
    h.update(f"jobs:{runner.revision()}".encode())
    return h.hexdigest()


def media_url(rel: str) -> str:
    """Project-relative path -> /media URL (CJK-safe percent encoding)."""
    return "/media/" + quote(rel, safe="/")


def playable_url(project: "Project", path: Any) -> tuple[str, str]:
    """(url, ext) for a media file, routed through the lazy browser-preview
    transcode when the codec/container is not browser-safe (.mkv/.flac/…).
    The substitution is transparent: the page just plays whatever URL it got;
    /preview transcodes once into the disposable cache and falls back to the
    raw bytes if ffmpeg is unavailable."""
    rel = project.relpath(path)
    ext = path.suffix.lstrip(".").lower()
    try:
        from ..media.webpreview import needs_preview

        if needs_preview(path):
            return "/preview/" + quote(rel, safe="/"), "mp4"
    except Exception:
        pass  # preview layer is a convenience, never a requirement
    return media_url(rel), ext


def media_urls_for_rel(rel: str | None) -> tuple[str | None, str | None]:
    """(preview_url, thumb_url) for a project-relative media path that is
    already known (round AA6, the ingest batch review view) — e.g. a batch
    item's ``landed`` take/ref/import path, which is stored as a plain
    ``project.relpath(...)`` STRING in ``reports/ingest_batches/<id>.yaml``,
    never a live ``Path``. Same routing convention as :func:`playable_url` /
    ``pages._take_media`` (``/preview`` transcode for a non-browser-safe
    container, ``/thumb`` for a video/image, never invented for audio or an
    unrecognized extension) — just working off the stored string directly
    instead of re-deriving it from an on-disk file, since a batch item's
    source file may not even exist as a Path handle at read time (only its
    already-landed project-relative copy does). ``(None, None)`` when *rel*
    itself is falsy (e.g. a skipped/failed row landed nothing) — this never
    invents a thumbnailer for a media kind the project has no preview path
    for; the caller renders that as no thumbnail, not an error."""
    if not rel:
        return None, None
    from pathlib import Path

    from ..media.webpreview import AUDIO_EXTS, MEDIA_EXTS, needs_preview

    suffix = Path(rel).suffix.lower()
    url = ("/preview/" if needs_preview(Path(rel)) else "/media/") + quote(rel, safe="/")
    thumb = "/thumb/" + quote(rel, safe="/") if suffix in MEDIA_EXTS and suffix not in AUDIO_EXTS else None
    return url, thumb


def _take_card(project: "Project", shot_id: str, take: Any, selected: str | None,
               notes: dict[str, str] | None = None) -> dict[str, Any]:
    sc = take.sidecar
    duration = sc.probe.duration_ms if (sc.probe and sc.probe.duration_ms) else None
    url = poster = thumb = None
    ext = ""
    if take.media_path is not None:
        url, ext = playable_url(project, take.media_path)
        if take.media_path.suffix.lower() not in (".wav", ".mp3", ".m4a", ".flac"):
            thumb = "/thumb/" + quote(project.relpath(take.media_path), safe="/")
    is_selected = take.name == selected
    if is_selected:
        frame = project.reports_dir / "frames" / f"{shot_id}.jpg"
        if frame.exists():
            poster = media_url(project.relpath(frame))
    return {
        "name": take.name,
        "provider": sc.provider,
        "spec_hash": short_hash(sc.spec_hash, 12),
        "duration_ms": duration,
        "url": url,
        "poster": poster or thumb,
        "thumb": thumb,
        "selected": is_selected,
        "ext": ext,
        "note": (notes or {}).get(take.name),
        # recipe travels with the output (Runway pattern): enough lineage to
        # redo with the same provider/seed in one click
        "seed": sc.params.get("seed") if isinstance(sc.params, dict) else None,
    }


def _voice_cards(project: "Project", shot_id: str) -> list[dict[str, Any]]:
    cards = []
    for media, sidecar in project.voice_takes(shot_id):
        cards.append({
            "name": media.stem,
            "url": playable_url(project, media)[0],
            "manual": sidecar is None,
        })
    return cards


def _shot_cards(project: "Project", statuses: Any = None,
                voices: Any = None) -> list[dict[str, Any]]:
    """``statuses`` / ``voices`` (G2): the RAW ``evaluate_all`` /
    ``evaluate_all_voices`` passes ``build_state`` already ran — threaded in so
    this does not recompute them a second time. Both default to ``None`` →
    computed here, so ``_shot_cards`` stays callable standalone."""
    voice_map: dict[str, Any] = {}
    try:  # advisory, like everywhere else the voice layer is consulted
        from ..build.voice import VoiceState, evaluate_all_voices

        voice_list = voices if voices is not None else evaluate_all_voices(project)
        voice_map = {v.shot_id: v for v in voice_list
                     if v.state != VoiceState.NOT_NEEDED}
    except Exception:
        voice_map = {}

    if statuses is None:
        statuses = evaluate_all(project)
    cards: list[dict[str, Any]] = []
    for st in statuses:
        action = speaker = dialogue = ""
        locked: list[str] = []
        take_notes: dict[str, str] = {}
        try:
            raw = project.load_shot_raw(st.shot_id)
            shot = project.load_shot(st.shot_id)
            action = shot.action.main
            speaker = shot.dialogue.speaker or ""
            dialogue = shot.dialogue.text or ""
            take_notes = dict(shot.status.take_notes)
            raw_locked = raw.get("locked")
            if isinstance(raw_locked, dict):
                locked = sorted(raw_locked)
            elif isinstance(raw_locked, list):
                locked = sorted(str(f) for f in raw_locked)
        except Exception:
            pass  # a broken shot file still gets a card (state says why)

        voice = voice_map.get(st.shot_id)
        cards.append({
            "id": st.shot_id,
            "state": st.state.value,
            "note": st.note,
            "action": action,
            "speaker": speaker,
            "dialogue": dialogue,
            "selected_take": st.selected_take,
            "spec_hash": short_hash(st.spec_hash, 12),
            "locked": locked,
            "voice": ({"state": voice.state.value, "why": voice.note or None}
                      if voice is not None else None),
            "takes": [_take_card(project, st.shot_id, t, st.selected_take, take_notes)
                      for t in project.takes(st.shot_id)],
            "voice_takes": _voice_cards(project, st.shot_id),
        })
    return cards


def _qc_summary(project: "Project") -> dict[str, Any] | None:
    qc_path = project.reports_dir / "qc.json"
    if not qc_path.exists():
        return None
    try:
        data = read_json(qc_path)
    except Exception:
        return {"ok": None, "errors": 0, "warnings": 0, "items": [
            {"level": "error", "message": "qc.json is unreadable", "shot": None,
             "suggestion": "re-run manju qc"}]}
    items_raw = data.get("items", []) if isinstance(data, dict) else []
    items = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        items.append({
            "level": str(it.get("level", "info")),
            "area": str(it.get("area", "")),
            "message": str(it.get("message", "")),
            "shot": it.get("subject") or None,
            "suggestion": it.get("suggestion") or None,
        })
    return {
        "ok": data.get("ok") if isinstance(data, dict) else None,
        "errors": sum(1 for i in items if i["level"] == "error"),
        "warnings": sum(1 for i in items if i["level"] == "warn"),
        "items": items,
    }


def _failures(project: "Project") -> list[dict[str, Any]]:
    """Recent structured failures (goal 10), newest first, for the header-
    adjacent 失败 cards. Full records — evidence/hint/log_path included — so
    a card expands without a second fetch; rides the state poll (failures.jsonl
    is in the fingerprint). Read-only and best-effort."""
    try:
        from ..core.failures import read_failures

        return read_failures(project, n=30)
    except Exception:
        return []


def build_state(project: "Project", runner: "JobRunner") -> dict[str, Any]:
    # G2: run the stale + voice evaluation ONCE and thread the results through
    # BOTH project_status(...) and _shot_cards(...) — before, each recomputed
    # them independently (evaluate_all + evaluate_all_voices ran twice per
    # build). Pure plumbing: the /api/state payload shape is identical.
    statuses = evaluate_all(project)
    voices: Any = None
    try:
        from ..build.voice import evaluate_all_voices

        voices = evaluate_all_voices(project)
    except Exception:
        voices = None  # advisory; project_status/_shot_cards recompute-and-catch

    status = project_status(project, statuses=statuses, voices=voices)
    config = project.load_config()

    latest_final = None
    if status.get("latest_final"):
        latest_final = {"path": status["latest_final"],
                        "url": media_url(status["latest_final"])}

    # version stack (Frame.io pattern): newest first, append-only lineage.
    # Round W (issue #70): sort NUMERICALLY (final_v10 beats final_v9) — a
    # lexicographic sort put final_v9 ahead of final_v10 once a project passed
    # 9 renders, both misordering the list and (with the [:10] cap below)
    # potentially dropping the true newest finals off the end entirely.
    def _final_version(p) -> int:
        m = re.fullmatch(r"final_v(\d+)", p.stem)
        return int(m.group(1)) if m else -1

    finals = []
    for p in sorted(project.final_dir.glob("final_v*.mp4"),
                    key=_final_version, reverse=True)[:10]:
        try:
            st = p.stat()
        except OSError:
            continue
        finals.append({
            "name": p.name,
            "url": media_url(project.relpath(p)),
            "size": st.st_size,
            "mtime": st.st_mtime,
            "has_key": p.with_suffix(".key.json").exists(),  # crashed-render honesty
        })

    return {
        "project": {
            "name": status["project"],
            "resolution": status["resolution"],
            "mode": status["mode"],
            "width": config.width,
            "height": config.height,
            "fps": config.fps,
        },
        "budget": {
            "total_cost": status["total_cost"],
            "currency": status["currency"],
            "limit": status["budget_limit"],
        },
        "next_step": status["next_step"],
        "shots_by_state": status["shots_by_state"],
        "voice_by_state": status["voice_by_state"],
        "timeline": status["timeline"],
        "latest_final": latest_final,
        "finals": finals,
        "latest_final_note": status.get("latest_final_note"),
        "build_lock": status.get("build_lock"),
        "qc": _qc_summary(project),
        "failures": _failures(project),
        "shots": _shot_cards(project, statuses=statuses, voices=voices),
        "events": tail_events(project.root, _EVENTS_TAIL),
        # round AA item 6: interrupted() (a past GUI process's dangling
        # queued/running/canceling jobs, computed once at JobRunner
        # construction) merged into the SAME list as the live jobs,
        # distinguished only by state="interrupted" — the SPA's jobs strip
        # (page.py renderJobs) already iterates this list generically.
        "jobs": [j.to_dict() for j in runner.list()] + runner.interrupted(),
    }
