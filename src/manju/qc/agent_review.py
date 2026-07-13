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
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..core.hashing import cache_key, hash_file, hash_value, short_hash
from .checks import QCItem

if TYPE_CHECKING:
    from ..core.container import Project, TakeInfo

# reports/qc_agent.jsonl — append-only, one verdict record per line, UTF-8.
AGENT_LOG = "qc_agent.jsonl"

# DR02 (bound acceptance evidence). v2 packets/verdicts are content-addressed
# derived evidence layered ON the existing agent-review pipe — NOT a second
# subsystem. Packets persist under reports/qc_packets/<packet_id>.json; v2
# verdict records append to the SAME reports/qc_agent.jsonl, self-described by
# their `schema` field and skipped by the legacy reader (orchestrator ruling #1).
PACKET_SCHEMA = "manju.qc.packet/v2"
VERDICT_SCHEMA = "manju.qc.verdict/v2"
PACKETS_DIR = "qc_packets"
# any record whose schema starts with this is a DR02 v2 artifact, not a legacy
# per-shot/unit verdict — the legacy merge path (agent_verdict_items /
# qc_coverage) skips it; assurance.py's v2 reader conversely keeps only these.
MANJU_SCHEMA_PREFIX = "manju.qc."

# a reviewer OBSERVES one of these per expectation; the final PASS/FAIL/UNKNOWN
# is a pure function of observation vs expectation (assurance.py), never a model
# writing the result. `confidence` is optional metadata, never used to compute.
OBSERVED_STATES = ("present", "absent", "uncertain", "not_evaluated")

# intake hardening: a verdict file bigger than this is refused (a reviewer echo
# should be small; an oversized embedded blob is either a mistake or an attack).
MAX_VERDICT_BYTES = 256_000

# packet_id = "pkt_" + 12 hex; also the on-disk filename stem — this exact shape
# is required before we touch the filesystem, so a forged/traversing id can
# never escape reports/qc_packets/.
_PACKET_ID_RE = re.compile(r"^pkt_[0-9a-f]{12}$")

# The skill that carries the A–J judgment standards (a sibling agent authors it).
CRITERIA_SKILL = "visual-qc-review"
CRITERIA_NOTE = "先 manju skills show visual-qc-review 获取判读标准 A–J"
CONSISTENCY_NOTE = ("先 manju skills show visual-qc-review 获取判读标准 A–J"
                    "(一致性判读重点看 A/B 身份服装、C/D 场景光照连续性)")

# Netflix severity tiers (§6a) → Manju's existing finding levels.
LEVELS = ("blocker", "issue", "fyi")
LEVEL_MAP = {"blocker": "error", "issue": "warn", "fyi": "info"}

# 08_10_12C WP4 (Path A) — additive OPTIONAL production-decision fields on the
# EXISTING verdict v2. Absent => legacy verdict, byte-compatible. Present =>
# validated at intake (payload-invalid ⇒ whole-batch reject, zero writes) and
# persisted verbatim on the stored record. The reviewer still never writes
# assurance: acceptance stays qc/assurance.py's pure function; these fields are
# routing/view data (candidate family views, repair mapping), never an
# acceptance input.
DISPOSITIONS = ("KEEP", "FIX_IN_POST", "EDIT_DONT_REGENERATE",
                "REROLL", "REWRITE_SOURCE")
# §9.3: the one primary variable the next diagnostic try may change.
REPAIR_VARIABLES = (
    "clip_scope", "source_action", "camera", "motion", "endpoint",
    "reference_role", "reference_asset", "framing", "lighting",
    "text_overlay", "audio", "seed", "provider_surface", "safety_wording",
    "post_trim", "post_mask", "post_grade",
)
# §9.4: transient (opening/endpoint) observation enums — bound to the reviewed
# media bytes, NEVER promoted into Bible identity facts.
OBS_STATE_VISIBILITY = ("VISIBLE", "NOT_VISIBLE", "UNCERTAIN",
                        "NOT_EVALUATED", "NOT_APPLICABLE")
OBS_STATE_POSITIONS = ("START", "END")

# AI_IDE_15 §3/§6 (addendum ruling 3, Path A additive) — the cloud-visual
# reviewer's per-DIMENSION observation, a NEW optional field on verdict v2 that
# rides ALONGSIDE the per-expectation `observations` above. The 11 reviewer
# dimensions (§3): only observable facts, never inferred motive/plot.
REVIEWER_DIMENSIONS = (
    "character_identity",       # 1. 脸/发型/体型/关键特征
    "appearance_variant",       # 2. 服装/饰品/伤势/年龄/形态
    "scene",                    # 3. 布局/时间/天气/灯光/主要背景物
    "prop_product",             # 4. 存在/几何/颜色/文字/Logo
    "spatial_relations",        # 5. 相对位置/屏幕方向/视线/180°轴线
    "action_phase",             # 6. 是否完成/是否重启/手-物交互
    "composition_photography",  # 7. 景别/相机高度/运动阶段
    "style",                    # 8. 色板/材质/渲染风格
    "technical_defect",         # 9. 畸变/多肢/闪烁/穿插/跨帧文字纹理突变
    "color_exposure",           # 10. 白平衡/曝光/对比/肤色/色域伽马
    "lip_sync",                 # 11. 仅在具备对齐证据时评价
)
# §6 observed vocabulary — DISTINCT from the per-expectation OBSERVED_STATES; it
# expresses match-vs-reference, not present-vs-must_show. Still never accepted by
# a model: the PASS/FAIL/UNKNOWN authority stays qc/assurance.py's pure function.
DIMENSION_OBSERVED = ("match", "mismatch", "uncertain", "not_visible")
# §6 severity tiers (info|warning|blocker) — the reviewer's OWN severity, distinct
# from the finding LEVELS (blocker|issue|fyi). `confidence` never computes accept.
DIMENSION_SEVERITY = ("info", "warning", "blocker")

