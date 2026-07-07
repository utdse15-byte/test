"""Version-compare engine (goal item 11, round-S): `manju compare <a> <b>`.

Diff two finals — ``final_v2`` vs ``final_v3`` — the way an editor reads a
version stack in Frame.io: a header of top-line deltas (duration, resolution,
fps) over a strip of per-clip changed/unchanged rows, each row carrying its
before/after and a root cause.

Ground truth is the per-final timeline snapshot ``final_vN.timeline.json`` that
:func:`manju.media.render.render_timeline` persists next to each engine-minted
final (canonical JSON of the compiled timeline it was rendered from). When both
sides have a snapshot, the diff is exact and per-shot:

- per shot: same take / different take (both takes named, with the provider
  behind each from its take sidecar) / added / removed / duration changed /
  order moved;
- captions: how many cues changed, and which;
- audio: bgm / sfx / ambient / voice source or gain changes;
- packaging: intro / outro / branding overlay changes;
- a root-cause line per change, correlated from events.jsonl by time window and
  shot ("S003: take_02→take_04 — redo by ai (2026-07-07)").

A final rendered before snapshots existed has no ``.timeline.json``; the diff
then degrades honestly to "keys differ; per-shot detail unavailable (pre-S
final)", still reporting whether the content keys match and a best-effort
probed duration/resolution delta.

The JSON shape is designed for the GUI diff page (wave-2 renders it): a flat
``changes`` list of ``{shot, change, a, b, why}`` rows plus a ``summary``
header and detail blocks (``captions`` / ``audio`` / ``packaging``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.events import tail_events

# Packaging pseudo-shots live on the video track as "__intro__" / "__outro__";
# they are routed to the packaging diff, not the story-shot strip.
_PACKAGING_SHOTS = ("__intro__", "__outro__")

# Event actions that can explain a shot changing between two finals (§10).
_CAUSE_ACTIONS = (
    "redo", "generate", "select", "auto_select", "voice", "repair", "rollback_shot",
)


class CompareError(RuntimeError):
    pass


# --------------------------------------------------------------- final resolution


def _version_of(name: str) -> int | None:
    """Version number from a final reference: ``final_v3`` / ``v3`` / ``3``."""
    m = re.fullmatch(r"(?:final_)?v?(\d+)", name.strip())
    return int(m.group(1)) if m else None


def _all_finals(project: Project) -> list[tuple[int, Path]]:
    """(version, path) for every ``final_vN.mp4``, ascending by version."""
    out = [
        (int(m.group(1)), p)
        for p in project.final_dir.glob("final_v*.mp4")
        if (m := re.fullmatch(r"final_v(\d+)", p.stem))
    ]
    return sorted(out, key=lambda t: t[0])


def resolve_final(project: Project, ref: str) -> Path:
    """A final reference (``final_v3`` / ``v3`` / ``3``) → its mp4 path."""
    version = _version_of(ref)
    if version is None:
        raise CompareError(
            f"not a final name: {ref!r} (expected final_vN, e.g. final_v3)"
        )
    path = project.final_dir / f"final_v{version}.mp4"
    if not path.exists():
        have = ", ".join(f"final_v{v}" for v, _ in _all_finals(project)) or "(none)"
        raise CompareError(f"no such final: final_v{version} — have: {have}")
    return path


def _default_pair(project: Project) -> tuple[Path, Path]:
    """The latest two finals (older = a, newer = b)."""
    finals = _all_finals(project)
    if len(finals) < 2:
        raise CompareError(
            "need at least two finals to compare — build again to mint another "
            f"(have {len(finals)}). 用法 usage: manju compare final_v2 final_v3"
        )
    return finals[-2][1], finals[-1][1]


# ------------------------------------------------------------------- sidecars


def _load_snapshot(final_path: Path) -> dict[str, Any] | None:
    """The per-final timeline snapshot as a raw dict (robust to schema drift —
    we read old snapshots without binding them to the current model), or None
    when this final predates snapshots (a pre-S final)."""
    snap = final_path.with_suffix(".timeline.json")
    if not snap.exists():
        return None
    try:
        data = json.loads(snap.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _load_key(final_path: Path) -> str | None:
    sidecar = final_path.with_suffix(".key.json")
    if not sidecar.exists():
        return None
    try:
        return str(json.loads(sidecar.read_text(encoding="utf-8")).get("final_key") or "") or None
    except (json.JSONDecodeError, OSError):
        return None


def _created_at(final_path: Path) -> str | None:
    """When this final was rendered — the key sidecar's ``created_at`` (written
    with the mp4), falling back to the mp4's mtime as an ISO string."""
    sidecar = final_path.with_suffix(".key.json")
    if sidecar.exists():
        try:
            ts = json.loads(sidecar.read_text(encoding="utf-8")).get("created_at")
            if ts:
                return str(ts)
        except (json.JSONDecodeError, OSError):
            pass
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(
            final_path.stat().st_mtime, timezone.utc
        ).isoformat(timespec="seconds")
    except OSError:
        return None


