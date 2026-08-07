"""Round-trip: external edits flow back as reviewable truth changes (WP6).

v1 carriers: JianYing **diff-stable skeleton** and **OTIO** only.
Native/encrypted drafts are NOT parsed (honest scope).

    plan_roundtrip(project, edited_path) -> plan dict
    apply_roundtrip(project, plan, rows) -> batch result

Diff is against the export baseline (``exports/<kind>/.baseline/<name>.json``),
not live truth. Conflicting rows (truth moved since export) are refused.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.container import Project, ProjectError
from ..core.events import append_event
from ..core.yamlio import atomic_write_text, read_yaml, write_yaml


#: Stable machine token for "the baseline file EXISTS but cannot be used".
#: That is a different state from "there is no baseline" and the two may never
#: be merged: no-baseline is a legal mode, an unusable baseline is a refusal.
BASELINE_CORRUPT = "roundtrip_baseline_corrupt"


class RoundtripBaselineError(ProjectError):
    """Subclass so every existing ``except ProjectError`` site (CLI, GUI) keeps
    catching it unchanged, while carrying the stable ``reason`` token."""

    def __init__(self, message: str, *, reason: str = BASELINE_CORRUPT) -> None:
        super().__init__(message)
        self.reason = reason


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_baseline(path: Path) -> dict[str, Any]:
    """Read a baseline payload, failing CLOSED. Only callable when the file is
    known to exist — an unreadable/unparsable/wrong-shaped payload refuses."""
    try:
        payload = _load_json(path)
    except (OSError, ValueError) as exc:
        raise RoundtripBaselineError(
            f"{BASELINE_CORRUPT}: 基线文件存在但无法解析: {path} ({exc})"
        ) from exc
    if not isinstance(payload, dict):
        raise RoundtripBaselineError(
            f"{BASELINE_CORRUPT}: 基线文件内容不是合法的基线负载: {path}"
        )
    doc = payload.get("document", payload)
    if not isinstance(doc, dict):
        raise RoundtripBaselineError(
            f"{BASELINE_CORRUPT}: 基线文件缺少可用的 document: {path}"
        )
    return payload


def detect_kind(data: Any, path: Path) -> str:
    """Return 'jianying' | 'otio' | raise."""
    if isinstance(data, dict):
        if "OTIO_SCHEMA" in data or data.get("schema") == "OpenTimelineIO.v1":
            return "otio"
        if "tracks" in data and isinstance(data.get("tracks"), list):
            # OTIO sometimes
            if any(
                isinstance(t, dict) and "OTIO_SCHEMA" in t
                for t in data.get("tracks") or []
            ):
                return "otio"
        # JianYing skeleton: materials + tracks with uuid5-ish ids
        if "materials" in data or "tracks" in data:
            if "canvas_config" in data or "fps" in data or "duration" in data:
                return "jianying"
            # OTIO timeline has name/tracks
            if data.get("name") is not None and "tracks" in data:
                return "otio"
    raise ProjectError(
        f"无法识别 round-trip 载体: {path.name} — 仅支持 JianYing skeleton "
        "draft_content.json 与 OTIO JSON(编辑 skeleton/OTIO 以支持回环)"
    )


def find_baseline(project: Project, edited: Path) -> Path | None:
    """Locate exports/<kind>/.baseline/<name>.json near the edited file.

    Two real layouts (see the exporters):

    - OTIO:     exports/otio/<name>.otio        + exports/otio/.baseline/<name>.json
      → the edited file's own STEM names the baseline;
    - JianYing: exports/jianying/<name>/draft_content.json
                + exports/jianying/.baseline/<name>.json
      → the DRAFT DIRECTORY's name (not the file stem) names the baseline,
      one level further up. The old stem-only walk never matched it, silently
      disabling caption-edit detection and truth-moved conflict refusal for
      the whole JianYing round-trip.
    """
    edited = Path(edited)
    # candidate baseline file names, most specific first
    names = [f"{edited.stem}.json"]
    if edited.parent.name and edited.parent.name != edited.stem:
        names.append(f"{edited.parent.name}.json")  # draft-dir mapping (JianYing)
    # Walk up looking for a .baseline sibling
    for parent in [edited.parent, *edited.parents]:
        for name in names:
            cand = parent / ".baseline" / name
            if cand.exists():
                return cand
    # Project-wide search under exports/
    exports = project.root / "exports"
    if exports.exists():
        for p in sorted(exports.rglob(".baseline"), key=lambda q: q.as_posix()):
            for name in names:
                cand = p / name
                if cand.exists():
                    return cand
    return None


def _captions_file_hash(project: Project) -> str | None:
    """sha256 of human captions.srt when present (for export-time baseline)."""
    from ..core.hashing import hash_text
    srt = project.captions_dir / "captions.srt"
    if not srt.exists():
        return None
    try:
        return hash_text(srt.read_text(encoding="utf-8"))
    except OSError:
        return None


def write_baseline(project: Project, kind: str, name: str, document: dict,
                   *, compiled_from: str = "") -> Path:
    """Write exports/<kind>/.baseline/<name>.json (derived, regenerable).

    Also records ``captions_hash`` of captions/captions.srt at export time so
    roundtrip caption apply can conflict when human SRT moved since export
    (not only when timeline fingerprint moved).
    """
    d = project.root / "exports" / kind / ".baseline"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"
    payload = {
        "document": document,
        "compiled_from": compiled_from,
        "captions_hash": _captions_file_hash(project),
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "name": name,
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def _normalize_shot_id(raw: Any) -> str | None:
    """Extract a bare shot id from exporter labels like ``S001/take_01`` or
    stamped ``manju.shot`` fields."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s.startswith("__"):
        return None
    # material_name / path style: "S001/take_01" or "S001\\take_01"
    if "/" in s or "\\" in s:
        s = s.replace("\\", "/").split("/")[0].strip()
    return s or None


