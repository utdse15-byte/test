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
    """The text the checks read: the authored action beat, falling back to a
    prompt_override when the action field is empty (the override IS the video
    prompt the model receives)."""
    text = (getattr(shot.action, "main", "") or "").strip()
    if text:
        return text
    override = getattr(shot.generation, "prompt_override", None)
    return (override or "").strip() if isinstance(override, str) else ""


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
    """Run :func:`check_shot` over every shot; each finding tagged with its
    ``shot`` id, in shot order. Used by ``manju prompt --check``."""
    out: list[dict[str, Any]] = []
    for sid in project.shot_ids():
        try:
            shot = project.load_shot(sid)
        except Exception:
            continue  # a broken shot file is `manju check`'s job, not this linter's
        for finding in check_shot(project, shot):
            out.append({"shot": sid, **finding})
    return out


def has_blocking(findings: list[dict[str, Any]]) -> bool:
    """True when any finding is at a blocking (WARNING) level — the exit-code
    predicate for ``manju prompt --check``."""
    return any(f.get("level") in FAIL_LEVELS for f in findings)
