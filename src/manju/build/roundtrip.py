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


def _load_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
    """Locate exports/<kind>/.baseline/<stem>.json near the edited file."""
    edited = Path(edited)
    # Walk up looking for .baseline sibling
    for parent in [edited.parent, *edited.parents]:
        base = parent / ".baseline" / f"{edited.stem}.json"
        if base.exists():
            return base
        # also try draft_content stem mapping
        base2 = parent / ".baseline" / f"{parent.name}.json"
        if base2.exists() and parent.name:
            return base2
    # Project-wide search under exports/
    exports = project.root / "exports"
    if exports.exists():
        for p in exports.rglob(".baseline"):
            cand = p / f"{edited.stem}.json"
            if cand.exists():
                return cand
    return None


def write_baseline(project: Project, kind: str, name: str, document: dict,
                   *, compiled_from: str = "") -> Path:
    """Write exports/<kind>/.baseline/<name>.json (derived, regenerable)."""
    d = project.root / "exports" / kind / ".baseline"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"
    payload = {
        "document": document,
        "compiled_from": compiled_from,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "name": name,
    }
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def _shot_order_from_jianying(data: dict) -> list[str]:
    """Best-effort shot order from skeleton materials/tracks metadata."""
    order: list[str] = []
    # materials.videos often carry name/path with shot id
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
            shot = shot or v.get("shot") or v.get("material_name") or v.get("name")
            if shot and str(shot) not in order and not str(shot).startswith("__"):
                order.append(str(shot))
    return order


def _shot_order_from_otio(data: dict) -> list[str]:
    order: list[str] = []
    tracks = data.get("tracks") or []
    for tr in tracks:
        if not isinstance(tr, dict):
            continue
        kind = str(tr.get("kind") or tr.get("name") or "").lower()
        if kind and "video" not in kind and "Video" not in str(tr.get("OTIO_SCHEMA", "")):
            # still scan children
            pass
        for clip in tr.get("children") or tr.get("clips") or []:
            if not isinstance(clip, dict):
                continue
            meta = clip.get("metadata") or {}
            manju = meta.get("manju") if isinstance(meta, dict) else None
            shot = None
            if isinstance(manju, dict):
                shot = manju.get("shot")
            shot = shot or clip.get("name")
            if shot and str(shot) not in order and not str(shot).startswith("__"):
                order.append(str(shot))
    return order


def _captions_from_otio(data: dict) -> list[dict[str, Any]] | None:
    cues = []
    for tr in data.get("tracks") or []:
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
    if baseline_path and baseline_path.exists():
        try:
            b = _load_json(baseline_path)
            baseline_doc = b.get("document", b)
            compiled_from = str(b.get("compiled_from") or "")
        except Exception:
            baseline_doc = None

    # Truth moved since export?
    truth_moved = False
    tl = project.load_timeline()
    if tl is not None and compiled_from and tl.meta.compiled_from:
        if compiled_from != tl.meta.compiled_from:
            truth_moved = True

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

    # Caption text edits (OTIO caption track / JY texts)
    if kind == "otio":
        cues = _captions_from_otio(data)
        if cues and baseline_doc:
            base_cues = _captions_from_otio(baseline_doc) or []
            for i, (c, b) in enumerate(zip(cues, base_cues)):
                if c.get("text") != b.get("text") or c.get("start_ms") != b.get("start_ms"):
                    rows.append({
                        "class": "caption_edit",
                        "state": "conflict" if truth_moved else "ok",
                        "evidence": {"index": i, "from": b, "to": c},
                        "target": "captions/captions.srt",
                        "action": "manual_captions",
                    })

    # Unmatched foreign media — not in our metadata
    # (simplified: if edited has more video materials than baseline)
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
) -> dict[str, Any]:
    """Apply accepted rows under one build_lock. Per-row isolation."""
    from ..runtime.buildlock import build_lock

    all_rows = plan.get("rows") or []
    indices = set(rows) if rows is not None else {
        i for i, r in enumerate(all_rows)
        if r.get("state") == "ok" and r.get("action")
    }
    applied = []
    skipped = []
    with build_lock(project.root, actor=actor):
        for i, row in enumerate(all_rows):
            if i not in indices:
                skipped.append({"index": i, "reason": "not selected"})
                continue
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
                    new_order = row["evidence"]["to"]
                    index = project.load_index()
                    # Keep any shots not in the permutation at the end
                    rest = [s for s in index.order if s not in new_order]
                    index.order = list(new_order) + rest
                    project.save_index(index)
                    applied.append({"index": i, "class": "reorder"})
                    append_event(project.root, actor, "roundtrip",
                                 {"class": "reorder", "order": new_order})
                elif row["action"] == "manual_captions":
                    # Takeover: write captions.srt + set mode manual
                    from ..providers.asr import parse_srt  # noqa: F401

                    cues = []
                    # Rebuild SRT from all caption_edit rows of this plan
                    # For simplicity apply this single cue change onto existing
                    srt_path = project.captions_dir / "captions.srt"
                    project.captions_dir.mkdir(parents=True, exist_ok=True)
                    ev = row["evidence"]
                    to = ev.get("to") or {}
                    # Minimal: append/replace one cue file from plan caption rows
                    caption_rows = [
                        r for r in all_rows if r.get("action") == "manual_captions"
                    ]
                    lines = []
                    for n, cr in enumerate(caption_rows, 1):
                        c = cr["evidence"].get("to") or {}
                        start = int(c.get("start_ms", 0))
                        end = int(c.get("end_ms", start + 1000))
                        def _ts(ms: int) -> str:
                            h, rem = divmod(ms, 3600000)
                            m, rem = divmod(rem, 60000)
                            s, milli = divmod(rem, 1000)
                            return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"
                        lines.append(f"{n}\n{_ts(start)} --> {_ts(end)}\n{c.get('text','')}\n")
                    atomic_write_text(srt_path, "\n".join(lines) + "\n")
                    rules = project.load_rules()
                    if rules.captions.mode != "manual":
                        rules.captions.mode = "manual"
                        project.save_rules(rules)
                    applied.append({"index": i, "class": "caption_edit"})
                    append_event(project.root, actor, "roundtrip",
                                 {"class": "caption_edit", "cues": len(caption_rows)})
                else:
                    skipped.append({"index": i, "reason": f"unsupported action {row['action']}"})
            except Exception as exc:
                skipped.append({"index": i, "reason": str(exc)[:200]})

    batch_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    batch_dir = project.root / "reports" / "roundtrip_batches"
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch = {
        "id": batch_id, "kind": "roundtrip",
        "applied": applied, "skipped": skipped,
        "plan_kind": plan.get("kind"),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "actor": actor,
    }
    write_yaml(batch_dir / f"{batch_id}.yaml", batch)
    return {"batch": batch_id, "applied": applied, "skipped": skipped}