# --------------------------------------------------------------- side helpers


def _video_map(snap: dict[str, Any]) -> dict[str, tuple[int, dict]]:
    """shot id → (order index, clip dict) for the video track."""
    clips = (snap.get("tracks") or {}).get("video") or []
    return {c.get("shot"): (i, c) for i, c in enumerate(clips) if c.get("shot")}


def _provider_for(project: Project, shot: str, take: str) -> str | None:
    """The provider behind a shot's take, from its sidecar. Packaging cards
    carry no take sidecar; a take whose sidecar was gc'd resolves to None."""
    if not take or take == "packaging":
        return "packaging" if take == "packaging" else None
    try:
        info = project.get_take(shot, take)
    except Exception:
        return None
    return info.sidecar.provider if info is not None else None


def _side(project: Project, shot: str, entry: tuple[int, dict] | None) -> dict | None:
    """The a-side / b-side payload for one shot on the GUI diff row."""
    if entry is None:
        return None
    order, clip = entry
    take = clip.get("take", "")
    return {
        "take": take,
        "provider": _provider_for(project, shot, take),
        "duration_ms": clip.get("duration_ms"),
        "source": clip.get("source"),
        "order": order,
    }


# --------------------------------------------------------------- root cause


def _root_cause(events: list[dict], shot: str, since: str | None, until: str | None) -> str:
    """One human root-cause line for a shot's change, correlated from
    events.jsonl: the last relevant event touching ``shot`` in the render
    window ``(since, until]`` — e.g. "redo by ai (2026-07-07)"."""
    best: dict | None = None
    for e in events:
        action = e.get("action")
        if action not in _CAUSE_ACTIONS:
            continue
        detail = e.get("detail") or {}
        if detail.get("shot") != shot:
            continue
        ts = str(e.get("ts") or "")
        if since is not None and ts <= since:
            continue
        if until is not None and ts > until:
            continue
        best = e  # events are oldest→newest, so the last match is the closest
    if best is None:
        return ""
    actor = best.get("actor") or "?"
    day = str(best.get("ts") or "")[:10]
    return f"{best.get('action')} by {actor}" + (f" ({day})" if day else "")


# --------------------------------------------------------------- captions/audio


def _cue_key(cue: dict) -> tuple:
    return (cue.get("start_ms"), cue.get("end_ms"), cue.get("text"))


def _caption_cue(cue: dict) -> dict:
    return {"start_ms": cue.get("start_ms"), "end_ms": cue.get("end_ms"),
            "text": cue.get("text"), "speaker": cue.get("speaker", "")}


def _captions_diff(snap_a: dict, snap_b: dict) -> dict:
    """Count + which cues changed, aligned by index (Frame.io per-cue strip)."""
    caps_a = (snap_a.get("tracks") or {}).get("captions") or []
    caps_b = (snap_b.get("tracks") or {}).get("captions") or []
    cues: list[dict] = []
    for i in range(max(len(caps_a), len(caps_b))):
        ca = caps_a[i] if i < len(caps_a) else None
        cb = caps_b[i] if i < len(caps_b) else None
        if ca is not None and cb is not None:
            if _cue_key(ca) == _cue_key(cb):
                continue
            change = "changed"
        elif cb is not None:
            change = "added"
        else:
            change = "removed"
        cues.append({
            "index": i,
            "change": change,
            "a": _caption_cue(ca) if ca is not None else None,
            "b": _caption_cue(cb) if cb is not None else None,
        })
    return {
        "changed": bool(cues),
        "a_count": len(caps_a),
        "b_count": len(caps_b),
        "cues": cues,
    }


def _audio_repr(clips: list[dict]) -> list[dict]:
    """Salient, order-stable representation of an audio track for diffing."""
    return [{"source": c.get("source"), "gain_db": c.get("gain_db"),
             "start_ms": c.get("start_ms"), "duration_ms": c.get("duration_ms")}
            for c in clips]


