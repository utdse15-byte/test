"""Single-action video-prompt checks (goal item 8).

Deterministic, LLM-free heuristics over a shot's *action* / video-prompt text.
A text-to-video model renders a SINGLE beat well; a prompt that packs several
sequential actions, several camera/subject motion paths, or a duration past the
routed provider's clip ceiling tends to smear, drop actions, or get truncated.
These checks name that BEFORE a paid generate call, and — where a split would
help — propose a concrete, deterministic split at clause boundaries (as data;
nothing is auto-applied, §0 keeps the LLM out of the tool).

Every finding is a JSON-serialisable dict::

    {"level": "advisory" | "warning",
     "code": str,
     "message": str,          # 中文-first (style: qc/checks.py)
     "suggestion": str,       # 中文-first, one actionable line
     "split": {...}}          # OPTIONAL — present only when a split is proposed

User-facing strings are Chinese-first. All heuristics are robust to pure-CJK
text (no spaces): clause and motion counting are regex-driven, never word-split.
"""

from __future__ import annotations

import re
from typing import Any

# ------------------------------------------------------------------ levels
ADVISORY = "advisory"
WARNING = "warning"

# `manju prompt --check` exits non-zero on findings at or above this tier. These
# deterministic heuristics escalate to WARNING at worst (a prompt problem is a
# quality risk, not a hard build error, §9), so WARNING is the blocking tier.
FAIL_LEVELS = frozenset({WARNING})

# ------------------------------------------------------------------ codes
CODE_TOO_MANY_ACTIONS = "too_many_actions"
CODE_TOO_MANY_MOTION_PATHS = "too_many_motion_paths"
CODE_EXCESSIVE_DURATION = "excessive_duration_for_provider"

# ------------------------------------------------------------------ thresholds
# A shot should carry ONE action; two is tolerated, three or more is "too many".
MAX_ACTIONS = 2
# One-to-two motion paths read cleanly; three or more fight each other. Exactly
# two earns an advisory nudge toward a single motion (the top of the good band).
MAX_MOTION_PATHS = 2

# --------------------------------------------------------------- clause markers
# Sequencing markers that separate distinct action clauses. Multi-character and
# longer markers come FIRST so the alternation prefers them (e.g. 然后 before 后,
# "and then" before "then"/"and") — regex alternation is leftmost-first.
_ZH_SEQ_MARKERS = ("然后", "接着", "并且", "同时", "先", "后", "再", "、")
_EN_SEQ_MARKERS = ("and then", "then", "while", "and")


def _clause_regex() -> re.Pattern[str]:
    parts: list[str] = [re.escape(m) for m in sorted(_ZH_SEQ_MARKERS, key=len, reverse=True)]
    # English markers are ASCII words — guard with \b so "and" never fires inside
    # "grand"/"android"; CJK markers need no boundary (no word chars around them).
    for m in sorted(_EN_SEQ_MARKERS, key=len, reverse=True):
        parts.append(r"\b" + re.escape(m) + r"\b")
    return re.compile("|".join(parts), re.IGNORECASE)


_CLAUSE_RE = _clause_regex()

# Trailing/leading punctuation trimmed off each clause fragment (CJK + ASCII).
_CLAUSE_TRIM = " \t\r\n,，。.;;、!?！?：:—-"


def split_clauses(text: str) -> list[str]:
    """Split action text into distinct action clauses on the sequencing markers.

    Robust to pure-CJK input (no spaces): splitting is purely marker-driven.
    Empty fragments (e.g. a leading 先) and pure-punctuation fragments are
    dropped. Deterministic — same text always yields the same clause list."""
    text = (text or "").strip()
    if not text:
        return []
    fragments = _CLAUSE_RE.split(text)
    out: list[str] = []
    for frag in fragments:
        clause = frag.strip(_CLAUSE_TRIM).strip()
        if clause:
            out.append(clause)
    return out


