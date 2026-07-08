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

Round X (agent XB): the round-V pipe above judges each shot in ISOLATION —
consistency is a CROSS-shot / shot-vs-reference property, not a per-shot one
(user pain #2). ``qc_brief(..., mode="consistency")`` builds COMPARISON UNITS
instead of per-shot rows:

    character  one CONTACT SHEET per character appearing in >1 reviewable shot
               — the character's bible ref image(s) + one take frame from
               EVERY shot they appear in, composed via ``media.boards.make_board``
               (identity/outfit drift, §A/B of visual-qc-review).
    pair       one side-by-side 2-cell board per ADJACENT shot pair sharing a
               scene (scene/lighting continuity, §C/D).
    scene      one contact sheet across every reviewable shot of a scene (same
               §C/D, wider lens).

Every unit is content-addressed under ``.manju/frames`` exactly like a scene
board, and every verdict against it binds to ALL member shots' take hashes at
once — any ONE member regenerating stales the whole unit's verdict (the same
staleness contract as a shot verdict, widened). ``qc_coverage`` reports, per
shot AND per unit, whether it has ever been AI-judged, and whether that
judgment is still current.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..core.hashing import cache_key, hash_file, short_hash
from .checks import QCItem

if TYPE_CHECKING:
    from ..core.container import Project, TakeInfo

# reports/qc_agent.jsonl — append-only, one verdict record per line, UTF-8.
AGENT_LOG = "qc_agent.jsonl"

# The skill that carries the A–J judgment standards (a sibling agent authors it).
CRITERIA_SKILL = "visual-qc-review"
CRITERIA_NOTE = "先 manju skills show visual-qc-review 获取判读标准 A–J"
CONSISTENCY_NOTE = ("先 manju skills show visual-qc-review 获取判读标准 A–J"
                    "(一致性判读重点看 A/B 身份服装、C/D 场景光照连续性)")

# Netflix severity tiers (§6a) → Manju's existing finding levels.
LEVELS = ("blocker", "issue", "fyi")
LEVEL_MAP = {"blocker": "error", "issue": "warn", "fyi": "info"}

# The 中文 prefix that marks a finding as the driving agent's own judgment.
AI_PREFIX = "[AI判读]"

# round X (agent XB): per-unit-kind criteria pointers for the consistency brief
# — narrower than CONSISTENCY_NOTE so each unit tells the reviewer exactly
# which A–J sections to apply (identity/outfit for a character contact sheet,
# scene/lighting for a pair or scene contact sheet).
_IDENTITY_CRITERIA = {
    "sections": "A/B",
    "note": ("角色身份/服装一致性:先看 A(A1 镜内不变脸/A2 跨镜身份一致/A3 固定标记不迁移/"
            "A4 人数稳定)再看 B(B1-B4 服装型色态/跨切一致/配饰/物理);"
            "manju skills show visual-qc-review"),
}
_CONTINUITY_CRITERIA = {
    "sections": "C/D",
    "note": ("场景/光照连续性:看 C(C1-C5 背景物保形保位/道具留位/无穿越出戏物/无 AI 纹理/"
            "无幻觉元素)和 D(D1-D4 阴影方向/面部光反射/时段曝光一致/无亮度闪烁);"
            "manju skills show visual-qc-review"),
}
_KIND_LABEL = {"character": "角色", "pair": "镜头对", "scene": "场景"}


class VerdictError(ValueError):
    """A malformed / unacceptable verdict payload (unknown shot, bad level…)."""


def agent_log_path(project: "Project"):
    """``reports/qc_agent.jsonl`` under the project."""
    return project.reports_dir / AGENT_LOG


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------- verdict contract


