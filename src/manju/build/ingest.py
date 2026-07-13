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

LIBRARY dedup (round X agent XF — extends the above, never duplicates it): a
file whose content hash is NOT already in the project but IS already sitting
in the user's private library (``core.library``, ``~/.manju/library``) never
silently mints a second copy either. ``--on-duplicate`` (default ``skip``)
governs what happens: ``skip`` marks the row ``skip_duplicate`` with a 中文
advisory naming the library asset's tags (mirroring the project-side
message); ``import`` classifies the row normally and just ATTACHES the
advisory (``IngestRow.library_hint``) so the reviewer sees it without losing
the row; ``link`` does the same but additionally sources the copy from the
LIBRARY'S OWN blob instead of the dropped file (byte-identical either way —
this only changes provenance, recorded in the hint). A project-internal hit
(the dedup above) always wins over a library hit — this section only runs
for files that were NOT already found inside the project.

POST-IMPORT CONFIRMATION (round AA, goal items 1+2): a batch lands through
the SAME classification/registration paths above, but nothing used to say
HOW CONFIDENT that landing was, nor leave a reviewable trail behind. Two
additions, layered on top without changing what lands where:

- **match states** — every :class:`IngestRow` now carries ``match`` (one of
  ``matched``/``pending``/``unmatched``/``conflict``/``manual``) and
  ``candidates`` (the ids that were plausible). ``matched`` is an exact hit —
  ``--shot`` forced, or the filename's UNTRANSFORMED stem (or a documented
  suffix marker: ``_take``/``_voice``/``_refN``) resolves to exactly one id.
  ``pending`` is a hit that only resolved via the generic "ignore everything
  after the first underscore" fallback (e.g. ``S001_v2_final.mov`` when only
  the leading token is a real id) — it still lands exactly as before, just
  flagged for a human/agent to eyeball. ``conflict`` is what today's
  first-match-wins candidate search would have silently picked between: MORE
  THAN ONE distinct id genuinely resolves (two shots, two bible assets, or a
  shot AND a bible asset on different ids) — a conflict row is NEVER landed
  on a guess, it downgrades to ``import`` with every candidate id recorded
  and a 中文 reason explaining the ambiguity. ``manual`` is set at
  :func:`apply_ingest` time on any row a caller corrected via ``overrides``.
  ``skip_duplicate`` rows stay ``matched`` (exact by content hash).
- **persisted batch record + empty-shot staging** — every :func:`apply_ingest`
  run is written to ``reports/ingest_batches/<batch_id>.yaml`` (see
  :mod:`build.batches`) so it can be reviewed (confirm/flag/discard) after
  the fact, and a ``take`` that lands on a shot with NO ``selected_take`` yet
  is auto-selected (never overwriting an existing pick) so an externally
  produced take doesn't need a separate manual `manju select` — see
  :mod:`build.batches` and :func:`apply_ingest` for both."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..core.container import Project
from ..core.events import append_event
from ..core.hashing import hash_file
from ..core.idents import is_safe_segment
from ..core.refs import (REF_SUFFIX_RE, bible_owner_map, copy_collision_safe,
                         set_bible_ref_image)
from ..core.writes import WriteRejected, select_take_checked, selected_take_lock_block
from ..media.preview import make_preview
from ..providers.manual import register_manual_take

__all__ = [
    "IngestError",
    "IngestRow",
    "IngestPlan",
    "IngestRowResult",
    "IngestApplyResult",
    "MATCH_STATES",
    "plan_ingest",
    "apply_ingest",
]

TAKE_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
VOICE_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac"}
REF_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}

# ``_ref`` / ``_ref2`` / ``_ref10`` id-candidate extraction (ref_candidates),
# the bible-owner map, and the never-overwrite copy — all shared with
# core/refs.py (round-AA goal item 3's ownership report/assign), imported
# above instead of defined here twice.

ROLES = ("auto", "take", "voice", "ref")
ON_DUPLICATE_STRATEGIES = ("skip", "import", "link")