# --------------------------------------------------------------- motion markers
# Each occurrence of one of these in the text is one motion-path mention. The
# camera's structured `movement` field (when not static) is counted separately
# in :func:`count_motion_paths` so both the free text and the camera field are
# honoured. Bounded quantifiers keep the CJK 从…到… span from matching greedily.
_MOTION_PATTERNS = (
    r"从.{1,20}?到",                    # 从A到B — a subject/camera path
    r"走向", r"转身", r"穿过", r"绕过",  # common subject-motion verbs
    r"镜头(?:推进|拉远|推|拉|摇|移|跟|升|降|环绕|旋转)",  # camera + motion verb (CJK)
    r"\bcamera\s+\w+",                  # camera + verb (English)
    r"\bpan\b", r"\btrack\b", r"\bpush\b", r"\bpull\b", r"\bzoom\b",
)
_MOTION_RE = re.compile("|".join(_MOTION_PATTERNS), re.IGNORECASE)


def count_motion_paths(text: str, camera_movement: str | None = None) -> int:
    """Count motion-path mentions in ``text`` plus the camera's own movement.

    A non-static ``camera_movement`` (the structured Camera field) is one motion
    path in its own right, added on top of the free-text mentions."""
    n = len(_MOTION_RE.findall(text or ""))
    if camera_movement and str(camera_movement).strip() not in ("", "static"):
        n += 1
    return n


# ------------------------------------------------------------- split proposals


def _distribute_ms(total: int, parts: int) -> list[int]:
    """Split ``total`` ms across ``parts`` sub-shots, deterministic — even base,
    the remainder folded into the LAST part so the sum is exact."""
    parts = max(1, parts)
    total = max(0, int(total))
    base = total // parts
    out = [base] * parts
    out[-1] += total - base * parts
    return out


def _suggest_split(clauses: list[str], duration_ms: int, *, reason: str,
                   min_parts: int = 2) -> dict[str, Any]:
    """A concrete deterministic split at clause boundaries — the CLI prints it,
    nothing auto-applies (§0). One sub-shot per clause when there are enough
    clauses; otherwise the (single) clause text is repeated across ``min_parts``
    duration-halved sub-shots (a duration-only split still gives the director a
    starting point). Returns JSON-serialisable data."""
    texts = clauses if len(clauses) >= 2 else None
    if texts is None:
        # too few clauses to split by boundary — fall back to N even slices that
        # keep the whole (single) action but each fit under the ceiling.
        n = max(min_parts, 2)
        base_text = clauses[0] if clauses else ""
        texts = [base_text for _ in range(n)]
    durations = _distribute_ms(duration_ms, len(texts))
    return {
        "reason": reason,
        "total_duration_ms": int(duration_ms),
        "sub_shots": [
            {"index": i + 1, "text": t, "duration_ms": d}
            for i, (t, d) in enumerate(zip(texts, durations))
        ],
    }


# ------------------------------------------------------------------- the checks


def _action_text(shot) -> str:
    """The text the checks read — MUST mirror ``providers.prompt.compile_prompt``'s
    EXACT precedence (round-W #71): ``generation.prompt_override``, when
    non-empty, wins VERBATIM and UNCONDITIONALLY — ``compile_prompt`` returns
    it before even looking at ``action.main``. ``action.main`` is only the
    FALLBACK the compiler itself falls back to when no override is set.

    The old code checked ``action.main`` first, falling back to
    ``prompt_override`` only when ``action.main`` was empty — the OPPOSITE
    precedence. A shot with BOTH fields set would have its real, provider-
    bound prompt (``prompt_override``) go completely unchecked while this
    linter scrutinized text that was never actually sent."""
    override = getattr(shot.generation, "prompt_override", None)
    if isinstance(override, str) and override.strip():
        return override.strip()
    return (getattr(shot.action, "main", "") or "").strip()


def _routed_max_duration_ms(project, shot) -> tuple[int | None, str | None]:
    """The routed provider's max clip length (ms) and its id — read from the
    manifest's ``limits.max_duration_ms``. Returns ``(None, provider)`` when the
    routed provider has no manifest or declares no ceiling (skip honestly), and
    ``(None, None)`` when routing itself cannot resolve (e.g. broken routing.yaml
    — that is `route explain`'s to surface, never this linter's crash)."""
    try:
        from ..providers.registry import fallback_chain, get_manifest
        from ..providers.routing import resolve

        chosen = resolve(project, shot).chosen
    except Exception:
        return None, None
    if not chosen:
        chain = fallback_chain(shot)
        chosen = chain[0] if chain else None
    if not chosen:
        return None, None
    manifest = get_manifest(chosen)
    max_ms = getattr(getattr(manifest, "limits", None), "max_duration_ms", None) if manifest else None
    return (int(max_ms) if max_ms else None), chosen


