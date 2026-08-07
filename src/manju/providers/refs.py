"""Reference-input resolution — one resolver, one contract (goal item 7).

Every provider that can consume a reference image or video resolves it THROUGH
this module, so the tiering, the lineage and the reliability signals are shared
instead of re-implemented five times. The order mirrors the Ken Burns provider's
original three-tier resolution, with an explicit shot-level tier slotted in:

    shot.params  >  shot refs  >  character/scene bible refs  >  media/refs

``resolve_refs`` returns a :class:`RefSet`: the ordered ``images`` / ``videos``
(local files that exist), the ``primary_image`` (kenburns' single push/pan
frame), and a serialisable ``lineage`` recording WHICH tier supplied each ref.
Delivery adapters (generic_cloud / comfyui / local_cmd) consume the RefSet and
record HOW each ref was delivered (``ref_delivery`` on the take) — resolution
lineage plus delivery lineage make every reference auditable per take (§4.2).

Nothing here talks HTTP or raises provider errors at import time; the two
provider-facing helpers (:func:`unreadable_ref_message`, :func:`encode_multipart`)
keep this module import-light so ``base.py`` can carry a RefSet field without a
circular import.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import stat
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from ..core.hashing import hash_value
from ..core.authoring import canonical_subject_scope
from ..core.reference_syntax import iter_authored_reference_bindings
from ..core.safeio import (
    SafeOutError,
    refuse_linked_within,
    refuse_unsafe_regular_file,
)

if TYPE_CHECKING:  # only for type hints — never imported at runtime (no cycle)
    from ..core.container import Project
    from ..core.models import ShotSpec

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_VIDEO_EXTS = (".mp4", ".mov", ".webm", ".m4v", ".mkv", ".gif")

# Tier names. The first three are kept byte-identical to the strings the Ken
# Burns provider has always recorded ("params"/"bible"/"refs_dir_fallback") so
# its lineage + the round-I generic-ref advisory keep working unchanged; the
# shot-level tier is the one new name.
TIER_PARAMS = "params"
TIER_SHOT = "shot_refs"
TIER_BIBLE = "bible"
TIER_REFS_DIR = "refs_dir_fallback"
TIER_NONE = "none"

# A shot "declares" a reference when it comes from one of these tiers. The
# media/refs fallback is a project-wide safety net, NOT a per-shot declaration —
# that distinction drives the reliability advisories (a generic fallback image
# is already covered by kenburns' own "通用参考图" advisory).
DECLARED_TIERS = (TIER_PARAMS, TIER_SHOT, TIER_BIBLE)


@dataclass(frozen=True)
class RefItem:
    """One resolved reference and the tier that supplied it.

    ``path`` is the resolved local file (``None`` for a URL ref); ``exists`` is
    whether that local file is present. A ref is either a local file or an
    ``http(s)://`` URL — video refs to cloud APIs are usually URL-only.
    """

    ref: str            # authored value (project-relative path, absolute, or URL)
    tier: str
    kind: str           # "image" | "video"
    path: Path | None   # resolved local file, or None for a URL
    is_url: bool
    exists: bool
    # goal item 13/17: set (in Chinese) when ``ref`` named an absolute path or
    # one that resolves outside the project root — the untrusted-project
    # containment guard refused it rather than treating it as "missing".
    blocked_reason: str | None = None
    # 08_10_12C WP2 §7.4 — reference-transfer declaration, additive on the
    # EXISTING binding (a refs entry may be the dict form
    # ``{ref: ..., controls: [...], ignore: [...], subject_ref: ...}``).
    # Authored in generation.params / the shot's refs — so it participates in
    # spec_hash and the request identity through the params the build already
    # hashes. ``transfer_errors`` carries validation problems (unknown enum,
    # controls∩ignore conflict) — never silently accepted, surfaced as
    # REFERENCE_CONTROL_CONFLICT by the prompt production checks. A plain
    # string ref stays byte-identical (declared_transfer=False), compatible
    # but flagged REFERENCE_TRANSFER_UNDECLARED by the checks.
    controls: tuple = ()
    ignore: tuple = ()
    subject_ref: str | None = None
    declared_transfer: bool = False
    transfer_errors: tuple = ()
    # The containment boundary this ref was accepted against. Carried on the
    # item so the pre-upload re-verification (:func:`open_verified_ref`) can
    # re-check containment without importing the container — the stored path
    # alone is never trusted at read time. ``None`` disables only the
    # containment half; the no-follow / regular-file half always applies.
    root: Path | None = None


class ReferenceControlConflict(ValueError):
    """A declared reference-transfer contract has no single closed-role owner."""

    def __init__(self, conflicts: list[dict[str, Any]]):
        self.conflicts = tuple(conflicts)
        message = "; ".join(str(item["message"]) for item in conflicts)
        super().__init__(message or "reference control ownership conflict")


class RequiredReferenceOmitted(ValueError):
    """A provider plan dropped a binding that owns a closed picture variable."""

    def __init__(self, provider_id: str, omissions: list[dict[str, Any]]):
        self.provider_id = provider_id
        self.omissions = tuple(omissions)
        refs = ", ".join(str(row.get("ref")) for row in omissions)
        super().__init__(
            f"provider {provider_id!r} would omit required reference owner(s): {refs}"
        )


@dataclass(frozen=True)
class OmittedBinding:
    item: RefItem
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref": self.item.ref,
            "tier": self.item.tier,
            "kind": self.item.kind,
            "controls": list(self.item.controls),
            "subject_ref": self.item.subject_ref,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ReferenceDeliveryPlan:
    """Immutable provider-specific binding selection formed before prompt/identity."""

    provider_id: str
    selected_bindings: tuple[RefItem, ...]
    omitted_bindings: tuple[OmittedBinding, ...]
    image_mode: str
    video_mode: str
    max_images: int | None
    max_videos: int | None
    budget_lineage: dict[str, Any] | None
    digest: str

    def selected_refset(self) -> "RefSet":
        return RefSet.from_items(list(self.selected_bindings))

    def selected(self, kind: str) -> tuple[RefItem, ...]:
        return tuple(item for item in self.selected_bindings if item.kind == kind)

    def evidence_base(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "image_mode": self.image_mode,
            "video_mode": self.video_mode,
            "images": [],
            "videos": [],
            "plan_digest": self.digest,
        }
        if self.budget_lineage is not None:
            out["budget"] = self.budget_lineage
        if self.omitted_bindings:
            out["omitted_bindings"] = [row.to_dict() for row in self.omitted_bindings]
        return out


@dataclass
class RefSet:
    """The refs available to one shot, most-specific first.

    ``images`` / ``videos`` are the LOCAL files that exist (kenburns and the
    base64/multipart delivery modes need a real file); URL refs live only in
    ``items`` / ``lineage`` because they have no local path. ``primary_image`` is
    the single frame kenburns animates. ``lineage`` is the auditable record of
    which tier each ref came from.
    """

    images: list[Path] = field(default_factory=list)
    videos: list[Path] = field(default_factory=list)
    primary_image: Path | None = None
    lineage: dict = field(default_factory=dict)
    items: list[RefItem] = field(default_factory=list)

    # -- construction ------------------------------------------------------

    @classmethod
    def from_items(cls, raw: list[RefItem]) -> "RefSet":
        items = _dedup(raw)
        images = _unique_paths(items, "image")
        videos = _unique_paths(items, "video")
        primary = images[0] if images else None
        lineage = {
            "images": [_ref_lineage(it) for it in items if it.kind == "image"],
            "videos": [_ref_lineage(it) for it in items if it.kind == "video"],
            "primary_image": (
                {"ref": _first_existing_image(items).ref,
                 "tier": _first_existing_image(items).tier}
                if primary is not None else None
            ),
        }
        return cls(images=images, videos=videos, primary_image=primary,
                   lineage=lineage, items=items)

    # -- accessors ---------------------------------------------------------

    def image_items(self) -> list[RefItem]:
        """All image refs in tier order (INCLUDING missing/URL ones) — delivery
        must see a declared-but-missing ref so it can error before a paid POST."""
        return [it for it in self.items if it.kind == "image"]

    def video_items(self) -> list[RefItem]:
        return [it for it in self.items if it.kind == "video"]

    @property
    def primary_image_source(self) -> str:
        """The tier that supplied ``primary_image`` — kenburns records this and
        the build graph advises on ``refs_dir_fallback``. ``none`` when absent."""
        first = _first_existing_image(self.items)
        return first.tier if first is not None else TIER_NONE

    def primary_image_path_str(self) -> str:
        """The primary image as a raw path string, for the comfyui/local_cmd
        ``{image}`` path-based workflows.

        Both accessors name the SAME item: the first EXISTING image (what
        :attr:`primary_image` and :attr:`primary_image_source` report), so the
        lineage a take records can never claim a different ref than the one
        delivered. With nothing usable it degrades to the first image ref that
        was NOT refused — a containment-blocked path (absolute / escaping the
        project) is never handed to a local command or recorded as the primary
        path."""
        first = _first_existing_image(self.items)
        if first is not None:
            return str(first.path) if first.path is not None else first.ref
        for it in self.items:
            if it.kind == "image" and not it.blocked_reason:
                return str(it.path) if it.path is not None else it.ref
        return ""

    @property
    def primary_image_item(self) -> RefItem | None:
        """The RefItem behind :attr:`primary_image` — the ONE verified item every
        accessor names. A caller that uploads bytes needs the item (not the bare
        path) so the read goes through :func:`read_ref_bytes`."""
        return _first_existing_image(self.items)

    @property
    def primary_video(self) -> Path | None:
        return self.videos[0] if self.videos else None

    def has_declared_refs(self) -> bool:
        return any(it.tier in DECLARED_TIERS for it in self.items)

    def declared_items(self, kind: str) -> list[RefItem]:
        return [it for it in self.items
                if it.kind == kind and it.tier in DECLARED_TIERS]


# ---------------------------------------------------------------- resolver


def resolve_refs(
    project: "Project",
    shot: "ShotSpec",
    bible: dict[str, dict] | None = None,
    *,
    params: dict | None = None,
) -> RefSet:
    """Resolve a shot's reference inputs across the four tiers (goal item 7).

    ``params`` overrides the params tier (the caller passes the REQUEST params,
    which on a ``manju redo`` carry the reused take's ``image`` — matching the
    Ken Burns provider's original ``req.params`` read). When omitted it falls
    back to ``shot.generation.params``.
    """
    params = params if params is not None else dict(shot.generation.params)
    bible = bible or {}
    items: list[RefItem] = []

    for binding in iter_authored_reference_bindings(
        shot, bible, params=params
    ):
        if binding.kind is None:
            items.append(_classify(
                project, binding.value, binding.tier,
                inferred_scope=binding.inferred_scope,
            ))
        else:
            items.append(_make_item(
                project, binding.value, binding.tier, binding.kind,
                inferred_scope=binding.inferred_scope,
            ))
    _collect_refs_dir(project, items)  # gated: only when no existing declared ref

    return RefSet.from_items(items)


_REQUIRED_DELIVERY_ROLES = frozenset({
    "character_identity", "face", "costume", "prop", "location", "background",
    "pose", "motion", "framing", "lighting",
})


def plan_reference_delivery(
    provider: Any,
    refset: RefSet,
    shot: "ShotSpec",
    bible: dict[str, dict] | None = None,
) -> ReferenceDeliveryPlan:
    """Select the exact logical bindings a provider can deliver.

    All authored bindings are validated first. The resulting immutable plan is
    then shared by prompt compilation, request identity and body rendering.
    """
    validate_control_ownership(refset)
    provider_id = str(getattr(provider, "id", "") or "<unknown>")
    manifest = getattr(provider, "manifest", None)
    adapter = str(getattr(manifest, "adapter", "") or "")

    # Providers without executable reference facts keep the historical all-ref
    # view. Manifest-backed generic and ComfyUI adapters have concrete delivery
    # limits and therefore receive an exact provider-specific subset.
    if manifest is None:
        return _make_delivery_plan(
            provider_id, list(refset.items), [], "native", "native", None, None, None
        )

    from .refbudget import allocate, classify_role

    # Budget physical blobs, not logical bindings. One image can legitimately
    # bind two scoped characters; it is uploaded once but both bindings survive.
    physical_refset = RefSet.from_items(_physical_items(refset.items))
    budget = allocate(physical_refset, getattr(manifest, "limits", None), shot, bible=bible)
    if adapter == "generic_cloud":
        cfg = manifest.refs
        image_mode = cfg.image_mode
        video_mode = cfg.video_mode
        max_images = max(0, int(cfg.max_images or 0))
        max_videos = max(0, int(cfg.max_videos or 0))
    elif adapter.endswith("ComfyUIProvider"):
        input_map = getattr(getattr(manifest, "comfyui", None), "input_map", {}) or {}
        templates = tuple(str(value) for value in input_map.values())
        uses_upload = any("{image_upload}" in value for value in templates)
        uses_path = any("{image}" in value for value in templates)
        image_mode = "upload" if uses_upload else "path" if uses_path else "none"
        video_mode = "none"
        max_images = 1 if image_mode != "none" else 0
        max_videos = 0
    else:
        return _make_delivery_plan(
            provider_id, list(refset.items), [], "native", "native", None, None,
            budget.to_lineage() if budget.active else None,
        )

    selected_physical = (
        (list(budget.selected_images)[:max_images] if image_mode != "none" else [])
        + (list(budget.selected_videos)[:max_videos] if video_mode != "none" else [])
    )
    selected_keys = {physical_ref_key(item) for item in selected_physical}
    selected_images = [item for item in refset.image_items()
                       if physical_ref_key(item) in selected_keys]
    selected_videos = [item for item in refset.video_items()
                       if physical_ref_key(item) in selected_keys]
    selected = selected_images + selected_videos

    omitted: list[OmittedBinding] = []
    reasons = {physical_ref_key(row.item): row.reason for row in budget.omitted}
    for item in refset.items:
        if physical_ref_key(item) in selected_keys:
            continue
        if image_mode == "none" and item.kind == "image":
            reason = "provider image_mode is none"
        elif video_mode == "none" and item.kind == "video":
            reason = "provider video_mode is none"
        else:
            reason = reasons.get(physical_ref_key(item), "provider reference capacity exceeded")
        omitted.append(OmittedBinding(item=item, reason=reason))

    required = [
        row.to_dict() for row in omitted
        if _REQUIRED_DELIVERY_ROLES & set(row.item.controls)
    ]
    if required:
        raise RequiredReferenceOmitted(provider_id, required)

    budget_lineage = budget.to_lineage() if budget.active else None
    if budget_lineage is not None:
        budget_lineage["selected"] = [
            _budget_role_row(item, classify_role(item, shot, bible))
            for item in selected
        ]
        budget_lineage["omitted"] = [
            {
                **_budget_role_row(
                    row.item, classify_role(row.item, shot, bible)
                ),
                "reason": row.reason,
            }
            for row in omitted
        ]

    return _make_delivery_plan(
        provider_id,
        selected,
        omitted,
        image_mode,
        video_mode,
        max_images,
        max_videos,
        budget_lineage,
    )


def _budget_role_row(item: RefItem, role: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ref": item.ref,
        "tier": item.tier,
        "kind": item.kind,
        "role": role,
    }
    if item.subject_ref is not None:
        row["subject_ref"] = item.subject_ref
    return row


def _make_delivery_plan(
    provider_id: str,
    selected: list[RefItem],
    omitted: list[OmittedBinding],
    image_mode: str,
    video_mode: str,
    max_images: int | None,
    max_videos: int | None,
    budget_lineage: dict[str, Any] | None,
) -> ReferenceDeliveryPlan:
    payload = {
        "provider_id": provider_id,
        "image_mode": image_mode,
        "video_mode": video_mode,
        "selected": [
            {
                "kind": item.kind,
                "ref": item.ref,
                "tier": item.tier,
                "controls": list(item.controls),
                "ignore": list(item.ignore),
                "subject_ref": item.subject_ref,
            }
            for item in selected
        ],
        "omitted": [row.to_dict() for row in omitted],
    }
    return ReferenceDeliveryPlan(
        provider_id=provider_id,
        selected_bindings=tuple(selected),
        omitted_bindings=tuple(omitted),
        image_mode=image_mode,
        video_mode=video_mode,
        max_images=max_images,
        max_videos=max_videos,
        budget_lineage=budget_lineage,
        digest=hash_value(payload),
    )


def refs_dir_intake_reason(path: Path, root: Path) -> str | None:
    """The media/refs intake guard: ``None`` when ``path`` may be read and
    uploaded as a reference, else a ready-to-surface 中文 refusal.

    The fallback tier is the ONE ref source that does not pass through
    :func:`resolve_local_ref`, so containment has to be enforced here or not at
    all. Reuses the ``core.safeio`` owner rather than forking a third policy:
    no link (symlink/junction/reparse point) on any segment from ``root`` down
    to the leaf, a regular file only (a directory/FIFO/device is never opened —
    an open of a FIFO would block), and no second hard-link name (a hardlink
    reads an outside file with no link flag to betray it). The realpath is
    re-checked against ``root`` afterwards as the closing containment claim.
    """
    try:
        refuse_linked_within(path, root, kind="参考")
        refuse_unsafe_regular_file(path, kind="参考文件")
    except SafeOutError as exc:
        return (
            f"media/refs 条目被拒绝,不作为参考也不会上传: {exc} — "
            "把真实文件复制进项目(manju import)再引用。"
        )
    try:
        real = Path(os.path.realpath(path))
        real.relative_to(Path(os.path.realpath(root)))
    except (OSError, ValueError):
        return (
            f"media/refs 条目解析后落在项目外,拒绝读取与上传: {path} — "
            "把真实文件复制进项目(manju import)再引用。"
        )
    return None


def _collect_refs_dir(project: "Project", items: list[RefItem]) -> None:
    """media/refs fallback — only contributes a kind when NO existing declared
    ref of that kind was found (mirrors kenburns' tier (c): fallback only when
    nothing more specific resolved).

    Selection is by no-follow lstat, never ``Path.is_file()`` (which FOLLOWS a
    link and made a planted symlink a valid primary reference). A refused entry
    is skipped AND recorded as a ``blocked_reason`` item placed after the
    usable one, so the refusal stays auditable in the lineage instead of
    vanishing."""
    have_image = any(it.kind == "image" and it.exists for it in items)
    have_video = any(it.kind == "video" and it.exists for it in items)
    if have_image and have_video:
        return
    refs_dir = project.refs_dir
    if not refs_dir.exists():
        return
    try:
        files = sorted(refs_dir.glob("*"), key=lambda p: p.as_posix())
    except OSError:
        return
    if not have_image:
        _pick_refs_dir(project, files, _IMAGE_EXTS, "image", items)
    if not have_video:
        _pick_refs_dir(project, files, _VIDEO_EXTS, "video", items)


def _pick_refs_dir(project: "Project", files: list[Path], exts: tuple[str, ...],
                   kind: str, items: list[RefItem]) -> None:
    root = project.root
    blocked: list[tuple[Path, str]] = []
    for p in files:
        if p.suffix.lower() not in exts:
            continue
        reason = refs_dir_intake_reason(p, root)
        if reason is not None:
            blocked.append((p, reason))
            continue
        items.append(RefItem(ref=_ref_str(project, p), tier=TIER_REFS_DIR,
                             kind=kind, path=p, is_url=False, exists=True,
                             root=root))
        break
    for p, reason in blocked:
        items.append(RefItem(ref=_ref_str(project, p), tier=TIER_REFS_DIR,
                             kind=kind, path=p, is_url=False, exists=False,
                             blocked_reason=reason, root=root))


# ------------------------------------------------------- delivery helpers


def unreadable_ref_message(items: list[RefItem]) -> str | None:
    """Return a one-line error for the first local ref that is missing or
    unreadable, naming the path AND the tier it came from — the pre-submit,
    zero-cost validation (goal reliability #3). ``None`` when every local ref is
    readable. URL refs are not checked here (they resolve remotely).

    A refused media/refs entry is a SAFETY-NET refusal, not a declaration: it
    only becomes the error when no usable ref of that kind survived, so a stray
    link in media/refs cannot turn every generation into a hard failure while a
    real fallback image sits right next to it."""
    usable = {it.kind for it in items
              if it.exists and (it.is_url or it.path is not None)}
    for it in items:
        if it.is_url:
            continue
        if it.tier == TIER_REFS_DIR and it.blocked_reason and it.kind in usable:
            continue
        if it.blocked_reason:
            return it.blocked_reason
        if it.path is None or not it.path.exists():
            return f"reference file not found: {it.ref} (tier: {it.tier})"
        try:
            with open_verified_ref(it):
                pass
        except (OSError, SafeOutError) as exc:
            return f"reference file unreadable: {it.ref} (tier: {it.tier}): {exc}"
    return None


def open_verified_ref(item: RefItem) -> IO[bytes]:
    """No-follow open of a local ref, re-verified on the OPEN descriptor.

    上传前重新校验:resolution and upload are separated in time, so the stored
    ``item.path`` is never trusted on its own — the fstat that proves regular
    file / single hard-link name runs on the SAME descriptor the caller then
    reads, and (when the item carries a ``root``) containment is re-asserted
    against that boundary. Same shape as ``core.library.open_verified_blob``.
    Raises :class:`SafeOutError` before any byte is read."""
    if item.path is None:
        raise SafeOutError(f"引用没有本地文件可读取: {item.ref}")
    path = Path(item.path)
    if item.root is not None:
        refuse_linked_within(path, item.root, kind="参考")
    elif path.is_symlink():
        raise SafeOutError(f"引用是链接(symlink/junction),拒绝读取与上传: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)  # a racing FIFO must never block the open
    fd = os.open(path, flags)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SafeOutError(f"引用不是普通文件,拒绝读取与上传: {path}")
        if st.st_nlink > 1:
            raise SafeOutError(
                f"引用有 {st.st_nlink} 个硬链接名 — 可能读取到项目外文件身份,"
                f"拒绝上传: {path}"
            )
        if item.root is not None:
            real = Path(os.path.realpath(path))
            try:
                real.relative_to(Path(os.path.realpath(item.root)))
            except ValueError as exc:
                raise SafeOutError(
                    f"引用解析后落在项目外,拒绝读取与上传: {path}"
                ) from exc
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "rb")


def read_ref_bytes(item: RefItem) -> bytes:
    """The ONE front door for reading a reference's bytes for delivery — every
    provider that uploads ref bytes reads through this, never ``read_bytes()``
    on the stored path (PROVIDER-REF-001)."""
    with open_verified_ref(item) as f:
        return f.read()


def base64_ref(item: RefItem, *, data_uri: bool = True, mime: str | None = None) -> str:
    """Base64 of a local ref. ``data_uri`` prepends ``data:<mime>;base64,``
    (Runway ``promptImage`` style); ``False`` yields the raw base64 string
    (Kling ``image_url`` explicitly forbids the data-URI prefix)."""
    assert item.path is not None
    raw = read_ref_bytes(item)
    b64 = base64.b64encode(raw).decode("ascii")
    if not data_uri:
        return b64
    guessed = mime or mimetypes.guess_type(item.path.name)[0] or "image/png"
    return f"data:{guessed};base64,{b64}"


def _safe_multipart_filename(name: str) -> str:
    """A multipart filename safe to embed in a hand-built Content-Disposition
    header (#44, goal W). POSIX filenames may legally contain double quotes,
    CR or LF — embedded raw, any of those can terminate the ``filename=""``
    attribute early or inject extra header/body content into the request.
    Strips CR/LF and double quotes; falls back to a generated safe name when
    nothing printable survives (an all-quote/newline filename, or empty)."""
    cleaned = name.replace("\r", "").replace("\n", "").replace('"', "").strip()
    return cleaned or f"upload-{uuid.uuid4().hex[:8]}"


def encode_multipart(
    fields: dict[str, str], files: list[tuple[str, str, bytes]]
) -> tuple[str, bytes]:
    """Encode a ``multipart/form-data`` body (stdlib only, no deps). Returns
    ``(content_type, body)``. ``files`` items are ``(field_name, filename,
    bytes)``. Used by the comfyui ``POST /upload/image`` and the generic_cloud
    ``multipart`` image mode. ``filename`` is sanitized (#44) before it is
    embedded in the Content-Disposition header — the one place every caller's
    multipart upload goes through, so the limit stays consistent."""
    boundary = "----manju" + uuid.uuid4().hex
    parts: list[bytes] = []

    def w(s: str | bytes) -> None:
        parts.append(s.encode("utf-8") if isinstance(s, str) else s)

    for name, value in fields.items():
        w(f"--{boundary}\r\n")
        w(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        w(f"{value}\r\n")
    for name, filename, data in files:
        safe_name = _safe_multipart_filename(filename)
        w(f"--{boundary}\r\n")
        w(f'Content-Disposition: form-data; name="{name}"; filename="{safe_name}"\r\n')
        w("Content-Type: application/octet-stream\r\n\r\n")
        w(data)
        w("\r\n")
    w(f"--{boundary}--\r\n")
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


# ---------------------------------------------------- reliability advisories


def ref_reliability_notes(shot_id: str, refset: RefSet, takes: list) -> list[str]:
    """Build-time signals so silent ref-dropping is impossible (reliability #1,
    #2). Reads the ``ref_delivery`` each provider records on its take:

    * a shot that HAS declared refs routed to a provider with mode ``none`` (or
      one that records no delivery at all) → an advisory (QC info);
    * a shot that HAS declared refs, provider DECLARES ref support, yet zero got
      delivered → a warning naming the tier mismatch.

    Returns messages already prefixed with the shot id. Empty when the shot has
    no declared refs (the generic media/refs fallback is covered elsewhere)."""
    notes: list[str] = []
    if not refset.has_declared_refs() or not takes:
        return notes
    provider = getattr(takes[0].sidecar, "provider", "?")
    delivery = _aggregate_delivery(takes)

    for kind, mode_key, decl in (
        ("image", "image_mode", refset.declared_items("image")),
        ("video", "video_mode", refset.declared_items("video")),
    ):
        if not decl:
            continue
        tiers = ", ".join(sorted({it.tier for it in decl}))
        mode = delivery.get(mode_key)
        delivered_n = len(delivery.get(f"{kind}s", []))
        if mode in (None, "none"):
            notes.append(
                f"{shot_id}: 声明了参考{'图' if kind == 'image' else '视频'}"
                f"(tier: {tiers})但 provider {provider} 不消费引用"
                f"({mode_key}: none)— 参考被丢弃(QC info)"
            )
        elif delivered_n == 0:
            notes.append(
                f"{shot_id}: 参考{'图' if kind == 'image' else '视频'}声明于 "
                f"tier {tiers},但 provider {provider} 的 {mode_key}={mode} "
                f"未投递任何引用(tier mismatch)"
            )
    return notes


def _aggregate_delivery(takes: list) -> dict:
    """Merge the ``ref_delivery`` blocks across a shot's takes (same provider,
    same refs — one representative delivery)."""
    merged: dict = {}
    for t in takes:
        rd = getattr(t.sidecar, "params", {}).get("ref_delivery")
        if not isinstance(rd, dict):
            continue
        for mk in ("image_mode", "video_mode"):
            if rd.get(mk) and mk not in merged:
                merged[mk] = rd[mk]
        for lk in ("images", "videos"):
            if rd.get(lk):
                merged.setdefault(lk, rd[lk])
    return merged


# --------------------------------------------------------------- internals


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v not in (None, "")]
    return [value] if value != "" else []


def _is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


# 08_10_12C WP2 §7.4 — the closed reference-transfer vocabulary. An unknown
# token is NEVER silently accepted: it lands in ``transfer_errors`` and the
# production checks refuse it (REFERENCE_CONTROL_CONFLICT).
REF_TRANSFER_VOCAB = frozenset({
    "character_identity", "face", "costume", "prop", "style",
    "background", "pose", "framing", "lighting", "location",
    "color_grade", "motion",
})


def control_owners(refset: RefSet) -> dict[str, tuple[RefItem, ...]]:
    """Explicit owners by ``(role, canonical subject scope)``.

    The return key keeps the role visible while encoding the scope as
    ``role@scope``. ``None`` is rendered as ``role@*`` for a global owner.
    ``ignore`` never claims ownership, and duplicate physical blobs remain
    distinct logical bindings until after this validation has run.
    """
    owners: dict[str, list[RefItem]] = {}
    for item in refset.items:
        ignored = set(item.ignore)
        for role in dict.fromkeys(item.controls):
            role = str(role)
            if role in ignored:
                continue
            scope = normalize_subject_scope(item.subject_ref, role=role)
            key = f"{role}@{scope or '*'}"
            owners.setdefault(key, []).append(item)
    return {role: tuple(items) for role, items in owners.items()}


def normalize_subject_scope(subject_ref: str | None, *, role: str | None = None) -> str | None:
    """Compatibility name for the shared authored-scope canonicalizer."""
    return canonical_subject_scope(subject_ref, role=role)


def reference_control_conflicts(refset: RefSet) -> list[dict[str, Any]]:
    """Return every invalid or multiply-owned closed role with exact refs."""
    conflicts: list[dict[str, Any]] = []
    for item in refset.items:
        for error in item.transfer_errors:
            conflicts.append({
                "role": None,
                "refs": [item.ref],
                "message": f"reference {item.ref} has invalid transfer ownership: {error}",
            })

    owners = control_owners(refset)
    by_role: dict[str, list[tuple[str, RefItem]]] = {}
    for key, items in owners.items():
        role, _, scope = key.partition("@")
        for item in items:
            by_role.setdefault(role, []).append((scope, item))
    for role, scoped_items in sorted(by_role.items()):
        counts: dict[str, int] = {}
        for scope, _item in scoped_items:
            counts[scope] = counts.get(scope, 0) + 1
        scopes = set(counts)
        # Same scoped subject conflicts. A global owner conflicts with every
        # scoped owner because it claims the whole role.
        bad_scopes = {scope for scope, count in counts.items() if count > 1}
        if "*" in scopes and len(scoped_items) > 1:
            bad_scopes = scopes
        if len(scoped_items) <= 1 or not bad_scopes:
            continue
        refs = [item.ref for scope, item in scoped_items if scope in bad_scopes]
        conflicts.append({
            "role": role,
            "scopes": sorted(bad_scopes),
            "refs": refs,
            "message": (
                f"closed reference role {role!r} has multiple owners in scope "
                f"{sorted(bad_scopes)!r}: " + ", ".join(refs)
            ),
        })
    return conflicts


def validate_control_ownership(refset: RefSet) -> None:
    """Fail before provider work when transfer ownership is contradictory."""
    conflicts = reference_control_conflicts(refset)
    if conflicts:
        raise ReferenceControlConflict(conflicts)


def _split_transfer(value: Any) -> tuple[Any, dict | None]:
    """Accept the additive dict-form binding entry — ``{ref|path|image|video:
    str, controls: [...], ignore: [...], subject_ref: ...}`` — returning the
    plain ref value plus the transfer spec. A non-dict entry passes through
    unchanged (legacy string bindings stay byte-identical)."""
    if not isinstance(value, dict):
        return value, None
    ref = (value.get("ref") or value.get("path")
           or value.get("image") or value.get("video") or "")
    return ref, value


def _transfer_fields(
    spec: dict | None, *, inferred_scope: str | None = None
) -> dict:
    """Validated RefItem transfer fields from a dict-form entry. Unknown enums
    and controls∩ignore conflicts are recorded, never dropped."""
    spec = spec or {}
    controls = tuple(str(c) for c in _as_list(spec.get("controls")))
    ignore = tuple(str(c) for c in _as_list(spec.get("ignore")))
    errors: list[str] = []
    unknown = sorted({c for c in (*controls, *ignore) if c not in REF_TRANSFER_VOCAB})
    if unknown:
        errors.append("未知 transfer 枚举: " + ", ".join(unknown)
                      + "(允许: " + ", ".join(sorted(REF_TRANSFER_VOCAB)) + ")")
    overlap = sorted(set(controls) & set(ignore))
    if overlap:
        errors.append("controls 与 ignore 冲突: " + ", ".join(overlap))
    subject = spec.get("subject_ref")
    subject_ref = normalize_subject_scope(subject or inferred_scope)
    return {
        "controls": controls,
        "ignore": ignore,
        "subject_ref": subject_ref,
        "declared_transfer": bool(controls or ignore or subject),
        "transfer_errors": tuple(errors),
    }


def _make_item(
    project: "Project", value: Any, tier: str, kind: str, *,
    inferred_scope: str | None = None,
) -> RefItem:
    value, transfer = _split_transfer(value)
    extra = _transfer_fields(transfer, inferred_scope=inferred_scope)
    s = str(value)
    if _is_url(s):
        return RefItem(ref=s, tier=tier, kind=kind, path=None, is_url=True,
                       exists=True, **extra)
    p, reason = resolve_local_ref(project, s)
    exists = bool(p and p.exists() and p.is_file())
    return RefItem(ref=s, tier=tier, kind=kind, path=p, is_url=False, exists=exists,
                   blocked_reason=reason, root=project.root, **extra)


def _classify(
    project: "Project", value: Any, tier: str, *,
    inferred_scope: str | None = None,
) -> RefItem:
    """A mixed refs list entry: video by extension, else image. The dict form
    may also name its kind via an explicit ``video:``/``image:`` key."""
    plain, spec = _split_transfer(value)
    if spec is not None and spec.get("video"):
        return _make_item(
            project, value, tier, "video", inferred_scope=inferred_scope
        )
    if spec is not None and spec.get("image"):
        return _make_item(
            project, value, tier, "image", inferred_scope=inferred_scope
        )
    s = str(plain)
    kind = "video" if Path(s.split("?", 1)[0]).suffix.lower() in _VIDEO_EXTS else "image"
    return _make_item(project, value, tier, kind, inferred_scope=inferred_scope)


def resolve_local_ref(project: "Project", value: str) -> tuple[Path | None, str | None]:
    """THE ONE containment guard for a local ref/keyframe path (goal item
    13/17) — every provider-facing resolver that turns an authored path into
    a local file that gets read and uploaded MUST call this instead of
    re-implementing its own absolute/escape handling.

    Refuses an ABSOLUTE path and any path that resolves outside the project
    root — a downloaded/untrusted project must never make a cloud provider
    read and upload a file outside its own directory (§ threat model).
    Returns ``(resolved_path, None)`` on success, or ``(None, reason)`` — a
    ready-to-surface 中文 explanation — when the path is refused. A path that
    is simply absent (never existed) is NOT refused here; it resolves to a
    real (missing) path and the caller's own existence check reports that.
    """
    p = Path(value)
    if p.is_absolute():
        return None, (
            f"引用路径不能是绝对路径: {value} — provider 会读取并上传该文件,"
            "不允许指向项目外部。把文件放进项目(manju import 或 media/refs)再引用。"
        )
    try:
        resolved = project.resolve(value)
    except Exception:
        return None, (
            f"引用路径超出项目边界: {value} — 把文件放进项目"
            "(manju import 或 media/refs)再引用。"
        )
    return resolved, None


def _ref_str(project: "Project", path: Path) -> str:
    try:
        return project.relpath(path)
    except Exception:
        return str(path)


def _dedup(items: list[RefItem]) -> list[RefItem]:
    """Deduplicate physical legacy refs without dropping logical bindings.

    Plain string refs keep the historical first-wins behavior. Explicit
    transfer declarations are logical bindings: the same blob may be bound to
    two subjects and must survive as two items. Exact duplicate declarations
    are still collapsed.
    """
    seen_physical: set[tuple[str, str]] = set()
    seen_logical: set[tuple[Any, ...]] = set()
    out: list[RefItem] = []
    for it in items:
        key = str(it.path.resolve()) if it.path is not None else it.ref
        if it.declared_transfer or it.transfer_errors or it.subject_ref is not None:
            logical = (it.kind, key, tuple(it.controls), tuple(it.ignore),
                       normalize_subject_scope(it.subject_ref))
            if logical in seen_logical:
                continue
            seen_logical.add(logical)
        else:
            sig = (it.kind, key)
            if sig in seen_physical:
                continue
            seen_physical.add(sig)
        out.append(it)
    return out


def physical_ref_key(item: RefItem) -> tuple[str, str]:
    """Stable physical-blob key used after logical binding validation."""
    key = str(item.path.resolve()) if item.path is not None else item.ref
    return item.kind, key


def _physical_items(items: list[RefItem]) -> list[RefItem]:
    seen: set[tuple[str, str]] = set()
    out: list[RefItem] = []
    for item in items:
        key = physical_ref_key(item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _unique_paths(items: list[RefItem], kind: str) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for item in items:
        if item.kind != kind or item.path is None or not item.exists:
            continue
        key = str(item.path.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(item.path)
    return out


def _first_existing_image(items: list[RefItem]) -> RefItem | None:
    for it in items:
        if it.kind == "image" and it.path is not None and it.exists:
            return it
    return None


def _ref_lineage(it: RefItem) -> dict:
    out = {"ref": it.ref, "tier": it.tier, "exists": it.exists, "is_url": it.is_url}
    # 08_10_12C WP2: declared reference-transfer rides the lineage (additive —
    # a legacy string binding emits the exact keys it always did).
    if it.declared_transfer or it.transfer_errors:
        out["controls"] = list(it.controls)
        out["ignore"] = list(it.ignore)
        out["subject_ref"] = it.subject_ref
        if it.transfer_errors:
            out["transfer_errors"] = list(it.transfer_errors)
    elif it.subject_ref is not None:
        out["subject_ref"] = it.subject_ref
    return out