# round AA (goal items 1+2): per-row classification confidence — see the
# module docstring's "POST-IMPORT CONFIRMATION" section for the full semantics.
MATCH_STATES = ("matched", "pending", "unmatched", "conflict", "manual")


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
    # round X agent XF: set when this file's content hash ALSO matches an
    # asset already in the user's private library (core.library) — a 中文
    # advisory naming the library asset's tags, kept even when the row still
    # proceeds as a normal action (--on-duplicate import/link). None when no
    # library hit was found (or the library is unavailable).
    library_hint: str | None = None
    # round AA (goal items 1+2): classification confidence — one of
    # MATCH_STATES — and the id(s) that were plausible when it is anything
    # other than a clean single hit (see module docstring). "unmatched" is
    # the safe default: any construction path that forgets to set this
    # explicitly reads as "needs a look", never as a false "matched".
    match: str = "unmatched"
    candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file, "name": self.name, "hash": self.hash,
            "action": self.action, "target": self.target, "reason": self.reason,
            "shot_id": self.shot_id, "asset_id": self.asset_id,
            "asset_kind": self.asset_kind, "library_hint": self.library_hint,
            "match": self.match, "candidates": list(self.candidates),
        }


@dataclass
class IngestPlan:
    rows: list[IngestRow] = field(default_factory=list)
    # goal: honest job cancellation — a should_cancel() checkpoint tripped
    # mid-hash (plan_ingest is READ-ONLY, but hashing a large batch of big
    # video files is genuinely multi-second, §GUI jobs). ``rows`` still holds
    # everything classified before the trip; nothing was ever written either
    # way. Mirrors BuildResult/BatchResult's canceled/errors shape so
    # gui/jobs.py's JobRunner can generically recognize this as "canceled".
    canceled: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"rows": [r.to_dict() for r in self.rows],
                "canceled": self.canceled, "errors": self.errors}


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
    stopped_at: int | None = None  # row index where a failure OR a cancel stopped the run
    # round AA (goal item 2): the persisted batch record's id — always set
    # (either caller-supplied or generated) once apply_ingest returns; see
    # build.batches for reports/ingest_batches/<batch_id>.yaml.
    batch_id: str = ""
    # goal: honest job cancellation — see IngestPlan.canceled above; every row
    # in ``results`` before the trip already executed (apply_ingest is append-
    # only registration, §3) and stays landed.
    canceled: bool = False
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "stopped_at": self.stopped_at, "batch_id": self.batch_id,
            "canceled": self.canceled, "errors": self.errors,
        }


# ------------------------------------------------------------------- plan


