"""Agent visual-QC pipe (round V, goal item 6).

Professional visual judgment moves from "algorithms + a vendor slot" to **the
driving agent's own eyes + the skill library's standards**. Manju never calls a
vision model (§0); it builds the deterministic PIPE around the agent's eyes:

    brief    ``qc_brief`` packages, per shot: review frames (mid + first/last of
             the selected take, via the frames.py content-addressed cache), the
             take name + content hash, the shot context (scene, characters with
             their bible ref images, must_show/avoid, continuity locks, dialogue)
             and a POINTER to the ``visual-qc-review`` skill that carries the
             A–J criteria — plus the verdict JSON contract so the agent knows the
             return shape. The judgment content lives in the skill, never here.

    verdict  ``record_verdicts`` intakes the agent's structured verdicts and
             appends them to ``reports/qc_agent.jsonl`` — each record bound to the
             BYTES it judged (the take media's content hash, reused from
             core.hashing.hash_file), so a regenerated take makes its prior
             verdicts provably stale.

    merge    ``agent_verdict_items`` folds the log back into ``run_qc``: the
             LATEST verdict per (shot, criterion) whose take hash still matches
             the current selected take surfaces as an ``[AI判读]`` content item at
             the mapped level (blocker→error / issue→warn / fyi→info); a shot
             whose bytes changed gets ONE "已过期,重新跑 manju qc brief" info item.
             Deterministic; a malformed line is skipped and counted, never fatal.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..core.hashing import hash_file
from .checks import QCItem

if TYPE_CHECKING:
    from ..core.container import Project, TakeInfo

# reports/qc_agent.jsonl — append-only, one verdict record per line, UTF-8.
AGENT_LOG = "qc_agent.jsonl"

# The skill that carries the A–J judgment standards (a sibling agent authors it).
CRITERIA_SKILL = "visual-qc-review"
CRITERIA_NOTE = "先 manju skills show visual-qc-review 获取判读标准 A–J"

# Netflix severity tiers (§6a) → Manju's existing finding levels.
LEVELS = ("blocker", "issue", "fyi")
LEVEL_MAP = {"blocker": "error", "issue": "warn", "fyi": "info"}

# The 中文 prefix that marks a finding as the driving agent's own judgment.
AI_PREFIX = "[AI判读]"


class VerdictError(ValueError):
    """A malformed / unacceptable verdict payload (unknown shot, bad level…)."""


def agent_log_path(project: "Project"):
    """``reports/qc_agent.jsonl`` under the project."""
    return project.reports_dir / AGENT_LOG


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- verdict contract


def verdict_contract() -> dict:
    """The return-shape spec embedded in every brief so the agent knows exactly
    what to hand back — the mirror of :func:`record_verdicts`'s validation."""
    return {
        "usage": "把判读结果写成下面的 JSON,用 `manju qc verdict --from-file <路径>` "
                 "(或 `-` 走 stdin)回填;MCP 用 qc_verdict 工具。",
        "shape": {
            "verdicts": [
                {
                    "shot": "镜头 id(必填,须为本项目镜头)",
                    "take": "take 名(来自本 brief;省略则绑定当前已选 take)",
                    "criterion": "A–J 判读标准代码或自由文本(如 A1 / 穿帮 / 手部)",
                    "level": "blocker | issue | fyi",
                    "message": "中文结论(必填)",
                    "evidence": "帧路径或文字佐证",
                    "frame_ms": "可选,问题所在的毫秒位置",
                }
            ]
        },
        "levels": {"blocker": "error/阻断(不可上线)", "issue": "warn/需修",
                   "fyi": "info/知悉"},
        "note": "判读结果与所判 take 的字节绑定;该 take 重生成后旧判读自动过期。",
    }


# ----------------------------------------------------------------------- brief