def _shot_order_from_jianying(data: dict) -> list[str]:
    """Shot order for JianYing skeleton — **track segments first**.

    Reordering in an NLE changes track segment order, NOT the materials
    library order. Preferring materials.videos first would miss every
    segment-only reorder (WP6 AC: reorder two segments → plan sees reorder).
    Fallback to materials only when no video-track segment carries manju.shot.
    """
    order: list[str] = []
    # 1) Primary: video track segments (edit order)
    for track in (data.get("tracks") or []):
        if not isinstance(track, dict):
            continue
        ttype = str(track.get("type") or "video")
        if ttype in ("audio", "text"):
            continue
        for seg in track.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            meta = seg.get("manju") or {}
            shot = _normalize_shot_id(
                (meta.get("shot") if isinstance(meta, dict) else None)
                or seg.get("shot")
            )
            if shot and shot not in order:
                order.append(shot)
    if order:
        return order
    # 2) Fallback: materials.videos (legacy / missing segment stamps)
    mats = data.get("materials") or {}
    videos = mats.get("videos") or mats.get("speeds") or []
    if isinstance(videos, list):
        for v in videos:
            if not isinstance(v, dict):
                continue
            meta = v.get("extra_info") or v.get("manju") or {}
            shot = None
            if isinstance(meta, dict):
                shot = meta.get("shot") or meta.get("shot_id")
            if not shot:
                shot = v.get("shot") or v.get("material_name") or v.get("name")
            shot = _normalize_shot_id(shot)
            if shot and shot not in order:
                order.append(shot)
    return order


def _otio_track_list(data: dict) -> list[dict]:
    """Normalize OTIO tracks: Timeline.tracks may be a Stack (with children)
    or a bare list of Track objects."""
    tracks = data.get("tracks")
    if tracks is None:
        return []
    if isinstance(tracks, list):
        return [t for t in tracks if isinstance(t, dict)]
    if isinstance(tracks, dict):
        children = tracks.get("children") or []
        return [t for t in children if isinstance(t, dict)]
    return []


def _shot_order_from_otio(data: dict) -> list[str]:
    order: list[str] = []
    for tr in _otio_track_list(data):
        for clip in tr.get("children") or tr.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            meta = clip.get("metadata") or {}
            manju = meta.get("manju") if isinstance(meta, dict) else None
            shot = None
            if isinstance(manju, dict):
                shot = manju.get("shot")
            shot = _normalize_shot_id(shot or clip.get("name"))
            if shot and shot not in order:
                order.append(shot)
    return order