# Reviewer qualification gate (addendum ruling 1): a review dispatched to a vision
# provider below DRY_RUN_VALID is refused with a structured REVIEWER_NOT_QUALIFIED.
# The gate itself lives in the PROVIDER layer (``reviewer_admission`` in
# providers.qualification) — the qc package must NOT couple to the qualification
# evidence store (AI_IDE_14 build-boundary guard §15), and admission is a
# provider-layer concern. The offline fake double drives record_verdicts DIRECTLY
# (no real dispatch) and never consults the gate — so core needs no reference to
# the fake's id (20A ruling: runtime never reads the corpus).

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


def packets_dir(project: "Project"):
    """``reports/qc_packets/`` — where issued v2 packets persist, one
    content-addressed ``<packet_id>.json`` per packet (DR02 ruling #2). Packets
    are derived, regenerable evidence needed only between brief and intake;
    deleting this dir only invalidates in-flight reviews (assurance reads the
    verdict records, which carry all binding fields themselves)."""
    return project.reports_dir / PACKETS_DIR


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
            "v2": _verdict_contract_v2("consistency"),
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
        "v2": _verdict_contract_v2("shots"),
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


def _verdict_contract_v2(mode: str) -> dict:
    """The DR02 v2 return shape a reviewer echoes back (ruling #4): the
    ``packet_id`` from the brief plus per-expectation OBSERVATIONS. Additive —
    legacy contract fields above are untouched. The final PASS/FAIL/UNKNOWN is a
    pure function of observation vs expectation (qc/assurance.py); the reviewer
    NEVER writes the result, and ``confidence`` is metadata that never computes."""
    subject = ({"kind": "unit", "id": "一致性组合 id(取自本 brief 的 unit 字段)"}
               if mode == "consistency"
               else {"kind": "shot", "id": "镜头 id(取自本 brief 的 shot 字段)"})
    return {
        "schema": VERDICT_SCHEMA,
        "usage": "回显 brief 中该镜头/组合的 packet_id,并对每条 expectation 给出观察结论;"
                 "用 `manju qc verdict --from-file <路径>` 回填(MCP 用 qc_verdict)。",
        "shape": {
            "schema": VERDICT_SCHEMA,
            "packet_id": "pkt_…(必填,回显本 brief 中该镜头/组合的 packet_id)",
            "subject": subject,
            "observations": [
                {
                    "expectation_id": "exp:…(必填,须为 packet.expectations 中的 id)",
                    "observed": "present | absent | uncertain | not_evaluated(必填)",
                    "confidence": "可选,仅元数据,绝不参与 PASS/FAIL 判定",
                    "evidence_refs": ["frame:12 或帧路径(可选)"],
                }
            ],
            "findings": [
                {"level": "blocker | issue | fyi", "message": "中文结论",
                 "evidence": "可选佐证"}
            ],
            "reviewer": {"kind": "model_visual | human", "name": "判读者标识"},
        },
        "note": "结论由纯函数按 present/absent 逐条比对得出;若当前媒体/spec/expectation "
                "与 packet 绑定不符,该判读只作为历史保留(binding=stale),不计入当前验收。",
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


def _shot_frame_plan(project: "Project", take: "TakeInfo") -> dict | None:
    """The deterministic first/25/50/75/last review-frame plan for a take (§5
    WP1), keyed by duration/fps/media-hash via :func:`media.frames.frame_plan`.
    Pure and cheap (one ffprobe reused from the frame pass); degrades to ``None``
    on any failure so it never breaks the legacy brief. The plan's positions
    address the SAME ``.manju/frames`` cache the legacy first/mid/last frames use
    — this ADDS a plan, never a second cache."""
    from ..media.frames import frame_plan

    try:
        from ..media.probe import probe as _probe

        info = _probe(take.media_path)
        return frame_plan(info.duration_ms, info.fps, _safe_hash(take.media_path))
    except Exception:
        return None


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
            mode: str = "shots", compose_boards: bool = True) -> dict:
    """Build the review package a vision-capable agent consumes (goal item 6).

    ``shots`` (a list of shot ids) scopes the brief; ``None`` briefs every shot
    with a usable selected take. Shots that cannot be reviewed (unknown id, no
    usable take) are reported under ``skipped`` with a 中文 reason rather than
    silently dropped. The judgment STANDARDS are not here — they live in the
    ``visual-qc-review`` skill the ``criteria`` pointer names.

    ``mode="consistency"`` (round X, agent XB) briefs CROSS-shot comparison
    units instead — see :func:`_qc_brief_consistency` and the module docstring.
    ``shots`` still scopes it (a unit is kept when any member is in the list).

    ``compose_boards=False`` (consistency mode only) skips composing the
    per-unit contact-sheet images — the ONLY ffprobe/ffmpeg cost in the brief.
    The GUI /review page uses it to render the unit structure + verdict forms
    cheaply and subprocess-free on the request thread, then fetches the boards
    lazily from ``POST /api/review/consistency`` (audit G1). The default (True)
    keeps every CLI / MCP / on-demand caller byte-identical.
    """
    if mode == "consistency":
        return _qc_brief_consistency(project, shots, compose_boards=compose_boards)
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

        frames = _shot_frames(project, take)
        row = {
            "shot": sid,
            "take": take.name,
            "take_hash": _safe_hash(take.media_path),
            "frames": frames,
            "frame_plan": _shot_frame_plan(project, take),
            "context": {
                "scene": _scene_context(matrix, shot.scene),
                "characters": characters,
                "must_show": list(shot.quality.must_show),
                "avoid": list(shot.quality.avoid),
                "continuity_locks": list(shot.continuity.locks),
                "dialogue": dialogue,
            },
        }
        # DR02 v2: issue a content-addressed packet binding this row to the
        # CURRENT take bytes + spec_hash + expectation digest, embed its fields
        # alongside the legacy ones (byte-compatible), and persist the packet as
        # an issuance record intake later verifies. Best-effort: a packet
        # failure never breaks the (legacy) brief (module degrade-gracefully
        # stance) — the row simply lacks its v2 fields.
        try:
            packet = _issue_shot_packet(project, sid, take, frames)
            row.update({
                "schema": PACKET_SCHEMA,
                "packet_id": packet["packet_id"],
                "media": packet["media"],
                "spec_hash": packet["spec_hash"],
                "expectation_digest": packet["expectation_digest"],
                "expectations": packet["expectations"],
                "evidence_frames": packet["evidence_frames"],
                "review_skill": packet["review_skill"],
            })
        except Exception:
            pass
        # 08_10_12C WP4 §9.1: candidate-family + continuation context ride the
        # brief row as DERIVED, optional views (never persisted, never packet
        # identity). Best-effort — a view failure never breaks the brief.
        try:
            from .production import candidate_families, continuation_view

            fams = candidate_families(project, sid)["families"]
            row["candidate_family"] = next(
                (f for f in fams if any(m["take"] == take.name for m in f["takes"])),
                None)
            cont = continuation_view(project, sid)
            if cont is not None:
                row["continuation"] = cont
        except Exception:
            pass
        reviewed.append(row)

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


