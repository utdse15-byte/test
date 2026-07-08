"""Reference-asset OWNERSHIP / traceability (round-AA goal item 3).

``media/refs`` is a folder of files whose ownership today is only implicit —
a filename convention (``{shot}_ref*`` / ``{asset_id}_ref*``, the same one
:mod:`manju.build.ingest` classifies against) and bible ``ref_image`` /
``ref_images`` / ``ref_video`` / ``ref_videos`` fields. A human dropping into
a project full of reference stills has no way to tell who uses a given file,
which files are orphans nobody references, or which bible entries point at a
file that no longer exists.

This module is a READ MODEL plus one narrow WRITE action — it does NOT invent
a parallel registry that can drift from the files/bible themselves (an
architect-pinned constraint). Everything :func:`refs_report` says is derived
fresh, every call, from:

- the ``{id}_ref[N]`` filename convention (mirrors
  :func:`manju.build.ingest._classify_ref` — a shot id wins over a same-named
  bible asset id on ambiguity, exactly like ingest's classifier);
- every shot's OWN declared local ref values (``generation.params.image(s)``/
  ``video(s)``/``refs``, and the top-level ``refs:`` field — the ``params``
  and ``shot`` tiers :mod:`manju.providers.refs` resolves against, read
  straight off the raw shot YAML so a file a human wired up WITHOUT renaming
  it still traces back to its shot); and
- every bible entry's ``ref_image``/``ref_images``/``ref_video``/
  ``ref_videos`` fields (the ``bible`` tier).

:func:`assign_ref` makes an ownership relationship REAL in truth instead of
recording it somewhere parallel: assigning to a shot RENAMES the file to the
``{shot}_ref`` convention (refs are project-local, not sacred imports —
unlike ``media/imports``, see ``build/ingest.py``'s module docstring, a
``media/refs`` file may be renamed in place); assigning to a bible asset
renames it to ``{asset_id}_ref`` AND sets that entry's ``ref_image``. Either
way, any EXISTING bible pin naming the old filename is repointed at the new
one so a rename can never orphan a pin.

This module also carries the few helpers ``build/ingest.py`` used to define
for itself (``_copy_collision_safe``, ``_set_bible_ref_image``,
``_bible_owner_map``, ``_ref_candidates``) — moved here as the one shared
home so neither module copy-pastes the other's logic; ``ingest.py`` imports
them from here.

Lock contract: like :func:`manju.build.ingest.apply_ingest`, :func:`assign_ref`
does NOT itself acquire the cross-process build lock
(``.manju/build.lock`` / ``manju.runtime.buildlock.BuildLock``) — it is a
plain function a caller may call from inside an already-locked section. The
CLI ``refs assign`` command is the one caller today and takes the lock itself
(``cli._write_lock``), the same pattern ``manju ingest --apply`` uses.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .container import BIBLE_FILES, Project, ProjectError
from .events import append_event
from .idents import is_safe_segment
from .yamlio import read_yaml, write_yaml

__all__ = [
    "RefsError",
    "ROLES",
    "refs_report",
    "assign_ref",
    "bible_owner_map",
    "ref_candidates",
    "copy_collision_safe",
    "rename_collision_safe",
    "set_bible_ref_image",
]

# Kept separate from providers/refs.py's tuples on purpose: core/ must not
# import providers/ (build -> providers -> core is the one-way layering; this
# module only needs "what kind of media is this", not resolution/delivery).
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
_VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".gif")
_AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".flac")

# Bible files whose entries are addressable, ref-image-bearing ASSETS with an
# id used in the ``{asset_id}_ref`` filename convention (mirrors
# ``build/ingest.py``'s ``_BIBLE_REF_FILES`` — style/voices are excluded,
# same reasoning: they are not id-keyed the same way a character/scene/prop
# entry is, core/assets.py's KIND_FILES mirrors this too).
_BIBLE_ASSET_FILES = ("characters", "scenes", "props")

_KIND_TO_ROLE = {
    "characters": "character_ref",
    "scenes": "scene_ref",
    "props": "prop_ref",
}
_ROLE_TO_KIND = {v: k for k, v in _KIND_TO_ROLE.items()}

ROLES = ("shot_ref", "character_ref", "scene_ref", "prop_ref", "unknown")

# Bible fields that can pin a file as a declared reference (§4). Mirrors
# core/assets.py's ``_REF_IMAGE_KEYS``/``_REF_VIDEO_KEYS`` and
# providers/refs.py's tier-c bible collection.
_REF_IMAGE_KEYS = ("ref_image", "ref_images")
_REF_VIDEO_KEYS = ("ref_video", "ref_videos")

# ``_ref`` / ``_ref2`` / ``_ref10`` — a trailing numeric suffix disambiguates
# several stills for the same id without changing the classification. Public:
# build/ingest.py's ref classification shares this exact convention.
REF_SUFFIX_RE = re.compile(r"^(?P<base>.+)_ref\d*$")


class RefsError(ValueError):
    """A refs report/assign error — bad --shot/--character/--scene/--prop
    combination, a path outside media/refs, or a target id that doesn't
    exist. Always carries a ready-to-surface 中文 message."""


# ------------------------------------------------------------------ shared
# helpers moved here from build/ingest.py (goal item 3: one home, not two).


def bible_owner_map(project: Project) -> dict[str, str]:
    """asset id -> which of characters/scenes/props.yaml it lives in. Reads
    the RAW files (not the merged ``Project.load_bible``) so a caller writing
    back to the bible edits the CORRECT file."""
    owner: dict[str, str] = {}
    for fname in _BIBLE_ASSET_FILES:
        path = project.root / "bible" / f"{fname}.yaml"
        if not path.exists():
            continue
        data = read_yaml(path) or {}
        if isinstance(data, dict):
            for k in data:
                owner.setdefault(str(k), fname)
    return owner


def ref_candidates(stem: str) -> list[str]:
    """Ordered, de-duplicated id candidates extracted from a ref filename
    stem: the ``_ref[N]``-stripped base first, then the whole stem, then the
    leading token before the first ``_`` — covering ``linxia_ref.png``,
    ``S001_ref2.jpg`` and ``convenience_store_ref.png`` alike."""
    out: list[str] = []
    m = REF_SUFFIX_RE.match(stem)
    if m:
        out.append(m.group("base"))
    out.append(stem)
    if "_" in stem:
        out.append(stem.split("_", 1)[0])
    seen: set[str] = set()
    uniq: list[str] = []
    for c in out:
        if c and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def copy_collision_safe(dest_dir: Path, stem: str, suffix: str, src: Path) -> Path:
    """COPY ``src`` to ``<stem><suffix>`` under ``dest_dir``, never
    overwriting — the same ``_2``/``_3`` suffixing every import path in this
    engine uses (cli.import_, gui _act_upload, gui _act_lab_save_ref,
    build/ingest.py). Imports/refs landed via ingest are always COPIES (the
    source is never consumed) — see :func:`rename_collision_safe` for the
    in-place-rename sibling :func:`assign_ref` uses."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = suffix if suffix.startswith(".") or not suffix else f".{suffix}"
    dest = dest_dir / f"{stem}{suffix}"
    n = 2
    while dest.exists():
        dest = dest_dir / f"{stem}_{n}{suffix}"
        n += 1
    shutil.copy2(src, dest)
    return dest