def check_shot(project, shot, *, duration_ms: int | None = None) -> list[dict[str, Any]]:
    """Run the single-action heuristics on one shot; return the findings list.

    Read-only and deterministic. ``duration_ms`` may be passed in (the bundle
    already computed it) to avoid recomputing the target duration; otherwise it
    is derived from the shot + rules exactly as the build plan does."""
    findings: list[dict[str, Any]] = []
    text = _action_text(shot)

    if duration_ms is None:
        try:
            from ..build.graph import _target_duration_ms

            duration_ms = _target_duration_ms(project, shot, project.load_rules())
        except Exception:
            duration_ms = 0

    # (1) too many actions — distinct sequential clauses in one shot.
    clauses = split_clauses(text)
    if len(clauses) > MAX_ACTIONS:
        finding = {
            "level": WARNING,
            "code": CODE_TOO_MANY_ACTIONS,
            "message": (
                f"单个镜头包含 {len(clauses)} 个连续动作(超过 {MAX_ACTIONS} 个)— "
                "文生视频模型对单一动作表现最好,多动作容易糊/丢动作"
            ),
            "suggestion": "把动作拆成每镜一个:见下面的拆分建议,或改写 action.main 只保留一个动作",
            "split": _suggest_split(clauses, duration_ms or 0,
                                    reason=CODE_TOO_MANY_ACTIONS),
        }
        findings.append(finding)

    # (2) too many motion paths — camera/subject movement mentions.
    motion = count_motion_paths(text, getattr(shot.camera, "movement", None))
    if motion > MAX_MOTION_PATHS:
        findings.append({
            "level": WARNING,
            "code": CODE_TOO_MANY_MOTION_PATHS,
            "message": (
                f"镜头运动路径过多({motion} 处运动/移动,建议不超过 {MAX_MOTION_PATHS} 处)"
                "— 同一镜头里多条运动路径会互相打架"
            ),
            "suggestion": "保留一条主运动(镜头运动或主体走位二选一),其余拆到相邻镜头",
        })
    elif motion == MAX_MOTION_PATHS:
        findings.append({
            "level": ADVISORY,
            "code": CODE_TOO_MANY_MOTION_PATHS,
            "message": (
                f"镜头运动接近上限({motion} 处运动)— 单一运动的镜头更稳"
            ),
            "suggestion": "如无必要,尽量让镜头只做一种运动",
        })

    # (3) excessive time span for the routed provider.
    max_ms, provider = _routed_max_duration_ms(project, shot)
    if max_ms is not None and duration_ms and duration_ms > max_ms:
        n_parts = max(2, -(-int(duration_ms) // max_ms))  # ceil division
        findings.append({
            "level": WARNING,
            "code": CODE_EXCESSIVE_DURATION,
            "message": (
                f"镜头时长 {duration_ms}ms 超过供应商 {provider} 的单段上限 "
                f"{max_ms}ms — 会被截断或拒绝"
            ),
            "suggestion": (
                f"把镜头切成 {n_parts} 段(每段 ≤ {max_ms}ms),或降低 duration;见下面的拆分建议"
            ),
            "split": _suggest_split(clauses, duration_ms or 0,
                                    reason=CODE_EXCESSIVE_DURATION, min_parts=n_parts),
        })

    return findings


def check_all(project) -> list[dict[str, Any]]:
    """Run :func:`check_shot` PLUS the 08_10_12C production checks over every
    shot; each finding tagged with its ``shot`` id, in shot order. Used by
    ``manju prompt --check`` (production findings carry the same
    ``level``/``code``/``message``/``suggestion`` keys, so the printer and
    :func:`has_blocking` treat them uniformly)."""
    out: list[dict[str, Any]] = []
    for sid in project.shot_ids():
        try:
            shot = project.load_shot(sid)
        except Exception:
            continue  # a broken shot file is `manju check`'s job, not this linter's
        for finding in check_shot(project, shot):
            out.append({"shot": sid, **finding})
        for finding in production_checks(project, shot):
            out.append({"shot": sid, **finding})
    return out


def has_blocking(findings: list[dict[str, Any]]) -> bool:
    """True when any finding is at a blocking (WARNING) level — the exit-code
    predicate for ``manju prompt --check``."""
    return any(f.get("level") in FAIL_LEVELS for f in findings)


# ============================================================================
# 08_10_12C WP2 — production checks (deterministic, read-only, never applied)
#
# Every finding names the problem + the authored source path and PROPOSES a
# patch; nothing here splits shots, edits prompts, or touches refs. Shape is a
# superset of the legacy finding dict (level/code/message/suggestion) plus the
# contract keys (severity/source_paths/proposal/auto_apply=False), so the CLI
# printer, ``has_blocking`` and the --json envelope all work unchanged.
# ============================================================================

CODE_CLIP_SCOPE_MULTIPLE = "CLIP_SCOPE_MULTIPLE_COMPLETED_ACTIONS"
CODE_CLIP_SCOPE_FUTURE_LEAK = "CLIP_SCOPE_FUTURE_BEAT_LEAK"
CODE_CLIP_SCOPE_ENDPOINT_MISSING = "CLIP_SCOPE_ENDPOINT_MISSING_FOR_CONTINUATION"
CODE_REF_ROLE_AMBIGUOUS = "REFERENCE_ROLE_AMBIGUOUS"
CODE_REF_TRANSFER_UNDECLARED = "REFERENCE_TRANSFER_UNDECLARED"
CODE_REF_CONTROL_CONFLICT = "REFERENCE_CONTROL_CONFLICT"
CODE_REF_SOURCE_STALE = "REFERENCE_SOURCE_STALE"
CODE_SURFACE_PROFILE_STALE = "SURFACE_PROFILE_STALE"
CODE_SURFACE_PROFILE_UNKNOWN = "SURFACE_PROFILE_UNKNOWN"
CODE_PROMPT_BUDGET_EXCEEDED = "PROMPT_BUDGET_EXCEEDED"
# PROVIDER_CAPABILITY_MISMATCH is DR04's fact — produced by CALLING preflight,
# never by re-deriving its rules here.
CODE_PROVIDER_CAPABILITY_MISMATCH = "PROVIDER_CAPABILITY_MISMATCH"

# future-beat markers: things a single clip cannot show yet (reserved beats).
_FUTURE_PATTERNS = (
    r"将要", r"将会", r"即将", r"随后将", r"之后会", r"接下来会", r"未来",
    r"下一镜", r"预示", r"埋下伏笔",
    r"\bwill\s+(?:later|then|soon)\b", r"\babout\s+to\b",
    r"\bin\s+the\s+next\s+shot\b", r"\bforeshadow\w*\b", r"\blater\s+reveal\w*\b",
)
_FUTURE_RE = re.compile("|".join(_FUTURE_PATTERNS), re.IGNORECASE)

# endpoint markers: the shot authors where its picture LANDS (for continuation).
_ENDPOINT_PATTERNS = (
    r"最后", r"最终", r"结尾", r"定格", r"收在", r"落在", r"止于", r"停在",
    r"\bends?\s+(?:on|with)\b", r"\bfinally\b", r"\bfreeze\s*frame\b",
)
_ENDPOINT_RE = re.compile("|".join(_ENDPOINT_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------- surface profiles
# §7.5: internal, source-dated data shipped WITH the compiler — never fetched,
# never guessed from a brand prefix, never a substitute for DR04's capability
# projection. Exactly one conservative family + an UNKNOWN fallback (no DSL).
SURFACE_PROFILES: dict[str, dict[str, Any]] = {
    "generic-video-v1": {
        "id": "generic-video-v1",
        "family": "generic-video",
        "version": 1,
        "status": "current",
        "evidence_date": "2026-06-30",
        "prompt_char_budget": 2000,
        "reference_expression": "image_list",
        "notes": "conservative cross-surface envelope; execution limits stay "
                 "with DR04 provider profile/preflight",
    },
    "generic-video-v0": {
        "id": "generic-video-v0",
        "family": "generic-video",
        "version": 0,
        "status": "superseded",  # kept so an authored pin surfaces as STALE
        "evidence_date": "2025-10-01",
        "prompt_char_budget": 1500,
        "reference_expression": "single_image",
        "notes": "superseded by generic-video-v1",
    },
}

UNKNOWN_SURFACE_PROFILE: dict[str, Any] = {
    "id": "unknown",
    "family": None,
    "version": None,
    "status": "unknown",
    "evidence_date": None,
    # conservative: assume the tightest budget we ship rather than guessing.
    "prompt_char_budget": 1500,
    "reference_expression": None,
    "notes": "no surface profile declared/matched — conservative assumptions, "
             "no guessed limits",
}


def surface_profile_for(shot) -> tuple[dict[str, Any], str]:
    """(profile, freshness) for a shot. Selection is EXPLICIT ONLY —
    ``generation.params.surface_profile`` names a profile id; no declaration
    (or an unknown id) yields the conservative UNKNOWN profile. freshness ∈
    CURRENT | STALE | UNKNOWN."""
    pid = (getattr(shot.generation, "params", {}) or {}).get("surface_profile")
    if not pid:
        return UNKNOWN_SURFACE_PROFILE, "UNKNOWN"
    profile = SURFACE_PROFILES.get(str(pid))
    if profile is None:
        return UNKNOWN_SURFACE_PROFILE, "UNKNOWN"
    return profile, ("CURRENT" if profile.get("status") == "current" else "STALE")


def surface_profile_digest(profile: dict[str, Any]) -> str:
    from ..core.hashing import hash_value

    return hash_value(profile)


def _pcheck(code: str, severity: str, message: str, source_paths: list[str],
            proposal: str) -> dict[str, Any]:
    return {
        "level": severity,          # printer/has_blocking compat
        "severity": severity,
        "code": code,
        "message": message,
        "suggestion": proposal,
        "source_paths": source_paths,
        "proposal": proposal,
        "auto_apply": False,
    }


def _clip_scope_checks(project, shot, findings: list[dict[str, Any]]) -> None:
    sid = shot.id
    action_path = f"shots/{sid}.yaml#/action"
    text = _action_text(shot)
    clauses = split_clauses(text)
    completed = [c for c in clauses if not _FUTURE_RE.search(c)]
    future = [c for c in clauses if _FUTURE_RE.search(c)]

    if len(completed) > MAX_ACTIONS:
        findings.append(_pcheck(
            CODE_CLIP_SCOPE_MULTIPLE, WARNING,
            f"单 clip 装了 {len(completed)} 个完成动作(> {MAX_ACTIONS})— "
            "一镜一个可见 beat,多动作会糊/丢",
            [action_path], "split_or_rescope:拆镜或收窄 action 到一个 beat"))
    if future:
        findings.append(_pcheck(
            CODE_CLIP_SCOPE_FUTURE_LEAK, WARNING,
            "action/Prompt 泄漏了未来 beat(本镜不该演出): " + "; ".join(future),
            [action_path],
            "把未来剧情移到下一镜/故事 source,本镜只写当前可见 beat"))

    # a shot OTHER shots continue from must author its endpoint.
    continued_by = []
    try:
        for other_id in project.shot_ids():
            if other_id == sid:
                continue
            try:
                other = project.load_shot(other_id)
            except Exception:
                continue
            if other.continuity and (other.continuity.prev or "").strip() == sid:
                continued_by.append(other_id)
    except Exception:
        continued_by = []
    if continued_by and text and not _ENDPOINT_RE.search(text):
        findings.append(_pcheck(
            CODE_CLIP_SCOPE_ENDPOINT_MISSING, WARNING,
            f"{', '.join(sorted(continued_by))} 声明从本镜续接,但本镜 action "
            "没有写明结尾落点(endpoint)",
            [action_path],
            "在 action 里写明画面收在哪个状态(如「最后定格在…」),"
            "续接才有可验收的 endpoint"))


def _reference_checks(project, shot, refset, findings: list[dict[str, Any]]) -> None:
    from ..providers.refs import DECLARED_TIERS

    sid = shot.id
    declared_images = [it for it in refset.items
                       if it.kind == "image" and it.tier in DECLARED_TIERS]
    for it in declared_images:
        src = [f"shots/{sid}.yaml#/generation/params ({it.tier}: {it.ref})"]
        if it.transfer_errors:
            findings.append(_pcheck(
                CODE_REF_CONTROL_CONFLICT, WARNING,
                f"reference {it.ref} 的 transfer 声明无效: "
                + "; ".join(it.transfer_errors),
                src, "修正 controls/ignore(不能重叠,只能用已知枚举)"))
        elif not it.declared_transfer:
            findings.append(_pcheck(
                CODE_REF_TRANSFER_UNDECLARED, ADVISORY,
                f"reference {it.ref} 未声明 transfer(controls/ignore/"
                "subject_ref)— 旧式绑定仍兼容,但背景/姿态是否该迁移不可知",
                src,
                "把该 ref 写成 {ref: …, controls: [character_identity], "
                "ignore: [background, pose]} 的显式绑定"))

    with_decl = [it for it in declared_images if it.declared_transfer]
    if len(declared_images) >= 2 and not any(
            it.subject_ref or it.controls for it in with_decl):
        findings.append(_pcheck(
            CODE_REF_ROLE_AMBIGUOUS, ADVISORY,
            f"{len(declared_images)} 张声明的 image reference 都没说各自控制什么"
            "(身份?场景?风格?)— 角色不明的多 ref 会互相打架",
            [f"shots/{sid}.yaml#/generation/params"],
            "给每个 ref 声明 controls/subject_ref,分清身份 ref 与场景/风格 ref"))

    # a ref pointing at another shot's take media that is no longer selected.
    for it in refset.items:
        if it.tier not in DECLARED_TIERS or not it.ref:
            continue
        stale = _take_ref_stale(project, it.ref)
        if stale is not None:
            ref_shot, ref_take, current = stale
            findings.append(_pcheck(
                CODE_REF_SOURCE_STALE, WARNING,
                f"reference {it.ref} 指向 {ref_shot} 的 take {ref_take},"
                f"但该镜当前选中的是 {current!r} — 引用源已过期",
                [f"shots/{sid}.yaml#/generation/params ({it.tier})"],
                f"确认要锚定的媒体:改指当前选中 take,或明确保留旧 take 的引用"))


def _take_ref_stale(project, ref: str) -> tuple[str, str, str | None] | None:
    """(shot, take, current_selected) when ``ref`` names a take media file of
    a shot whose CURRENT selection differs; None otherwise. Pure path math on
    the project's own gen layout — never guesses beyond it."""
    norm = ref.replace("\\", "/")
    if "media/gen/" not in norm:
        return None
    try:
        tail = norm.split("media/gen/", 1)[1]
        ref_shot, fname = tail.split("/", 1)
        ref_take = fname.rsplit(".", 1)[0]
    except (ValueError, IndexError):
        return None
    try:
        if project.get_take(ref_shot, ref_take) is None:
            return None
        current = project.load_shot(ref_shot).status.selected_take
    except Exception:
        return None
    if current == ref_take:
        return None
    return ref_shot, ref_take, current


def _surface_checks(shot, video_prompt: str | None,
                    findings: list[dict[str, Any]]) -> None:
    sid = shot.id
    params_path = f"shots/{sid}.yaml#/generation/params/surface_profile"
    profile, freshness = surface_profile_for(shot)
    declared = bool((getattr(shot.generation, "params", {}) or {})
                    .get("surface_profile"))

    if declared and freshness == "UNKNOWN":
        findings.append(_pcheck(
            CODE_SURFACE_PROFILE_UNKNOWN, WARNING,
            f"声明的 surface_profile 不在已知表中 — 不猜测其限制,"
            f"按保守 unknown profile 处理",
            [params_path], "改用已知 profile id,或移除声明走保守假设"))
    elif declared and freshness == "STALE":
        findings.append(_pcheck(
            CODE_SURFACE_PROFILE_STALE, WARNING,
            f"surface_profile {profile['id']} 已过期(status="
            f"{profile['status']}, evidence {profile['evidence_date']})",
            [params_path], f"升级到当前 profile(如 generic-video-v1)"))

    budget = profile.get("prompt_char_budget")
    if budget and video_prompt and len(video_prompt) > int(budget):
        findings.append(_pcheck(
            CODE_PROMPT_BUDGET_EXCEEDED,
            WARNING if declared else ADVISORY,
            f"video prompt {len(video_prompt)} 字符超出 profile "
            f"{profile['id']} 的预算 {budget}(可能被截断)",
            [f"shots/{sid}.yaml#/action", params_path],
            "收窄 action/prompt_override,或拆镜"))


def _capability_checks(project, shot, duration_ms, refset,
                       findings: list[dict[str, Any]]) -> None:
    """PROVIDER_CAPABILITY_MISMATCH — produced by CALLING DR04's preflight on
    the routed provider (the one execution-limit authority), never re-derived."""
    try:
        from ..providers.catalog import descriptor_for_manifest
        from ..providers.preflight import (
            STATUS_INCOMPATIBLE,
            check_request_compatibility,
        )
        from ..providers.registry import get_manifest

        _max_ms, provider = _routed_max_duration_ms(project, shot)
        if not provider:
            return
        manifest = get_manifest(provider)
        if manifest is None:
            return  # builtin/no facts — preflight's UNKNOWN never hard-blocks
        images = len(refset.image_items()) if refset is not None else 0
        videos = len(refset.video_items()) if refset is not None else 0
        capability = "image_to_video" if images else "text_to_video"
        doc = check_request_compatibility(
            descriptor_for_manifest(manifest), capability=capability,
            duration_ms=duration_ms, ref_image_count=images,
            ref_video_count=videos,
            params=dict(getattr(shot.generation, "params", {}) or {}))
    except Exception:
        return  # preflight unavailability is DR04's to surface, never a crash here
    if doc.get("status") == STATUS_INCOMPATIBLE:
        reasons = "; ".join(str(r.get("message") or r.get("code") or r)
                            for r in (doc.get("reasons") or [])) or "见 preflight"
        findings.append(_pcheck(
            CODE_PROVIDER_CAPABILITY_MISMATCH, WARNING,
            f"路由供应商 {provider} 与本请求不兼容(preflight): {reasons}",
            [f"shots/{shot.id}.yaml#/generation"],
            "换 provider/改 generation.params,或按 preflight 建议调整请求"))


def production_checks(project, shot, *, duration_ms: int | None = None,
                      video_prompt: str | None = None,
                      refset=None) -> list[dict[str, Any]]:
    """The 08_10_12C deterministic production checks for one shot — pure,
    read-only, proposal-only (§7.2). ``video_prompt``/``refset``/``duration_ms``
    may be passed by a caller that already computed them (the prompt bundle);
    otherwise they are derived from the same code paths the build uses."""
    findings: list[dict[str, Any]] = []

    if duration_ms is None:
        try:
            from ..build.graph import _target_duration_ms

            duration_ms = _target_duration_ms(project, shot, project.load_rules())
        except Exception:
            duration_ms = 0
    if video_prompt is None:
        try:
            from ..providers.prompt import compile_prompt

            video_prompt = compile_prompt(shot, project.load_bible())
        except Exception:
            video_prompt = ""
    if refset is None:
        try:
            from ..providers.refs import resolve_refs

            refset = resolve_refs(project, shot, project.load_bible())
        except Exception:
            refset = None

    _clip_scope_checks(project, shot, findings)
    if refset is not None:
        _reference_checks(project, shot, refset, findings)
    _surface_checks(shot, video_prompt, findings)
    _capability_checks(project, shot, duration_ms, refset, findings)

    # WP5 continuation-source gate (qc.production derives it from accepted
    # evidence; lazy import — production imports this module's clause tools).
    # Hardening WP4 6.2 (claim 8): pre-hardening this was `except: pass` — a
    # raising derivation made the gate silently VANISH for a shot that declares
    # continuity.prev. Unavailable evidence now blocks (WARNING level enters
    # has_blocking); shots without a continuation stay finding-free.
    try:
        from .production import continuation_checks

        findings.extend(continuation_checks(project, shot.id))
    except Exception as exc:
        prev_id = ""
        try:
            prev_id = (shot.continuity.prev or "").strip() if shot.continuity else ""
        except Exception:
            prev_id = ""
        if prev_id:
            findings.append(_pcheck(
                "CONTINUATION_CHECK_UNAVAILABLE", WARNING,
                f"续接源 {prev_id} 的续接检查本身推导失败({type(exc).__name__})"
                "— 证据不可用时不得静默放行续写",
                [f"shots/{shot.id}.yaml#/continuity/prev"],
                "修复评审证据(reports/ 下 v2 记录/媒体可读性)后重试,"
                "或显式改写 continuity.prev",
            ))

    # AI_IDE_16 §10 collaborative gate surface: an ADVISORY KEYFRAME_NOT_ADOPTED
    # finding for a shot with pending (unadopted) keyframe candidates. Never
    # blocks a human (advisory tier — not in FAIL_LEVELS); the unattended
    # refusal rides the build-dispatch gate, not this linter. Isolated so a
    # derivation error never drops the continuation gate above.
    try:
        from .production import keyframe_ladder_checks

        findings.extend(keyframe_ladder_checks(project, shot.id))
    except Exception:
        pass
    return findings