def _shot_frames(project: "Project", take: "TakeInfo") -> dict[str, str | None]:
    """Review frames for one take — mid + first/last — via the frames.py
    content-addressed cache (no re-encode on a repeat brief). Degrades to nulls
    on any failure (missing ffmpeg / unreadable media), never raises."""
    from ..media.frames import extract_frame

    try:
        media_rel = project.relpath(take.media_path)  # type: ignore[arg-type]
    except Exception:
        return {"mid": None}

    dur = 0
    try:
        from ..media.probe import probe as _probe

        info = _probe(take.media_path)
        dur = int(info.duration_ms or 0)
    except Exception:
        dur = 0

    if dur > 0:
        points = {"first": 0, "mid": dur // 2, "last": dur}
    else:
        points = {"mid": 0}

    out: dict[str, str | None] = {}
    for label, at_ms in points.items():
        try:
            frame = extract_frame(project, media_rel, at_ms)
            out[label] = project.relpath(frame)
        except Exception:
            out[label] = None
    return out


def _character_context(matrix: dict, cid: str) -> dict:
    """A character's brief context: id + name + its bible ref image paths (from
    the asset matrix — the same read model @mentions resolve against)."""
    from ..core.assets import find_asset

    row = find_asset(matrix, cid)
    if row is None:
        return {"id": cid, "name": None, "refs": []}
    return {
        "id": cid,
        "name": row.get("name"),
        "refs": list((row.get("refs") or {}).get("images") or []),
    }


def _scene_context(matrix: dict, scene_id: str | None) -> dict | None:
    if not scene_id:
        return None
    from ..core.assets import find_asset

    row = find_asset(matrix, scene_id)
    if row is None:
        return {"id": scene_id, "name": None, "refs": []}
    return {
        "id": scene_id,
        "name": row.get("name"),
        "refs": list((row.get("refs") or {}).get("images") or []),
    }


def qc_brief(project: "Project", shots: list[str] | None = None) -> dict:
    """Build the review package a vision-capable agent consumes (goal item 6).

    ``shots`` (a list of shot ids) scopes the brief; ``None`` briefs every shot
    with a usable selected take. Shots that cannot be reviewed (unknown id, no
    usable take) are reported under ``skipped`` with a 中文 reason rather than
    silently dropped. The judgment STANDARDS are not here — they live in the
    ``visual-qc-review`` skill the ``criteria`` pointer names.
    """
    from ..build.stale import evaluate_all
    from ..core.assets import asset_matrix

    statuses = {st.shot_id: st for st in evaluate_all(project)}
    try:
        matrix = asset_matrix(project)
    except Exception:
        matrix = {"kinds": {}}

    if shots is None:
        order = list(statuses.keys())
    else:
        order = list(shots)

    reviewed: list[dict] = []
    skipped: list[dict] = []
    known = set(project.shot_ids())

    for sid in order:
        if sid not in known:
            skipped.append({"shot": sid, "reason": "不是本项目镜头"})
            continue
        st = statuses.get(sid)
        take = st.take if st else None
        if take is None or take.media_path is None or not take.media_path.exists():
            skipped.append({"shot": sid, "reason": "无可用的已选 take(先 build/select)"})
            continue
        try:
            shot = project.load_shot(sid)
        except Exception:
            skipped.append({"shot": sid, "reason": "镜头文件无法读取"})
            continue

        characters = [_character_context(matrix, cid) for cid in shot.characters]
        dialogue = None
        if shot.dialogue and (shot.dialogue.text or shot.dialogue.speaker):
            dialogue = {"speaker": shot.dialogue.speaker, "text": shot.dialogue.text}

        reviewed.append({
            "shot": sid,
            "take": take.name,
            "take_hash": _safe_hash(take.media_path),
            "frames": _shot_frames(project, take),
            "context": {
                "scene": _scene_context(matrix, shot.scene),
                "characters": characters,
                "must_show": list(shot.quality.must_show),
                "avoid": list(shot.quality.avoid),
                "continuity_locks": list(shot.continuity.locks),
                "dialogue": dialogue,
            },
        })

    return {
        "project": project.load_config().name,
        "criteria": {"skill": CRITERIA_SKILL, "note": CRITERIA_NOTE},
        "verdict_contract": verdict_contract(),
        "shots": reviewed,
        "skipped": skipped,
    }


def _safe_hash(path) -> str | None:
    try:
        return hash_file(path)
    except Exception:
        return None


# --------------------------------------------------------------- verdict intake


def _resolve_take(project: "Project", shot: str, take_name: str) -> tuple[str, str | None]:
    """(effective take name, content hash) for the take a verdict judged.

    Prefers the named take; falls back to the shot's current selected take so a
    verdict that omits ``take`` still binds to concrete bytes. Missing media →
    hash ``None`` (the verdict is recorded but can never match — i.e. stale)."""
    take = project.get_take(shot, take_name) if take_name else None
    if take is None:
        try:
            sel = project.load_shot(shot).status.selected_take
            if sel:
                take = project.get_take(shot, sel)
        except Exception:
            take = None
    if take is None:
        return take_name, None
    h = None
    if take.media_path is not None and take.media_path.exists():
        h = _safe_hash(take.media_path)
    return take.name, h


def record_verdicts(project: "Project", payload: Any, *, actor: str = "ai") -> dict:
    """Validate + persist agent verdicts to ``reports/qc_agent.jsonl``.

    The whole batch is validated before a single line is written, so an unknown
    shot rejects the request cleanly (no partial log). Each record carries its
    ``ts``, the ``actor`` who judged, and the ``take_hash`` binding it to bytes.
    Raises :class:`VerdictError` on any structural problem.
    """
    if not isinstance(payload, dict):
        raise VerdictError("verdict 载荷必须是含 'verdicts' 数组的 JSON 对象")
    verdicts = payload.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        raise VerdictError("'verdicts' 必须是非空数组")

    known = set(project.shot_ids())
    records: list[dict] = []
    for i, v in enumerate(verdicts):
        if not isinstance(v, dict):
            raise VerdictError(f"verdict #{i} 必须是对象")
        shot = str(v.get("shot") or "").strip()
        if not shot:
            raise VerdictError(f"verdict #{i} 缺少 'shot'")
        if shot not in known:
            raise VerdictError(f"verdict #{i}: 未知镜头 {shot!r}(不是本项目镜头)")
        level = str(v.get("level") or "").strip().lower()
        if level not in LEVELS:
            raise VerdictError(
                f"verdict #{i}: level 必须是 blocker|issue|fyi 之一,收到 {v.get('level')!r}"
            )
        take_name, take_hash = _resolve_take(project, shot, str(v.get("take") or "").strip())
        rec: dict[str, Any] = {
            "ts": _now_iso(),
            "actor": actor,
            "shot": shot,
            "take": take_name,
            "take_hash": take_hash,
            "criterion": str(v.get("criterion") or "").strip(),
            "level": level,
            "message": str(v.get("message") or "").strip(),
            "evidence": str(v.get("evidence") or "").strip(),
        }
        fm = v.get("frame_ms")
        if fm is not None:
            try:
                rec["frame_ms"] = int(fm)
            except (TypeError, ValueError):
                pass
        records.append(rec)

    path = agent_log_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

    counts: dict[str, int] = {}
    for rec in records:
        counts[rec["level"]] = counts.get(rec["level"], 0) + 1
    try:
        from ..core.events import append_event

        append_event(project.root, actor, "qc_verdict",
                     {"count": len(records), "levels": counts})
    except Exception:
        pass

    return {"written": len(records), "levels": counts, "path": project.relpath(path)}


# ----------------------------------------------------------------------- merge


def _current_take_hash(project: "Project", shot_id: str) -> str | None:
    """Content hash of the shot's CURRENT selected take media, or None."""
    try:
        sel = project.load_shot(shot_id).status.selected_take
    except Exception:
        return None
    if not sel:
        return None
    take = project.get_take(shot_id, sel)
    if take is None or take.media_path is None or not take.media_path.exists():
        return None
    return _safe_hash(take.media_path)


def _read_records(project: "Project") -> tuple[list[dict], int]:
    """Parse qc_agent.jsonl → (records in file order, malformed-line count).
    Never raises: a torn/invalid line is skipped and counted."""
    path = agent_log_path(project)
    if not path.exists():
        return [], 0
    records: list[dict] = []
    malformed = 0
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            malformed += 1
            continue
        if not isinstance(rec, dict) or not rec.get("shot"):
            malformed += 1
            continue
        records.append(rec)
    return records, malformed


def agent_verdict_items(project: "Project") -> list[QCItem]:
    """Fold ``reports/qc_agent.jsonl`` into QC items (goal item 6, part C).

    Keeps only the LATEST verdict per (shot, criterion). A verdict whose bound
    take hash still equals the shot's current selected-take bytes surfaces as an
    ``[AI判读]`` content item at the mapped level; a shot whose bytes have since
    changed gets ONE "已过期" info item. Deterministic (sorted output); a
    malformed line is skipped and surfaced as a single info count."""
    records, malformed = _read_records(project)
    items: list[QCItem] = []

    # latest verdict per (shot, criterion) — append-only log, so last line wins.
    latest: dict[tuple[str, str], dict] = {}
    for rec in records:
        latest[(str(rec.get("shot")), str(rec.get("criterion") or ""))] = rec

    known = set(project.shot_ids())
    hash_cache: dict[str, str | None] = {}

    def current_hash(shot_id: str) -> str | None:
        if shot_id not in hash_cache:
            hash_cache[shot_id] = _current_take_hash(project, shot_id)
        return hash_cache[shot_id]

    stale_shots: set[str] = set()
    for (shot, crit), rec in sorted(latest.items()):
        if shot not in known:
            continue  # shot no longer exists — nothing to review
        chash = current_hash(shot)
        if chash is not None and rec.get("take_hash") == chash:
            level = LEVEL_MAP.get(str(rec.get("level")), "info")
            crit_label = crit or "—"
            body = rec.get("message") or ""
            message = f"{AI_PREFIX} {crit_label} · {rec.get('level')}: {body}".rstrip()
            hint_bits = []
            if rec.get("evidence"):
                hint_bits.append(f"依据:{rec['evidence']}")
            if rec.get("frame_ms") is not None:
                hint_bits.append(f"@{rec['frame_ms']}ms")
            hint_bits.append(f"take={rec.get('take')} 判读者={rec.get('actor', 'ai')}")
            items.append(QCItem(
                level, "content", shot, message,
                suggestion="; ".join(hint_bits),
            ))
        else:
            stale_shots.add(shot)

    for shot in sorted(stale_shots):
        items.append(QCItem(
            "info", "content", shot,
            "该镜头已有新版本,此前的 AI 判读已过期 — 重新跑 manju qc brief",
            suggestion="manju qc brief --shots " + shot,
        ))

    if malformed:
        items.append(QCItem(
            "info", "content", "qc_agent",
            f"reports/{AGENT_LOG} 有 {malformed} 行无法解析,已跳过",
        ))

    return items