def verdict_contract(mode: str = "shots") -> dict:
    """The return-shape spec embedded in every brief so the agent knows exactly
    what to hand back — the mirror of :func:`record_verdicts`'s validation.

    ``mode="consistency"`` (round X, agent XB) is the same JSON contract but
    keyed by ``unit`` (a comparison-unit id from the consistency brief) instead
    of ``shot`` — a verdict against a unit binds to ALL its member take hashes
    at once (§ module docstring)."""
    if mode == "consistency":
        return {
            "usage": "把每个一致性组合的判读结果写成下面的 JSON,用 "
                     "`manju qc verdict --from-file <路径>`(或 `-` 走 stdin)回填;"
                     "MCP 用 qc_verdict 工具。",
            "shape": {
                "verdicts": [
                    {
                        "unit": "一致性组合 id(必填,取自本 brief 的 unit 字段,如 "
                                "character:linxia / pair:S001~S002 / scene:convenience_store)",
                        "criterion": "A–J 判读标准代码或自由文本(如 A2 / C1;必填)",
                        "level": "blocker | issue | fyi",
                        "message": "中文结论(必填)",
                        "evidence": "帧路径或文字佐证",
                        "frame_ms": "可选,问题所在的毫秒位置",
                    }
                ]
            },
            "levels": {"blocker": "error/阻断(不可上线)", "issue": "warn/需修",
                       "fyi": "info/知悉"},
            "note": "判读结果与组合内所有成员镜头当前 take 的字节绑定;任一成员镜头"
                    "重生成后,该组合的旧判读整体过期。",
        }
    return {
        "usage": "把判读结果写成下面的 JSON,用 `manju qc verdict --from-file <路径>` "
                 "(或 `-` 走 stdin)回填;MCP 用 qc_verdict 工具。",
        "shape": {
            "verdicts": [
                {
                    "shot": "镜头 id(必填,须为本项目镜头)",
                    "take": "take 名(来自本 brief;省略则绑定当前已选 take)",
                    "criterion": "A–J 判读标准代码或自由文本(如 A1 / 穿帮 / 手部;必填)",
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


def qc_brief(project: "Project", shots: list[str] | None = None, *,
            mode: str = "shots") -> dict:
    """Build the review package a vision-capable agent consumes (goal item 6).

    ``shots`` (a list of shot ids) scopes the brief; ``None`` briefs every shot
    with a usable selected take. Shots that cannot be reviewed (unknown id, no
    usable take) are reported under ``skipped`` with a 中文 reason rather than
    silently dropped. The judgment STANDARDS are not here — they live in the
    ``visual-qc-review`` skill the ``criteria`` pointer names.

    ``mode="consistency"`` (round X, agent XB) briefs CROSS-shot comparison
    units instead — see :func:`_qc_brief_consistency` and the module docstring.
    ``shots`` still scopes it (a unit is kept when any member is in the list).
    """
    if mode == "consistency":
        return _qc_brief_consistency(project, shots)
    if mode != "shots":
        raise ValueError(f"mode 必须是 shots|consistency,收到 {mode!r}")

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

    try:
        coverage = qc_coverage(project)
    except Exception:
        coverage = None

    return {
        "project": project.load_config().name,
        "mode": "shots",
        "criteria": {"skill": CRITERIA_SKILL, "note": CRITERIA_NOTE},
        "verdict_contract": verdict_contract(),
        "shots": reviewed,
        "skipped": skipped,
        "coverage": coverage,
    }


def _safe_hash(path) -> str | None:
    try:
        return hash_file(path)
    except Exception:
        return None


# ------------------------------------------------ consistency units (round X)


def _member_info(project: "Project", statuses: dict, sid: str) -> dict | None:
    """The lightweight (shot, take, take_hash) binding for one comparison-unit
    member — NO frame extraction here (kept cheap so it is safe to call for
    every verdict submission and every coverage check, not only when
    composing a brief's contact-sheet image; see :func:`_member_frame`)."""
    st = statuses.get(sid)
    take = st.take if st else None
    if take is None or take.media_path is None or not take.media_path.exists():
        return None
    return {"shot": sid, "take": take.name, "take_hash": _safe_hash(take.media_path)}


def _member_frame(project: "Project", shot_id: str, take_name: str):
    """One mid-point review frame for a comparison-unit member, via the
    content-addressed frames.py cache (same degrade-to-None-on-failure stance
    as :func:`_shot_frames`)."""
    take = project.get_take(shot_id, take_name)
    if take is None or take.media_path is None or not take.media_path.exists():
        return None
    from ..media.frames import extract_frame

    try:
        media_rel = project.relpath(take.media_path)
    except Exception:
        return None
    dur = 0
    try:
        from ..media.probe import probe as _probe

        info = _probe(take.media_path)
        dur = int(info.duration_ms or 0)
    except Exception:
        dur = 0
    at_ms = dur // 2 if dur else 0
    try:
        return extract_frame(project, media_rel, at_ms)
    except Exception:
        return None


def _consistency_units(project: "Project") -> tuple[list[dict], list[dict]]:
    """Build the three consistency-QC comparison-unit kinds (round X, agent
    XB): per character (>1 reviewable appearance), per adjacent shot pair
    sharing a scene, per scene (>=2 reviewable shots). Pure and cheap — no
    frame extraction, no ffmpeg — so it is safe on every verdict submission and
    every coverage check, not only when composing a brief. Returns
    ``(units, skipped)``; a unit's ``members`` are :func:`_member_info` dicts."""
    from ..build.stale import evaluate_all
    from ..core.assets import asset_matrix, find_asset

    statuses = {st.shot_id: st for st in evaluate_all(project)}
    cache: dict[str, dict | None] = {}

    def member(sid: str) -> dict | None:
        if sid not in cache:
            cache[sid] = _member_info(project, statuses, sid)
        return cache[sid]

    try:
        matrix = asset_matrix(project)
    except Exception:
        matrix = {"kinds": {}}

    units: list[dict] = []
    skipped: list[dict] = []

    # (1) per character appearing in >1 reviewable shot — identity/outfit.
    for row in matrix.get("kinds", {}).get("character", []):
        cid = row["id"]
        appearances = row.get("appearances") or []
        members = [m for sid in appearances if (m := member(sid)) is not None]
        if len(members) < 2:
            if appearances:
                skipped.append({
                    "unit": f"character:{cid}", "kind": "character",
                    "reason": "可判读镜头(已选 take)不足 2 个,跳过一致性组合",
                })
            continue
        units.append({
            "unit": f"character:{cid}", "kind": "character",
            "label": row.get("name") or cid,
            "members": members,
            "refs": list((row.get("refs") or {}).get("images") or []),
            "criteria": _IDENTITY_CRITERIA,
        })

    order = project.shot_ids()
    shot_scene: dict[str, str | None] = {}
    for sid in order:
        try:
            shot_scene[sid] = project.load_shot(sid).scene
        except Exception:
            shot_scene[sid] = None

    # (2) per adjacent shot pair sharing a scene — scene/lighting continuity.
    for prev_id, cur_id in zip(order, order[1:]):
        scene = shot_scene.get(prev_id)
        if not scene or scene != shot_scene.get(cur_id):
            continue
        m1, m2 = member(prev_id), member(cur_id)
        if m1 is None or m2 is None:
            skipped.append({
                "unit": f"pair:{prev_id}~{cur_id}", "kind": "pair",
                "reason": "成对镜头缺少可用 take,跳过一致性组合",
            })
            continue
        srow = find_asset(matrix, scene)
        scene_label = (srow.get("name") if srow else None) or scene
        units.append({
            "unit": f"pair:{prev_id}~{cur_id}", "kind": "pair",
            "label": f"{prev_id} → {cur_id}({scene_label})",
            "members": [m1, m2],
            "refs": [],
            "criteria": _CONTINUITY_CRITERIA,
        })

    # (3) per scene with >=2 reviewable shots — the wide-lens contact sheet.
    scenes: dict[str, list[str]] = {}
    for sid in order:
        sc = shot_scene.get(sid)
        if sc:
            scenes.setdefault(sc, []).append(sid)
    for scene_id, shots_in_scene in scenes.items():
        if len(shots_in_scene) < 2:
            continue
        members = [m for sid in shots_in_scene if (m := member(sid)) is not None]
        if len(members) < 2:
            skipped.append({
                "unit": f"scene:{scene_id}", "kind": "scene",
                "reason": "可判读镜头(已选 take)不足 2 个,跳过一致性组合",
            })
            continue
        srow = find_asset(matrix, scene_id)
        units.append({
            "unit": f"scene:{scene_id}", "kind": "scene",
            "label": (srow.get("name") if srow else None) or scene_id,
            "members": members,
            "refs": [],
            "criteria": _CONTINUITY_CRITERIA,
        })

    units.sort(key=lambda u: u["unit"])
    skipped.sort(key=lambda s: s["unit"])
    return units, skipped


def _compose_unit_board(project: "Project", unit: dict) -> str | None:
    """Compose (or reuse — content-addressed, same discipline as
    ``media.frames``/``media.boards.scene_board``) the unit's contact-sheet
    image under ``.manju/frames``. Degrades to ``None`` on any failure (no
    ffmpeg, no readable member frame at all) — the brief still lists the unit
    and its members; only the visual aid is absent."""
    from ..media.boards import board_cell_dims, grid_dims, make_board
    from ..media.ffmpeg import MediaError, default_log
    from ..media.frames import frames_cache_dir

    cells: list[tuple] = []
    if unit["kind"] == "character":
        for rel in unit.get("refs") or []:
            try:
                p = project.resolve(rel)
            except Exception:
                continue
            if p.is_file():
                cells.append((p, "参考图"))
    for m in unit["members"]:
        cells.append((_member_frame(project, m["shot"], m["take"]), m["shot"]))

    if not cells or not any(img is not None for img, _lab in cells):
        return None

    n = len(cells)
    grid = 2 if n == 2 else (4 if n <= 4 else 9)
    cols, rows = grid_dims(grid)
    cells = cells[: cols * rows]
    images = [c[0] for c in cells]
    labels = [c[1] for c in cells]

    cw, ch = board_cell_dims(project)
    key = _unit_board_key(unit["unit"], images, labels, grid, cw, ch)
    cache = frames_cache_dir(project.root)
    dest = cache / f"qc_consistency_{key}.jpg"
    if dest.exists():
        return project.relpath(dest)

    cache.mkdir(parents=True, exist_ok=True)
    try:
        make_board(images, grid, dest, labels=labels, cell=(cw, ch),
                  log=default_log(project.root, "qc_consistency"))
    except MediaError:
        return None
    except Exception:
        return None
    return project.relpath(dest)


def _unit_board_key(unit_id: str, images: list, labels: list[str], grid: int,
                    cw: int, ch: int) -> str:
    """Content-addressed key — input frame hashes + labels + grid + cell dims +
    the unit id (so two units that happen to share identical frames never
    collide on one cache file)."""
    parts: list[list[str]] = []
    for img, lab in zip(images, labels):
        fh = hash_file(img) if (img is not None and img.is_file()) else "blank"
        parts.append([fh, lab or ""])
    return short_hash(cache_key("qc_consistency_board_v1", unit_id, grid, cw, ch, parts))


def _qc_brief_consistency(project: "Project", shots: list[str] | None = None) -> dict:
    """The consistency-mode brief body (round X, agent XB) — see the module
    docstring and :func:`qc_brief`."""
    units, skipped = _consistency_units(project)
    if shots:
        wanted = set(shots)
        units = [u for u in units if wanted & {m["shot"] for m in u["members"]}]

    out_units: list[dict] = []
    for u in units:
        image = _compose_unit_board(project, u)
        out_units.append({
            "unit": u["unit"],
            "kind": u["kind"],
            "label": u["label"],
            "image": image,
            "members": [{"shot": m["shot"], "take": m["take"], "take_hash": m["take_hash"]}
                       for m in u["members"]],
            "criteria": u["criteria"],
        })

    try:
        coverage = qc_coverage(project)
    except Exception:
        coverage = None

    return {
        "project": project.load_config().name,
        "mode": "consistency",
        "criteria": {"skill": CRITERIA_SKILL, "note": CONSISTENCY_NOTE},
        "verdict_contract": verdict_contract(mode="consistency"),
        "units": out_units,
        "skipped": skipped,
        "coverage": coverage,
    }


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
    """Validate + persist agent OR human verdicts to ``reports/qc_agent.jsonl``.

    The whole batch is validated before a single line is written, so an unknown
    shot rejects the request cleanly (no partial log). Each record carries its
    ``ts``, the ``actor`` who judged, and the ``take_hash`` binding it to bytes.
    Raises :class:`VerdictError` on any structural problem.

    Round X (agent XB): a verdict may target a consistency comparison UNIT
    instead of a shot — set ``unit`` (a comparison-unit id from a
    ``mode="consistency"`` brief) instead of ``shot``. The record then binds
    to ALL of that unit's CURRENT member take hashes at once (recomputed at
    write time via :func:`_consistency_units`, mirroring the shot path's
    :func:`_resolve_take`); the ``actor`` param already covers "human" filing a
    verdict from the /review GUI, not only "ai" (§C, review flow)."""
    if not isinstance(payload, dict):
        raise VerdictError("verdict 载荷必须是含 'verdicts' 数组的 JSON 对象")
    verdicts = payload.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        raise VerdictError("'verdicts' 必须是非空数组")

    known = set(project.shot_ids())
    units_cache: list[dict] | None = None  # lazy: only built if a unit verdict appears

    def unit_lookup(unit_id: str) -> dict | None:
        nonlocal units_cache
        if units_cache is None:
            units_cache, _skipped = _consistency_units(project)
        return next((u for u in units_cache if u["unit"] == unit_id), None)

    records: list[dict] = []
    for i, v in enumerate(verdicts):
        if not isinstance(v, dict):
            raise VerdictError(f"verdict #{i} 必须是对象")

        unit_id = str(v.get("unit") or "").strip()
        if unit_id:
            unit_def = unit_lookup(unit_id)
            if unit_def is None:
                raise VerdictError(
                    f"verdict #{i}: 未知一致性组合 {unit_id!r}"
                    "(不是当前可判读的组合;先 manju qc brief --mode consistency 出题)"
                )
            level = str(v.get("level") or "").strip().lower()
            if level not in LEVELS:
                raise VerdictError(
                    f"verdict #{i}: level 必须是 blocker|issue|fyi 之一,收到 {v.get('level')!r}"
                )
            criterion = str(v.get("criterion") or "").strip()
            if not criterion:
                raise VerdictError(f"verdict #{i} 缺少 'criterion'(判读标准代码或简述,不能为空)")
            message = str(v.get("message") or "").strip()
            if not message:
                raise VerdictError(f"verdict #{i} 缺少 'message'(中文结论,不能为空)")
            rec: dict[str, Any] = {
                "ts": _now_iso(),
                "actor": actor,
                "unit": unit_id,
                "kind": unit_def["kind"],
                "members": [
                    {"shot": m["shot"], "take": m["take"], "take_hash": m["take_hash"]}
                    for m in unit_def["members"]
                ],
                "criterion": criterion,
                "level": level,
                "message": message,
                "evidence": str(v.get("evidence") or "").strip(),
            }
        else:
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
            # round-W #33: criterion AND message are required non-empty — an
            # empty criterion/message verdict has no actionable meaning (which
            # A–J standard? what's the finding?), and — before this validation
            # existed — several empty-criterion verdicts on the same shot
            # would silently overwrite each other on merge (see the
            # aggregation key below).
            criterion = str(v.get("criterion") or "").strip()
            if not criterion:
                raise VerdictError(f"verdict #{i} 缺少 'criterion'(判读标准代码或简述,不能为空)")
            message = str(v.get("message") or "").strip()
            if not message:
                raise VerdictError(f"verdict #{i} 缺少 'message'(中文结论,不能为空)")
            take_name, take_hash = _resolve_take(project, shot, str(v.get("take") or "").strip())
            rec = {
                "ts": _now_iso(),
                "actor": actor,
                "shot": shot,
                "take": take_name,
                "take_hash": take_hash,
                "criterion": criterion,
                "level": level,
                "message": message,
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
    Never raises: a torn/invalid line is skipped and counted. Round X (agent
    XB): a record identifies itself by EITHER ``shot`` (the round-V per-shot
    verdict) OR ``unit`` (a round-X consistency-comparison verdict) — either
    is sufficient to keep the line."""
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
        if not isinstance(rec, dict) or not (rec.get("shot") or rec.get("unit")):
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
    malformed line is skipped and surfaced as a single info count.

    Round X (agent XB): also folds consistency-UNIT verdicts (records carrying
    ``unit`` instead of ``shot`` — see :func:`_consistency_verdict_items`), the
    same shape widened to bind ALL member take hashes at once."""
    records, malformed = _read_records(project)
    items: list[QCItem] = []

    shot_records = [r for r in records if not r.get("unit")]
    unit_records = [r for r in records if r.get("unit")]

    # Latest verdict per (shot, criterion, message-hash) — append-only log, so
    # the last line for a given key wins. round-W #33: keying on (shot,
    # criterion) alone let multiple DISTINCT findings that happened to share an
    # empty criterion (legacy files written before criterion/message became
    # required — see record_verdicts) silently overwrite each other, leaving
    # only the last one visible. record_verdicts now REJECTS an empty
    # criterion/message outright, so this collision is impossible for new
    # records; the wider key is kept so an old jsonl file from before this fix
    # still surfaces every distinct legacy finding instead of losing them.
    latest: dict[tuple[str, str, str], dict] = {}
    for rec in shot_records:
        crit = str(rec.get("criterion") or "")
        msg_hash = hashlib.sha1(str(rec.get("message") or "").encode("utf-8")).hexdigest()[:12]
        latest[(str(rec.get("shot")), crit, msg_hash)] = rec

    known = set(project.shot_ids())
    hash_cache: dict[str, str | None] = {}

    def current_hash(shot_id: str) -> str | None:
        if shot_id not in hash_cache:
            hash_cache[shot_id] = _current_take_hash(project, shot_id)
        return hash_cache[shot_id]

    stale_shots: set[str] = set()
    for (shot, crit, _msg_hash), rec in sorted(latest.items()):
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

    if unit_records:
        items.extend(_consistency_verdict_items(project, unit_records))

    if malformed:
        items.append(QCItem(
            "info", "content", "qc_agent",
            f"reports/{AGENT_LOG} 有 {malformed} 行无法解析,已跳过",
        ))

    return items


def _consistency_verdict_items(project: "Project", unit_records: list[dict]) -> list[QCItem]:
    """The unit-scoped twin of the shot-fold loop above (round X, agent XB): a
    verdict surfaces only when its recorded member (shot → take_hash) map
    equals the unit's CURRENT member map exactly — any one member regenerating
    stales the whole unit's verdict, reported as ONE info item per unit."""
    latest: dict[tuple[str, str, str], dict] = {}
    for rec in unit_records:
        uid = str(rec.get("unit") or "")
        crit = str(rec.get("criterion") or "")
        msg_hash = hashlib.sha1(str(rec.get("message") or "").encode("utf-8")).hexdigest()[:12]
        latest[(uid, crit, msg_hash)] = rec

    units, _skipped = _consistency_units(project)
    current_members = {u["unit"]: {m["shot"]: m["take_hash"] for m in u["members"]}
                       for u in units}
    unit_kind = {u["unit"]: u["kind"] for u in units}
    unit_label = {u["unit"]: u["label"] for u in units}

    items: list[QCItem] = []
    stale_units: set[str] = set()
    for (uid, crit, _msg_hash), rec in sorted(latest.items()):
        cur = current_members.get(uid)
        if cur is None:
            continue  # the unit no longer exists (shot removed/renamed) — nothing to review
        rec_members = {m.get("shot"): m.get("take_hash") for m in (rec.get("members") or [])}
        if rec_members and rec_members == cur:
            level = LEVEL_MAP.get(str(rec.get("level")), "info")
            crit_label = crit or "—"
            body = rec.get("message") or ""
            kind_label = _KIND_LABEL.get(unit_kind.get(uid, ""), unit_kind.get(uid, ""))
            label = unit_label.get(uid, uid)
            message = (f"{AI_PREFIX} 一致性/{kind_label} {label} · {crit_label} · "
                      f"{rec.get('level')}: {body}").rstrip()
            hint_bits = []
            if rec.get("evidence"):
                hint_bits.append(f"依据:{rec['evidence']}")
            if rec.get("frame_ms") is not None:
                hint_bits.append(f"@{rec['frame_ms']}ms")
            hint_bits.append(f"成员={','.join(sorted(cur))} 判读者={rec.get('actor', 'ai')}")
            items.append(QCItem(level, "content", uid, message,
                                suggestion="; ".join(hint_bits)))
        else:
            stale_units.add(uid)

    for uid in sorted(stale_units):
        items.append(QCItem(
            "info", "content", uid,
            "该一致性组合有成员镜头已更新,此前的 AI 判读已过期 — "
            "重新跑 manju qc brief --mode consistency",
            suggestion="manju qc brief --mode consistency",
        ))
    return items


# --------------------------------------------------------------- coverage


def qc_coverage(project: "Project") -> dict:
    """Per-shot AND per-consistency-unit AI-judgment coverage (round X, agent
    XB): ``reviewed`` (a verdict exists whose bound bytes match the CURRENT
    take(s)), ``stale`` (a verdict exists but the bytes moved), or ``never``
    (no verdict was ever recorded). Read-only, never raises — a broken log or
    matrix degrades to an empty coverage picture rather than failing the
    caller (``run_qc`` folds ``summary.gaps`` in as one info item)."""
    from ..build.stale import evaluate_all

    try:
        statuses = {st.shot_id: st for st in evaluate_all(project)}
    except Exception:
        statuses = {}
    records, _malformed = _read_records(project)

    shot_records: dict[str, list[dict]] = {}
    unit_records: dict[str, list[dict]] = {}
    for r in records:
        uid = r.get("unit")
        if uid:
            unit_records.setdefault(str(uid), []).append(r)
        else:
            sid = r.get("shot")
            if sid:
                shot_records.setdefault(str(sid), []).append(r)

    reviewable_shots = [
        sid for sid in project.shot_ids()
        if _member_info(project, statuses, sid) is not None
    ]
    shots_out: dict[str, str] = {}
    for sid in reviewable_shots:
        current = _current_take_hash(project, sid)
        recs = shot_records.get(sid, [])
        if not recs:
            shots_out[sid] = "never"
        elif current is not None and any(r.get("take_hash") == current for r in recs):
            shots_out[sid] = "reviewed"
        else:
            shots_out[sid] = "stale"

    units, _skipped = _consistency_units(project)
    units_out: dict[str, dict] = {}
    for u in units:
        uid = u["unit"]
        current_hashes = {m["shot"]: m["take_hash"] for m in u["members"]}
        recs = unit_records.get(uid, [])
        if not recs:
            state = "never"
        else:
            matches = any(
                {m.get("shot"): m.get("take_hash") for m in (r.get("members") or [])}
                == current_hashes
                for r in recs
            )
            state = "reviewed" if matches else "stale"
        units_out[uid] = {"state": state, "kind": u["kind"], "label": u["label"]}

    def _count(states: list[str], want: str) -> int:
        return sum(1 for s in states if s == want)

    shot_states = list(shots_out.values())
    unit_states = [v["state"] for v in units_out.values()]
    summary = {
        "shots_total": len(shot_states),
        "shots_reviewed": _count(shot_states, "reviewed"),
        "shots_stale": _count(shot_states, "stale"),
        "shots_never": _count(shot_states, "never"),
        "units_total": len(unit_states),
        "units_reviewed": _count(unit_states, "reviewed"),
        "units_stale": _count(unit_states, "stale"),
        "units_never": _count(unit_states, "never"),
    }
    summary["gaps"] = summary["shots_never"] + summary["units_never"]

    return {"shots": shots_out, "units": units_out, "summary": summary}