# ------------------------------------------------------- DR02 v2 packet issuance


def _packet_id(packet_body: dict) -> str:
    """``pkt_`` + first 12 hex of sha256 over the canonical packet payload
    EXCLUDING ``packet_id`` — content-derived, so re-issuing an identical packet
    yields the same id (idempotent) and a forged/edited packet no longer hashes
    to its own filename (intake step 1 catches it)."""
    body = {k: v for k, v in packet_body.items() if k != "packet_id"}
    return "pkt_" + short_hash(hash_value(body), 12)


def _write_packet(project: "Project", packet: dict) -> None:
    """Persist a packet to ``reports/qc_packets/<packet_id>.json`` (atomic).
    Content-addressed => immutable: an identical packet already on disk is left
    untouched (the write is idempotent, ruling #2)."""
    from ..core.yamlio import atomic_write_text

    pdir = packets_dir(project)
    dest = pdir / f"{packet['packet_id']}.json"
    if dest.exists():
        return  # same id => same content => nothing to rewrite
    pdir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(dest, json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))


def _load_packet(project: "Project", packet_id: str) -> dict | None:
    """Load a packet by id, or None if absent/unreadable. The id must match the
    ``pkt_<12 hex>`` shape BEFORE it is used as a filename, so a traversing or
    malformed id can never escape reports/qc_packets/."""
    if not (isinstance(packet_id, str) and _PACKET_ID_RE.match(packet_id)):
        return None
    dest = packets_dir(project) / f"{packet_id}.json"
    if not dest.is_file():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _issue_shot_packet(project: "Project", shot_id: str, take: "TakeInfo",
                       frames: dict) -> dict:
    """Build + persist the v2 packet for one shot row. The media sha256 is
    computed ONCE here from the CURRENT selected-take file (reusing
    core.hashing.hash_file) — THAT snapshot is the binding intake later checks a
    verdict against, which is the whole race fix. spec_hash + expectation digest
    come from :func:`compile_expectations` (never re-derived)."""
    from .expectations import compile_expectations

    es = compile_expectations(project, shot_id)
    evidence_frames = [frames[k] for k in ("first", "mid", "last") if frames.get(k)]
    body = {
        "schema": PACKET_SCHEMA,
        "subject": {"kind": "shot", "id": shot_id},
        "media": {"project_path": project.relpath(take.media_path),
                  "sha256": _safe_hash(take.media_path)},
        "spec_hash": es["spec_hash"],
        "expectation_digest": es["digest"],
        "expectations": es["expectations"],
        "evidence_frames": evidence_frames,
        "review_skill": CRITERIA_SKILL,
    }
    body["packet_id"] = _packet_id(body)
    _write_packet(project, body)
    return body


def _issue_unit_packet(project: "Project", unit: dict) -> dict:
    """Build + persist the v2 packet for one consistency UNIT: instead of a
    single media binding it binds EVERY member's take sha (``media_members`` map
    {shot: {take, sha256}}), so any one member regenerating stales the unit's
    evidence (ruling #4/#5). Units carry no per-shot expectations."""
    members_map = {
        m["shot"]: {"take": m["take"], "sha256": m["take_hash"]}
        for m in unit["members"]
    }
    body = {
        "schema": PACKET_SCHEMA,
        "subject": {"kind": "unit", "id": unit["unit"]},
        "media_members": members_map,
        "expectations": [],
        "review_skill": CRITERIA_SKILL,
    }
    body["packet_id"] = _packet_id(body)
    _write_packet(project, body)
    return body


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


def _member_duration_ms(take: "TakeInfo") -> int:
    """The member take's duration in ms for the review-frame midpoint.

    Reads the take's SIDECAR probe FIRST (``sidecar.probe.duration_ms``) — a
    generated take fills it at synthesis and takes are append-only, so the cache
    can never go stale; this is the SAME cache the video compiler reads at
    ``timeline/compiler.py`` (audit Tier-1 #2 / G1). Only a sidecar that carries
    no probe duration falls back to a live ffprobe. Returns 0 when neither is
    available, preserving the legacy ``int(info.duration_ms or 0)`` degrade.

    The value feeds an UNCHANGED midpoint (``dur // 2``), so a cached-duration
    path and a live-probe path that see the same duration request the byte-
    identical frame timestamp — the redundant per-member probe the content-
    addressed frame cache had already made moot, now removed."""
    probe_info = take.sidecar.probe if take.sidecar is not None else None
    if probe_info is not None and probe_info.duration_ms is not None:
        return int(probe_info.duration_ms or 0)
    try:
        from ..media.probe import probe as _probe

        info = _probe(take.media_path)
        return int(info.duration_ms or 0)
    except Exception:
        return 0