def rename_collision_safe(dest_dir: Path, stem: str, suffix: str, src: Path) -> Path:
    """RENAME (not copy) ``src`` to ``<stem><suffix>`` under ``dest_dir``,
    collision-safe with the same ``_2``/``_3`` suffixing
    :func:`copy_collision_safe` uses. ``media/refs`` files are project-local,
    not sacred imports (unlike ``media/imports`` — build/ingest.py's module
    docstring), so :func:`assign_ref` makes an ownership relationship real by
    renaming the file IN PLACE rather than minting a second copy.

    A no-op (returns the already-correct path, touches nothing) when ``src``
    already IS the target name — including when it already occupies one of
    the ``_N`` collision slots from an earlier assign."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = suffix if suffix.startswith(".") or not suffix else f".{suffix}"
    src_resolved = src.resolve()
    dest = dest_dir / f"{stem}{suffix}"
    if dest.resolve() == src_resolved:
        return dest
    n = 2
    while dest.exists():
        dest = dest_dir / f"{stem}_{n}{suffix}"
        n += 1
        if dest.resolve() == src_resolved:
            return dest
    src.rename(dest)
    return dest


def set_bible_ref_image(project: Project, asset_kind: str, asset_id: str, ref_rel: str) -> None:
    """Register ``ref_rel`` onto ``asset_id``'s ``ref_image`` field. Never
    destructive: an absent field is set; an existing scalar becomes a
    2-item list rather than being overwritten; an existing list is appended
    to (deduplicated) — the bible edit is purely additive."""
    path = project.root / "bible" / f"{asset_kind}.yaml"
    data = read_yaml(path) or {}
    if not isinstance(data, dict):
        raise RefsError(f"bible/{asset_kind}.yaml 不是合法的映射,拒绝写入")
    entry = data.get(asset_id)
    if not isinstance(entry, dict):
        raise RefsError(f"bible/{asset_kind}.yaml 中未找到资产: {asset_id}")
    cur = entry.get("ref_image")
    if cur in (None, ""):
        entry["ref_image"] = ref_rel
    elif isinstance(cur, list):
        if ref_rel not in cur:
            cur.append(ref_rel)
    elif cur != ref_rel:
        entry["ref_image"] = [cur, ref_rel]
    write_yaml(path, data)


def _repoint_bible_pins(project: Project, old_rel: str, new_rel: str) -> list[str]:
    """After a ref file is renamed, repoint every bible ``ref_image``/
    ``ref_images``/``ref_video``/``ref_videos`` value that named ``old_rel``
    at ``new_rel`` instead — a rename must never leave a dangling pin.
    Returns the ``"<bible_file>:<asset_id>"`` addresses touched, sorted."""
    touched: set[str] = set()
    for fname in BIBLE_FILES:
        path = project.root / "bible" / f"{fname}.yaml"
        if not path.exists():
            continue
        data = read_yaml(path) or {}
        if not isinstance(data, dict):
            continue
        changed = False
        for asset_id, entry in data.items():
            if not isinstance(entry, dict):
                continue
            for key in _REF_IMAGE_KEYS + _REF_VIDEO_KEYS:
                cur = entry.get(key)
                if isinstance(cur, list):
                    if old_rel in cur:
                        entry[key] = [new_rel if v == old_rel else v for v in cur]
                        changed = True
                        touched.add(f"{fname}:{asset_id}")
                elif cur == old_rel:
                    entry[key] = new_rel
                    changed = True
                    touched.add(f"{fname}:{asset_id}")
        if changed:
            write_yaml(path, data)
    return sorted(touched)


# -------------------------------------------------------------------- misc


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)] if value != "" else []


def _kind_of(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _VIDEO_EXTS:
        return "video"
    if ext in _AUDIO_EXTS:
        return "audio"
    return "other"


def _normalize_rel(project: Project, value: str) -> str | None:
    """A project-relative ref value -> POSIX relpath, or ``None`` when it is
    a URL, absolute, or escapes the project root (never raises — this is a
    read-only classification, not the containment guard providers/refs.py
    enforces at generation time)."""
    if value.startswith(("http://", "https://")):
        return None
    try:
        resolved = (project.root / value).resolve()
        return resolved.relative_to(project.root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


def _shot_ref_values(shot_raw: dict[str, Any]) -> list[str]:
    """Every local ref value a shot's raw YAML declares — mirrors
    providers/refs.py's ``params`` and ``shot`` tiers (the ``bible`` tier is
    covered separately by the bible-pin scan below). Read off the RAW dict
    (not a validated ShotSpec) so a malformed shot never breaks the report."""
    out: list[str] = []
    generation = shot_raw.get("generation")
    params = generation.get("params") if isinstance(generation, dict) else None
    if isinstance(params, dict):
        for key in ("image", "images", "video", "videos", "refs"):
            out.extend(_as_list(params.get(key)))
    refs = shot_raw.get("refs")
    if isinstance(refs, dict):
        for key in ("image", "images", "video", "videos", "refs"):
            out.extend(_as_list(refs.get(key)))
    elif refs is not None:
        out.extend(_as_list(refs))
    return out


def _bible_pins(project: Project) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """``relpath -> ["<bible_file>:<asset_id>", ...]`` for every ref/video pin
    that resolves to an existing project file, plus ``missing``: pin values
    that do not resolve to an existing file, each as
    ``{bible_file, asset_id, field, value}``. Scans ALL bible files (not just
    the three asset kinds) — style/voices entries may carry a ``ref_image``
    too (core/assets.py's ``_entry_refs`` reads across all five kinds)."""
    pins: dict[str, list[str]] = {}
    missing: list[dict[str, Any]] = []
    for fname in BIBLE_FILES:
        path = project.root / "bible" / f"{fname}.yaml"
        if not path.exists():
            continue
        data = read_yaml(path) or {}
        if not isinstance(data, dict):
            continue
        for asset_id, entry in data.items():
            if not isinstance(entry, dict):
                continue
            for key in _REF_IMAGE_KEYS + _REF_VIDEO_KEYS:
                for val in _as_list(entry.get(key)):
                    if val.startswith(("http://", "https://")):
                        continue  # a URL pin can never be "missing" locally
                    rel = _normalize_rel(project, val)
                    if rel is None or not (project.root / rel).exists():
                        missing.append({
                            "bible_file": fname, "asset_id": str(asset_id),
                            "field": key, "value": val,
                        })
                        continue
                    addr = f"{fname}:{asset_id}"
                    bucket = pins.setdefault(rel, [])
                    if addr not in bucket:
                        bucket.append(addr)
    return pins, missing


# ------------------------------------------------------------------- report


def refs_report(project: Project) -> dict[str, Any]:
    """One row per file under ``media/refs`` (recursive) — file / kind / role
    / owners / bible_pinned / orphan — plus project-wide ``missing`` bible
    pins, per-role counts and an orphan count. Nothing here writes; see
    :func:`assign_ref` to fix an orphan or a missing pointer.

    ``role`` comes ONLY from the ``{id}_ref[N]`` filename convention (a shot
    id wins over a same-named bible asset id, mirroring
    ``build/ingest.py``'s classifier) — it never changes just because a shot
    happens to reference the file some other way. ``owners`` is broader: the
    naming-convention hit (if the named id actually exists) UNION every shot
    that explicitly declares this exact file as a ref UNION every bible entry
    that pins it. ``orphan`` is true only when that full union is empty AND
    the file is not bible-pinned (the two coincide in practice — a pin always
    contributes to ``owners`` too — this mirrors the round's explicit
    "no owner resolves and nothing pins it" contract).
    """
    refs_dir = project.refs_dir
    files = sorted(
        (p for p in refs_dir.rglob("*") if p.is_file()),
        key=lambda p: project.relpath(p),
    ) if refs_dir.exists() else []

    shot_ids = set(project.shot_ids())
    owner_map = bible_owner_map(project)
    pins, missing = _bible_pins(project)

    # every shot's own declared local refs: relpath -> [shot_id, ...]
    shot_declared: dict[str, list[str]] = {}
    for sid in project.shot_ids():
        try:
            raw = project.load_shot_raw(sid)
        except Exception:
            continue  # a broken shot is `manju check`'s problem, not this report's
        for val in _shot_ref_values(raw):
            rel = _normalize_rel(project, val)
            if rel is None:
                continue
            bucket = shot_declared.setdefault(rel, [])
            if sid not in bucket:
                bucket.append(sid)

    role_counts: dict[str, int] = {role: 0 for role in ROLES}
    orphan_count = 0
    rows: list[dict[str, Any]] = []

    for p in files:
        rel = project.relpath(p)
        stem = p.stem
        role = "unknown"
        owners: list[str] = []

        candidates = ref_candidates(stem)
        shot_hit = next((c for c in candidates if is_safe_segment(c) and c in shot_ids), None)
        bible_hit = next((c for c in candidates if is_safe_segment(c) and c in owner_map), None)
        if shot_hit:
            role = "shot_ref"
            owners.append(shot_hit)
        elif bible_hit:
            bfile = owner_map[bible_hit]
            role = _KIND_TO_ROLE.get(bfile, "unknown")
            owners.append(f"{bfile}:{bible_hit}")

        for sid in shot_declared.get(rel, []):
            if sid not in owners:
                owners.append(sid)
        pin_addrs = pins.get(rel, [])
        for addr in pin_addrs:
            if addr not in owners:
                owners.append(addr)

        pinned = bool(pin_addrs)
        orphan = not owners and not pinned
        if orphan:
            orphan_count += 1
        role_counts[role] = role_counts.get(role, 0) + 1

        rows.append({
            "file": rel,
            "kind": _kind_of(p),
            "role": role,
            "owners": sorted(owners),
            "bible_pinned": pinned,
            "orphan": orphan,
        })

    return {
        "files": rows,
        "total": len(rows),
        "missing": sorted(missing, key=lambda m: (m["bible_file"], m["asset_id"], m["field"])),
        "counts_by_role": role_counts,
        "orphan_count": orphan_count,
        "note": (
            "关系从文件名约定 + 镜头 YAML 的 refs 字段 + bible ref_image 字段推导,"
            "不是并行索引;修正归属请用 `manju refs assign`(改文件名/写 bible 字段),"
            "而不是编辑本报告。"
        ),
    }


# ------------------------------------------------------------------- assign


_TARGET_KIND_TO_BIBLE_FILE = {"character": "characters", "scene": "scenes", "prop": "props"}


def assign_ref(
    project: Project,
    relpath: str,
    *,
    shot: str | None = None,
    character: str | None = None,
    scene: str | None = None,
    prop: str | None = None,
    actor: str,
) -> dict[str, Any]:
    """Assign an existing ``media/refs`` file to EXACTLY ONE owner, making the
    relationship real in truth (no parallel registry):

    - ``shot=...`` renames the file to the ``{shot}_ref`` collision-safe
      convention within media/refs;
    - ``character=`` / ``scene=`` / ``prop=...`` renames it to
      ``{asset_id}_ref`` AND sets that bible entry's ``ref_image`` (additive,
      via :func:`set_bible_ref_image`).

    Either way, if the file was ALREADY bible-pinned under its old name, every
    pin naming the old path is repointed at the new one
    (:func:`_repoint_bible_pins`) — a rename never orphans an existing pin.

    Refuses (中文 :class:`RefsError`) when: not exactly one target is given;
    ``relpath`` does not exist or is not a file; ``relpath`` resolves outside
    ``media/refs``; or the named shot/character/scene/prop does not exist.

    Appends a ``ref_assign`` event. Does NOT take the cross-process build
    lock itself — see the module docstring's lock contract; the CLI layer
    holds it.
    """
    chosen = {k: v for k, v in
              (("shot", shot), ("character", character), ("scene", scene), ("prop", prop))
              if v}
    if len(chosen) != 1:
        raise RefsError(
            "必须且只能指定一个归属目标 --shot / --character / --scene / --prop 之一,"
            f"收到 {len(chosen)} 个" + (f": {', '.join(chosen)}" if chosen else "")
        )
    kind, target_id = next(iter(chosen.items()))

    try:
        src = project.resolve(relpath)
    except ProjectError:
        raise RefsError(f"路径越界项目目录,拒绝: {relpath}") from None
    if not src.exists() or not src.is_file():
        raise RefsError(f"参考文件不存在或不是文件: {relpath}")
    refs_dir_resolved = project.refs_dir.resolve()
    src_resolved = src.resolve()
    if src_resolved != refs_dir_resolved and refs_dir_resolved not in src_resolved.parents:
        raise RefsError(f"参考文件必须在 media/refs 目录内,拒绝: {relpath}")

    bible_file: str | None = None
    if kind == "shot":
        if not project.shot_path(target_id).exists():
            raise RefsError(f"镜头不存在: {target_id} — 先 `manju shot {target_id}` 新建,或核对拼写")
    else:
        if not is_safe_segment(target_id):
            raise RefsError(f"--{kind} 不合法: {target_id!r} — 只能包含字母、数字、下划线、连字符,长度 1-64")
        bible_file = _TARGET_KIND_TO_BIBLE_FILE[kind]
        bpath = project.root / "bible" / f"{bible_file}.yaml"
        data = read_yaml(bpath) or {} if bpath.exists() else {}
        entry = data.get(target_id) if isinstance(data, dict) else None
        if not isinstance(entry, dict):
            raise RefsError(f"bible/{bible_file}.yaml 中未找到资产: {target_id}")

    old_rel = project.relpath(src)
    new_stem = f"{target_id}_ref"
    dest = rename_collision_safe(project.refs_dir, new_stem, src.suffix.lower(), src)
    new_rel = project.relpath(dest)

    repointed = _repoint_bible_pins(project, old_rel, new_rel) if new_rel != old_rel else []

    if bible_file is not None:
        set_bible_ref_image(project, bible_file, target_id, new_rel)

    address = target_id if kind == "shot" else f"{bible_file}:{target_id}"
    detail = {
        "relpath": old_rel, "old": old_rel, "new": new_rel, "kind": kind,
        "target": target_id, "address": address, "repointed": repointed,
        "renamed": new_rel != old_rel,
    }
    append_event(project.root, actor, "ref_assign", detail)
    return {"ok": True, **detail}
