"""Missing-media report + hash-verified relink (FP roadmap §6.3).

The DETECTION seam has existed since round one: ``core/container.py`` models a
take whose sidecar survives but whose media file is gone as
``TakeInfo.media_path = None``. What was missing is (a) one REPORT that walks
every place the project's truth points at media bytes and says which of those
bytes are absent, and (b) a SAFE way to bring the bytes back.

Three functions, three disciplines:

``missing_media_report``
    ZERO-WRITE scan. Three row kinds — exactly the media the truth model
    actually tracks, no invented categories:

    - ``take``: a take sidecar (``media/gen/<shot>/take_NN.yaml``) whose media
      file is absent (the container seam above);
    - ``timeline_source``: a ``timeline.json`` clip source (video/overlay/
      voice/music/sfx/ambient) that does not resolve to a file —
      ``__slate__/**`` virtual placeholders are BY DESIGN not files and are
      never reported;
    - ``ref``: a bible ``ref_image``/``ref_images``/``ref_video``/``ref_videos``
      pin naming a file that no longer exists (the same pins
      ``core/refs.py`` tracks — this report reuses ``refs_report``).

    ``expected_hash`` comes ONLY from recorded lineage: the attempt-evidence
    stream (``build/attempts.py``) records every generated take's media output
    as ``{path, sha256, bytes}`` — the one durable content-hash record for
    media bytes in the project. Where no hash was ever recorded the row says
    ``expected_hash: null`` and that hash-verified relink is UNAVAILABLE —
    never fabricated.

``relink_plan``
    ZERO-WRITE candidate search over caller-given roots, bounded (a max-files
    scan cap and a per-file size cap for hashing — a huge root degrades to a
    structured partial-scan note, never a hang). Hash-first: an item with a
    recorded hash is matched by CONTENT ONLY (a byte-identical file is found
    under any name); name matching is never used when a hash exists. Hashless
    items get name-matched candidates marked ``name_only_advisory``. Ordering
    is deterministic (rows and candidate lists sorted).

``apply_relink``
    CAS apply, per-row atomic. Every candidate is RE-HASHED AT APPLY TIME; a
    tampered or vanished candidate refuses that row (structured, per-row —
    the result says exactly what happened, other rows proceed). Relink means
    the honest minimal action: copy the candidate's bytes back to the
    ORIGINAL recorded project-relative path (media is append-only; a take's
    identity IS its path+bytes), then hash the WRITTEN copy again. Truth
    files (sidecars/shots/timeline/bible) are NEVER rewritten — the media
    returns to where the truth already points. Unverified (name-only) rows
    require ``allow_unverified=True`` AND are re-hashed + recorded as
    unverified restores. Restore targets are confined to ``media/gen/**`` and
    ``media/refs/**``; ``media/imports/`` is ingest-only and anything
    escaping the project refuses. An existing target file is never
    overwritten (append-only discipline: refuse, don't clobber).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from ..core.container import MEDIA_EXTS, Project
from ..core.hashing import hash_file

# The plan document's schema id (§4 contract governance; registered in
# CONTRACTS.yaml as experimental — an inspect/apply plan file, internal).
RELINK_PLAN_SCHEMA = "manju.relink-plan/v1"

# Scan bounds (relink_plan). The cap is on files CONSIDERED (statted), so a
# pathological root (node_modules, a whole home dir) degrades to a structured
# partial-scan note instead of an unbounded walk; the per-file byte cap bounds
# hashing cost — an oversize file is size-skipped and counted, never hashed.
DEFAULT_MAX_FILES = 20_000
DEFAULT_MAX_BYTES_PER_FILE = 2 * 1024 ** 3  # 2 GiB

# Restore targets are confined to these prefixes (project-relative, POSIX).
# media/imports is deliberately NOT here: imports are ingest-only (§3) — a
# candidate may be READ from anywhere, including imports/, but bytes are only
# ever restored to the generated/ref locations truth records.
_RESTORE_PREFIXES = ("media/gen/", "media/refs/")

_NO_HASH_NOTE = (
    "no recorded content hash — hash-verified relink unavailable; "
    "candidates can only ever be name-only ADVISORY"
)

# Timeline tracks whose clips carry a ``source`` media path (core/models.py:
# VideoClip / OverlayClip / AudioClip). Captions carry text, not media.
_SOURCE_TRACKS = ("video", "overlay", "voice", "music", "sfx", "ambient")


class RelinkError(ValueError):
    """A relink workflow error (malformed plan, wrong schema). Per-ROW
    problems never raise — they land as structured refusals in the result."""


# --------------------------------------------------------------------- lineage


def _recorded_output_indices(project: Project) -> tuple[dict, dict]:
    """The recorded media-bytes lineage, projected from the attempt-evidence
    stream (``build/attempts.read_attempts`` — ``outputs`` rows of
    ``{path, sha256, bytes, take}``, path already project-relative).

    Returns ``(by_path, by_take)``:

    - ``by_path``:  relpath -> {"sha256", "bytes"}
    - ``by_take``:  (shot_id, take_name) -> {"path", "sha256", "bytes"}

    First record wins (the attempt that produced the bytes; media paths are
    append-only, so one path is only ever written once anyway). Read-only —
    a malformed/absent stream degrades to empty indices, never a crash."""
    try:
        from ..build.attempts import read_attempts

        records, _malformed = read_attempts(project)
    except Exception:
        return {}, {}
    by_path: dict[str, dict[str, Any]] = {}
    by_take: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in records:
        unit = rec.get("unit") or {}
        shot = unit.get("shot")
        for out in rec.get("outputs") or []:
            if not isinstance(out, dict):
                continue
            path, sha = out.get("path"), out.get("sha256")
            if not path or not sha:
                continue
            size = out.get("bytes") if isinstance(out.get("bytes"), int) else None
            by_path.setdefault(str(path), {"sha256": sha, "bytes": size})
            take = out.get("take")
            if shot and take:
                by_take.setdefault((str(shot), str(take)),
                                   {"path": str(path), "sha256": sha, "bytes": size})
    return by_path, by_take


def _normalize_rel(project: Project, value: str) -> str | None:
    """A recorded path value -> project-relative POSIX relpath, or ``None``
    when it is a URL, absolute-outside, or escapes the root (read-only
    classification — mirrors core/refs.py's normalizer)."""
    if not value or value.startswith(("http://", "https://")):
        return None
    try:
        resolved = (project.root / value).resolve()
        return resolved.relative_to(project.root.resolve()).as_posix()
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------- report


def missing_media_report(project: Project) -> dict[str, Any]:
    """ZERO-WRITE: every take sidecar whose media is absent, every timeline
    clip source that does not resolve, every bible ref pin naming an absent
    file. Rows carry ``expected_hash``/``last_known_relpath`` ONLY when the
    lineage actually recorded them (attempt-evidence outputs) — otherwise
    ``null`` plus an honest note that hash-verified relink is unavailable."""
    by_path, by_take = _recorded_output_indices(project)
    rows: list[dict[str, Any]] = []
    skipped_virtual = 0

    # ---- takes: the container.py media_path=None seam, verbatim
    for shot_id in project.shot_ids():
        try:
            takes = project.takes(shot_id)
        except Exception:
            continue  # a broken shot id is `manju check`'s finding, not ours
        for take in takes:
            if take.media_path is not None:
                continue
            lineage = by_take.get((shot_id, take.name))
            rows.append(_finish_row({
                "kind": "take",
                "id": f"take:{shot_id}/{take.name}",
                "shot": shot_id,
                "take": take.name,
                "sidecar": project.relpath(take.sidecar_path),
                "last_known_relpath": lineage["path"] if lineage else None,
                "expected_hash": lineage["sha256"] if lineage else None,
                "expected_bytes": lineage["bytes"] if lineage else None,
            }))

    # ---- timeline sources (timeline.json is truth; generated preview is not)
    try:
        timeline = project.load_timeline()
    except Exception:
        timeline = None  # an unparseable timeline is `manju check`'s finding
    if timeline is not None:
        missing_sources: dict[str, dict[str, Any]] = {}
        for track in _SOURCE_TRACKS:
            for i, clip in enumerate(getattr(timeline.tracks, track, []) or []):
                source = getattr(clip, "source", "") or ""
                if not source:
                    continue  # text overlays etc. carry no media source
                if source.startswith("__slate__") or \
                        getattr(clip, "take", None) == "__slate__":
                    skipped_virtual += 1
                    continue  # a virtual placeholder is not missing media
                rel = _normalize_rel(project, source)
                if rel is not None and (project.root / rel).exists():
                    continue
                entry = missing_sources.setdefault(source, {
                    "kind": "timeline_source",
                    "id": f"timeline_source:{source}",
                    "source": source,
                    "last_known_relpath": rel,
                    "referenced_by": [],
                    "expected_hash": (by_path.get(rel) or {}).get("sha256") if rel else None,
                    "expected_bytes": (by_path.get(rel) or {}).get("bytes") if rel else None,
                    "derived": bool(rel and rel.startswith("media/generated/")),
                })
                entry["referenced_by"].append(f"{track}[{i}]")
        for entry in missing_sources.values():
            if entry["last_known_relpath"] is None:
                entry["note"] = ("source 不是项目内相对路径(URL/绝对/越界)— "
                                 "无法定位;" + _NO_HASH_NOTE)
            elif entry["derived"]:
                entry["note"] = ("derived/rebuildable(media/generated/** 由 "
                                 "`manju build` 重建)— relink 非必需;" + (
                                     "" if entry["expected_hash"] else _NO_HASH_NOTE))
            rows.append(_finish_row(entry))

    # ---- bible ref pins (the reference media the model tracks — core/refs.py)
    try:
        from ..core.refs import refs_report

        ref_missing = refs_report(project)["missing"]
    except Exception:
        ref_missing = []
    for m in ref_missing:
        rel = _normalize_rel(project, m["value"])
        rows.append(_finish_row({
            "kind": "ref",
            "id": f"ref:{m['bible_file']}:{m['asset_id']}:{m['field']}:{m['value']}",
            "bible_file": m["bible_file"],
            "asset_id": m["asset_id"],
            "field": m["field"],
            "value": m["value"],
            "last_known_relpath": rel,
            "expected_hash": (by_path.get(rel) or {}).get("sha256") if rel else None,
            "expected_bytes": (by_path.get(rel) or {}).get("bytes") if rel else None,
        }))

    rows.sort(key=lambda r: (r["kind"], r["id"]))
    by_kind = {"take": 0, "timeline_source": 0, "ref": 0}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    hash_available = sum(1 for r in rows if r["hash_relink_available"])
    return {
        "rows": rows,
        "summary": {
            "total": len(rows),
            "by_kind": by_kind,
            "hash_relink_available": hash_available,
            "advisory_only": len(rows) - hash_available,
        },
        "skipped_virtual_sources": skipped_virtual,
        "note": ("零写入报告:truth 文件从不改写;relink 是把字节恢复到 truth 已"
                 "记录的路径。无记录哈希的行只能得到 name-only ADVISORY 候选。"),
    }


def _finish_row(row: dict[str, Any]) -> dict[str, Any]:
    """Stamp the honesty fields every row carries: hash availability and, when
    unavailable, the explicit advisory note (no fabricated hashes, ever)."""
    row["hash_relink_available"] = row.get("expected_hash") is not None
    if not row["hash_relink_available"]:
        row["note"] = (row.get("note") + " — " if row.get("note") else "") + _NO_HASH_NOTE
    else:
        row.setdefault("note", "")
    return row


# ----------------------------------------------------------------------- plan


def relink_plan(project: Project, search_roots: list[Path], *,
                max_files: int = DEFAULT_MAX_FILES,
                max_bytes_per_file: int = DEFAULT_MAX_BYTES_PER_FILE
                ) -> dict[str, Any]:
    """ZERO-WRITE candidate search. Hash-first for every item with a recorded
    hash (content match under ANY filename; name matching is never used when a
    hash exists); name-only ADVISORY candidates for hashless items. Bounded:
    at most ``max_files`` files are considered across all roots and no file
    larger than ``max_bytes_per_file`` is hashed (size-skipped + counted).
    Deterministic: rows, candidates and scan order are all sorted."""
    report = missing_media_report(project)
    missing_rows = report["rows"]
    notes: list[str] = []

    # what the scan is looking for
    wanted_hashes: dict[str, list[str]] = {}      # sha -> [row ids]
    wanted_sizes: set[int] = set()                # recorded sizes (prefilter)
    any_sizeless_hash = False
    name_wanted: dict[str, list[str]] = {}        # basename -> [row ids]
    stem_wanted: dict[str, list[str]] = {}        # take stem -> [row ids]
    for row in missing_rows:
        if row["expected_hash"] is not None:
            wanted_hashes.setdefault(row["expected_hash"], []).append(row["id"])
            if row.get("expected_bytes") is not None:
                wanted_sizes.add(int(row["expected_bytes"]))
            else:
                any_sizeless_hash = True
        elif row.get("last_known_relpath"):
            name_wanted.setdefault(Path(row["last_known_relpath"]).name,
                                   []).append(row["id"])
        elif row["kind"] == "take":
            # no recorded relpath: the sidecar's stem convention is all we have
            stem_wanted.setdefault(row["take"], []).append(row["id"])

    # bounded, deterministic walk
    files_scanned = 0
    complete = True
    skipped_oversize = 0
    hash_hits: dict[str, list[Path]] = {}         # row id -> candidate paths
    name_hits: dict[str, list[Path]] = {}
    sizes: dict[str, int] = {}                    # str(path) -> size

    def _consider(path: Path) -> None:
        nonlocal skipped_oversize
        try:
            size = path.stat().st_size
        except OSError:
            return
        sizes[str(path)] = size
        # hash-first matching (only when some item actually wants a hash)
        if wanted_hashes and (any_sizeless_hash or size in wanted_sizes):
            if size > max_bytes_per_file:
                skipped_oversize += 1
            else:
                try:
                    digest = hash_file(path)
                except OSError:
                    digest = None
                if digest is not None and digest in wanted_hashes:
                    for row_id in wanted_hashes[digest]:
                        hash_hits.setdefault(row_id, []).append(path)
        # name matching is ADVISORY and only for items with NO recorded hash
        for row_id in name_wanted.get(path.name, []):
            name_hits.setdefault(row_id, []).append(path)
        if path.suffix.lower() in MEDIA_EXTS:
            for row_id in stem_wanted.get(path.stem, []):
                name_hits.setdefault(row_id, []).append(path)

    for root in search_roots:
        # resolve so recorded candidate paths are absolute — a plan written
        # from one CWD must apply identically from any other
        root = Path(root).resolve()
        if not root.is_dir():
            notes.append(f"search root 不是目录,已跳过: {root}")
            continue
        if not complete:
            break
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            if not complete:
                break
            for name in sorted(filenames):
                if files_scanned >= max_files:
                    complete = False
                    notes.append(
                        f"partial scan: 达到 max_files={max_files} 上限,扫描"
                        "提前结束 — 剩余文件未检查(缩小 search root 或提高上限)")
                    break
                path = Path(dirpath) / name
                if path.is_symlink():
                    continue  # never follow symlinks (pack discipline)
                files_scanned += 1
                _consider(path)

    # one plan row per missing item — a complete, deterministic account
    plan_rows: list[dict[str, Any]] = []
    for row in missing_rows:
        target_ok, target_reason = _restore_target_status(
            project, row.get("last_known_relpath"))
        if row["expected_hash"] is not None:
            candidates = sorted(hash_hits.get(row["id"], []), key=str)
            verified, method, verification = True, "content_hash", "content_hash"
            cand_hash = row["expected_hash"]
        else:
            candidates = sorted(name_hits.get(row["id"], []), key=str)
            verified, method, verification = False, "name_only", "name_only_advisory"
            cand_hash = None  # never hashed at plan time → never claimed
        if not candidates:
            plan_rows.append({
                "missing": row, "candidate": None, "other_candidates": [],
                "verified": False, "method": None, "verification": None,
                "action": "skip", "reason": "no_candidate",
            })
            continue
        first = candidates[0]
        plan_rows.append({
            "missing": row,
            "candidate": {"path": str(first), "size": sizes.get(str(first)),
                          "hash": cand_hash},
            "other_candidates": [str(c) for c in candidates[1:]],
            "verified": verified,
            "method": method,
            "verification": verification,
            # a candidate with no legal restore target can only be reported,
            # never applied — apply re-guards this regardless (defense in depth)
            "action": "relink" if target_ok else "skip",
            "reason": None if target_ok else target_reason,
        })
    plan_rows.sort(key=lambda r: (r["missing"]["kind"], r["missing"]["id"]))

    actionable = [r for r in plan_rows if r["action"] == "relink"]
    return {
        "schema": RELINK_PLAN_SCHEMA,
        "search_roots": [str(r) for r in search_roots],
        "rows": plan_rows,
        "scan": {
            "files_scanned": files_scanned,
            "complete": complete,
            "cap_files": max_files,
            "cap_bytes_per_file": max_bytes_per_file,
            "skipped_oversize": skipped_oversize,
            "notes": notes,
        },
        "summary": {
            "missing": len(missing_rows),
            "relink": len(actionable),
            "verified": sum(1 for r in actionable if r["verified"]),
            "advisory": sum(1 for r in actionable if not r["verified"]),
            "skip": len(plan_rows) - len(actionable),
        },
    }


def _restore_target_status(project: Project,
                           relpath: str | None) -> tuple[bool, str | None]:
    """May bytes be restored to ``relpath``? Only a recorded, in-project path
    under media/gen/** or media/refs/**. imports/ is ingest-only; truth dirs,
    renders and anything escaping the root refuse."""
    if not relpath:
        return False, "no_recorded_target"
    try:
        resolved = (project.root / relpath).resolve()
        norm = resolved.relative_to(project.root.resolve()).as_posix()
    except (OSError, ValueError):
        return False, "target_escapes_project"
    if norm.startswith("media/imports/"):
        return False, "target_in_imports"
    if not norm.startswith(_RESTORE_PREFIXES):
        return False, "target_not_restorable"
    return True, None


# ---------------------------------------------------------------------- apply


def apply_relink(project: Project, plan: dict[str, Any], *,
                 allow_unverified: bool = False,
                 actor: str = "engine") -> dict[str, Any]:
    """CAS apply, per-row atomic. Re-hashes every candidate AT APPLY TIME;
    restores bytes to the recorded project-relative path via temp-write +
    verify-written-hash + rename. Never rewrites truth files; never
    overwrites an existing target; never restores into media/imports or
    outside media/gen/** / media/refs/**. A per-row problem is a structured
    refusal in the result — other rows proceed independently."""
    if not isinstance(plan, dict) or plan.get("schema") != RELINK_PLAN_SCHEMA:
        raise RelinkError(
            f"不是 {RELINK_PLAN_SCHEMA} 计划文件 — 先用 relink_plan/`manju relink "
            f"plan` 生成计划,再 apply(收到 schema={plan.get('schema') if isinstance(plan, dict) else type(plan).__name__!r})")
    rows_in = plan.get("rows")
    if not isinstance(rows_in, list):
        raise RelinkError("计划缺少 rows 列表 — 计划文件损坏或不是 relink 计划")

    results: list[dict[str, Any]] = []
    for row in rows_in:
        results.append(_apply_row(project, row, allow_unverified=allow_unverified))

    summary = {
        "restored": sum(1 for r in results if r["status"] == "restored"),
        "restored_unverified": sum(
            1 for r in results if r["status"] == "restored_unverified"),
        "refused": sum(1 for r in results if r["status"] == "refused"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
    }
    result = {"rows": results, "summary": summary,
              "ok": summary["refused"] == 0}

    # evidence (append-only): one relink_apply event; best-effort — the
    # restores above already happened and must be reported truthfully even
    # if the events append fails.
    try:
        from ..core.events import append_event

        append_event(project.root, actor, "relink_apply", {
            "summary": summary,
            "rows": [{"id": r["id"], "target": r["target"],
                      "status": r["status"], "reason": r["reason"]}
                     for r in results],
        })
        result["event_appended"] = True
    except Exception:
        result["event_appended"] = False
    return result


def _apply_row(project: Project, row: Any, *,
               allow_unverified: bool) -> dict[str, Any]:
    """One row, guards first, all writes last. Refusal reasons are stable
    machine tokens (an agent can branch on them)."""
    def _res(status: str, reason: str | None = None, *, target: str | None = None,
             candidate: str | None = None, expected: str | None = None,
             written: str | None = None, verified: bool = False,
             row_id: str | None = None) -> dict[str, Any]:
        return {"id": row_id, "target": target, "candidate": candidate,
                "status": status, "reason": reason, "expected_hash": expected,
                "written_hash": written, "verified": verified}

    if not isinstance(row, dict) or not isinstance(row.get("missing"), dict):
        return _res("refused", "malformed_row")
    missing = row["missing"]
    row_id = missing.get("id")
    target_rel = missing.get("last_known_relpath")
    expected = missing.get("expected_hash")

    if row.get("action") != "relink":
        return _res("skipped", row.get("reason") or "not_planned",
                    target=target_rel, row_id=row_id, expected=expected)

    candidate = row.get("candidate")
    if not isinstance(candidate, dict) or not candidate.get("path"):
        return _res("refused", "malformed_row", target=target_rel, row_id=row_id,
                    expected=expected)
    cand_path = Path(candidate["path"])

    # ---- target guards (apply NEVER trusts the plan — re-checked here)
    target_ok, target_reason = _restore_target_status(project, target_rel)
    if not target_ok:
        return _res("refused", target_reason, target=target_rel, row_id=row_id,
                    candidate=str(cand_path), expected=expected)
    target_abs = (project.root / target_rel).resolve()
    norm_rel = target_abs.relative_to(project.root.resolve()).as_posix()
    if target_abs.exists():
        # append-only media: never overwrite bytes that exist again
        return _res("refused", "target_already_present", target=norm_rel,
                    row_id=row_id, candidate=str(cand_path), expected=expected)

    # ---- candidate CAS: re-hash NOW, against the recorded hash when verified
    if not cand_path.is_file():
        return _res("refused", "candidate_vanished", target=norm_rel,
                    row_id=row_id, candidate=str(cand_path), expected=expected)
    is_verified_row = bool(row.get("verified")) and row.get("method") == "content_hash"
    if is_verified_row and not expected:
        return _res("refused", "malformed_row", target=norm_rel, row_id=row_id,
                    candidate=str(cand_path))  # "verified" without a hash is a lie
    if not is_verified_row and not allow_unverified:
        return _res("refused", "unverified_requires_opt_in", target=norm_rel,
                    row_id=row_id, candidate=str(cand_path), expected=expected)
    try:
        apply_hash = hash_file(cand_path)
    except OSError as exc:
        return _res("refused", f"candidate_unreadable: {exc}", target=norm_rel,
                    row_id=row_id, candidate=str(cand_path), expected=expected)
    if is_verified_row and apply_hash != expected:
        return _res("refused", "candidate_hash_mismatch", target=norm_rel,
                    row_id=row_id, candidate=str(cand_path), expected=expected,
                    written=None)

    # ---- restore: temp write → hash the WRITTEN bytes → rename into place
    tmp = target_abs.with_name(target_abs.name + ".relink_tmp")
    try:
        target_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cand_path, tmp)
        written_hash = hash_file(tmp)
        if written_hash != apply_hash:
            tmp.unlink(missing_ok=True)
            return _res("refused", "written_bytes_mismatch", target=norm_rel,
                        row_id=row_id, candidate=str(cand_path),
                        expected=expected, written=written_hash)
        if target_abs.exists():  # raced in while we copied — still never clobber
            tmp.unlink(missing_ok=True)
            return _res("refused", "target_already_present", target=norm_rel,
                        row_id=row_id, candidate=str(cand_path), expected=expected)
        os.replace(tmp, target_abs)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return _res("refused", f"io_error: {exc}", target=norm_rel, row_id=row_id,
                    candidate=str(cand_path), expected=expected)

    status = "restored" if is_verified_row else "restored_unverified"
    return _res(status, None, target=norm_rel, row_id=row_id,
                candidate=str(cand_path), expected=expected,
                written=written_hash, verified=is_verified_row)
