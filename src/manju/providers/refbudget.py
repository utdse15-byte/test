"""Reference-image / -video budget allocation (goal item 9).

Some cloud video/image APIs accept only a bounded number of reference inputs
(one first-frame image, one style image, …). When a manifest declares a budget
(``limits.max_ref_images`` / ``limits.max_ref_videos``) this module decides,
DETERMINISTICALLY and by priority, WHICH of a shot's resolved refs are actually
delivered and which are omitted — with every omission explained in 中文 so the
consequence ("场景一致性将只依赖提示词描述") is auditable per take rather than a
silent drop.

Priority when over budget (contract order, kept-first → dropped-last)::

    explicit shot-param refs > character primary > scene primary >
    prop > style > additional angles/extras

Classification uses the tier lineage :mod:`manju.providers.refs` already records
(``params`` / ``shot_refs`` / ``bible`` / ``refs_dir_fallback``); the bible tier
is refined to character-vs-scene-vs-prop when the Bible is available (both the
build call site and the CLI pass it).

**Byte-identity contract:** with NO budget configured (``max_ref_images`` and
``max_ref_videos`` both ``None``) :func:`allocate` returns the refs in their
original resolved order with zero omissions, so a delivery adapter that always
uses ``budget.selected_images`` produces exactly today's bytes and records no
``budget`` block. A configured limit is the only thing that changes behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .refs import (
    TIER_BIBLE,
    TIER_PARAMS,
    TIER_REFS_DIR,
    TIER_SHOT,
    RefItem,
    RefSet,
    normalize_subject_scope,
)
from ..core.reference_syntax import iter_authored_reference_bindings

if TYPE_CHECKING:  # type hints only — never imported at runtime (no cycle)
    from ..core.models import ShotSpec

# ------------------------------------------------------------------- roles
# A ref's role — a finer classification than the tier alone, used both for the
# allocation priority and for the 中文 impact message on an omission.
ROLE_SHOT_PARAM = "shot_param"            # generation.params / request params (explicit)
ROLE_SHOT_REF = "shot_ref"               # a shot's own top-level refs: block
ROLE_CHARACTER_PRIMARY = "character_primary"  # first character's bible ref
ROLE_CHARACTER = "character"             # a further character's bible ref
ROLE_SCENE = "scene"                     # the scene's bible ref
ROLE_PROP = "prop"                       # a prop's bible ref
ROLE_STYLE = "style"                     # a style bible ref (reserved; not yet resolved)
ROLE_FALLBACK = "fallback"               # media/refs generic fallback
ROLE_OTHER = "other"

# Priority rank: LOWER = kept first when over budget. Matches the contract's
# left-to-right order exactly.
_ROLE_RANK: dict[str, int] = {
    ROLE_SHOT_PARAM: 0,
    ROLE_SHOT_REF: 1,
    ROLE_CHARACTER_PRIMARY: 2,
    ROLE_CHARACTER: 3,
    ROLE_SCENE: 4,
    ROLE_PROP: 5,
    ROLE_STYLE: 6,
    ROLE_FALLBACK: 7,
    ROLE_OTHER: 8,
}
# An EXTRA ref (the 2nd+ ref of the SAME role — "additional angles/extras") is
# demoted to the bottom band, below style, per the contract order.
_RANK_EXTRA = 7

# 中文 role labels (for the omission line + the CLI report).
_ROLE_ZH: dict[str, str] = {
    ROLE_SHOT_PARAM: "镜头显式参考",
    ROLE_SHOT_REF: "镜头级参考",
    ROLE_CHARACTER_PRIMARY: "角色主参考图",
    ROLE_CHARACTER: "角色参考图",
    ROLE_SCENE: "场景参考图",
    ROLE_PROP: "道具参考图",
    ROLE_STYLE: "风格参考图",
    ROLE_FALLBACK: "通用回退参考图(media/refs)",
    ROLE_OTHER: "参考图",
}

# 中文 impact-of-omission clause per role (what consistency is now lost).
_IMPACT_ZH: dict[str, str] = {
    ROLE_SHOT_PARAM: "该镜头指定画面将被忽略,构图/内容只依赖提示词描述",
    ROLE_SHOT_REF: "镜头级参考将被忽略,构图/内容只依赖提示词描述",
    ROLE_CHARACTER_PRIMARY: "角色外观一致性将只依赖提示词描述",
    ROLE_CHARACTER: "该角色的一致性将只依赖提示词描述",
    ROLE_SCENE: "场景一致性将只依赖提示词描述",
    ROLE_PROP: "道具外观将只依赖提示词描述",
    ROLE_STYLE: "整体风格将只依赖提示词描述",
    ROLE_FALLBACK: "影响很小(通用回退图并非镜头专属)",
    ROLE_OTHER: "相应一致性将只依赖提示词描述",
}


# --------------------------------------------------------------- data model


@dataclass(frozen=True)
class RefSlot:
    """One classified ref, ready to be ranked. ``position`` is its index in the
    resolved (tier) order, breaking rank ties deterministically."""

    item: RefItem
    kind: str            # "image" | "video"
    role: str
    role_label: str      # 中文
    rank: int
    position: int
    occurrence: int      # 0 = the primary of its role; 1+ = an additional angle
    is_extra: bool


@dataclass(frozen=True)
class Omission:
    """A ref dropped by the budget, with the 中文 consequence spelled out."""

    item: RefItem
    kind: str
    role: str
    role_label: str
    reason: str          # 中文, e.g. 省略了场景参考图:场景一致性将只依赖提示词描述

    def to_dict(self) -> dict:
        return {"ref": self.item.ref, "tier": self.item.tier, "kind": self.kind,
                "role": self.role, "reason": self.reason}


@dataclass
class BudgetReport:
    """The allocation outcome for one shot: what is delivered, what is dropped.

    ``selected_images`` / ``selected_videos`` are the :class:`RefItem`s a
    delivery adapter should send, in delivery order. When a kind is NOT capped
    they equal the shot's refs in their original resolved order (byte-identity);
    when capped they are the priority-ordered survivors.
    """

    max_images: int | None
    max_videos: int | None
    images_capped: bool
    videos_capped: bool
    selected_slots: list[RefSlot] = field(default_factory=list)   # images then videos
    omitted: list[Omission] = field(default_factory=list)

    @property
    def active(self) -> bool:
        """True when ANY budget is configured — the only case a delivery adapter
        records a ``budget`` block. False ⇒ behaviour byte-identical to today."""
        return self.images_capped or self.videos_capped

    @property
    def selected_images(self) -> list[RefItem]:
        return [s.item for s in self.selected_slots if s.kind == "image"]

    @property
    def selected_videos(self) -> list[RefItem]:
        return [s.item for s in self.selected_slots if s.kind == "video"]

    def to_lineage(self) -> dict:
        """The audit block recorded on the take's ``ref_delivery.budget`` — both
        the selected (as delivered) and the omitted (with reasons)."""
        return {
            "max_images": self.max_images,
            "max_videos": self.max_videos,
            "selected": [
                {"ref": s.item.ref, "tier": s.item.tier, "kind": s.kind, "role": s.role}
                for s in self.selected_slots
            ],
            "omitted": [o.to_dict() for o in self.omitted],
        }

    def human_lines(self) -> list[str]:
        """中文 lines for the CLI ``manju refs`` budget section."""
        lines: list[str] = []
        cap_bits = []
        if self.max_images is not None:
            cap_bits.append(f"max_ref_images={self.max_images}")
        if self.max_videos is not None:
            cap_bits.append(f"max_ref_videos={self.max_videos}")
        if not cap_bits:
            lines.append("未配置参考预算上限 — 投递与今日一致(byte-identical)")
            return lines
        lines.append("预算上限:" + ", ".join(cap_bits))
        if self.selected_slots:
            lines.append("选用(按投递顺序):")
            for s in self.selected_slots:
                lines.append(f"  ✓ [{s.item.tier}] {s.item.ref}  {s.role_label}")
        else:
            lines.append("选用:无")
        if self.omitted:
            lines.append("省略:")
            for o in self.omitted:
                lines.append(f"  ✗ [{o.item.tier}] {o.item.ref} — {o.reason}")
        return lines


# ------------------------------------------------------------- classification


def classify_role(item: RefItem, shot: "ShotSpec | None",
                  bible: dict | None = None) -> str:
    """Classify one resolved ref into a role from its tier + the Bible.

    Uses the tier lineage refs.py records; the ``bible`` tier is refined to
    character/scene/prop by matching the authored ref value against the same
    Bible keys the resolver read. Degrades to ``character`` for an unmatched
    bible ref (or when no Bible is supplied)."""
    tier = item.tier
    if tier == TIER_PARAMS:
        return ROLE_SHOT_PARAM
    if tier == TIER_SHOT:
        return ROLE_SHOT_REF
    if tier == TIER_REFS_DIR:
        return ROLE_FALLBACK
    if tier == TIER_BIBLE:
        scoped = _role_for_scope(item.subject_ref, shot)
        if scoped is not None:
            return scoped
        return _bible_ref_roles(shot, bible).get(item.ref, ROLE_CHARACTER)
    return ROLE_OTHER


def _role_for_scope(subject_ref: str | None, shot: "ShotSpec | None") -> str | None:
    scope = normalize_subject_scope(subject_ref)
    if not scope:
        return None
    kind, separator, subject_id = scope.partition(":")
    if not separator or not subject_id:
        return None
    if kind == "character":
        characters = [str(item) for item in (
            getattr(shot, "characters", None) or ()
        )]
        return (
            ROLE_CHARACTER_PRIMARY
            if characters and subject_id == characters[0]
            else ROLE_CHARACTER
        )
    if kind == "scene":
        return ROLE_SCENE
    if kind == "prop":
        return ROLE_PROP
    return None


def _bible_ref_roles(shot: "ShotSpec | None", bible: dict | None) -> dict[str, str]:
    """Legacy unscoped path fallback using the shared authored syntax parser."""
    roles: dict[str, str] = {}
    if not bible or shot is None:
        return roles
    for binding in iter_authored_reference_bindings(shot, bible):
        if binding.tier != TIER_BIBLE:
            continue
        role = _role_for_scope(binding.inferred_scope, shot)
        ref = _authored_ref_value(binding.value)
        if role is not None and ref:
            roles.setdefault(ref, role)
    return roles


def _authored_ref_value(value: Any) -> str:
    if isinstance(value, dict):
        value = (
            value.get("ref") or value.get("path")
            or value.get("image") or value.get("video") or ""
        )
    return str(value) if value not in (None, "") else ""


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")]
    return [value] if value != "" else []


# ----------------------------------------------------------------- allocate


def allocate(refset: RefSet, limits: Any, shot: "ShotSpec | None",
             *, bible: dict | None = None) -> BudgetReport:
    """Allocate a shot's refs against a manifest budget (goal item 9).

    ``limits`` is a ``LimitsConfig`` (or ``None``); only ``max_ref_images`` /
    ``max_ref_videos`` are read, via ``getattr`` so no import of the manifest
    module is needed (keeps this module cycle-free). Deterministic: the same
    inputs always produce the same selected/omitted ordering."""
    max_images = _cap(limits, "max_ref_images")
    max_videos = _cap(limits, "max_ref_videos")

    img_slots = _slots(refset.image_items(), "image", shot, bible)
    vid_slots = _slots(refset.video_items(), "video", shot, bible)

    sel_img, om_img = _apply_cap(img_slots, max_images)
    sel_vid, om_vid = _apply_cap(vid_slots, max_videos)

    return BudgetReport(
        max_images=max_images,
        max_videos=max_videos,
        images_capped=max_images is not None,
        videos_capped=max_videos is not None,
        selected_slots=list(sel_img) + list(sel_vid),
        omitted=list(om_img) + list(om_vid),
    )


def _cap(limits: Any, name: str) -> int | None:
    value = getattr(limits, name, None)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _slots(items: list[RefItem], kind: str, shot: "ShotSpec | None",
           bible: dict | None) -> list[RefSlot]:
    counts: dict[str, int] = {}
    slots: list[RefSlot] = []
    for pos, it in enumerate(items):
        role = classify_role(it, shot, bible)
        occ = counts.get(role, 0)
        counts[role] = occ + 1
        is_extra = occ > 0
        base = _ROLE_RANK.get(role, _ROLE_RANK[ROLE_OTHER])
        rank = max(base, _RANK_EXTRA) if is_extra else base
        slots.append(RefSlot(
            item=it, kind=kind, role=role, role_label=_ROLE_ZH.get(role, "参考图"),
            rank=rank, position=pos, occurrence=occ, is_extra=is_extra,
        ))
    return slots


def _apply_cap(slots: list[RefSlot], cap: int | None) -> tuple[list[RefSlot], list[Omission]]:
    """Return (kept_slots, omissions). ``cap is None`` (no budget for this kind)
    keeps EVERY slot in its original resolved order with zero omissions — the
    byte-identity path. A cap that drops nothing also keeps the natural order;
    only a genuine trim reorders the survivors by priority."""
    if cap is None:
        return list(slots), []
    ordered = sorted(slots, key=lambda s: (s.rank, s.position))
    cap = max(0, cap)
    kept = ordered[:cap]
    dropped = ordered[cap:]
    if not dropped:
        return list(slots), []  # nothing trimmed → keep the resolved order
    return kept, [_omission(s) for s in dropped]


def _omission(slot: RefSlot) -> Omission:
    if slot.is_extra:
        reason = f"省略了{slot.role_label}(额外角度):仅保留主参考,一致性影响较小"
    elif slot.kind == "video":
        reason = "省略了参考视频:运动/时序参考将丢失,只依赖提示词描述"
    else:
        impact = _IMPACT_ZH.get(slot.role, _IMPACT_ZH[ROLE_OTHER])
        reason = f"省略了{slot.role_label}:{impact}"
    return Omission(item=slot.item, kind=slot.kind, role=slot.role,
                    role_label=slot.role_label, reason=reason)