def _member_frame(project: "Project", shot_id: str, take_name: str):
    """One mid-point review frame for a comparison-unit member, via the
    content-addressed frames.py cache (same degrade-to-None-on-failure stance
    as :func:`_shot_frames`).

    The midpoint duration comes from :func:`_member_duration_ms` (sidecar probe
    first, a live ffprobe only when the sidecar carries none). The midpoint
    arithmetic is UNCHANGED, so the extracted frame is byte-identical whether the
    duration came from the sidecar or a live probe."""
    take = project.get_take(shot_id, take_name)
    if take is None or take.media_path is None or not take.media_path.exists():
        return None
    from ..media.frames import extract_frame

    try:
        media_rel = project.relpath(take.media_path)
    except Exception:
        return None
    dur = _member_duration_ms(take)
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


def _qc_brief_consistency(project: "Project", shots: list[str] | None = None, *,
                          compose_boards: bool = True) -> dict:
    """The consistency-mode brief body (round X, agent XB) — see the module
    docstring and :func:`qc_brief`.

    ``compose_boards=False`` yields every unit with ``image=None`` and composes
    NO contact sheet — the unit structure (members, criteria, packet) is pure
    and cheap (no ffprobe/ffmpeg), which is what the GUI /review render uses so
    the request thread stays subprocess-free (audit G1); the boards are fetched
    lazily afterwards with the default ``compose_boards=True``."""
    units, skipped = _consistency_units(project)
    if shots:
        wanted = set(shots)
        units = [u for u in units if wanted & {m["shot"] for m in u["members"]}]

    out_units: list[dict] = []
    for u in units:
        image = _compose_unit_board(project, u) if compose_boards else None
        row = {
            "unit": u["unit"],
            "kind": u["kind"],
            "label": u["label"],
            "image": image,
            "members": [{"shot": m["shot"], "take": m["take"], "take_hash": m["take_hash"]}
                       for m in u["members"]],
            "criteria": u["criteria"],
        }
        # DR02 v2: one packet per unit binding the member media map (best-effort,
        # never breaks the legacy consistency brief).
        try:
            packet = _issue_unit_packet(project, u)
            row["schema"] = PACKET_SCHEMA
            row["packet_id"] = packet["packet_id"]
            row["media_members"] = packet["media_members"]
        except Exception:
            pass
        out_units.append(row)

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
    verdict from the /review GUI, not only "ai" (§C, review flow).

    DR02: when the payload declares ``schema: "manju.qc.verdict/v2"`` it takes
    the bound-evidence intake path (:func:`_record_verdicts_v2`) instead — the
    legacy path below is untouched for every existing (schema-less) caller."""
    if isinstance(payload, dict) and \
            str(payload.get("schema") or "").startswith("manju.qc.verdict/"):
        return _record_verdicts_v2(project, payload, actor=actor)

    if not isinstance(payload, dict):
        raise VerdictError("verdict 载荷必须是含 'verdicts' 数组的 JSON 对象 "
                           "(the verdict payload must be a JSON object with a 'verdicts' array)")
    verdicts = payload.get("verdicts")
    if not isinstance(verdicts, list) or not verdicts:
        raise VerdictError("'verdicts' 必须是非空数组 ('verdicts' must be a non-empty array)")

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
            raise VerdictError(f"verdict #{i} 必须是对象 (verdict #{i} must be an object)")

        unit_id = str(v.get("unit") or "").strip()
        if unit_id:
            unit_def = unit_lookup(unit_id)
            if unit_def is None:
                raise VerdictError(
                    f"verdict #{i}: 未知一致性组合 {unit_id!r}"
                    "(不是当前可判读的组合;先 manju qc brief --mode consistency 出题) "
                    f"(unknown consistency unit {unit_id!r} — not a currently reviewable unit; "
                    "run manju qc brief --mode consistency first)"
                )
            level = str(v.get("level") or "").strip().lower()
            if level not in LEVELS:
                raise VerdictError(
                    f"verdict #{i}: level 必须是 blocker|issue|fyi 之一,收到 {v.get('level')!r} "
                    "(level must be one of blocker|issue|fyi)"
                )
            criterion = str(v.get("criterion") or "").strip()
            if not criterion:
                raise VerdictError(f"verdict #{i} 缺少 'criterion'(判读标准代码或简述,不能为空) "
                                   "(missing 'criterion' — a non-empty criterion code or brief description)")
            message = str(v.get("message") or "").strip()
            if not message:
                raise VerdictError(f"verdict #{i} 缺少 'message'(中文结论,不能为空) "
                                   "(missing 'message' — a non-empty conclusion)")
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
                raise VerdictError(f"verdict #{i} 缺少 'shot' (missing 'shot')")
            if shot not in known:
                raise VerdictError(f"verdict #{i}: 未知镜头 {shot!r}(不是本项目镜头) "
                                   f"(unknown shot {shot!r} — not a shot in this project)")
            level = str(v.get("level") or "").strip().lower()
            if level not in LEVELS:
                raise VerdictError(
                    f"verdict #{i}: level 必须是 blocker|issue|fyi 之一,收到 {v.get('level')!r} "
                    "(level must be one of blocker|issue|fyi)"
                )
            # round-W #33: criterion AND message are required non-empty — an
            # empty criterion/message verdict has no actionable meaning (which
            # A–J standard? what's the finding?), and — before this validation
            # existed — several empty-criterion verdicts on the same shot
            # would silently overwrite each other on merge (see the
            # aggregation key below).
            criterion = str(v.get("criterion") or "").strip()
            if not criterion:
                raise VerdictError(f"verdict #{i} 缺少 'criterion'(判读标准代码或简述,不能为空) "
                                   "(missing 'criterion' — a non-empty criterion code or brief description)")
            message = str(v.get("message") or "").strip()
            if not message:
                raise VerdictError(f"verdict #{i} 缺少 'message'(中文结论,不能为空) "
                                   "(missing 'message' — a non-empty conclusion)")
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
    # Windows gate round 1: this append had NO cross-writer lock — POSIX
    # append-mode small-writes are effectively atomic so it never showed on
    # Linux, but the gate's first run tore it (12 verdicts -> 6 intact lines).
    # Ride THE append coordinator (core/events.events_lock, WP2 §4.4 pattern:
    # own lock name, own sibling file under the disposable runtime dir).
    # Semantics preserved deliberately: a lock timeout degrades to today's
    # unlocked append rather than DROPPING verdicts — record_verdicts is an
    # explicit evidence API whose callers already wrote media/spend.
    from ..core.events import events_lock

    project.runtime_dir.mkdir(parents=True, exist_ok=True)
    with events_lock(project.root, lock_name=".manju/agent_review.lock"):
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


# ------------------------------------------------------ DR02 v2 verdict intake


def _record_verdicts_v2(project: "Project", payload: dict, *, actor: str) -> dict:
    """Intake bound-acceptance v2 verdicts. Accepts a single v2 verdict object
    or a batch ``{"schema": …, "verdicts": [obj, …]}``.

    Validation runs in the contract order per verdict. Steps 1/2/7/8 failures
    mean the PAYLOAD itself is invalid -> the WHOLE batch is rejected with a
    structured error and ZERO writes (partial-batch illegality never half-writes
    the log). Steps 3-6 mismatches mean the payload was valid for its packet but
    the WORLD moved -> the record is still stored (the reviewer's work is
    history) but marked ``binding: "stale"`` with the exact ``binding_failures``,
    so assurance never counts it as current evidence. Crucially the stored
    binding fields come from the PACKET (bytes A), never from re-hashing the
    current file (bytes B) — that is the race fix."""
    if isinstance(payload.get("verdicts"), list):
        raw_verdicts = payload["verdicts"]
        if not raw_verdicts:
            raise VerdictError("'verdicts' 必须是非空数组 ('verdicts' must be a non-empty array)")
    else:
        raw_verdicts = [payload]

    prepared: list[dict] = []
    errors: list[str] = []
    for i, v in enumerate(raw_verdicts):
        rec, errs = _prepare_v2_record(project, v, i, actor)
        if errs:
            errors.extend(errs)
        elif rec is not None:
            prepared.append(rec)

    if errors:
        # steps 1/2/7/8: reject the whole batch, zero writes, every violation.
        raise VerdictError("v2 verdict 批次被拒绝(未写入任何记录):" + "; ".join(errors))

    path = agent_log_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for rec in prepared:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())

    bindings: dict[str, int] = {}
    for rec in prepared:
        bindings[rec["binding"]] = bindings.get(rec["binding"], 0) + 1
    try:
        from ..core.events import append_event

        append_event(project.root, actor, "qc_verdict_v2",
                     {"count": len(prepared), "bindings": bindings})
    except Exception:
        pass

    return {"written": len(prepared), "bindings": bindings,
            "schema": VERDICT_SCHEMA, "path": project.relpath(path)}


def _prepare_v2_record(project: "Project", v: Any, idx: int, actor: str
                       ) -> tuple[dict | None, list[str]]:
    """Validate one v2 verdict against its packet + the CURRENT world state.
    Returns ``(record, [])`` when it may be stored (bound OR binding-stale) or
    ``(None, [reasons])`` when the payload is invalid (reject the whole batch).
    """
    errs: list[str] = []
    if not isinstance(v, dict):
        return None, [f"verdict #{idx}: 必须是对象"]

    # ---- step 8 (well-formedness: schema/types/enums/size/traversal/secret) ---
    vschema = v.get("schema")
    if vschema is not None and vschema != VERDICT_SCHEMA:
        errs.append(f"verdict #{idx}: 不支持的 schema {vschema!r}")

    packet_id = v.get("packet_id")
    if not (isinstance(packet_id, str) and packet_id.strip()):
        errs.append(f"verdict #{idx}: 缺少 packet_id")
        packet_id = ""
    else:
        packet_id = packet_id.strip()

    try:
        size = len(json.dumps(v, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        size = 0
        errs.append(f"verdict #{idx}: 载荷无法序列化为 JSON")
    if size > MAX_VERDICT_BYTES:
        errs.append(f"verdict #{idx}: 载荷过大({size} 字节 > {MAX_VERDICT_BYTES} 上限)")

    observations = v.get("observations")
    if observations is None:
        observations = []
    if not isinstance(observations, list):
        errs.append(f"verdict #{idx}: observations 必须是数组")
        observations = []
    obs_norm: list[dict] = []
    for j, o in enumerate(observations):
        if not isinstance(o, dict):
            errs.append(f"verdict #{idx} observation #{j}: 必须是对象")
            continue
        observed = o.get("observed")
        if observed not in OBSERVED_STATES:
            errs.append(f"verdict #{idx} observation #{j}: observed 必须是 "
                        f"{OBSERVED_STATES} 之一,收到 {observed!r}")
        eid = str(o.get("expectation_id") or "").strip()
        if not eid:
            errs.append(f"verdict #{idx} observation #{j}: 缺少 expectation_id")
        for ref in (o.get("evidence_refs") or []):
            if _is_unsafe_ref(ref):
                errs.append(f"verdict #{idx} observation #{j}: 不安全的 evidence_ref {ref!r}")
        obs_norm.append({
            "expectation_id": eid,
            "observed": observed,
            "confidence": o.get("confidence"),
            "evidence_refs": list(o.get("evidence_refs") or []),
        })

    # ---- step 7 (payload validity): no DUPLICATE expectation_id inside ONE
    # payload. assurance.diff() maps observations by id, so a repeated id is
    # silently last-one-wins — an earlier (possibly conflicting) observation
    # would be dropped. That is an invalid payload, not a world move: reject the
    # WHOLE batch with ZERO writes, never half-resolve the conflict. (P0-B, I)
    seen_eids: set[str] = set()
    dup_eids: list[str] = []
    for o in obs_norm:
        eid = o["expectation_id"]
        if not eid:
            continue
        if eid in seen_eids and eid not in dup_eids:
            dup_eids.append(eid)
        seen_eids.add(eid)
    for eid in dup_eids:
        errs.append(f"verdict #{idx}: expectation_id {eid!r} 在同一 payload 内重复"
                    "(每个期望至多一条 observation;整批拒绝,零写入)")

    findings = v.get("findings")
    if findings is None:
        findings = []
    if not isinstance(findings, list):
        errs.append(f"verdict #{idx}: findings 必须是数组")
        findings = []
    for j, fnd in enumerate(findings):
        if not isinstance(fnd, dict):
            errs.append(f"verdict #{idx} finding #{j}: 必须是对象")
            continue
        if fnd.get("level") not in LEVELS:
            errs.append(f"verdict #{idx} finding #{j}: level 必须是 "
                        f"blocker|issue|fyi 之一,收到 {fnd.get('level')!r}")
        if _is_unsafe_ref(fnd.get("evidence")):
            errs.append(f"verdict #{idx} finding #{j}: 不安全的 evidence 路径")

    # ---- step 8 (08_10_12C WP4, Path A additive): production decision +
    # transient observed states. OPTIONAL — absent is a legal legacy payload.
    # Present but malformed ⇒ payload-invalid ⇒ whole-batch reject, zero writes.
    decision_norm = None
    decision = v.get("decision")
    if decision is not None:
        if not isinstance(decision, dict):
            errs.append(f"verdict #{idx}: decision 必须是对象")
        else:
            dispo = decision.get("disposition")
            if dispo not in DISPOSITIONS:
                errs.append(f"verdict #{idx}: decision.disposition 必须是 "
                            f"{'|'.join(DISPOSITIONS)} 之一,收到 {dispo!r}")
            prv = decision.get("primary_repair_variable")
            if dispo in DISPOSITIONS and dispo != "KEEP" and prv is None:
                errs.append(f"verdict #{idx}: 非 KEEP 的 decision 必须指定一个 "
                            "primary_repair_variable(§9.3 one-variable rule)")
            if prv is not None and prv not in REPAIR_VARIABLES:
                errs.append(f"verdict #{idx}: 未知 primary_repair_variable {prv!r}"
                            f"(必须是 {'|'.join(REPAIR_VARIABLES)} 之一)")
            iso = decision.get("diagnostic_isolation", True)
            if not isinstance(iso, bool):
                errs.append(f"verdict #{idx}: decision.diagnostic_isolation 必须是布尔值")
            decision_norm = {"disposition": dispo, "diagnostic_isolation": iso}
            if prv is not None:
                decision_norm["primary_repair_variable"] = prv
            reason = decision.get("reason")
            if reason is not None:
                decision_norm["reason"] = str(reason)

    obs_states_norm = None
    obs_states = v.get("observed_states")
    if obs_states is not None:
        if not isinstance(obs_states, list):
            errs.append(f"verdict #{idx}: observed_states 必须是数组")
        else:
            obs_states_norm = []
            for j, s in enumerate(obs_states):
                if not isinstance(s, dict):
                    errs.append(f"verdict #{idx} observed_state #{j}: 必须是对象")
                    continue
                dim = str(s.get("dimension") or "").strip()
                if not dim:
                    errs.append(f"verdict #{idx} observed_state #{j}: 缺少 dimension")
                if s.get("position") not in OBS_STATE_POSITIONS:
                    errs.append(f"verdict #{idx} observed_state #{j}: position 必须是 "
                                f"{OBS_STATE_POSITIONS} 之一,收到 {s.get('position')!r}")
                if s.get("visibility") not in OBS_STATE_VISIBILITY:
                    errs.append(f"verdict #{idx} observed_state #{j}: visibility 必须是 "
                                f"{OBS_STATE_VISIBILITY} 之一,收到 {s.get('visibility')!r}")
                for ref in (s.get("evidence_refs") or []):
                    if _is_unsafe_ref(ref):
                        errs.append(f"verdict #{idx} observed_state #{j}: "
                                    f"不安全的 evidence_ref {ref!r}")
                conf = s.get("confidence")
                if conf is not None and (not isinstance(conf, (int, float))
                                         or isinstance(conf, bool)
                                         or not 0.0 <= float(conf) <= 1.0):
                    errs.append(f"verdict #{idx} observed_state #{j}: confidence "
                                f"必须是 0..1 的数,收到 {conf!r}")
                obs_states_norm.append({
                    "dimension": dim,
                    "position": s.get("position"),
                    "value": str(s.get("value") or ""),
                    "visibility": s.get("visibility"),
                    "evidence_refs": list(s.get("evidence_refs") or []),
                    "confidence": conf,
                })

    # ---- step 8 (AI_IDE_15 §6, Path A additive): per-DIMENSION observations.
    # OPTIONAL — absent is a legal legacy payload. Present but malformed (unknown
    # dimension/observed/severity/variable, bad confidence, unsafe ref) ⇒
    # payload-invalid ⇒ whole-batch reject, ZERO writes (same path as above). The
    # reviewer NEVER writes accepted: these are routing/drift view data, never an
    # acceptance input (that stays qc/assurance.py's pure diff).
    dim_obs_norm = None
    dim_obs = v.get("dimension_observations")
    if dim_obs is not None:
        if not isinstance(dim_obs, list):
            errs.append(f"verdict #{idx}: dimension_observations 必须是数组")
        else:
            dim_obs_norm = []
            for j, o in enumerate(dim_obs):
                if not isinstance(o, dict):
                    errs.append(f"verdict #{idx} dimension_observation #{j}: 必须是对象")
                    continue
                dim = o.get("dimension")
                if dim not in REVIEWER_DIMENSIONS:
                    errs.append(f"verdict #{idx} dimension_observation #{j}: dimension "
                                f"必须是 11 个 reviewer 维度之一,收到 {dim!r}")
                observed = o.get("observed")
                if observed not in DIMENSION_OBSERVED:
                    errs.append(f"verdict #{idx} dimension_observation #{j}: observed "
                                f"必须是 {DIMENSION_OBSERVED} 之一,收到 {observed!r}")
                sev = o.get("severity")
                if sev is not None and sev not in DIMENSION_SEVERITY:
                    errs.append(f"verdict #{idx} dimension_observation #{j}: severity "
                                f"必须是 {DIMENSION_SEVERITY} 之一或省略,收到 {sev!r}")
                var = o.get("suggested_repair_variable")
                if var is not None and var not in REPAIR_VARIABLES:
                    errs.append(f"verdict #{idx} dimension_observation #{j}: 未知 "
                                f"suggested_repair_variable {var!r}")
                conf = o.get("confidence")
                if conf is not None and (not isinstance(conf, (int, float))
                                         or isinstance(conf, bool)
                                         or not 0.0 <= float(conf) <= 1.0):
                    errs.append(f"verdict #{idx} dimension_observation #{j}: confidence "
                                f"必须是 0..1 的数,收到 {conf!r}")
                for ref in (o.get("evidence_refs") or []):
                    if _is_unsafe_ref(ref):
                        errs.append(f"verdict #{idx} dimension_observation #{j}: "
                                    f"不安全的 evidence_ref {ref!r}")
                dim_obs_norm.append({
                    "dimension": dim,
                    "subject_ref": (str(o["subject_ref"])
                                    if o.get("subject_ref") is not None else None),
                    "observed": observed,
                    "severity": sev,
                    "confidence": conf,
                    "evidence_refs": list(o.get("evidence_refs") or []),
                    "explanation": (str(o["explanation"])
                                    if o.get("explanation") is not None else None),
                    "suggested_repair_variable": var,
                })

    if _contains_secret(v):
        errs.append(f"verdict #{idx}: 载荷疑似包含 API key/密钥,拒绝存储"
                    "(密钥只应存在于环境变量,§8.2)")

    # ---- step 1: packet exists on disk, schema supported, content-id matches ---
    packet = _load_packet(project, packet_id) if packet_id else None
    if packet_id and packet is None:
        errs.append(f"verdict #{idx}: packet {packet_id!r} 不存在于 reports/qc_packets/"
                    "(缺失或已删除)")
    elif packet is not None:
        if packet.get("schema") != PACKET_SCHEMA:
            errs.append(f"verdict #{idx}: packet schema 不支持 {packet.get('schema')!r}")
        elif _packet_id(packet) != packet_id:
            errs.append(f"verdict #{idx}: packet 内容与其 id 不符(伪造/篡改)")

    # ---- step 2: subject matches the packet's subject (when the verdict names one)
    subject = packet.get("subject") if isinstance(packet, dict) else None
    if isinstance(packet, dict) and v.get("subject") is not None \
            and v.get("subject") != packet.get("subject"):
        errs.append(f"verdict #{idx}: subject {v.get('subject')!r} 与 packet 的 "
                    f"{packet.get('subject')!r} 不符")

    # ---- step 7: every observed expectation_id is a member of the packet ----
    if isinstance(packet, dict):
        packet_eids = {e.get("id") for e in (packet.get("expectations") or [])}
        for o in obs_norm:
            if o["expectation_id"] and o["expectation_id"] not in packet_eids:
                errs.append(f"verdict #{idx}: 未知 expectation_id "
                            f"{o['expectation_id']!r}(不在 packet.expectations 内)")

    # ---- step 8 (cont.): a payload that ECHOES binding fields must echo its own
    # packet's values — an echo contradicting the packet means the reviewer is
    # confused about WHAT they judged. That is invalid payload (whole-batch
    # reject), not a world move (which steps 3-6 handle as binding-stale).
    if isinstance(packet, dict):
        for field, expected in (
            ("media_sha256", (packet.get("media") or {}).get("sha256")),
            ("spec_hash", packet.get("spec_hash")),
            ("expectation_digest", packet.get("expectation_digest")),
        ):
            got = v.get(field)
            if got is not None and expected is not None and got != expected:
                errs.append(f"verdict #{idx}: {field} 回显 {got!r} 与 packet 的 "
                            f"{expected!r} 不符(判读对象混淆)")

    if errs:
        return None, errs

    # ---- steps 3-6: recompute CURRENT state; a mismatch marks the record stale
    binding_failures = _binding_failures(project, packet)
    rec: dict[str, Any] = {
        "ts": _now_iso(),
        "actor": actor,
        "schema": VERDICT_SCHEMA,
        "binding": "stale" if binding_failures else "bound",
        "packet_id": packet_id,
        "subject": subject,
        # binding fields come from the PACKET (bytes A), NOT from re-hashing the
        # current file — re-hashing above was only for COMPARISON. This is the
        # core race fix: intake never stamps the current bytes onto the evidence.
        "spec_hash": packet.get("spec_hash"),
        "expectation_digest": packet.get("expectation_digest"),
        "observations": obs_norm,
        "findings": findings,
        "reviewer": v.get("reviewer") if isinstance(v.get("reviewer"), dict) else {},
    }
    if "media" in packet:
        rec["media_sha256"] = (packet.get("media") or {}).get("sha256")
        rec["media_project_path"] = (packet.get("media") or {}).get("project_path")
    if "media_members" in packet:
        rec["media_members"] = packet.get("media_members")
    if binding_failures:
        rec["binding_failures"] = binding_failures
    # 08_10_12C WP4 additive: persist the validated production decision +
    # transient observed states verbatim (absent for legacy payloads).
    if decision_norm is not None:
        rec["decision"] = decision_norm
    if obs_states_norm is not None:
        rec["observed_states"] = obs_states_norm
    # AI_IDE_15 §6 additive: persist the validated per-dimension observations
    # verbatim (absent for legacy payloads). The reviewer's severity/confidence
    # ride here as routing/drift data — assurance never reads them.
    if dim_obs_norm is not None:
        rec["dimension_observations"] = dim_obs_norm
    return rec, []


def _binding_failures(project: "Project", packet: dict) -> list[str]:
    """Steps 3-6: which of the packet's bindings no longer match CURRENT state.
    Returns a sorted subset of ``["expectations", "media", "spec"]`` (empty ==
    still bound). For a UNIT packet only the member media map is checked (any
    member move => ``"media"``); shot packets check current selected-take
    path+bytes (``"media"``), current spec_hash (``"spec"``) and current
    recompiled expectation digest (``"expectations"``)."""
    subject = packet.get("subject") or {}
    failures: set[str] = set()

    if subject.get("kind") == "unit":
        current = _current_unit_members(project, subject.get("id"))
        recorded = {sid: info.get("sha256")
                    for sid, info in (packet.get("media_members") or {}).items()}
        if current != recorded:
            failures.add("media")
        return sorted(failures)

    sid = subject.get("id")
    cur_path, cur_sha = _current_selected_media(project, sid)
    media = packet.get("media") or {}
    if cur_sha is None or cur_sha != media.get("sha256") \
            or cur_path != media.get("project_path"):
        failures.add("media")
    if _current_spec_hash(project, sid) != packet.get("spec_hash"):
        failures.add("spec")
    if _current_expectation_digest(project, sid) != packet.get("expectation_digest"):
        failures.add("expectations")
    return sorted(failures)


def _current_selected_media(project: "Project", shot_id) -> tuple[str | None, str | None]:
    """(project-relative path, sha256) of the shot's CURRENT selected take media,
    hashed NOW purely for comparison. (None, None) when there is none."""
    try:
        sel = project.load_shot(shot_id).status.selected_take
    except Exception:
        return None, None
    if not sel:
        return None, None
    take = project.get_take(shot_id, sel)
    if take is None or take.media_path is None or not take.media_path.exists():
        return None, None
    try:
        return project.relpath(take.media_path), _safe_hash(take.media_path)
    except Exception:
        return None, None


def _current_spec_hash(project: "Project", shot_id) -> str | None:
    from ..core.spec import SPEC_VERSION, compute_spec_hash

    try:
        shot = project.load_shot(shot_id)
        return compute_spec_hash(shot, project.load_bible(),
                                 version=SPEC_VERSION, project_root=project.root)
    except Exception:
        return None


def _current_expectation_digest(project: "Project", shot_id) -> str | None:
    from .expectations import compile_expectations

    try:
        return compile_expectations(project, shot_id)["digest"]
    except Exception:
        return None


def _current_unit_members(project: "Project", unit_id) -> dict[str, str | None]:
    """{shot: current take sha} for a consistency unit's members NOW, or {} when
    the unit no longer exists — the comparison basis for a unit's step-4 analog."""
    try:
        units, _skipped = _consistency_units(project)
    except Exception:
        return {}
    for u in units:
        if u["unit"] == unit_id:
            return {m["shot"]: m["take_hash"] for m in u["members"]}
    return {}