def _captions_from_otio(data: dict) -> list[dict[str, Any]] | None:
    cues = []
    for tr in _otio_track_list(data):
        if not isinstance(tr, dict):
            continue
        name = str(tr.get("name") or "").lower()
        if "caption" not in name and "subtitle" not in name and "text" not in name:
            continue
        for clip in tr.get("children") or []:
            if not isinstance(clip, dict):
                continue
            meta = clip.get("metadata") or {}
            manju = meta.get("manju") if isinstance(meta, dict) else {}
            text = clip.get("name") or (manju or {}).get("text") or ""
            # source_range duration
            sr = clip.get("source_range") or {}
            start = 0
            dur = 0
            try:
                start_t = sr.get("start_time") or {}
                dur_t = sr.get("duration") or {}
                start = int(float(start_t.get("value", 0)) / float(start_t.get("rate", 1)) * 1000)
                dur = int(float(dur_t.get("value", 0)) / float(dur_t.get("rate", 1)) * 1000)
            except Exception:
                pass
            cues.append({"start_ms": start, "end_ms": start + max(dur, 1), "text": str(text)})
    return cues or None


def _cues_to_srt(cues: list[dict[str, Any]]) -> str:
    def _ts(ms: int) -> str:
        h, rem = divmod(max(0, int(ms)), 3600000)
        m, rem = divmod(rem, 60000)
        s, milli = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"

    lines: list[str] = []
    for n, c in enumerate(cues, 1):
        start = int(c.get("start_ms", 0))
        end = int(c.get("end_ms", start + 1000))
        text = str(c.get("text", ""))
        lines.append(f"{n}\n{_ts(start)} --> {_ts(end)}\n{text}\n")
    return "\n".join(lines) + ("\n" if lines else "")


