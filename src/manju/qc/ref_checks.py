"""Reference-image cleanliness checks (goal item 10).

Two families, both structured findings ``{level, code, message(中文), evidence,
hint}``:

* **LOCAL heuristics** (ffmpeg / ffprobe only, no vision model, no new deps):
  1. ``busy_background`` — a character ref whose subject is clean but whose
     surround is edge-heavy (a distracting/busy background). Measured by
     comparing the edge density (``edgedetect`` → ``signalstats`` YAVG) of the
     border region against the centre subject.
  2. ``lighting_conflict`` — across one shot's refs, mean-brightness or
     colour-temperature spread beyond a threshold (they were shot under
     different light).
  3. ``unclear_scale`` — resolution too low (short side < 512) or an extreme
     aspect ratio, so the model can't tell how the subject is framed.

* **IMAGE-JUDGMENT** checks (a scene ref containing people, a prop ref
  containing people, an outfit conflict between character refs) genuinely need
  eyes. Round V (§6, goal item 6) retires the vision-vendor slot: the structured
  ``needs_vision`` finding stays (so callers that count it are unchanged), but
  its prose now points at the AGENT pipe — ``manju qc brief`` → the
  ``visual-qc-review`` skill's A–J standards → ``manju qc verdict``. An injected
  screener (a test double / legacy hook) still runs when present; a heuristic
  NEVER fakes a vision verdict.

Nothing here raises: a missing ffmpeg/ffprobe or a failed probe degrades to
"no local finding", never to a crash (mirrors ``qc/content.py``).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..providers.refbudget import (
    ROLE_CHARACTER,
    ROLE_CHARACTER_PRIMARY,
    ROLE_PROP,
    ROLE_SCENE,
    classify_role,
)

if TYPE_CHECKING:
    from ..core.container import Project
    from ..core.models import ShotSpec
    from ..providers.refs import RefItem, RefSet

# --------------------------------------------------------------- thresholds
# All documented constants so the heuristics are tunable in one place.

# (1) busy background: border edge-density (YAVG of an edgedetect pass, 0–255)
# must clear this floor AND dominate the centre subject by this ratio. A clean
# subject on a solid background reads ~0 at the border; camera noise / clutter
# reads well into double digits (calibrated on synthetic noise-border refs).
BUSY_BG_EDGE_MIN = 6.0
BUSY_BG_RATIO = 2.0

# (2) lighting conflict across a shot's refs
LIGHTING_BRIGHTNESS_DELTA = 60.0   # YAVG (0–255) spread across refs
LIGHTING_WARMTH_DELTA = 40.0       # (VAVG − UAVG) chroma spread across refs

# (3) unclear scale
MIN_SHORT_SIDE = 512               # px: below this the framing/detail is unclear
MAX_ASPECT_RATIO = 3.0             # long / short side beyond this is extreme

_YAVG_RE = re.compile(r"lavfi\.signalstats\.YAVG=(-?\d+(?:\.\d+)?)")
_STAT_RE = re.compile(r"lavfi\.signalstats\.(YAVG|UAVG|VAVG)=(-?\d+(?:\.\d+)?)")


# ------------------------------------------------------------------ finding


@dataclass
class RefFinding:
    """One cleanliness finding. ``level`` ∈
    ``error`` | ``warn`` | ``info`` | ``needs_vision``."""

    level: str
    code: str
    message: str                       # 中文
    evidence: dict = field(default_factory=dict)
    hint: str = ""
    subject: str = ""                  # ref path / shot id

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "code": self.code,
            "message": self.message,
            "evidence": self.evidence,
            "hint": self.hint,
            "subject": self.subject,
        }


# A vendor screener injected by a caller (or a test) to actually RUN a
# vision-required check: (code, subject, refs) -> RefFinding | None.
VisionScreener = Callable[[str, str, "list[RefItem]"], "RefFinding | None"]


# ---------------------------------------------------------------- ffmpeg io


def _signalstats(path: Path, pre_filter: str = "") -> dict[str, float] | None:
    """Run one ``signalstats`` pass (optionally behind ``pre_filter``) and return
    ``{YAVG, UAVG, VAVG}``. ``None`` on any failure (ffmpeg missing, unreadable
    file, no metadata)."""
    chain = (f"{pre_filter}," if pre_filter else "") + \
        "signalstats,metadata=mode=print:file=-"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error",
             "-i", str(path), "-vf", chain, "-frames:v", "1", "-f", "null", "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    stats: dict[str, float] = {}
    for m in _STAT_RE.finditer(text):
        stats[m.group(1)] = float(m.group(2))
    return stats or None


def _edge_density(path: Path, crop: str = "") -> float | None:
    """Edge density of a region (or the whole frame) — the mean luma of an
    ``edgedetect`` map (0 = flat, higher = busier)."""
    pre = (f"{crop}," if crop else "") + "edgedetect"
    stats = _signalstats(path, pre)
    return None if stats is None else stats.get("YAVG")


# --------------------------------------------------------------- the entry


def check_refs(
    project: "Project",
    shot: "ShotSpec",
    refset: "RefSet",
    *,
    bible: dict | None = None,
    vision: VisionScreener | None = None,
) -> list[RefFinding]:
    """Cleanliness findings for a shot's resolved refs (goal item 10).

    ``vision`` is an optional injected vendor screener; when absent the
    vision-required checks emit ``needs_vision`` advisories (naming the
    configured ``qc_vision`` provider, if any, per the content.py slot)."""
    findings: list[RefFinding] = []

    # local image refs we can actually probe (existing local files)
    local_imgs = [it for it in refset.image_items()
                  if not it.is_url and it.path is not None and it.exists]

    findings.extend(_scale_findings(project, local_imgs))
    findings.extend(_busy_background_findings(project, shot, local_imgs, bible))
    findings.extend(_lighting_findings(project, shot, local_imgs))
    findings.extend(_vision_findings(shot, refset, bible, vision))
    return findings


# ------------------------------------------------------ (3) unclear scale


def _scale_findings(project, items: "list[RefItem]") -> list[RefFinding]:
    from ..media.probe import probe as _probe

    out: list[RefFinding] = []
    for it in items:
        try:
            info = _probe(it.path)
        except Exception:
            continue
        w, h = info.width, info.height
        if not w or not h:
            continue
        rel = _rel(project, it)
        short = min(w, h)
        if short < MIN_SHORT_SIDE:
            out.append(RefFinding(
                "warn", "unclear_scale",
                f"参考图 {rel} 分辨率偏低({w}×{h},短边 {short}px < {MIN_SHORT_SIDE}px)"
                "— 细节/比例不清,模型难以判断主体",
                evidence={"width": w, "height": h, "short_side": short},
                hint="换用短边 ≥512px 的清晰参考图",
                subject=rel,
            ))
        ratio = max(w, h) / min(w, h)
        if ratio > MAX_ASPECT_RATIO:
            out.append(RefFinding(
                "warn", "unclear_scale",
                f"参考图 {rel} 长宽比过于极端({w}×{h},比例 {ratio:.1f}:1 "
                f"> {MAX_ASPECT_RATIO:.0f}:1)— 主体尺度/取景不明确",
                evidence={"width": w, "height": h, "aspect_ratio": round(ratio, 2)},
                hint="裁剪为接近成片画幅的比例,或换用取景合理的参考图",
                subject=rel,
            ))
    return out


# --------------------------------------------------- (1) busy background


def _busy_background_findings(project, shot, items: "list[RefItem]",
                              bible: dict | None) -> list[RefFinding]:
    out: list[RefFinding] = []
    for it in items:
        role = classify_role(it, shot, bible)
        if role not in (ROLE_CHARACTER_PRIMARY, ROLE_CHARACTER):
            continue  # the check targets CHARACTER refs (clean subject wanted)
        full = _edge_density(it.path)
        center = _edge_density(it.path, "crop=iw/2:ih/2:iw/4:ih/4")
        if full is None or center is None:
            continue  # detector unavailable → no finding (never a false positive)
        # border density backed out of full = ¼·centre + ¾·border
        border = (full - 0.25 * center) / 0.75
        if border >= BUSY_BG_EDGE_MIN and border >= center * BUSY_BG_RATIO:
            rel = _rel(project, it)
            out.append(RefFinding(
                "warn", "busy_background",
                f"角色参考图 {rel} 背景较杂乱(边缘密度 边框≈{border:.1f} 远高于 "
                f"主体中心≈{center:.1f})— 杂乱背景会干扰角色一致性",
                evidence={"border_edge": round(border, 2),
                          "center_edge": round(center, 2),
                          "full_edge": round(full, 2)},
                hint="换用干净/纯色背景的角色参考图,或先抠图去背景",
                subject=rel,
            ))
    return out


# -------------------------------------------------- (2) lighting conflict


def _lighting_findings(project, shot, items: "list[RefItem]") -> list[RefFinding]:
    measured: list[tuple[str, float, float]] = []  # (rel, brightness, warmth)
    for it in items:
        stats = _signalstats(it.path)
        if stats is None:
            continue
        y = stats.get("YAVG")
        u = stats.get("UAVG")
        v = stats.get("VAVG")
        if y is None or u is None or v is None:
            continue
        measured.append((_rel(project, it), y, v - u))
    if len(measured) < 2:
        return []

    brights = [m[1] for m in measured]
    warmths = [m[2] for m in measured]
    b_delta = max(brights) - min(brights)
    w_delta = max(warmths) - min(warmths)
    if b_delta <= LIGHTING_BRIGHTNESS_DELTA and w_delta <= LIGHTING_WARMTH_DELTA:
        return []

    reasons = []
    if b_delta > LIGHTING_BRIGHTNESS_DELTA:
        reasons.append(f"亮度差 {b_delta:.0f}(阈值 {LIGHTING_BRIGHTNESS_DELTA:.0f})")
    if w_delta > LIGHTING_WARMTH_DELTA:
        reasons.append(f"色温差 {w_delta:.0f}(阈值 {LIGHTING_WARMTH_DELTA:.0f})")
    return [RefFinding(
        "warn", "lighting_conflict",
        f"本镜头的参考图之间光照/色温不一致({';'.join(reasons)})— "
        "混用不同光线的参考会让生成结果忽明忽暗、冷暖打架",
        evidence={"brightness_delta": round(b_delta, 1),
                  "warmth_delta": round(w_delta, 1),
                  "refs": [{"ref": r, "brightness": round(b, 1),
                            "warmth": round(w, 1)} for r, b, w in measured]},
        hint="统一参考图的光线/色温(同一光照下的图),或只保留一张主参考",
        subject=shot.id,
    )]


# ---------------------------------------------------- vision-required slot


def _vision_findings(shot, refset: "RefSet", bible: dict | None,
                     vision: VisionScreener | None) -> list[RefFinding]:
    """The three checks that genuinely need eyes (§9 vendor slot). Run the
    injected vendor when present; otherwise emit an honest ``needs_vision``
    advisory naming the configured provider (if any) and the skipped check."""
    provider = _configured_vision_provider()

    image_items = [it for it in refset.image_items()
                   if it.path is not None or it.is_url]
    scene_refs = [it for it in image_items
                  if classify_role(it, shot, bible) == ROLE_SCENE]
    prop_refs = [it for it in image_items
                 if classify_role(it, shot, bible) == ROLE_PROP]
    char_refs = [it for it in image_items
                 if classify_role(it, shot, bible) in (ROLE_CHARACTER_PRIMARY,
                                                       ROLE_CHARACTER)]

    checks: list[tuple[str, str, list, str, str]] = []
    for it in scene_refs:
        checks.append(("scene_has_people", it.ref, [it],
                       f"场景参考图 {it.ref} 是否混入了人物",
                       "场景参考图应只含环境;若含人物会污染场景一致性"))
    for it in prop_refs:
        checks.append(("prop_has_people", it.ref, [it],
                       f"道具参考图 {it.ref} 是否混入了人物",
                       "道具参考图应只含道具本身;若含人物会污染道具外观"))
    if len(char_refs) >= 2:
        subj = shot.id
        checks.append(("outfit_conflict", subj, list(char_refs),
                       f"镜头 {subj} 的多张角色参考图之间服装是否冲突",
                       "多张角色参考若服装不一致,生成会在两套衣服间摇摆"))

    out: list[RefFinding] = []
    for code, subject, refs, what, why in checks:
        if vision is not None:
            finding = vision(code, subject, refs)
            if finding is not None:
                out.append(finding)
            continue
        # Round V (§6, goal item 6): image judgment is the driving agent's own
        # eyes + the visual-qc-review skill's A–J standards — not a vision vendor.
        # The structured `needs_vision`/code slot stays; the prose points at the
        # agent pipe. `provider` (if any) is kept as a SILENT legacy field only.
        evidence = {"refs": [r.ref for r in refs], "what": what}
        if provider:
            evidence["qc_vision"] = provider
        out.append(RefFinding(
            "needs_vision", code,
            f"{what} — 需要图像判读,交给驱动 Manju 的 agent:manju qc brief 出题,"
            "判读标准见 manju skills show visual-qc-review,结果用 manju qc verdict 回填",
            evidence=evidence,
            hint=f"{why}(交给驱动 Manju 的 agent 用眼判读)",
            subject=subject,
        ))
    return out


def _configured_vision_provider() -> str | None:
    """Reuse the content.py slot resolver (manifest type 'vision')."""
    try:
        from .content import vision_provider_id

        return vision_provider_id()
    except Exception:
        return None


# --------------------------------------------------------------- helpers


def _rel(project, item: "RefItem") -> str:
    if item.path is None:
        return item.ref
    try:
        return project.relpath(item.path)
    except Exception:
        return item.ref