def _is_unsafe_ref(ref: Any) -> bool:
    """A path-ish reference that escapes the project (``..`` component, absolute,
    home/backslash-rooted, or a NUL byte) — rejected at intake so a verdict can
    never smuggle a traversal into stored evidence. Plain refs like ``frame:12``
    are fine (no path separators to escape)."""
    if not isinstance(ref, str) or not ref:
        return False
    if "\x00" in ref:
        return True
    if ref.startswith(("/", "~", "\\")):
        return True
    return ".." in re.split(r"[\\/]", ref)


def _contains_secret(v: Any) -> bool:
    """Cheap secret scan reusing core.check.SECRET_PATTERNS: a verdict must not
    carry API keys/tokens into stored evidence (§8.2). Degrades to False if the
    helper is unavailable."""
    try:
        from ..core.check import SECRET_PATTERNS
    except Exception:
        return False
    try:
        blob = json.dumps(v, ensure_ascii=False)
    except (TypeError, ValueError):
        return False
    return any(p.search(blob) for p in SECRET_PATTERNS)


def read_v2_records(project: "Project") -> tuple[list[dict], int]:
    """Every DR02 v2 verdict record in ``reports/qc_agent.jsonl`` (file order) +
    the malformed-line count. The COMPLEMENT of the legacy reader: keeps only
    lines whose ``schema`` is the v2 verdict schema, skips legacy lines. Used by
    assurance.py (the v2 reader lives there conceptually; this is the shared
    jsonl-format knowledge kept in one module). Never raises."""
    path = agent_log_path(project)
    if not path.exists():
        return [], 0
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    records: list[dict] = []
    malformed = 0
    for raw in raw_lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except Exception:
            malformed += 1
            continue
        if not isinstance(rec, dict):
            malformed += 1
            continue
        if rec.get("schema") == VERDICT_SCHEMA:
            records.append(rec)
    return records, malformed


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
    """Parse qc_agent.jsonl → (LEGACY records in file order, malformed-line
    count). Never raises: a torn/invalid line is skipped and counted. Round X
    (agent XB): a record identifies itself by EITHER ``shot`` (the round-V
    per-shot verdict) OR ``unit`` (a round-X consistency-comparison verdict) —
    either is sufficient to keep the line.

    DR02 ruling #1: a line carrying a ``schema`` key starting with
    ``manju.qc.`` is a v2 artifact (bound-evidence verdict) — the legacy merge
    path SKIPS it (it is neither kept NOR counted malformed here; assurance.py's
    v2 reader consumes it). Legacy lines have no ``schema`` key and are
    unaffected, so every existing reader stays byte-identical."""
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
        if isinstance(rec, dict) and \
                str(rec.get("schema") or "").startswith(MANJU_SCHEMA_PREFIX):
            continue  # a DR02 v2 record — not legacy; assurance reads these
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
