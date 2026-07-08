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
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
        images = [it.path for it in items
                  if it.kind == "image" and it.path is not None and it.exists]
        videos = [it.path for it in items
                  if it.kind == "video" and it.path is not None and it.exists]
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
        """The first image ref as a raw path string (existing or not), for the
        comfyui/local_cmd ``{image}`` path-based workflows — mirrors the old
        ``_resolve_ref_image`` which passed an authored path through unchanged."""
        for it in self.items:
            if it.kind == "image":
                return str(it.path) if it.path is not None else it.ref
        return ""

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

    _collect_params(project, params, items)
    _collect_shot(project, shot, items)
    _collect_bible(project, shot, bible, items)
    _collect_refs_dir(project, items)  # gated: only when no existing declared ref

    return RefSet.from_items(items)


def _collect_params(project: "Project", params: dict, items: list[RefItem]) -> None:
    for key in ("image", "images"):
        for val in _as_list(params.get(key)):
            items.append(_make_item(project, val, TIER_PARAMS, "image"))
    for key in ("video", "videos"):
        for val in _as_list(params.get(key)):
            items.append(_make_item(project, val, TIER_PARAMS, "video"))
    for val in _as_list(params.get("refs")):
        items.append(_classify(project, val, TIER_PARAMS))


def _collect_shot(project: "Project", shot: "ShotSpec", items: list[RefItem]) -> None:
    # A shot may carry a top-level `refs:` (ShotSpec allows extra keys) — either
    # a flat list of paths/URLs or a {images/videos/image/video} mapping.
    refs = getattr(shot, "refs", None)
    if refs is None:
        return
    if isinstance(refs, dict):
        for key in ("image", "images"):
            for val in _as_list(refs.get(key)):
                items.append(_make_item(project, val, TIER_SHOT, "image"))
        for key in ("video", "videos"):
            for val in _as_list(refs.get(key)):
                items.append(_make_item(project, val, TIER_SHOT, "video"))
        for val in _as_list(refs.get("refs")):
            items.append(_classify(project, val, TIER_SHOT))
    else:
        for val in _as_list(refs):
            items.append(_classify(project, val, TIER_SHOT))


def _collect_bible(project: "Project", shot: "ShotSpec", bible: dict,
                   items: list[RefItem]) -> None:
    keys: list[str] = list(shot.characters)
    if shot.scene:
        keys.append(shot.scene)
    for key in keys:
        entry = bible.get(key)
        if not isinstance(entry, dict):
            continue
        for k in ("ref_image", "ref_images"):
            for val in _as_list(entry.get(k)):
                items.append(_make_item(project, val, TIER_BIBLE, "image"))
        for k in ("ref_video", "ref_videos"):
            for val in _as_list(entry.get(k)):
                items.append(_make_item(project, val, TIER_BIBLE, "video"))


def _collect_refs_dir(project: "Project", items: list[RefItem]) -> None:
    """media/refs fallback — only contributes a kind when NO existing declared
    ref of that kind was found (mirrors kenburns' tier (c): fallback only when
    nothing more specific resolved)."""
    have_image = any(it.kind == "image" and it.exists for it in items)
    have_video = any(it.kind == "video" and it.exists for it in items)
    if have_image and have_video:
        return
    refs_dir = project.refs_dir
    if not refs_dir.exists():
        return
    files = sorted((p for p in refs_dir.glob("*") if p.is_file()), key=lambda p: p.name)
    if not have_image:
        for p in files:
            if p.suffix.lower() in _IMAGE_EXTS:
                items.append(RefItem(ref=_ref_str(project, p), tier=TIER_REFS_DIR,
                                     kind="image", path=p, is_url=False, exists=True))
                break
    if not have_video:
        for p in files:
            if p.suffix.lower() in _VIDEO_EXTS:
                items.append(RefItem(ref=_ref_str(project, p), tier=TIER_REFS_DIR,
                                     kind="video", path=p, is_url=False, exists=True))
                break


# ------------------------------------------------------- delivery helpers


def unreadable_ref_message(items: list[RefItem]) -> str | None:
    """Return a one-line error for the first local ref that is missing or
    unreadable, naming the path AND the tier it came from — the pre-submit,
    zero-cost validation (goal reliability #3). ``None`` when every local ref is
    readable. URL refs are not checked here (they resolve remotely)."""
    for it in items:
        if it.is_url:
            continue
        if it.blocked_reason:
            return it.blocked_reason
        if it.path is None or not it.path.exists():
            return f"reference file not found: {it.ref} (tier: {it.tier})"
        try:
            with open(it.path, "rb"):
                pass
        except OSError as exc:
            return f"reference file unreadable: {it.ref} (tier: {it.tier}): {exc}"
    return None


def base64_ref(item: RefItem, *, data_uri: bool = True, mime: str | None = None) -> str:
    """Base64 of a local ref. ``data_uri`` prepends ``data:<mime>;base64,``
    (Runway ``promptImage`` style); ``False`` yields the raw base64 string
    (Kling ``image_url`` explicitly forbids the data-URI prefix)."""
    assert item.path is not None
    raw = item.path.read_bytes()
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


def _make_item(project: "Project", value: Any, tier: str, kind: str) -> RefItem:
    s = str(value)
    if _is_url(s):
        return RefItem(ref=s, tier=tier, kind=kind, path=None, is_url=True, exists=True)
    p, reason = resolve_local_ref(project, s)
    exists = bool(p and p.exists() and p.is_file())
    return RefItem(ref=s, tier=tier, kind=kind, path=p, is_url=False, exists=exists,
                   blocked_reason=reason)


def _classify(project: "Project", value: Any, tier: str) -> RefItem:
    """A mixed refs list entry: video by extension, else image."""
    s = str(value)
    kind = "video" if Path(s.split("?", 1)[0]).suffix.lower() in _VIDEO_EXTS else "image"
    return _make_item(project, value, tier, kind)


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
    """Keep first occurrence (most-specific tier) per (kind, resolved key)."""
    seen: set[tuple[str, str]] = set()
    out: list[RefItem] = []
    for it in items:
        key = str(it.path.resolve()) if it.path is not None else it.ref
        sig = (it.kind, key)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(it)
    return out


def _first_existing_image(items: list[RefItem]) -> RefItem | None:
    for it in items:
        if it.kind == "image" and it.path is not None and it.exists:
            return it
    return None


def _ref_lineage(it: RefItem) -> dict:
    return {"ref": it.ref, "tier": it.tier, "exists": it.exists, "is_url": it.is_url}