def plan_ingest(
    project: Project,
    paths: list[Path] | list[str],
    *,
    role: str = "auto",
    shot: str | None = None,
    on_duplicate: str = "skip",
    should_cancel: "Callable[[], bool] | None" = None,
) -> IngestPlan:
    """Classify every file under ``paths`` (files and/or directories,
    expanded recursively) into a target step. READ-ONLY: nothing moves,
    nothing is written — see :func:`apply_ingest` for execution.

    ``on_duplicate`` (round X agent XF) governs ONLY files whose content
    hash matches something already in the user's private LIBRARY (never the
    project-internal dedup above, which always skips) — see the module
    docstring's "LIBRARY dedup" section.

    ``should_cancel`` (goal: honest job cancellation) is checked before EVERY
    file's hash — hashing a large batch of big video files is genuinely
    multi-second, and this is a GUI-jobs-runner concern only (``None``, the
    default, means every CLI call stays byte-identical to before)."""
    if role not in ROLES:
        raise IngestError(f"未知 --role: {role!r} — 只能是 {'/'.join(ROLES)}")
    if on_duplicate not in ON_DUPLICATE_STRATEGIES:
        raise IngestError(
            f"未知 --on-duplicate: {on_duplicate!r} — 只能是 "
            f"{'/'.join(ON_DUPLICATE_STRATEGIES)}"
        )
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
    library_index = _library_hash_index()
    shot_ids = set(project.shot_ids())
    bible_owner = bible_owner_map(project)

    rows: list[IngestRow] = []
    seen_in_batch: dict[str, str] = {}  # hash -> first file name claiming it
    canceled = False

    for f in files:
        # goal: honest job cancellation — checked before hashing the NEXT
        # file, so a trip never kills a hash already in progress.
        if should_cancel is not None and should_cancel():
            canceled = True
            break
        h = hash_file(f)
        if h in existing_index:
            rows.append(IngestRow(
                file=str(f), name=f.name, hash=h, action="skip_duplicate",
                target=existing_index[h],
                reason=f"内容已存在于 {existing_index[h]},跳过(素材只增不改,§3)",
                match="matched",  # exact by content hash (round AA, #1)
            ))
            continue
        if h in seen_in_batch:
            rows.append(IngestRow(
                file=str(f), name=f.name, hash=h, action="skip_duplicate",
                target=seen_in_batch[h],
                reason=f"与本批次中的 {seen_in_batch[h]} 内容相同,跳过重复项",
                match="matched",
            ))
            continue
        seen_in_batch[h] = f.name

        lib_hit = library_index.get(h)
        if lib_hit is not None:
            hash8, tags_str = lib_hit["hash8"], lib_hit["tags_str"]
            hint = (f"内容已在素材库中(标签: {tags_str}) — hash8={hash8};"
                    f"可用 `manju lib use {hash8}` 复用,或用 --on-duplicate 调整此行为")
            if on_duplicate == "skip":
                rows.append(IngestRow(
                    file=str(f), name=f.name, hash=h, action="skip_duplicate",
                    target=f"素材库 library:{hash8}",
                    reason=f"内容已存在于素材库(标签: {tags_str}),跳过(--on-duplicate=skip)",
                    library_hint=hint, match="matched",
                ))
                continue
            row = _classify(f, h, role=role, shot=shot, shot_ids=shot_ids,
                            bible_owner=bible_owner)
            if on_duplicate == "link" and lib_hit.get("blob_exists"):
                row = replace(row, file=lib_hit["blob_path"],
                             library_hint=hint + " — 已从素材库复制(provenance: library)")
            else:
                row = replace(row, library_hint=hint)
            rows.append(row)
            continue

        rows.append(_classify(f, h, role=role, shot=shot, shot_ids=shot_ids,
                              bible_owner=bible_owner))

    errors = []
    if canceled:
        errors.append(
            f"已取消:{len(rows)}/{len(files)} 个文件已分类(只读预演,未写入任何内容)——"
            "可重新生成计划继续核对剩余文件"
        )
    return IngestPlan(rows=rows, canceled=canceled, errors=errors)


def _library_hash_index() -> dict[str, dict[str, Any]]:
    """content hash -> {hash8, tags_str, blob_path, blob_exists} for every
    asset in the user's private library (round X agent XF). Best-effort:
    a missing/unreadable library degrades to an empty index, never an
    error — ingest must keep working even when the library shelf is absent."""
    try:
        from ..core.library import Library, LibraryError, _hex
    except ImportError:
        return {}
    try:
        lib = Library()
        assets = lib.assets()
    except LibraryError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for a in assets:
        blob = lib.blob_path(a)
        out[a["hash"]] = {
            "hash8": _hex(a["hash"])[:8],
            "tags_str": ", ".join(a.get("tags") or []) or "无标签",
            "blob_path": str(blob),
            "blob_exists": blob.exists(),
        }
    return out


def _collect_files(paths: list[Path] | list[str]) -> list[Path]:
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            raise IngestError(f"路径不存在: {p} — 核对拼写和当前目录")
        if p.is_dir():
            # sorted(Path) is platform-dependent (Windows folds case in
            # PurePath ordering — gate run #4 moved plan row indices). Sort by
            # the POSIX string: byte-identical order on every platform.
            out.extend(sorted((x for x in p.rglob("*")
                               if x.is_file() and not x.name.startswith(".")),
                              key=lambda x: x.as_posix()))
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
    for f in sorted(candidates, key=lambda x: x.as_posix()):
        try:
            h = hash_file(f)
        except OSError:
            continue
        index.setdefault(h, project.relpath(f))
    return index