def _full_caption_baseline(
    project: Project,
    plan: dict[str, Any],
    *,
    baseline_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Full cue list to patch against: prefer current human SRT, else baseline
    export captions, else compiled timeline captions."""
    srt_path = project.captions_dir / "captions.srt"
    if srt_path.exists():
        try:
            from ..providers.asr import parse_srt
            segs = parse_srt(srt_path.read_text(encoding="utf-8"))
            return [
                {"start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text, "speaker": ""}
                for s in segs
            ]
        except Exception:
            pass
    # Baseline document captions
    if baseline_path and Path(baseline_path).exists():
        # Present-but-unusable refuses here too (_read_baseline); a cue list
        # that simply is not in the baseline falls through to the timeline.
        b = _read_baseline(Path(baseline_path))
        doc = b.get("document", b)
        cues = _captions_from_otio(doc) or _captions_from_jianying(doc)
        if cues:
            return [
                {"start_ms": c["start_ms"], "end_ms": c["end_ms"],
                 "text": c["text"], "speaker": c.get("speaker", "")}
                for c in cues
            ]
    tl = project.load_timeline()
    if tl is not None:
        return [
            {"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text,
             "speaker": c.speaker}
            for c in tl.tracks.captions
        ]
    return []


def _captions_from_jianying(data: dict) -> list[dict[str, Any]] | None:
    """Caption cues from skeleton materials.texts + text track segments."""
    mats = (data.get("materials") or {}).get("texts") or []
    by_id = {m.get("id"): m for m in mats if isinstance(m, dict)}
    cues: list[dict[str, Any]] = []
    for track in data.get("tracks") or []:
        if not isinstance(track, dict):
            continue
        if str(track.get("type") or "") != "text":
            continue
        for seg in track.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            mat = by_id.get(seg.get("material_id")) or {}
            manju = seg.get("manju") or mat.get("manju") or {}
            tr = seg.get("target_timerange") or {}
            start_us = int(tr.get("start") or 0)
            dur_us = int(tr.get("duration") or 0)
            start_ms = start_us // 1000
            end_ms = (start_us + dur_us) // 1000
            text = str(mat.get("content") or manju.get("text") or "")
            cues.append({
                "start_ms": start_ms,
                "end_ms": max(end_ms, start_ms + 1),
                "text": text,
                "speaker": str(mat.get("speaker") or ""),
                "shot": manju.get("shot", "") if isinstance(manju, dict) else "",
                "cue_index": manju.get("cue_index") if isinstance(manju, dict) else None,
            })
    return cues or None


def _seg_identity(meta: dict[str, Any] | None) -> tuple[str, str, str] | None:
    """Stable identity for baseline alignment: (shot, take, kind).

    Prefer full manju stamp; fall back to shot-only (take/kind empty) when
    partial. Returns None when even shot is missing.
    """
    if not isinstance(meta, dict):
        return None
    shot = _normalize_shot_id(meta.get("shot"))
    if not shot:
        return None
    take = str(meta.get("take") or "")
    kind = str(meta.get("kind") or "video")
    return (shot, take, kind)


def _index_baseline_segs(
    segs: list[dict],
    *,
    meta_from,
) -> tuple[dict[tuple[str, str, str], dict], list[dict]]:
    """Build identity→seg map + ordered list for idx fallback.

    ``meta_from(seg) -> manju dict`` extracts metadata per carrier.
    """
    by_id: dict[tuple[str, str, str], dict] = {}
    ordered: list[dict] = []
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        ordered.append(seg)
        meta = meta_from(seg) or {}
        ident = _seg_identity(meta if isinstance(meta, dict) else {})
        if ident and ident not in by_id:
            by_id[ident] = seg
        # also index shot-only fallback key for partial stamps
        if ident:
            shot_only = (ident[0], "", "")
            if shot_only not in by_id:
                by_id[shot_only] = seg
    return by_id, ordered


def _lookup_baseline_seg(
    by_id: dict[tuple[str, str, str], dict],
    ordered: list[dict],
    meta: dict[str, Any],
    idx: int,
) -> dict:
    """Prefer manju identity match; only then fall back to segment idx."""
    ident = _seg_identity(meta)
    if ident:
        if ident in by_id:
            return by_id[ident]
        shot_only = (ident[0], "", "")
        if shot_only in by_id:
            return by_id[shot_only]
    if 0 <= idx < len(ordered):
        return ordered[idx]
    return {}


def _volume_transition_rows(
    shot: str,
    meta: dict[str, Any],
    bmeta: dict[str, Any],
    *,
    truth_moved: bool,
) -> list[dict[str, Any]]:
    """Diff per-clip volume/mute and transition_out → plan rows.

    Unsupported rich effects are not invented here; only gain/mute and
    transition type/duration map to existing truth writers.
    """
    out: list[dict[str, Any]] = []
    state = "conflict" if truth_moved else "ok"
    # volume / mute
    new_mute = bool(meta.get("source_mute"))
    old_mute = bool(bmeta.get("source_mute"))
    new_gain = float(meta.get("source_gain_db") or 0.0)
    old_gain = float(bmeta.get("source_gain_db") or 0.0)
    if new_mute != old_mute or abs(new_gain - old_gain) > 1e-6:
        out.append({
            "class": "volume",
            "state": state,
            "evidence": {
                "shot": shot,
                "from": {"gain_db": old_gain, "mute": old_mute},
                "to": {"gain_db": new_gain, "mute": new_mute},
            },
            "target": f"shots/{shot}.yaml#source_audio",
            "action": "set_source_audio",
        })
    # transition_out → rules.transition_overrides[shot]
    new_tr = meta.get("transition_out")
    old_tr = bmeta.get("transition_out")
    if new_tr != old_tr:
        out.append({
            "class": "transition",
            "state": state,
            "evidence": {
                "shot": shot,
                "from": old_tr,
                "to": new_tr,
            },
            "target": "timeline/rules.yaml#transition_overrides",
            "action": "set_transition_override",
        })
    return out


def plan_roundtrip(project: Project, edited_path: Path | str) -> dict[str, Any]:
    edited_path = Path(edited_path)
    if not edited_path.exists():
        raise ProjectError(f"文件不存在: {edited_path}")
    try:
        data = _load_json(edited_path)
    except Exception as exc:
        raise ProjectError(f"无法解析 JSON: {exc}") from exc
    kind = detect_kind(data, edited_path)
    baseline_path = find_baseline(project, edited_path)
    baseline_doc = None
    compiled_from = ""
    baseline_cap_hash = None
    # ONE read: absent baseline stays the legal no-baseline mode; a baseline
    # that is present but unusable refuses instead of silently diffing the
    # edit against current truth.
    if baseline_path and baseline_path.exists():
        b = _read_baseline(baseline_path)
        baseline_doc = b.get("document", b)
        compiled_from = str(b.get("compiled_from") or "")
        baseline_cap_hash = b.get("captions_hash")

    # Truth moved since export?
    truth_moved = False
    tl = project.load_timeline()
    if tl is not None and compiled_from and tl.meta.compiled_from:
        if compiled_from != tl.meta.compiled_from:
            truth_moved = True
    # Captions truth moved since export? (manual SRT edited in Manju after T0)
    captions_conflict = False
    if baseline_cap_hash:
        now_hash = _captions_file_hash(project)
        if now_hash and now_hash != baseline_cap_hash:
            captions_conflict = True

    rows: list[dict[str, Any]] = []

    # Reorder detection
    if kind == "jianying":
        new_order = _shot_order_from_jianying(data)
        old_order = _shot_order_from_jianying(baseline_doc) if baseline_doc else []
    else:
        new_order = _shot_order_from_otio(data)
        old_order = _shot_order_from_otio(baseline_doc) if baseline_doc else []

    index = project.load_index()
    current_order = list(index.order)
    if new_order and set(new_order) <= set(current_order):
        # Only consider reorder if sequence of known shots differs
        filtered_new = [s for s in new_order if s in current_order]
        filtered_old = [s for s in (old_order or current_order) if s in current_order]
        if filtered_new and filtered_new != filtered_old and filtered_new != current_order:
            state = "conflict" if truth_moved else "ok"
            rows.append({
                "class": "reorder",
                "state": state,
                "evidence": {"from": filtered_old, "to": filtered_new},
                "target": "shots/index.yaml",
                "action": "permute_index",
            })

    # Caption text edits (OTIO + JianYing skeleton)
    if kind in ("otio", "jianying"):
        if kind == "otio":
            cues = _captions_from_otio(data)
            base_cues = _captions_from_otio(baseline_doc) if baseline_doc else None
        else:
            cues = _captions_from_jianying(data)
            base_cues = _captions_from_jianying(baseline_doc) if baseline_doc else None
        if cues and base_cues and len(cues) != len(base_cues):
            # Insert/delete in the NLE: the diff below is PURELY POSITIONAL
            # (zip + index patch) — with shifted pairs every later cue reads
            # as "edited" and --apply would rewrite captions.srt into
            # duplicated/lost cues. v1 honestly supports text/timing edits
            # only; a count change is surfaced as a non-appliable conflict
            # instead of corrupting truth.
            rows.append({
                "class": "caption_edit",
                "state": "conflict",
                "evidence": {
                    "why": (f"字幕条数变化({len(base_cues)} → {len(cues)}):"
                            "v1 round-trip 不支持增删字幕,逐条对位会错位覆写;"
                            "请直接编辑 captions/captions.srt(或 GUI /subtitles)"),
                    "baseline_cues": len(base_cues),
                    "edited_cues": len(cues),
                },
                "target": "captions/captions.srt",
                "action": None,
            })
        elif cues and base_cues:
            for i, (c, b) in enumerate(zip(cues, base_cues)):
                if (c.get("text") != b.get("text")
                        or c.get("start_ms") != b.get("start_ms")
                        or c.get("end_ms") != b.get("end_ms")):
                    # conflict if timeline moved OR human SRT moved since export
                    state = (
                        "conflict" if (truth_moved or captions_conflict) else "ok"
                    )
                    rows.append({
                        "class": "caption_edit",
                        "state": state,
                        "evidence": {
                            "index": i, "from": b, "to": c,
                            **({"why": "captions.srt moved since export"}
                               if captions_conflict and not truth_moved else {}),
                        },
                        "target": "captions/captions.srt",
                        "action": "manual_captions",
                    })

    # OTIO trim: video clip source_range in-point / duration change
    if kind == "otio":
        def _ms(rt: Any) -> int:
            if not isinstance(rt, dict):
                return 0
            try:
                return int(
                    float(rt.get("value", 0)) / float(rt.get("rate", 1) or 1) * 1000
                )
            except Exception:
                return 0

        base_otio: list[dict] = []
        if baseline_doc:
            for btr in _otio_track_list(baseline_doc):
                bn = str(btr.get("kind") or btr.get("name") or "")
                if "Video" in bn or "video" in bn.lower():
                    base_otio = [
                        c for c in (btr.get("children") or []) if isinstance(c, dict)
                    ]
                    break
        otio_by_id, otio_ordered = _index_baseline_segs(
            base_otio,
            meta_from=lambda c: (c.get("metadata") or {}).get("manju") or {},
        )
        for tr in _otio_track_list(data):
            kind_name = str(tr.get("kind") or tr.get("name") or "")
            if "Video" not in kind_name and "video" not in kind_name.lower():
                continue
            children = [c for c in (tr.get("children") or []) if isinstance(c, dict)]
            for idx, clip in enumerate(children):
                meta = (clip.get("metadata") or {}).get("manju") or {}
                if not isinstance(meta, dict):
                    meta = {}
                shot = _normalize_shot_id(meta.get("shot") or clip.get("name"))
                take = meta.get("take") if meta else None
                if not shot:
                    rows.append({
                        "class": "unmatched",
                        "state": "unmatched",
                        "evidence": {"name": clip.get("name"), "hint": "manju ingest"},
                        "target": None,
                        "action": None,
                    })
                    continue
                bclip = _lookup_baseline_seg(otio_by_id, otio_ordered, meta, idx)
                sr = clip.get("source_range") or {}
                bsr = bclip.get("source_range") or {} if isinstance(bclip, dict) else {}
                in_ms = _ms(sr.get("start_time") or {})
                dur_ms = _ms(sr.get("duration") or {})
                bin_ms = _ms(bsr.get("start_time") or {})
                bdur_ms = _ms(bsr.get("duration") or {})
                if in_ms != bin_ms or (dur_ms and bdur_ms and dur_ms != bdur_ms):
                    rows.append({
                        "class": "trim",
                        "state": "conflict" if truth_moved else "ok",
                        "evidence": {
                            "shot": shot, "take": take,
                            "from": {"in_ms": bin_ms, "duration_ms": bdur_ms},
                            "to": {"in_ms": in_ms, "duration_ms": dur_ms,
                                   "out_ms": in_ms + max(dur_ms, 1)},
                        },
                        "target": f"media/gen/{shot}/{take}",
                        "action": "set_inout",
                    })
                bmeta = ((bclip.get("metadata") or {}).get("manju")
                         if isinstance(bclip, dict) else {}) or {}
                rows.extend(_volume_transition_rows(
                    shot, meta,
                    bmeta if isinstance(bmeta, dict) else {},
                    truth_moved=truth_moved,
                ))

    # JianYing: volume/mute/transition — align baseline by manju identity
    if kind == "jianying":
        base_segs: list[dict] = []
        if baseline_doc:
            for tr in (baseline_doc.get("tracks") or []):
                if isinstance(tr, dict) and str(tr.get("type") or "") == "video":
                    base_segs = [s for s in (tr.get("segments") or [])
                                 if isinstance(s, dict)]
                    break
        jy_by_id, jy_ordered = _index_baseline_segs(
            base_segs,
            meta_from=lambda s: s.get("manju") or {},
        )
        for tr in (data.get("tracks") or []):
            if not isinstance(tr, dict) or str(tr.get("type") or "") != "video":
                continue
            for idx, seg in enumerate(tr.get("segments") or []):
                if not isinstance(seg, dict):
                    continue
                meta = seg.get("manju") or {}
                if not isinstance(meta, dict):
                    meta = {}
                # The LIVE JianYing fields (muted / volume) override the manju
                # export stamps: the stamps are frozen at export time, so for a
                # clip exported with non-default gain/mute they would forever
                # SHADOW the user's later volume/un-mute edits (the stamp equals
                # the baseline stamp → no diff row → edit silently dropped).
                # Stamps remain the fallback when the live fields are absent.
                if "muted" in seg:
                    meta = {**meta, "source_mute": bool(seg.get("muted"))}
                elif "source_mute" not in meta and seg.get("muted"):
                    meta = {**meta, "source_mute": True}
                if "volume" in seg and not seg.get("muted"):
                    try:
                        vol = float(seg["volume"])
                        if vol > 0:
                            import math

                            # tolerance 0.005 linear: a hand-written volume 0.5
                            # "meaning" the stamp's -6.0dB (exact would be
                            # 0.50119) must read UNMOVED — the stamp is the
                            # precise value; only a clearly different volume
                            # (a real drag in JianYing) overrides it.
                            stamp = meta.get("source_gain_db")
                            unmoved = (
                                isinstance(stamp, (int, float))
                                and not isinstance(stamp, bool)
                                and abs(min(2.0, 10 ** (float(stamp) / 20.0)) - vol) < 0.005
                            )
                            if not unmoved:  # volume actually moved → live wins
                                meta = {**meta,
                                        "source_gain_db": round(20 * math.log10(vol), 2)}
                            meta = {**meta, "source_mute": False}
                    except Exception:
                        pass
                shot = _normalize_shot_id(meta.get("shot"))
                if not shot:
                    continue
                bseg = _lookup_baseline_seg(jy_by_id, jy_ordered, meta, idx)
                bmeta = (bseg.get("manju") if isinstance(bseg, dict) else {}) or {}
                if not isinstance(bmeta, dict):
                    bmeta = {}
                rows.extend(_volume_transition_rows(
                    shot, meta, bmeta, truth_moved=truth_moved,
                ))

    # Filter no_changes if we only have empty
    if not rows:
        rows.append({
            "class": "no_changes",
            "state": "ok",
            "evidence": {},
            "target": None,
            "action": None,
        })

    return {
        "kind": kind,
        "edited": str(edited_path),
        "baseline": str(baseline_path) if baseline_path else None,
        "truth_moved": truth_moved,
        "rows": rows,
        "carrier_note": (
            "round-trip 仅支持 skeleton/OTIO;原生剪映/CapCut 草稿请编辑 skeleton 副本"
        ),
    }


def apply_roundtrip(
    project: Project,
    plan: dict[str, Any],
    *,
    rows: list[int] | None = None,
    actor: str = "human",
    should_cancel=None,
) -> dict[str, Any]:
    """Apply accepted rows under one build_lock. Per-row isolation.

    ``should_cancel`` (C52): checked before each selected row so GUI cancel
    stops mid-batch without undoing already-applied rows.
    """
    from ..runtime.buildlock import build_lock

    all_rows = plan.get("rows") or []
    indices = set(rows) if rows is not None else {
        i for i, r in enumerate(all_rows)
        if r.get("state") == "ok" and r.get("action")
    }
    applied = []
    skipped = []
    canceled = False
    with build_lock(project.root, actor=actor):
        for i, row in enumerate(all_rows):
            if i not in indices:
                skipped.append({"index": i, "reason": "not selected"})
                continue
            # C52: cooperative cancel between selected rows.
            if should_cancel is not None and should_cancel():
                canceled = True
                remaining = sum(1 for j in indices if j >= i)
                skipped.append({
                    "index": i,
                    "reason": f"已取消:{len(applied)} 行已应用,{remaining} 行未处理",
                })
                break
            if row.get("state") == "conflict":
                skipped.append({"index": i, "reason": "conflict — truth moved since export"})
                continue
            if row.get("state") == "unmatched":
                skipped.append({"index": i, "reason": "unmatched — try manju ingest"})
                continue
            if not row.get("action"):
                skipped.append({"index": i, "reason": "no action"})
                continue
            try:
                if row["action"] == "permute_index":
                    from ..core.writes import WriteRejected, permute_index

                    new_order = list(row["evidence"]["to"])
                    # Keep any shots not in the permutation at the end
                    current = project.shot_ids()
                    rest = [s for s in current if s not in new_order]
                    full = new_order + rest
                    try:
                        permute_index(project, full, actor=actor, via="roundtrip")
                    except WriteRejected as exc:
                        skipped.append({"index": i, "reason": str(exc)[:200]})
                        continue
                    applied.append({"index": i, "class": "reorder"})
                    append_event(project.root, actor, "roundtrip",
                                 {"class": "reorder", "order": full})
                elif row["action"] == "manual_captions":
                    # Takeover once: FULL cue list patched by SELECTED changed
                    # indices — never rebuild SRT from only the changed rows.
                    if any(a.get("class") == "caption_edit" for a in applied):
                        applied.append({"index": i, "class": "caption_edit",
                                        "note": "batched with prior caption_edit"})
                        continue
                    srt_path = project.captions_dir / "captions.srt"
                    project.captions_dir.mkdir(parents=True, exist_ok=True)
                    edited_p = Path(plan.get("edited") or "")
                    bl = find_baseline(project, edited_p) if edited_p.name else None
                    base_cues = _full_caption_baseline(
                        project, plan, baseline_path=bl,
                    )
                    selected_caps = [
                        (j, r) for j, r in enumerate(all_rows)
                        if j in indices
                        and r.get("action") == "manual_captions"
                        and r.get("state") == "ok"
                    ]
                    for _j, cr in selected_caps:
                        ev = cr.get("evidence") or {}
                        idx = int(ev.get("index", -1))
                        to = ev.get("to") or {}
                        if 0 <= idx < len(base_cues):
                            base_cues[idx] = {
                                "start_ms": int(to.get(
                                    "start_ms", base_cues[idx]["start_ms"])),
                                "end_ms": int(to.get(
                                    "end_ms", base_cues[idx]["end_ms"])),
                                "text": str(to.get(
                                    "text", base_cues[idx]["text"])),
                                "speaker": str(to.get(
                                    "speaker",
                                    base_cues[idx].get("speaker", ""))),
                            }
                        elif idx >= len(base_cues) and to.get("text"):
                            base_cues.append({
                                "start_ms": int(to.get("start_ms", 0)),
                                "end_ms": int(to.get("end_ms", 1000)),
                                "text": str(to.get("text", "")),
                                "speaker": str(to.get("speaker", "")),
                            })
                    atomic_write_text(srt_path, _cues_to_srt(base_cues))
                    rules = project.load_rules()
                    if rules.captions.mode != "manual":
                        rules.captions.mode = "manual"
                        project.save_rules(rules)
                    applied.append({
                        "index": i, "class": "caption_edit",
                        "cues_total": len(base_cues),
                        "cues_patched": len(selected_caps),
                    })
                    append_event(project.root, actor, "roundtrip", {
                        "class": "caption_edit",
                        "cues_total": len(base_cues),
                        "cues_patched": len(selected_caps),
                    })
                elif row["action"] == "set_inout":
                    from ..media.repair_ops import set_inout_take

                    ev = row.get("evidence") or {}
                    shot = ev.get("shot")
                    take = ev.get("take")
                    to = ev.get("to") or {}
                    in_ms = int(to.get("in_ms", 0))
                    out_ms = int(to.get("out_ms") or (in_ms + int(to.get("duration_ms") or 1)))
                    if not shot or not take:
                        skipped.append({"index": i, "reason": "trim missing shot/take"})
                        continue
                    info = set_inout_take(
                        project, shot, take, in_ms, out_ms, mode="virtual",
                    )
                    # Select the new take
                    from ..core.writes import select_take_checked
                    try:
                        select_take_checked(
                            project, shot, info.name, actor=actor,
                            via="roundtrip", action="set_inout",
                        )
                    except Exception:
                        # still applied take; selection may be locked
                        pass
                    applied.append({
                        "index": i, "class": "trim",
                        "shot": shot, "take": info.name,
                    })
                    append_event(project.root, actor, "roundtrip", {
                        "class": "trim", "shot": shot, "take": info.name,
                        "in_ms": in_ms, "out_ms": out_ms,
                    })
                elif row["action"] == "set_source_audio":
                    # Direct shot write (already under build_lock — avoid
                    # apply_mixer's nested lock). Same truth field mixer uses.
                    from ..core.models import SourceAudio

                    ev = row.get("evidence") or {}
                    shot = ev.get("shot")
                    to = ev.get("to") or {}
                    if not shot:
                        skipped.append({"index": i, "reason": "volume missing shot"})
                        continue
                    sa = SourceAudio.model_validate({
                        "gain_db": float(to.get("gain_db") or 0.0),
                        "mute": bool(to.get("mute")),
                    })

                    def _set_sa(d, _sa=sa.model_dump()):
                        d["source_audio"] = _sa

                    project.update_shot_raw(shot, _set_sa)
                    applied.append({"index": i, "class": "volume", "shot": shot})
                    append_event(project.root, actor, "roundtrip", {
                        "class": "volume", "shot": shot, "to": to,
                    })
                elif row["action"] == "set_transition_override":
                    from ..core.models import TransitionSpec

                    ev = row.get("evidence") or {}
                    shot = ev.get("shot")
                    to = ev.get("to")
                    if not shot:
                        skipped.append({"index": i, "reason": "transition missing shot"})
                        continue
                    rules = project.load_rules()
                    overrides = dict(rules.transition_overrides or {})
                    if to is None:
                        overrides[shot] = None  # explicit hard cut
                    elif isinstance(to, dict):
                        overrides[shot] = TransitionSpec.model_validate(to)
                    else:
                        skipped.append({
                            "index": i,
                            "reason": "unsupported transition payload",
                        })
                        continue
                    rules.transition_overrides = overrides
                    project.save_rules(rules)
                    applied.append({
                        "index": i, "class": "transition", "shot": shot,
                    })
                    append_event(project.root, actor, "roundtrip", {
                        "class": "transition", "shot": shot, "to": to,
                    })
                else:
                    skipped.append({
                        "index": i,
                        "reason": f"unsupported action {row['action']} "
                                  f"(effects/keyframes/speed not in v1)",
                    })
            except Exception as exc:
                skipped.append({"index": i, "reason": str(exc)[:200]})

    base_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    batch_dir = project.root / "reports" / "roundtrip_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    # two applies inside the same second (scripted/CLI/GUI-driven) must not
    # overwrite each other's audit record — probe a free suffix
    batch_id, serial = base_id, 2
    while (batch_dir / f"{batch_id}.yaml").exists():
        batch_id = f"{base_id}-{serial}"
        serial += 1
    batch = {
        "id": batch_id, "kind": "roundtrip",
        "applied": applied, "skipped": skipped,
        "plan_kind": plan.get("kind"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
    }
    write_yaml(batch_dir / f"{batch_id}.yaml", batch)
    return {
        "batch": batch_id,
        "applied": applied,
        "skipped": skipped,
        "canceled": canceled,
    }
