"""Batch ingest (round X, goal: workflow smoothness #1) — map a directory (or
file list) of externally-produced assets onto specific project steps in one
reviewed move.

The pain this closes: a human sends three regenerated takes for S001, a batch
of TTS voice lines, and a folder of character reference stills to an outside
tool, then has to hand-place every single file back with `manju import` /
manual copies — losing the shot/voice/ref lineage `register_manual_take`
already gives a single file. Batch ingest reads the FILENAME CONVENTION (the
same one a human already uses to keep a folder of rushes organized) and maps
each file onto the right registration path, in bulk, with a reviewed dry-run
in between.

Two-phase, mirroring `manju build --dry-run` / `redo_batch`:

- :func:`plan_ingest` is READ-ONLY — it classifies every file and returns an
  :class:`IngestPlan` (JSON-serializable rows). Nothing moves.
- :func:`apply_ingest` executes a (possibly hand-edited) plan through the
  EXISTING registration paths — `providers.manual.register_manual_take` for
  takes, the same hand-dropped-voice convention `build/voice.py` already
  treats as MANUAL (no sidecar), and collision-safe copies into media/refs
  for reference stills — so nothing here invents a second write mechanism.
  Imports stay sacred (§3): every action COPIES, never moves/consumes, the
  source file.

Classification (filename convention, anchored at the START of the filename,
id validated via :mod:`core.idents` before it is ever used as a path
segment):

- ``S001.mp4`` / ``S001_take.mp4`` / ``S001_v2.mov`` → a new TAKE for shot
  S001 (video extension + an id matching an EXISTING shot).
- ``S001.wav`` / ``S001_voice.mp3`` → a new VOICE take for shot S001 (audio
  extension + an id matching an existing shot) — landed exactly like a
  hand-dropped voice file (no sidecar, never auto-invalidated, §4.3).
- ``S001_ref.png`` / ``S001_ref2.jpg`` → a REF image for shot S001 (image
  extension + ``_ref`` suffix + an id matching an existing shot) — copied
  into media/refs; binding it into `generation.params.refs` is left as an
  explicit hint (the shot lab's 存为参考 flow is the one that writes the spec).
- ``linxia_ref.png`` → a REF image for a bible character/scene/prop whose id
  is ``linxia`` — copied into media/refs AND registered onto that asset's
  ``ref_image`` field (bible files are free-form dicts, §4; this never
  overwrites an existing ref_image, it appends).
- anything else (unknown id, ambiguous id, unrecognized extension) → a plain
  import into media/imports, same as `manju import`, carrying a 中文 reason.

Per-file DEDUP: a file whose content hash already exists among
media/imports, media/gen/** (every shot's takes AND voice takes) or
media/refs is ``action="skip_duplicate"`` — imports are sacred, dedup is
never destructive (mirrors `media.preview.find_duplicate_import`'s stance),
it just avoids minting a second copy of bytes already in the project. A
duplicate WITHIN the batch itself (two dropped files with identical content)
is caught the same way, against the first row that claimed those bytes.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.events import append_event
from ..core.hashing import hash_file
from ..core.idents import is_safe_segment
from ..core.yamlio import read_yaml, write_yaml
from ..media.preview import make_preview
from ..providers.manual import register_manual_take

__all__ = [
    "IngestError",
    "IngestRow",
    "IngestPlan",
    "IngestRowResult",
    "IngestApplyResult",
    "plan_ingest",
    "apply_ingest",
]

TAKE_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
VOICE_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac"}
REF_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}

# ``_ref`` / ``_ref2`` / ``_ref10`` — a trailing numeric suffix disambiguates
# several stills for the same id without changing the classification.
_REF_SUFFIX_RE = re.compile(r"^(?P<base>.+)_ref\d*$")

# Bible files whose entries are addressable, ref-image-bearing assets (§4).
# ``style``/``voices`` are excluded — they are not id-keyed the same way an
# asset-matrix character/scene/prop entry is (core/assets.py mirrors this).
_BIBLE_REF_FILES = ("characters", "scenes", "props")

ROLES = ("auto", "take", "voice", "ref")


class IngestError(ValueError):
    """A batch-ingest planning/apply error — bad --role/--shot, a path that
    does not exist, or an id that fails the safe-segment check before it is
    ever used as a filesystem path segment."""


# --------------------------------------------------------------------- data


@dataclass
class IngestRow:
    """One planned action for one source file — DATA, not a side effect."""

    file: str  # source path exactly as given/resolved (absolute)
    name: str  # basename, for display
    hash: str  # "sha256:..." content hash
    action: str  # take | voice | shot_ref | bible_ref | import | skip_duplicate
    target: str  # human-facing 中文 description of where this lands
    reason: str  # why this classification/action (中文)
    shot_id: str | None = None  # resolved shot id (take / voice / shot_ref)
    asset_id: str | None = None  # resolved bible asset id (bible_ref)
    asset_kind: str | None = None  # "characters" | "scenes" | "props"

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file, "name": self.name, "hash": self.hash,
            "action": self.action, "target": self.target, "reason": self.reason,
            "shot_id": self.shot_id, "asset_id": self.asset_id,
            "asset_kind": self.asset_kind,
        }


@dataclass
class IngestPlan:
    rows: list[IngestRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"rows": [r.to_dict() for r in self.rows]}


@dataclass
class IngestRowResult:
    row: IngestRow
    ok: bool
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"row": self.row.to_dict(), "ok": self.ok, "detail": self.detail}
        if self.error is not None:
            out["error"] = self.error
        return out


@dataclass
class IngestApplyResult:
    results: list[IngestRowResult] = field(default_factory=list)
    stopped_at: int | None = None  # row index where a failure stopped the run

    def to_dict(self) -> dict[str, Any]:
        return {"results": [r.to_dict() for r in self.results], "stopped_at": self.stopped_at}


# ------------------------------------------------------------------- plan


def plan_ingest(
    project: Project,
    paths: list[Path] | list[str],
    *,
    role: str = "auto",
    shot: str | None = None,
) -> IngestPlan:
    """Classify every file under ``paths`` (files and/or directories,
    expanded recursively) into a target step. READ-ONLY: nothing moves,
    nothing is written — see :func:`apply_ingest` for execution."""
    if role not in ROLES:
        raise IngestError(f"未知 --role: {role!r} — 只能是 {'/'.join(ROLES)}")
    if shot is not None:
        if not is_safe_segment(shot):
            raise IngestError(
                f"--shot 不合法: {shot!r} — 只能包含字母、数字、下划线、连字符,长度 1-64"
            )
        if not project.shot_path(shot).exists():
            raise IngestError(
                f"--shot 指定的镜头不存在: {shot} — 先 `manju shot {shot}` 新建,或核对拼写"
            )

    files = _collect_files(paths)
    existing_index = _existing_hash_index(project)
    shot_ids = set(project.shot_ids())
    bible_owner = _bible_owner_map(project)

    rows: list[IngestRow] = []
    seen_in_batch: dict[str, str] = {}  # hash -> first file name claiming it

    for f in files:
        h = hash_file(f)
        if h in existing_index:
            rows.append(IngestRow(
                file=str(f), name=f.name, hash=h, action="skip_duplicate",
                target=existing_index[h],
                reason=f"内容已存在于 {existing_index[h]},跳过(素材只增不改,§3)",
            ))
            continue
        if h in seen_in_batch:
            rows.append(IngestRow(
                file=str(f), name=f.name, hash=h, action="skip_duplicate",
                target=seen_in_batch[h],
                reason=f"与本批次中的 {seen_in_batch[h]} 内容相同,跳过重复项",
            ))
            continue
        seen_in_batch[h] = f.name
        rows.append(_classify(f, h, role=role, shot=shot, shot_ids=shot_ids,
                              bible_owner=bible_owner))
    return IngestPlan(rows=rows)


def _collect_files(paths: list[Path] | list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            raise IngestError(f"路径不存在: {p} — 核对拼写和当前目录")
        if p.is_dir():
            out.extend(sorted(x for x in p.rglob("*") if x.is_file() and not x.name.startswith(".")))
        elif p.is_file():
            out.append(p)
        else:
            raise IngestError(f"不是普通文件也不是目录,拒绝入库: {p}")
    seen: set[Path] = set()
    uniq: list[Path] = []
    for f in out:
        rp = f.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(f)
    return uniq


def _existing_hash_index(project: Project) -> dict[str, str]:
    """First-seen content hash -> project-relative path, across every place
    an ingested file could already have landed: imports, every shot's
    takes+voice takes (media/gen/**), and media/refs. Mirrors
    ``media.preview.find_duplicate_import``'s size-then-sha256 stance, just
    pre-indexed once for the whole batch instead of per-candidate."""
    index: dict[str, str] = {}
    dirs = [project.imports_dir, project.gen_dir, project.refs_dir]
    candidates: list[Path] = []
    for d in dirs:
        if d.exists():
            candidates.extend(x for x in d.rglob("*") if x.is_file())
    for f in sorted(candidates):
        try:
            h = hash_file(f)
        except OSError:
            continue
        index.setdefault(h, project.relpath(f))
    return index


def _bible_owner_map(project: Project) -> dict[str, str]:
    """asset id -> which of characters/scenes/props.yaml it lives in. Reads
    the RAW files (not the merged ``load_bible``) so ``apply_ingest`` writes
    back to the correct file."""
    owner: dict[str, str] = {}
    for fname in _BIBLE_REF_FILES:
        path = project.root / "bible" / f"{fname}.yaml"
        if not path.exists():
            continue
        data = read_yaml(path) or {}
        if isinstance(data, dict):
            for k in data:
                owner.setdefault(str(k), fname)
    return owner


# -------------------------------------------------------------- classify


def _candidates(stem: str, *, suffix_markers: tuple[str, ...]) -> list[str]:
    """Ordered, de-duplicated id candidates extracted from a filename stem:
    an explicit role suffix (``_take``/``_voice``) stripped first, then the
    whole stem, then the leading token before the first ``_`` — covering
    ``S001.mp4``, ``S001_take.mp4`` and ``S001_v2.mov`` alike."""
    out: list[str] = []
    for marker in suffix_markers:
        if stem.endswith(marker) and len(stem) > len(marker):
            out.append(stem[: -len(marker)])
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


def _ref_candidates(stem: str) -> list[str]:
    out: list[str] = []
    m = _REF_SUFFIX_RE.match(stem)
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


def _match_shot(stem: str, suffix_markers: tuple[str, ...], shot_ids: set[str]) -> str | None:
    for c in _candidates(stem, suffix_markers=suffix_markers):
        if is_safe_segment(c) and c in shot_ids:
            return c
    return None


def _classify(
    f: Path, h: str, *, role: str, shot: str | None, shot_ids: set[str],
    bible_owner: dict[str, str],
) -> IngestRow:
    ext = f.suffix.lower()
    stem = f.stem
    name = f.name

    if shot is not None:
        # --shot forces every file onto ONE shot ("regenerated three
        # candidates externally") — filename id extraction is skipped.
        if role in ("auto", "take") and ext in TAKE_VIDEO_EXTS:
            return _take_row(f, h, shot)
        if role in ("auto", "voice") and ext in VOICE_AUDIO_EXTS:
            return _voice_row(f, h, shot)
        if role in ("auto", "ref") and ext in REF_IMAGE_EXTS:
            return _shot_ref_row(f, h, shot)
        return _import_row(f, h, reason=(
            f"{name}: --shot {shot} 与 --role {role}/扩展名 {ext or '(无)'} 不匹配,"
            "按普通素材导入"
        ))

    if role == "take":
        if ext not in TAKE_VIDEO_EXTS:
            return _import_row(f, h, reason=f"{name}: --role take 需要视频扩展名,按普通素材导入")
        sid = _match_shot(stem, ("_take",), shot_ids)
        if sid:
            return _take_row(f, h, sid)
        return _import_row(f, h, reason=f"{name}: 文件名未匹配到已存在的镜头 id,按普通素材导入")

    if role == "voice":
        if ext not in VOICE_AUDIO_EXTS:
            return _import_row(f, h, reason=f"{name}: --role voice 需要音频扩展名,按普通素材导入")
        sid = _match_shot(stem, ("_voice",), shot_ids)
        if sid:
            return _voice_row(f, h, sid)
        return _import_row(f, h, reason=f"{name}: 文件名未匹配到已存在的镜头 id,按普通素材导入")

    if role == "ref":
        if ext not in REF_IMAGE_EXTS:
            return _import_row(f, h, reason=f"{name}: --role ref 需要图片扩展名,按普通素材导入")
        return _classify_ref(f, h, stem, name, shot_ids, bible_owner)

    # role == "auto"
    if ext in TAKE_VIDEO_EXTS:
        sid = _match_shot(stem, ("_take",), shot_ids)
        if sid:
            return _take_row(f, h, sid)
        return _import_row(f, h, reason=(
            f"{name}: 看起来是视频素材,但文件名开头未匹配到已存在的镜头 id,按普通素材导入"
        ))
    if ext in VOICE_AUDIO_EXTS:
        sid = _match_shot(stem, ("_voice",), shot_ids)
        if sid:
            return _voice_row(f, h, sid)
        return _import_row(f, h, reason=(
            f"{name}: 看起来是音频素材,但文件名开头未匹配到已存在的镜头 id,按普通素材导入"
        ))
    if ext in REF_IMAGE_EXTS:
        return _classify_ref(f, h, stem, name, shot_ids, bible_owner)
    return _import_row(f, h, reason=f"{name}: 未识别的命名约定/扩展名,按普通素材导入")


def _classify_ref(
    f: Path, h: str, stem: str, name: str, shot_ids: set[str], bible_owner: dict[str, str],
) -> IngestRow:
    candidates = _ref_candidates(stem)
    shot_hit = next((c for c in candidates if is_safe_segment(c) and c in shot_ids), None)
    bible_hit = next((c for c in candidates if is_safe_segment(c) and c in bible_owner), None)
    if shot_hit and bible_hit and shot_hit != bible_hit:
        return _import_row(f, h, reason=(
            f"{name}: id 有歧义 — 既可能是镜头 {shot_hit} 也可能是 bible 资产 {bible_hit},"
            "无法确定,按普通素材导入(可用 --shot 强制指定,或给文件改名消歧)"
        ))
    if shot_hit:
        note = "(该 id 同时也是 bible 资产,已优先按镜头参考图处理)" if bible_hit else ""
        return _shot_ref_row(f, h, shot_hit, extra=note)
    if bible_hit:
        return _bible_ref_row(f, h, bible_hit, bible_owner[bible_hit])
    return _import_row(f, h, reason=(
        f"{name}: 未找到匹配的镜头/角色/场景/道具 id,按普通素材导入"
    ))


def _take_row(f: Path, h: str, shot_id: str) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="take", shot_id=shot_id,
        target=f"{shot_id}: 新 take",
        reason=f"文件名匹配镜头 {shot_id},登记为新 take(追加,不覆盖已有 take,§3)",
    )


def _voice_row(f: Path, h: str, shot_id: str) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="voice", shot_id=shot_id,
        target=f"{shot_id}: 新配音 take",
        reason=f"文件名匹配镜头 {shot_id},登记为人工配音(追加,永不自动失效,§4.3)",
    )


def _shot_ref_row(f: Path, h: str, shot_id: str, *, extra: str = "") -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="shot_ref", shot_id=shot_id,
        target=f"media/refs → 镜头 {shot_id} 参考图",
        reason=(f"文件名匹配镜头 {shot_id} 的参考图约定(_ref);复制到 media/refs,"
                f"如需绑定到该镜头,请在 generation.params.refs 中加入生成的路径{extra}"),
    )


def _bible_ref_row(f: Path, h: str, asset_id: str, asset_kind: str) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="bible_ref",
        asset_id=asset_id, asset_kind=asset_kind,
        target=f"media/refs → bible/{asset_kind}.yaml:{asset_id}",
        reason=f"文件名匹配 bible 资产 {asset_id}({asset_kind}),复制到 media/refs 并登记为其 ref_image",
    )


def _import_row(f: Path, h: str, *, reason: str) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="import",
        target="media/imports", reason=reason,
    )


# ------------------------------------------------------------------ apply


def _copy_collision_safe(dest_dir: Path, stem: str, suffix: str, src: Path) -> Path:
    """The same never-overwrite _2/_3 suffixing every other import path in
    this engine uses (cli.import_, gui _act_upload, gui _act_lab_save_ref)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    suffix = suffix if suffix.startswith(".") or not suffix else f".{suffix}"
    dest = dest_dir / f"{stem}{suffix}"
    n = 2
    while dest.exists():
        dest = dest_dir / f"{stem}_{n}{suffix}"
        n += 1
    shutil.copy2(src, dest)
    return dest


def _register_manual_voice_take(project: Project, shot_id: str, file: Path) -> Path:
    """Hand-dropped voice take: copied under the append-only voice_take_NN
    name, WITHOUT a sidecar — the exact convention ``build/voice.py``'s
    ``VoiceState.MANUAL`` already reads (a media file with no
    ``.sidecar.yaml`` pair is manual, never auto-invalidated, §4.3). Reuses
    ``Project.takes_dir``/``next_voice_take_name`` — no new naming scheme."""
    tdir = project.takes_dir(shot_id)
    tdir.mkdir(parents=True, exist_ok=True)
    name = project.next_voice_take_name(shot_id)
    dest = tdir / (name + file.suffix.lower())
    if dest.exists():  # paranoia: append-only means never clobber
        raise IngestError(f"拒绝覆盖已存在的配音 take: {dest}")
    shutil.copy2(file, dest)
    return dest


def _set_bible_ref_image(project: Project, asset_kind: str, asset_id: str, ref_rel: str) -> None:
    """Register ``ref_rel`` onto ``asset_id``'s ``ref_image`` field. Never
    destructive: an absent field is set; an existing scalar becomes a
    2-item list rather than being overwritten; an existing list is appended
    to (deduplicated) — the bible edit is purely additive."""
    path = project.root / "bible" / f"{asset_kind}.yaml"
    data = read_yaml(path) or {}
    if not isinstance(data, dict):
        raise IngestError(f"bible/{asset_kind}.yaml 不是合法的映射,拒绝写入")
    entry = data.get(asset_id)
    if not isinstance(entry, dict):
        raise IngestError(f"bible/{asset_kind}.yaml 中未找到资产: {asset_id}")
    cur = entry.get("ref_image")
    if cur in (None, ""):
        entry["ref_image"] = ref_rel
    elif isinstance(cur, list):
        if ref_rel not in cur:
            cur.append(ref_rel)
    elif cur != ref_rel:
        entry["ref_image"] = [cur, ref_rel]
    write_yaml(path, data)


def _execute_row(project: Project, row: IngestRow) -> dict[str, Any]:
    src = Path(row.file)
    if not src.is_file():
        raise IngestError(f"源文件不存在或不是文件: {row.file}")

    if row.action == "take":
        if not row.shot_id:
            raise IngestError("take 需要 shot_id")
        if not project.shot_path(row.shot_id).exists():
            raise IngestError(f"镜头不存在: {row.shot_id}")
        take = register_manual_take(project, row.shot_id, src)
        media_rel = project.relpath(take.media_path) if take.media_path else None
        return {"shot": row.shot_id, "take": take.name, "media": media_rel}

    if row.action == "voice":
        if not row.shot_id:
            raise IngestError("voice 需要 shot_id")
        if not project.shot_path(row.shot_id).exists():
            raise IngestError(f"镜头不存在: {row.shot_id}")
        dest = _register_manual_voice_take(project, row.shot_id, src)
        return {"shot": row.shot_id, "voice_take": dest.stem, "media": project.relpath(dest)}

    if row.action == "shot_ref":
        if not row.shot_id:
            raise IngestError("shot_ref 需要 shot_id")
        if not project.shot_path(row.shot_id).exists():
            raise IngestError(f"镜头不存在: {row.shot_id}")
        dest = _copy_collision_safe(project.refs_dir, f"{row.shot_id}_ref", src.suffix.lower(), src)
        return {"shot": row.shot_id, "ref": project.relpath(dest)}

    if row.action == "bible_ref":
        if not row.asset_id:
            raise IngestError("bible_ref 需要 asset_id")
        asset_kind = row.asset_kind
        if not asset_kind:
            owner = _bible_owner_map(project)
            asset_kind = owner.get(row.asset_id)
        if not asset_kind:
            raise IngestError(f"bible 中未找到资产: {row.asset_id}")
        dest = _copy_collision_safe(project.refs_dir, f"{row.asset_id}_ref", src.suffix.lower(), src)
        ref_rel = project.relpath(dest)
        _set_bible_ref_image(project, asset_kind, row.asset_id, ref_rel)
        return {"asset_id": row.asset_id, "asset_kind": asset_kind, "ref": ref_rel}

    if row.action == "import":
        dest = _copy_collision_safe(project.imports_dir, src.stem, src.suffix, src)
        rel = project.relpath(dest)
        preview_rel = None
        try:
            preview = make_preview(dest, project.runtime_dir / "thumbs")
            if preview is not None:
                preview_rel = project.relpath(preview)
        except Exception:
            pass
        return {"imported": rel, "preview": preview_rel}

    raise IngestError(f"未知 action: {row.action!r}")


def _apply_override(row: IngestRow, override: dict[str, Any] | None) -> IngestRow:
    if not override:
        return row
    action = override.get("action", row.action)
    shot_id = override.get("shot_id", row.shot_id)
    asset_id = override.get("asset_id", row.asset_id)
    asset_kind = row.asset_kind if asset_id == row.asset_id else None
    return replace(row, action=action, shot_id=shot_id, asset_id=asset_id, asset_kind=asset_kind)


def apply_ingest(
    project: Project,
    plan: IngestPlan,
    *,
    actor: str,
    overrides: dict[int, dict[str, Any]] | None = None,
) -> IngestApplyResult:
    """Execute a (possibly hand-edited) plan through the existing
    registration paths. Runs rows IN ORDER; a row that raises stops the
    whole run right there (``stopped_at``) — every row before it has
    already landed and is reported truthfully, nothing after it was
    attempted. ``overrides`` (row index -> {"action"/"shot_id"/"asset_id"})
    lets a reviewer (CLI re-run with different flags, or the GUI's per-row
    dropdown) correct a classification before it executes."""
    overrides = overrides or {}
    results: list[IngestRowResult] = []
    stopped_at: int | None = None
    landed = 0

    for i, row in enumerate(plan.rows):
        eff = _apply_override(row, overrides.get(i))
        if eff.action == "skip_duplicate":
            results.append(IngestRowResult(row=eff, ok=True, detail={"skipped": True}))
            append_event(project.root, actor, "ingest_row", {
                "index": i, "file": eff.name, "action": eff.action,
                "target": eff.target, "ok": True, "skipped": True,
            })
            continue
        try:
            detail = _execute_row(project, eff)
        except Exception as exc:
            err = " ".join(str(exc).split()) or exc.__class__.__name__
            results.append(IngestRowResult(row=eff, ok=False, error=err))
            append_event(project.root, actor, "ingest_row", {
                "index": i, "file": eff.name, "action": eff.action, "ok": False, "error": err,
            })
            stopped_at = i
            break
        results.append(IngestRowResult(row=eff, ok=True, detail=detail))
        append_event(project.root, actor, "ingest_row", {
            "index": i, "file": eff.name, "action": eff.action, "ok": True, "detail": detail,
        })
        landed += 1

    append_event(project.root, actor, "ingest", {
        "rows": len(plan.rows), "attempted": len(results), "landed": landed,
        "stopped_at": stopped_at,
    })
    return IngestApplyResult(results=results, stopped_at=stopped_at)