# -------------------------------------------------------------- classify


def _dedup(items: list[str]) -> list[str]:
    """Order-preserving de-dup — shared by every candidate list below."""
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _split_candidates(stem: str, *, suffix_markers: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """(exact, fuzzy) id candidates extracted from a filename stem (round AA,
    goal item 1 — used to be one flat, priority-ordered list; now split so a
    caller can tell an EXACT hit from an INFERRED one).

    ``exact`` covers the documented conventions with zero guessing: an
    explicit role suffix (``_take``/``_voice``) stripped, or the stem taken
    verbatim (``S001.mp4``). ``fuzzy`` is the one generic fallback — the
    leading token before the first ``_``, which lets an arbitrary trailing
    token still resolve (``S001_v2_final.mov``) but is an inference, not a
    literal convention hit, hence lower confidence."""
    exact: list[str] = []
    for marker in suffix_markers:
        if stem.endswith(marker) and len(stem) > len(marker):
            exact.append(stem[: -len(marker)])
    exact.append(stem)
    fuzzy: list[str] = []
    if "_" in stem:
        fuzzy.append(stem.split("_", 1)[0])
    exact = _dedup(exact)
    fuzzy = [c for c in _dedup(fuzzy) if c not in exact]
    return exact, fuzzy


def _ref_split_candidates(stem: str) -> tuple[list[str], list[str]]:
    """Same (exact, fuzzy) split as :func:`_split_candidates`, for the ref
    naming convention (``_ref``/``_refN`` suffix instead of ``_take``/``_voice``)."""
    exact: list[str] = []
    m = REF_SUFFIX_RE.match(stem)
    if m:
        exact.append(m.group("base"))
    exact.append(stem)
    fuzzy: list[str] = []
    if "_" in stem:
        fuzzy.append(stem.split("_", 1)[0])
    exact = _dedup(exact)
    fuzzy = [c for c in _dedup(fuzzy) if c not in exact]
    return exact, fuzzy


def _resolve_match(exact: list[str], fuzzy: list[str], valid: set[str]) -> tuple[str | None, str, list[str]]:
    """(winning id, match state, candidates) — the core round-AA decision.

    More than one DISTINCT id resolving across ``exact``+``fuzzy`` is a
    genuine conflict: today's candidate search would have silently picked
    the first one, which is exactly the "resolved deterministically but
    could have been wrong" gap this closes (module docstring). A single hit
    from ``exact`` is ``matched`` (candidates left empty — nothing to
    review); a single hit that only came from ``fuzzy`` is ``pending``
    (candidates=[winner], flagged for a human/agent to confirm); no hit at
    all is ``unmatched``."""
    exact_hits = _dedup([c for c in exact if is_safe_segment(c) and c in valid])
    fuzzy_hits = [c for c in _dedup(fuzzy) if is_safe_segment(c) and c in valid and c not in exact_hits]
    all_hits = _dedup(exact_hits + fuzzy_hits)
    if not all_hits:
        return None, "unmatched", []
    if len(all_hits) > 1:
        return None, "conflict", all_hits
    winner = all_hits[0]
    if winner in exact_hits:
        return winner, "matched", []
    return winner, "pending", [winner]


def _resolve_shot_match(stem: str, suffix_markers: tuple[str, ...], shot_ids: set[str]) -> tuple[str | None, str, list[str]]:
    exact, fuzzy = _split_candidates(stem, suffix_markers=suffix_markers)
    return _resolve_match(exact, fuzzy, shot_ids)


def _conflict_import_row(f: Path, h: str, name: str, candidates: list[str], *, kind: str) -> IngestRow:
    """A row whose filename genuinely matches MORE THAN ONE owner — never
    landed on a guess (module docstring): downgraded to a plain import with
    every candidate id recorded and a 中文 reason naming them."""
    owners = "、".join(candidates)
    return _import_row(f, h, reason=(
        f"{name}: 文件名可能同时匹配多个{kind} id({owners}),无法确定,按普通素材导入"
        "(可用 --shot 强制指定,或给文件改名消歧)"
    ), match="conflict", candidates=list(candidates))


def _classify(
    f: Path, h: str, *, role: str, shot: str | None, shot_ids: set[str],
    bible_owner: dict[str, str],
) -> IngestRow:
    ext = f.suffix.lower()
    stem = f.stem
    name = f.name

    if shot is not None:
        # --shot forces every file onto ONE shot ("regenerated three
        # candidates externally") — filename id extraction is skipped, so
        # every row that actually lands here is an exact hit by definition.
        if role in ("auto", "take") and ext in TAKE_VIDEO_EXTS:
            return _take_row(f, h, shot, match="matched")
        if role in ("auto", "voice") and ext in VOICE_AUDIO_EXTS:
            return _voice_row(f, h, shot, match="matched")
        if role in ("auto", "ref") and ext in REF_IMAGE_EXTS:
            return _shot_ref_row(f, h, shot, match="matched")
        return _import_row(f, h, reason=(
            f"{name}: --shot {shot} 与 --role {role}/扩展名 {ext or '(无)'} 不匹配,"
            "按普通素材导入"
        ))

    if role == "take":
        if ext not in TAKE_VIDEO_EXTS:
            return _import_row(f, h, reason=f"{name}: --role take 需要视频扩展名,按普通素材导入")
        winner, state, cands = _resolve_shot_match(stem, ("_take",), shot_ids)
        if state == "conflict":
            return _conflict_import_row(f, h, name, cands, kind="镜头")
        if winner:
            return _take_row(f, h, winner, match=state, candidates=cands)
        return _import_row(f, h, reason=f"{name}: 文件名未匹配到已存在的镜头 id,按普通素材导入")

    if role == "voice":
        if ext not in VOICE_AUDIO_EXTS:
            return _import_row(f, h, reason=f"{name}: --role voice 需要音频扩展名,按普通素材导入")
        winner, state, cands = _resolve_shot_match(stem, ("_voice",), shot_ids)
        if state == "conflict":
            return _conflict_import_row(f, h, name, cands, kind="镜头")
        if winner:
            return _voice_row(f, h, winner, match=state, candidates=cands)
        return _import_row(f, h, reason=f"{name}: 文件名未匹配到已存在的镜头 id,按普通素材导入")

    if role == "ref":
        if ext not in REF_IMAGE_EXTS:
            return _import_row(f, h, reason=f"{name}: --role ref 需要图片扩展名,按普通素材导入")
        return _classify_ref(f, h, stem, name, shot_ids, bible_owner)

    # role == "auto"
    if ext in TAKE_VIDEO_EXTS:
        winner, state, cands = _resolve_shot_match(stem, ("_take",), shot_ids)
        if state == "conflict":
            return _conflict_import_row(f, h, name, cands, kind="镜头")
        if winner:
            return _take_row(f, h, winner, match=state, candidates=cands)
        return _import_row(f, h, reason=(
            f"{name}: 看起来是视频素材,但文件名开头未匹配到已存在的镜头 id,按普通素材导入"
        ))
    if ext in VOICE_AUDIO_EXTS:
        winner, state, cands = _resolve_shot_match(stem, ("_voice",), shot_ids)
        if state == "conflict":
            return _conflict_import_row(f, h, name, cands, kind="镜头")
        if winner:
            return _voice_row(f, h, winner, match=state, candidates=cands)
        return _import_row(f, h, reason=(
            f"{name}: 看起来是音频素材,但文件名开头未匹配到已存在的镜头 id,按普通素材导入"
        ))
    if ext in REF_IMAGE_EXTS:
        return _classify_ref(f, h, stem, name, shot_ids, bible_owner)
    return _import_row(f, h, reason=f"{name}: 未识别的命名约定/扩展名,按普通素材导入")


def _classify_ref(
    f: Path, h: str, stem: str, name: str, shot_ids: set[str], bible_owner: dict[str, str],
) -> IngestRow:
    exact, fuzzy = _ref_split_candidates(stem)
    shot_win, shot_state, shot_cands = _resolve_match(exact, fuzzy, shot_ids)
    bible_win, bible_state, bible_cands = _resolve_match(exact, fuzzy, set(bible_owner))

    # Multiple distinct owners WITHIN one namespace (two shots, or two bible
    # assets) are exactly as ambiguous as the cross-namespace case below —
    # never landed on a guess (round AA, module docstring).
    if shot_state == "conflict" or bible_state == "conflict":
        owners = _dedup(
            (shot_cands if shot_state == "conflict" else []) +
            (bible_cands if bible_state == "conflict" else [])
        )
        if shot_state == "conflict" and bible_state != "conflict":
            kind = "镜头"
        elif bible_state == "conflict" and shot_state != "conflict":
            kind = "bible 资产"
        else:
            kind = "镜头/bible 资产"
        return _conflict_import_row(f, h, name, owners, kind=kind)

    if shot_win and bible_win and shot_win != bible_win:
        return IngestRow(
            file=str(f), name=f.name, hash=h, action="import", target="media/imports",
            match="conflict", candidates=[shot_win, bible_win],
            reason=(
                f"{name}: id 有歧义 — 既可能是镜头 {shot_win} 也可能是 bible 资产 {bible_win},"
                "无法确定,按普通素材导入(可用 --shot 强制指定,或给文件改名消歧)"
            ),
        )
    if shot_win:
        note = "(该 id 同时也是 bible 资产,已优先按镜头参考图处理)" if bible_win else ""
        return _shot_ref_row(f, h, shot_win, extra=note, match=shot_state, candidates=shot_cands)
    if bible_win:
        return _bible_ref_row(f, h, bible_win, bible_owner[bible_win],
                              match=bible_state, candidates=bible_cands)
    return _import_row(f, h, reason=(
        f"{name}: 未找到匹配的镜头/角色/场景/道具 id,按普通素材导入"
    ))


def _take_row(f: Path, h: str, shot_id: str, *, match: str = "matched", candidates: list[str] | None = None) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="take", shot_id=shot_id,
        target=f"{shot_id}: 新 take",
        reason=f"文件名匹配镜头 {shot_id},登记为新 take(追加,不覆盖已有 take,§3)",
        match=match, candidates=list(candidates or []),
    )