def _audio_diff(snap_a: dict, snap_b: dict) -> dict:
    """bgm / sfx / ambient / voice source-or-gain changes (§7 ⑤)."""
    tracks_a = snap_a.get("tracks") or {}
    tracks_b = snap_b.get("tracks") or {}
    changes: list[dict] = []
    for track, label in (("music", "bgm"), ("sfx", "sfx"),
                         ("ambient", "ambient"), ("voice", "voice")):
        ra = _audio_repr(tracks_a.get(track) or [])
        rb = _audio_repr(tracks_b.get(track) or [])
        if ra == rb:
            continue
        changes.append({
            "track": label,
            "a": {"count": len(ra), "clips": ra},
            "b": {"count": len(rb), "clips": rb},
        })
    return {"changed": bool(changes), "tracks": changes}


# --------------------------------------------------------------- packaging


_OVERLAY_KINDS = ("title_card", "info_card", "logo", "watermark", "badge", "cta")


def _overlay_repr(ov: dict) -> dict:
    return {"kind": ov.get("kind"), "text": ov.get("text", ""),
            "source": ov.get("source", ""), "corner": ov.get("corner", ""),
            "position": ov.get("position", ""), "start_ms": ov.get("start_ms"),
            "duration_ms": ov.get("duration_ms"), "size_pct": ov.get("size_pct", 0.0),
            "opacity": ov.get("opacity", 1.0)}


def _packaging_diff(snap_a: dict, snap_b: dict) -> dict:
    """intro / outro (video pseudo-shots) + branding overlay changes."""
    va, vb = _video_map(snap_a), _video_map(snap_b)
    changes: list[dict] = []

    for pkg_shot, label in ((("__intro__"), "intro"), (("__outro__"), "outro")):
        ea, eb = va.get(pkg_shot), vb.get(pkg_shot)
        if ea is None and eb is None:
            continue

        def _card(entry):
            return None if entry is None else {
                "source": entry[1].get("source"), "duration_ms": entry[1].get("duration_ms")}

        ca, cb = _card(ea), _card(eb)
        if ca == cb:
            continue
        change = ("added" if ca is None else "removed" if cb is None else "changed")
        changes.append({"item": label, "change": change, "a": ca, "b": cb})

    overlays_a = (snap_a.get("tracks") or {}).get("overlay") or []
    overlays_b = (snap_b.get("tracks") or {}).get("overlay") or []

    def _by_kind(overlays):
        grouped: dict[str, list[dict]] = {}
        for ov in overlays:
            grouped.setdefault(ov.get("kind"), []).append(_overlay_repr(ov))
        return grouped

    ga, gb = _by_kind(overlays_a), _by_kind(overlays_b)
    for kind in _OVERLAY_KINDS:
        la, lb = ga.get(kind, []), gb.get(kind, [])
        if la == lb:
            continue
        change = ("added" if not la else "removed" if not lb else "changed")
        changes.append({"item": kind, "change": change,
                        "a": la or None, "b": lb or None})

    return {"changed": bool(changes), "items": changes}


# --------------------------------------------------------------- main


def _summary(snap_a: dict | None, snap_b: dict | None,
             probe_a: dict | None, probe_b: dict | None) -> dict:
    """Top-line header deltas. Prefers the exact snapshot values; falls back to
    a best-effort ffprobe of the mp4 on the degraded path."""
    def dim(snap, probe):
        if snap is not None:
            return (snap.get("width"), snap.get("height"), snap.get("fps"),
                    snap.get("duration_ms"))
        if probe is not None:
            return (probe.get("width"), probe.get("height"), probe.get("fps"),
                    probe.get("duration_ms"))
        return (None, None, None, None)

    wa, ha, fa, da = dim(snap_a, probe_a)
    wb, hb, fb, db = dim(snap_b, probe_b)
    res_a = f"{wa}x{ha}" if wa and ha else None
    res_b = f"{wb}x{hb}" if wb and hb else None
    return {
        "duration_a_ms": da,
        "duration_b_ms": db,
        "duration_delta_ms": (db - da) if (da is not None and db is not None) else None,
        "resolution_a": res_a,
        "resolution_b": res_b,
        "resolution_changed": res_a != res_b,
        "fps_a": fa,
        "fps_b": fb,
        "fps_changed": fa != fb,
    }


def _probe_final(final_path: Path) -> dict | None:
    """Best-effort ffprobe of a final on the degraded path (no snapshot)."""
    try:
        from ..media.probe import probe

        info = probe(final_path)
        return {"width": info.width, "height": info.height,
                "fps": info.fps, "duration_ms": info.duration_ms}
    except Exception:
        return None


