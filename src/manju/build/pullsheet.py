"""AI_IDE_16 §9 — Storyboard Pull Sheet round-trip (CSV + Markdown).

EXPORT is a pure derivation from the compiled timeline + shots — shot order,
duration, frame refs, camera, action, dialogue, transition, refs, quality and
status — written to ``exports/pullsheet/`` (a rebuildable derived artifact).

IMPORT maps the edited sheet's DIFF onto the EXISTING ShotDraftPackage (DR03A)
plan/apply machinery (Fable ruling 7): a zero-write inspect plan → human confirm
→ CAS apply. A NEW shot row becomes a ``manju.shot-draft-package/v1`` create
op (routed straight through :mod:`manju.build.shotpackage`); an EDITED existing
shot becomes a proposal applied through the SAME checked-write CAS every other
source edit uses (:func:`manju.core.writes.checked_shot_write`). It NEVER
touches ``selected_take``, media, or locks, and a stale source (the file moved
since inspect) fails the CAS — never a silent overwrite.

PDF export is SKIPPED_WITH_EVIDENCE: no headless-Chromium / PDF-table path
exists in this environment and the addendum forbids adding one this batch. CSV
+ Markdown + the existing JSON exporters cover the export requirement.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.hashing import hash_text
from ..core.idents import windows_segment_problems
from ..core.safeio import checked_out_path
from ..core.yamlio import atomic_write_text

if TYPE_CHECKING:
    from ..core.container import Project
    from ..core.models import Timeline

SHEET_SCHEMA = "manju.pull-sheet/v1"
PLAN_SCHEMA = "manju.pull-sheet-import-plan/v1"

# The sheet columns (contract §9). frame_refs / transition / refs / status are
# EXPORT-ONLY (display): frame refs + refs carry transfer contracts and status
# rides selected_take/media — the round-trip never writes them back. The EDITABLE
# subset a re-import may propose is the pure Shot-source creative fields below.
COLUMNS = (
    "shot_id", "scene", "characters", "duration_ms", "frame_refs",
    "shot_size", "movement", "angle", "action", "speaker", "dialogue",
    "transition", "refs", "must_show", "avoid", "status",
)
# Fields a re-import is allowed to PROPOSE onto an existing shot (never status,
# never selected_take, never media, never locks, never the timeline transition).
EDITABLE = ("scene", "characters", "duration_ms", "shot_size", "movement",
            "angle", "action", "speaker", "dialogue", "must_show", "avoid")

# editable-field key -> the dotted shot path it moves (for the lock guard).
_TOUCH_PATHS = {
    "scene": "scene", "characters": "characters", "duration": "duration",
    "camera": "camera", "action_main": "action.main", "dialogue": "dialogue",
    "must_show": "quality.must_show", "avoid": "quality.avoid",
}


class PullSheetError(ValueError):
    """A pull sheet is malformed or unsafe to import."""


# ============================================================ EXPORT (derived)


def _timeline_index(timeline: "Timeline | None") -> dict[str, dict[str, Any]]:
    """shot id -> {duration_ms, transition} from the compiled timeline."""
    out: dict[str, dict[str, Any]] = {}
    if timeline is None:
        return out
    for clip in timeline.tracks.video:
        sid = str(getattr(clip, "shot", "") or "")
        if not sid or sid.startswith("__"):
            continue
        trans = getattr(clip, "transition_out", None)
        out[sid] = {
            "duration_ms": int(getattr(clip, "duration_ms", 0) or 0),
            "transition": (getattr(trans, "type", "") if trans else "cut"),
        }
    return out


def _frame_refs(shot) -> str:
    """Authored keyframe images (frame refs), ``;``-joined."""
    refs = []
    for kf in getattr(shot, "keyframes", []) or []:
        img = getattr(kf, "image", None)
        if img:
            role = getattr(kf, "role", None) or getattr(kf, "position", None) or ""
            refs.append(f"{role}:{img}" if role else str(img))
    return "; ".join(refs)


def _refs_summary(project: "Project", shot) -> str:
    try:
        from ..providers.refs import resolve_refs

        rs = resolve_refs(project, shot, project.load_bible())
    except Exception:
        return ""
    out = []
    for it in rs.items:
        tag = it.ref
        if getattr(it, "controls", None):
            tag += f"[{'+'.join(it.controls)}]"
        out.append(tag)
    return "; ".join(out)


def _shot_row(project: "Project", shot, tl_info: dict[str, Any]) -> dict[str, str]:
    cam = shot.camera
    dur_ms = tl_info.get("duration_ms")
    if not dur_ms:
        d = shot.duration
        dur_ms = int(d * 1000) if isinstance(d, (int, float)) else ""
    status = shot.status
    return {
        "shot_id": shot.id,
        "scene": shot.scene or "",
        "characters": ";".join(shot.characters),
        "duration_ms": str(dur_ms or ""),
        "frame_refs": _frame_refs(shot),
        "shot_size": cam.shot_size,
        "movement": cam.movement,
        "angle": cam.angle,
        "action": shot.action.main or "",
        "speaker": shot.dialogue.speaker or "",
        "dialogue": shot.dialogue.text or "",
        "transition": str(tl_info.get("transition", "")),
        "refs": _refs_summary(project, shot),
        "must_show": ";".join(shot.quality.must_show),
        "avoid": ";".join(shot.quality.avoid),
        "status": status.review_state + (
            f" ({status.selected_take})" if status.selected_take else ""),
    }


def pull_sheet_rows(project: "Project", timeline: "Timeline | None" = None) -> list[dict[str, str]]:
    """The derived pull-sheet rows in shot-index order (the order authority)."""
    if timeline is None:
        timeline = _load_or_compile(project)
    idx = _timeline_index(timeline)
    rows: list[dict[str, str]] = []
    for sid in project.shot_ids():
        try:
            shot = project.load_shot(sid)
        except Exception:
            continue
        rows.append(_shot_row(project, shot, idx.get(sid, {})))
    return rows


def _load_or_compile(project: "Project") -> "Timeline | None":
    try:
        tl = project.load_timeline()
        if tl is not None:
            return tl
    except Exception:
        pass
    try:
        from ..media.probe import probe_duration_ms
        from ..timeline.compiler import compile_timeline, gather_compile_input

        return compile_timeline(gather_compile_input(
            project, probe_duration_ms, allow_missing_takes=True))
    except Exception:
        return None


def compile_pull_sheet_csv(project: "Project", timeline: "Timeline | None" = None) -> str:
    rows = pull_sheet_rows(project, timeline)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(COLUMNS), lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


def _md_cell(v: str) -> str:
    # Newlines encode REVERSIBLY as <br> (Markdown renderers show a line break;
    # _decode_md_cell restores "\n" on import). The old lossy `\n → " "` made an
    # UNEDITED export diff dirty against truth, and --apply then rewrote every
    # multi-line dialogue/action to its flattened form.
    return str(v).replace("|", "\\|").replace("\n", "<br>")


def _decode_md_cell(v: str) -> str:
    return str(v).replace("<br>", "\n")


def compile_pull_sheet_md(project: "Project", timeline: "Timeline | None" = None) -> str:
    rows = pull_sheet_rows(project, timeline)
    lines = ["# Storyboard Pull Sheet", ""]
    lines.append("| " + " | ".join(COLUMNS) + " |")
    lines.append("| " + " | ".join("---" for _ in COLUMNS) + " |")
    for r in rows:
        lines.append("| " + " | ".join(_md_cell(r.get(c, "")) for c in COLUMNS) + " |")
    return "\n".join(lines) + "\n"


def _safe_sheet_stem(name: str) -> str:
    """PULLSHEET-P0-001: the project name is DISPLAY identity, not a path — a
    separator / absolute root / ``..`` / reserved device name in it must never
    survive into the output filename (``out_dir / f"{name}.csv"`` would otherwise
    drop the ``exports/pullsheet`` prefix and overwrite an arbitrary file). Fold
    every path-ish or Windows-lexically-unsafe char to ``_``; CJK/spaces stay
    (legitimate on both platforms). An empty/reserved result falls back to a
    fixed safe leaf. The final path is still re-checked by ``checked_out_path``."""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", (name or "").strip())
    cleaned = cleaned.replace("..", "_").strip(" .")[:120]
    if not cleaned or windows_segment_problems(cleaned):
        return "pullsheet"
    return cleaned


def export_pull_sheet(project: "Project", timeline: "Timeline | None" = None) -> dict[str, Path]:
    """Write the CSV + Markdown pull sheet to ``exports/pullsheet/`` (derived,
    rebuildable). Returns {kind: path}. PDF is SKIPPED_WITH_EVIDENCE.

    The filename leaf comes from a sanitized single-segment slug of the project
    name and the final path is re-validated by ``core.safeio.checked_out_path``
    (inside-project → only under ``exports/``; never a link/dir leaf) so a
    hostile ``project.yaml.name`` can never escape ``exports/pullsheet``
    (PULLSHEET-P0-001)."""
    stem = _safe_sheet_stem(project.load_config().name)
    out_dir = project.exports_dir / "pullsheet"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = checked_out_path(out_dir / f"{stem}.csv", project_root=project.root,
                                inside_roots=("exports",), kind="pull sheet CSV")
    md_path = checked_out_path(out_dir / f"{stem}.md", project_root=project.root,
                               inside_roots=("exports",), kind="pull sheet Markdown")
    atomic_write_text(csv_path, compile_pull_sheet_csv(project, timeline))
    atomic_write_text(md_path, compile_pull_sheet_md(project, timeline))
    return {"csv": csv_path, "md": md_path}


# ============================================================ IMPORT (round-trip)


def _parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def _split_md_row(s: str) -> list[str]:
    """Split a Markdown table row on UNESCAPED pipes, then unescape ``\\|``."""
    cells: list[str] = []
    buf: list[str] = []
    esc = False
    for ch in s.strip().strip("|"):
        if esc:
            buf.append(ch if ch == "|" else "\\" + ch)  # only \| is our escape
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    cells.append("".join(buf).strip())
    return cells


def _parse_md(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            continue
        cells = _split_md_row(s)
        if all(set(c) <= {"-", ":"} and c for c in cells):
            continue  # the ---|--- separator
        if header is None:
            header = cells
            continue
        if len(cells) != len(header):
            continue
        rows.append({k: _decode_md_cell(c) for k, c in zip(header, cells)})
    return rows


def parse_sheet(text: str, *, fmt: str) -> list[dict[str, str]]:
    rows = _parse_md(text) if fmt == "md" else _parse_csv(text)
    out = []
    for r in rows:
        sid = (r.get("shot_id") or "").strip()
        if not sid:
            continue
        out.append({k: (r.get(k) or "").strip() for k in COLUMNS if k in r} | {"shot_id": sid})
    return out


def _editable_from_row(row: dict[str, str]) -> dict[str, Any]:
    """The proposed EDITABLE Shot-source values from one sheet row (typed)."""
    out: dict[str, Any] = {}
    if "scene" in row:
        out["scene"] = row["scene"] or None
    if "characters" in row:
        out["characters"] = [c for c in row["characters"].split(";") if c.strip()]
    # duration_ms is the COMPILED (derived) value in the sheet — never diffed
    # back onto an existing shot (an "auto" source must not read as changed). It
    # is mapped only for a brand-NEW shot's create suggestion (_new_shot_package).
    cam = {}
    for k in ("shot_size", "movement", "angle"):
        if row.get(k):
            cam[k] = row[k]
    if cam:
        out["camera"] = cam
    if "action" in row:
        out["action_main"] = row["action"]
    if "speaker" in row or "dialogue" in row:
        out["dialogue"] = {"speaker": row.get("speaker", ""), "text": row.get("dialogue", "")}
    if "must_show" in row:
        out["must_show"] = [x for x in row["must_show"].split(";") if x.strip()]
    if "avoid" in row:
        out["avoid"] = [x for x in row["avoid"].split(";") if x.strip()]
    return out


def _current_editable(project: "Project", shot) -> dict[str, Any]:
    return {
        "scene": shot.scene or None,
        "characters": list(shot.characters),
        "duration": (round(shot.duration, 3)
                     if isinstance(shot.duration, (int, float)) else None),
        "camera": {"shot_size": shot.camera.shot_size, "movement": shot.camera.movement,
                   "angle": shot.camera.angle},
        "action_main": shot.action.main or "",
        "dialogue": {"speaker": shot.dialogue.speaker or "", "text": shot.dialogue.text or ""},
        "must_show": list(shot.quality.must_show),
        "avoid": list(shot.quality.avoid),
    }


def _diff_fields(cur: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    """Only the proposed fields that actually DIFFER from current."""
    changed = {}
    for k, v in proposed.items():
        cv = cur.get(k)
        if k == "camera":
            merged = {**(cv or {}), **v}
            if merged != cv:
                changed[k] = v
        elif v != cv:
            changed[k] = v
    return changed


def _apply_editable(raw: dict[str, Any], fields: dict[str, Any]) -> None:
    """Mutate a raw shot dict with the editable fields — nothing else (status,
    selected_take, media, locked, generation, continuity are untouched)."""
    if "scene" in fields:
        raw["scene"] = fields["scene"]
    if "characters" in fields:
        raw["characters"] = fields["characters"]
    if "duration" in fields:
        raw["duration"] = fields["duration"]
    from ..core.writes import ensure_mapping

    if "camera" in fields:
        ensure_mapping(raw, "camera").update(fields["camera"])
    if "action_main" in fields:
        ensure_mapping(raw, "action")["main"] = fields["action_main"]
    if "dialogue" in fields:
        dlg = ensure_mapping(raw, "dialogue")
        dlg["speaker"] = fields["dialogue"]["speaker"]
        dlg["text"] = fields["dialogue"]["text"]
    if "must_show" in fields:
        ensure_mapping(raw, "quality")["must_show"] = fields["must_show"]
    if "avoid" in fields:
        ensure_mapping(raw, "quality")["avoid"] = fields["avoid"]


def _new_shot_package(project: "Project", new_rows: list[dict[str, str]]) -> dict[str, Any] | None:
    """Map NEW sheet rows onto a manju.shot-draft-package/v1 (DR03A machinery)."""
    if not new_rows:
        return None
    shots = []
    for row in new_rows:
        ed = _editable_from_row(row)
        cs: dict[str, Any] = {}
        if row.get("duration_ms"):
            try:
                cs["duration_ms"] = int(row["duration_ms"])
            except ValueError:
                pass
        if "camera" in ed:
            cs["camera"] = ed["camera"]
        if "action_main" in ed:
            cs["action"] = ed["action_main"]
        shots.append({
            "draft_id": f"pullsheet_{row['shot_id']}",
            "proposed_shot_id": row["shot_id"],
            "source_facts": {
                "scene_ref": ed.get("scene"),
                "character_refs": ed.get("characters", []),
            },
            "creative_suggestions": cs,
        })
    from .shotpackage import project_revision

    return {
        "schema": "manju.shot-draft-package/v1",
        "package_id": "pullsheet-import",
        "producer": {"kind": "pull_sheet", "name": "manju.pullsheet"},
        "source": {"source_revision": project_revision(project)},
        "shots": shots,
    }


def plan_pull_sheet_import(project: "Project", sheet_path: str | Path) -> dict[str, Any]:
    """ZERO-WRITE inspect: parse the edited sheet, diff it against current truth,
    and produce a shot-package-style plan. NEVER writes anything."""
    from ..core.writes import shot_text_hash

    p = Path(sheet_path)
    if not p.exists():
        raise PullSheetError(f"pull sheet not found: {sheet_path}")
    fmt = "md" if p.suffix.lower() in (".md", ".markdown") else "csv"
    rows = parse_sheet(p.read_text(encoding="utf-8"), fmt=fmt)

    existing = set(project.shot_ids())
    ops: list[dict[str, Any]] = []
    new_rows: list[dict[str, str]] = []
    for row in rows:
        sid = row["shot_id"]
        if sid not in existing:
            new_rows.append(row)
            ops.append({"op": "create_shot", "shot_id": sid,
                        "via": "shot_draft_package", "safe": True})
            continue
        try:
            shot = project.load_shot(sid)
        except Exception:
            ops.append({"op": "skip", "shot_id": sid, "reason": "unreadable", "safe": False})
            continue
        changed = _diff_fields(_current_editable(project, shot),
                               _editable_from_row(row))
        if not changed:
            ops.append({"op": "unchanged", "shot_id": sid, "safe": True})
            continue
        locked = set((project.load_shot_raw(sid).get("locked") or {}))
        touch = tuple(_TOUCH_PATHS[k] for k in changed if k in _TOUCH_PATHS)
        ops.append({
            "op": "update_shot", "shot_id": sid,
            "fields": sorted(changed.keys()),
            "values": changed,                 # the concrete reviewed values
            "touch_paths": list(touch),        # dotted paths the write moves
            "expected_text_hash": shot_text_hash(project, sid),  # CAS token
            "locked_paths": sorted(locked),
            "safe": True,
        })

    pkg = _new_shot_package(project, new_rows)
    create_plan = None
    if pkg is not None:
        from .shotpackage import build_shot_import_plan

        create_plan = build_shot_import_plan(project, pkg)

    n_update = sum(1 for o in ops if o["op"] == "update_shot")
    n_create = sum(1 for o in ops if o["op"] == "create_shot")
    return {
        "schema": PLAN_SCHEMA,
        "operations": ops,
        "create_plan": create_plan,           # a real manju.shot-import-plan/v1
        "_package": pkg,                       # carried for the CAS apply
        "summary": {"create": n_create, "update": n_update,
                    "unchanged": sum(1 for o in ops if o["op"] == "unchanged")},
        "proposal_only": True,                 # inspect never writes (§9 pin)
        "do_not_execute_automatically": True,
    }


def apply_pull_sheet_import(project: "Project", plan: dict[str, Any], *,
                            actor: str = "human") -> dict[str, Any]:
    """CAS apply of a reviewed plan. Updates go through the SAME checked-write
    CAS every source edit uses (stale ``expected_text_hash`` → refused, never a
    silent overwrite; a locked field blocks); creates go through the DR03A
    shot-package apply. NEVER touches selected_take, media, or locks (pin)."""
    from ..core.writes import WriteRejected, checked_shot_write

    applied: list[str] = []
    refused: list[dict[str, Any]] = []

    # 1) updates (existing shots) — CAS on the shot text; locks block.
    for op in plan.get("operations", []):
        if op.get("op") != "update_shot":
            continue
        sid = op["shot_id"]
        # The reviewed concrete values + the dotted paths the write moves were
        # recorded on the op at inspect time (so the human reviewed exactly what
        # applies). expected_text_hash is the CAS token: a source that moved since
        # inspect is refused, never silently overwritten.
        fields = op.get("values")
        if fields is None:
            refused.append({"shot_id": sid, "reason": "plan carried no values"})
            continue

        def _mut(raw, _f=fields):
            _apply_editable(raw, _f)

        try:
            checked_shot_write(project, sid, _mut,
                               guard_paths=tuple(op.get("touch_paths", ())),
                               expected_text_hash=op.get("expected_text_hash"))
            applied.append(sid)
        except WriteRejected as exc:
            refused.append({"shot_id": sid, "reason": str(exc)})

    # 2) creates — the DR03A shot-package apply (CAS + rollback + never overwrite)
    created: list[str] = []
    create_plan = plan.get("create_plan")
    pkg = plan.get("_package")
    if create_plan is not None and pkg is not None:
        from .shotpackage import apply_shot_import_plan

        res = apply_shot_import_plan(project, pkg, actor=actor, plan=create_plan)
        if res.get("ok"):
            created = list(res.get("created", []))
        else:
            refused.append({"create": res.get("code"), "reasons": res.get("reasons")})

    return {
        "ok": not refused,
        "applied": applied,
        "created": created,
        "refused": refused,
    }