def _voice_row(f: Path, h: str, shot_id: str, *, match: str = "matched", candidates: list[str] | None = None) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="voice", shot_id=shot_id,
        target=f"{shot_id}: 新配音 take",
        reason=f"文件名匹配镜头 {shot_id},登记为人工配音(追加,永不自动失效,§4.3)",
        match=match, candidates=list(candidates or []),
    )


def _shot_ref_row(
    f: Path, h: str, shot_id: str, *, extra: str = "",
    match: str = "matched", candidates: list[str] | None = None,
) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="shot_ref", shot_id=shot_id,
        target=f"media/refs → 镜头 {shot_id} 参考图",
        reason=(f"文件名匹配镜头 {shot_id} 的参考图约定(_ref);复制到 media/refs,"
                f"如需绑定到该镜头,请在 generation.params.refs 中加入生成的路径{extra}"),
        match=match, candidates=list(candidates or []),
    )


def _bible_ref_row(
    f: Path, h: str, asset_id: str, asset_kind: str, *,
    match: str = "matched", candidates: list[str] | None = None,
) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="bible_ref",
        asset_id=asset_id, asset_kind=asset_kind,
        target=f"media/refs → bible/{asset_kind}.yaml:{asset_id}",
        reason=f"文件名匹配 bible 资产 {asset_id}({asset_kind}),复制到 media/refs 并登记为其 ref_image",
        match=match, candidates=list(candidates or []),
    )