def compare_finals(project: Project, a: str | None = None, b: str | None = None) -> dict:
    """Compare two finals. ``a``/``b`` are final names (``final_v2``); default
    is the latest two. Returns the GUI-ready diff dict documented at module top."""
    if (a is None) != (b is None):
        raise CompareError("give both final names or neither (default: latest two)")
    if a is None:
        path_a, path_b = _default_pair(project)
    else:
        path_a, path_b = resolve_final(project, a), resolve_final(project, b)

    key_a, key_b = _load_key(path_a), _load_key(path_b)
    created_a, created_b = _created_at(path_a), _created_at(path_b)
    snap_a, snap_b = _load_snapshot(path_a), _load_snapshot(path_b)

    def head(path, key, created, snap):
        return {
            "name": path.stem,
            "key": key,
            "created_at": created,
            "has_snapshot": snap is not None,
        }

    result: dict[str, Any] = {
        "a": head(path_a, key_a, created_a, snap_a),
        "b": head(path_b, key_b, created_b, snap_b),
        "identical": key_a is not None and key_a == key_b,
        "degraded": snap_a is None or snap_b is None,
        "note": "",
        "changes": [],
        "captions": {"changed": False, "a_count": 0, "b_count": 0, "cues": []},
        "audio": {"changed": False, "tracks": []},
        "packaging": {"changed": False, "items": []},
    }

    # ---- degraded: at least one final predates timeline snapshots -----------
    if snap_a is None or snap_b is None:
        which = [h for h, s in (("a", snap_a), ("b", snap_b)) if s is None]
        result["note"] = (
            "keys differ; per-shot detail unavailable (pre-S final: "
            + ", ".join(result[w]["name"] for w in which)
            + " has no timeline snapshot)"
        ) if not result["identical"] else (
            "content keys match — the two finals are byte-identical renders; "
            "per-shot detail unavailable (pre-S final)"
        )
        result["summary"] = _summary(
            snap_a, snap_b, _probe_final(path_a), _probe_final(path_b)
        )
        return result

    # ---- full per-shot diff -------------------------------------------------
    result["summary"] = _summary(snap_a, snap_b, None, None)
    events = tail_events(project.root, 100000)

    va, vb = _video_map(snap_a), _video_map(snap_b)
    story_shots = [s for s in va if s not in _PACKAGING_SHOTS]
    story_shots += [s for s in vb if s not in _PACKAGING_SHOTS and s not in va]
    # b's order first (what the newer film shows), then any a-only shots.
    order_index = {s: (vb.get(s) or va.get(s))[0] for s in story_shots}
    story_shots.sort(key=lambda s: order_index[s])

    shots_changed = 0
    for shot in story_shots:
        ea, eb = va.get(shot), vb.get(shot)
        if ea is not None and eb is None:
            change = "removed"
        elif eb is not None and ea is None:
            change = "added"
        else:
            take_a, take_b = ea[1].get("take"), eb[1].get("take")
            if take_a != take_b:
                change = "take_changed"
            elif ea[1].get("duration_ms") != eb[1].get("duration_ms"):
                change = "duration_changed"
            elif ea[0] != eb[0]:
                change = "moved"
            else:
                change = "unchanged"
        why = "" if change == "unchanged" else _root_cause(events, shot, created_a, created_b)
        result["changes"].append({
            "shot": shot,
            "change": change,
            "a": _side(project, shot, ea),
            "b": _side(project, shot, eb),
            "why": why,
        })
        if change != "unchanged":
            shots_changed += 1

    # ---- captions / audio / packaging --------------------------------------
    result["captions"] = _captions_diff(snap_a, snap_b)
    result["audio"] = _audio_diff(snap_a, snap_b)
    result["packaging"] = _packaging_diff(snap_a, snap_b)

    # Fold the detail blocks into the flat GUI ``changes`` strip as summary rows.
    if result["captions"]["changed"]:
        caps = result["captions"]
        n = len(caps["cues"])
        result["changes"].append({
            "shot": "captions",
            "change": "captions_changed",
            "a": {"count": caps["a_count"]},
            "b": {"count": caps["b_count"]},
            "why": f"{n} cue(s) changed",
        })
    for tr in result["audio"]["tracks"]:
        result["changes"].append({
            "shot": f"audio/{tr['track']}",
            "change": "audio_changed",
            "a": tr["a"], "b": tr["b"], "why": "",
        })
    for item in result["packaging"]["items"]:
        result["changes"].append({
            "shot": f"packaging/{item['item']}",
            "change": f"packaging_{item['change']}",
            "a": item["a"], "b": item["b"], "why": "",
        })

    result["summary"]["shots_changed"] = shots_changed
    result["summary"]["captions_changed"] = result["captions"]["changed"]
    result["summary"]["audio_changed"] = result["audio"]["changed"]
    result["summary"]["packaging_changed"] = result["packaging"]["changed"]
    return result