def _import_row(f: Path, h: str, *, reason: str, match: str = "unmatched", candidates: list[str] | None = None) -> IngestRow:
    return IngestRow(
        file=str(f), name=f.name, hash=h, action="import",
        target="media/imports", reason=reason,
        match=match, candidates=list(candidates or []),
    )


# ------------------------------------------------------------------ apply


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


def _maybe_auto_select(project: Project, shot_id: str, take: str, *, actor: str) -> dict[str, Any]:
    """After a ``take`` row lands on a shot with NO ``selected_take`` yet,
    auto-select it (round AA, goal item 2) — an empty shot that just got its
    first externally-produced take otherwise still needs a separate manual
    `manju select` before `manju build` will use it; this closes that one
    extra step. NEVER overwrites an existing selection (checked first,
    read-only) and NEVER lands past a lock — ``via="ingest"`` lets
    events.jsonl tell this apart from every other ``selected_take`` writer
    (core/writes.py, #39). Reversible: the batch-review ``discard`` decision
    (build.batches.review_item) undoes exactly this write, IF nobody picked
    a different take since (see its docstring).

    Never raises: by the time this runs the take file itself is already
    safely on disk (``register_manual_take`` succeeded), so a rejection here
    — locked field, a `manju check` regression, or any other surprise — only
    ever downgrades to ``staged=False`` with the reason recorded. It must
    never turn a successful ingest row into a failed one."""
    try:
        shot = project.load_shot(shot_id)
        if shot.status and shot.status.selected_take:
            return {"staged": False, "staged_note": "已有选定 take,未自动切换"}
        blocking = selected_take_lock_block(project, shot_id)
        if blocking is not None:
            return {"staged": False, "staged_note": (
                f"{shot_id}.status.selected_take 已锁定,未自动选用"
                "(人工 `manju select` 或先 `manju unlock` 解锁)"
            )}
        try:
            select_take_checked(project, shot_id, take, actor=actor, via="ingest", action="auto_select")
        except WriteRejected as exc:
            return {"staged": False, "staged_note": f"自动选用被拒绝,未选用: {exc}"}
        return {"staged": True, "staged_note": "空镜头,已自动选用该 take,可在批次评审中撤销"}
    except Exception as exc:
        # Defensive catch-all (see docstring): the take already landed, this
        # step is advisory only.
        return {"staged": False, "staged_note": f"自动选用检查出错,未选用: {exc}"}


def _execute_row(project: Project, row: IngestRow, *, actor: str) -> dict[str, Any]:
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
        detail: dict[str, Any] = {"shot": row.shot_id, "take": take.name, "media": media_rel}
        detail.update(_maybe_auto_select(project, row.shot_id, take.name, actor=actor))
        return detail

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
        dest = copy_collision_safe(project.refs_dir, f"{row.shot_id}_ref", src.suffix.lower(), src)
        return {"shot": row.shot_id, "ref": project.relpath(dest)}

    if row.action == "bible_ref":
        if not row.asset_id:
            raise IngestError("bible_ref 需要 asset_id")
        asset_kind = row.asset_kind
        if not asset_kind:
            owner = bible_owner_map(project)
            asset_kind = owner.get(row.asset_id)
        if not asset_kind:
            raise IngestError(f"bible 中未找到资产: {row.asset_id}")
        dest = copy_collision_safe(project.refs_dir, f"{row.asset_id}_ref", src.suffix.lower(), src)
        ref_rel = project.relpath(dest)
        set_bible_ref_image(project, asset_kind, row.asset_id, ref_rel)
        return {"asset_id": row.asset_id, "asset_kind": asset_kind, "ref": ref_rel}

    if row.action == "import":
        dest = copy_collision_safe(project.imports_dir, src.stem, src.suffix, src)
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
    # round AA (goal item 1): a human/agent corrected the classification at
    # apply time — the row's own inferred match/candidates no longer apply.
    return replace(row, action=action, shot_id=shot_id, asset_id=asset_id, asset_kind=asset_kind,
                   match="manual", candidates=[])


def apply_ingest(
    project: Project,
    plan: IngestPlan,
    *,
    actor: str,
    overrides: dict[int, dict[str, Any]] | None = None,
    batch_id: str | None = None,
    clock: Callable[[], datetime] | None = None,
    source: str = "",
    should_cancel: "Callable[[], bool] | None" = None,
) -> IngestApplyResult:
    """Execute a (possibly hand-edited) plan through the existing
    registration paths. Runs rows IN ORDER; a row that raises stops the
    whole run right there (``stopped_at``) — every row before it has
    already landed and is reported truthfully, nothing after it was
    attempted. ``overrides`` (row index -> {"action"/"shot_id"/"asset_id"})
    lets a reviewer (CLI re-run with different flags, or the GUI's per-row
    dropdown) correct a classification before it executes.

    Round AA (goal items 1+2): every run is ALSO persisted as a reviewable
    batch record (``reports/ingest_batches/<batch_id>.yaml`` —
    :mod:`build.batches`) once the loop below finishes, one item per row
    carrying its match-state/candidates plus exactly what landed, so a
    human/agent can come back later and confirm/flag/discard individual rows
    without re-deriving anything from events.jsonl. ``batch_id`` defaults to
    a ``clock``-derived ``bYYYYMMDD-HHMMSS`` id when the caller does not
    supply one (a caller-supplied id is validated via ``core.idents`` before
    it is ever used as a path segment, exactly like every other id this
    engine turns into a path); ``clock`` exists purely so callers/tests can
    pin the timestamp instead of reaching for ``datetime.now()`` here
    directly. ``source`` is a free-text, purely informational note (usually
    the paths handed to :func:`plan_ingest`) recorded on the batch record.

    ``should_cancel`` (goal: honest job cancellation) is checked before EVERY
    row — a trip never kills a row already being registered, only stops the
    NEXT one; every row already landed (``results``) stays (import is
    append-only/copy-only, §3), and the batch record below is still written
    for whatever landed. ``None`` (every CLI call) is byte-identical to
    before."""
    from .batches import new_batch_id, write_batch_record

    overrides = overrides or {}
    if batch_id is not None and not is_safe_segment(batch_id):
        raise IngestError(
            f"batch_id 不合法: {batch_id!r} — 只能包含字母、数字、下划线、连字符,长度 1-64"
        )
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    resolved_batch_id = batch_id or new_batch_id(now)

    results: list[IngestRowResult] = []
    stopped_at: int | None = None
    canceled = False
    landed = 0

    for i, row in enumerate(plan.rows):
        if should_cancel is not None and should_cancel():
            stopped_at = i
            canceled = True
            break
        eff = _apply_override(row, overrides.get(i))
        if eff.action == "skip_duplicate":
            results.append(IngestRowResult(row=eff, ok=True, detail={"skipped": True}))
            append_event(project.root, actor, "ingest_row", {
                "index": i, "file": eff.name, "action": eff.action,
                "target": eff.target, "ok": True, "skipped": True,
            })
            continue
        try:
            detail = _execute_row(project, eff, actor=actor)
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

    errors = []
    if canceled:
        remaining = len(plan.rows) - len(results)
        errors.append(
            f"已取消:{landed}/{len(plan.rows)} 项已落地(已完成的不受影响),"
            f"剩余 {remaining} 项未处理"
        )
    append_event(project.root, actor, "ingest", {
        "rows": len(plan.rows), "attempted": len(results), "landed": landed,
        "stopped_at": stopped_at, "canceled": canceled,
    })

    write_batch_record(
        project, batch_id=resolved_batch_id, created=now, actor=actor,
        source=source, results=results,
    )

    return IngestApplyResult(results=results, stopped_at=stopped_at,
                             batch_id=resolved_batch_id,
                             canceled=canceled, errors=errors)
