"""Persisted ingest batch records — the post-import confirmation layer
(round AA, goal items 1+2) that sits on top of ``build.ingest``.

``build.ingest.apply_ingest`` runs a batch once and reports it in-process
(events.jsonl + its return value); nothing used to be left behind for a
human/agent to come back to LATER and say "yes, that classification was
right" / "no, look at this one" / "undo that". This module is that record:

- :func:`write_batch_record` — called once by ``apply_ingest`` right after
  its execution loop — writes ``reports/ingest_batches/<batch_id>.yaml``.
  ``reports/`` is this project's truth home for review records generally
  (mirrors ``reports/qc_agent.jsonl``, ``qc.agent_review``'s verdict log);
  ``ingest_batches/`` is this feature's own subdirectory under it, one file
  per batch, one item per planned row (index/name/hash/action/target/
  shot_id/asset_id/asset_kind/match/candidates/landed/ok/error/staged/
  review/note — see its docstring for the exact shape).
- :func:`list_batches` / :func:`load_batch` — read the shelf back (CLI
  ``manju ingest-batches`` / ``manju ingest-review``, and any future GUI).
- :func:`review_item` — a human/agent's confirm/flag/discard decision on ONE
  item, applied in place; ``discard`` on an item that auto-staged a take
  (``build.ingest``'s empty-shot auto-select, goal item 2) additionally
  undoes that selection, but only when nobody re-selected since (see its
  own docstring for the exact contract, including the cross-process LOCKING
  contract every caller must uphold).

Every write here goes through ``core.yamlio`` (temp file + ``os.replace``,
§14) — a batch record is exactly as crash-safe as any other truth file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.container import Project
from ..core.events import append_event
from ..core.idents import is_safe_segment
from ..core.writes import WriteRejected, checked_shot_write
from ..core.yamlio import read_yaml, write_yaml

__all__ = [
    "BatchError",
    "REVIEW_DECISIONS",
    "new_batch_id",
    "batches_dir",
    "batch_path",
    "write_batch_record",
    "list_batches",
    "load_batch",
    "review_item",
]

BATCH_DIR_NAME = "ingest_batches"

REVIEW_DECISIONS = ("confirm", "flag", "discard")
_DECISION_TO_STATE = {"confirm": "confirmed", "flag": "flagged", "discard": "discarded"}

# Mirrors core.writes._SELECTED_TAKE_GUARD — the same dotted-path family a
# lock on "status" or "status.selected_take" blocks, so the undo-on-discard
# write below is refused under EXACTLY the same conditions a forward select
# would be.
_SELECTED_TAKE_GUARD = ("status", "status.selected_take")


class BatchError(ValueError):
    """A batch id/index/decision was invalid, or the batch record itself
    could not be found or parsed."""


def new_batch_id(now: datetime | None = None) -> str:
    """``bYYYYMMDD-HHMMSS`` — sortable (lexicographic order == chronological
    order), short, self-documenting. *now* is normally the same timestamp
    capture ``apply_ingest`` already took (via its ``clock`` kwarg) so the
    id and the record's ``created`` field always agree; defaults to
    ``datetime.now(UTC)`` for direct callers (e.g. the CLI's ``--all-matched``
    helper never needs one, but tests probing id shape alone do)."""
    now = now or datetime.now(timezone.utc)
    return f"b{now:%Y%m%d-%H%M%S}"


def batches_dir(project: Project) -> Path:
    return project.reports_dir / BATCH_DIR_NAME


def batch_path(project: Project, batch_id: str) -> Path:
    if not is_safe_segment(batch_id):
        raise BatchError(
            f"batch_id 不合法: {batch_id!r} — 只能包含字母、数字、下划线、连字符,长度 1-64"
        )
    return batches_dir(project) / f"{batch_id}.yaml"


def write_batch_record(
    project: Project,
    *,
    batch_id: str,
    created: datetime,
    actor: str,
    source: str,
    results: list[Any],
) -> None:
    """Persist one ``apply_ingest`` run — ``results`` is that call's
    ``list[IngestRowResult]`` (duck-typed here, not imported, to avoid a
    ``build.ingest`` <-> ``build.batches`` import cycle: each element needs
    only ``.row`` (an ``IngestRow``-shaped object) / ``.ok`` / ``.detail`` /
    ``.error``). One atomic write (``core.yamlio.write_yaml``) — never a
    partial batch file, even if the process dies mid-run the CALLER's loop
    already recorded (events.jsonl has every ``ingest_row``); this is purely
    the reviewable summary on top.

    ``review`` starts ``"pending"`` for every item EXCEPT ``skip_duplicate``
    rows, which start ``"auto"`` (they never needed a human call — exact by
    content hash, §3). ``staged`` mirrors ``landed["staged"]`` when present
    (only ``take`` rows ever set it — see ``build.ingest``'s empty-shot
    auto-select, goal item 2) so a reviewer/GUI never has to dig into
    ``landed`` just to render a staged-take badge."""
    items: list[dict[str, Any]] = []
    for idx, r in enumerate(results):
        row = r.row
        detail = dict(r.detail or {})
        review = "auto" if row.action == "skip_duplicate" else "pending"
        items.append({
            "index": idx,
            "name": row.name,
            "hash": row.hash,
            "action": row.action,
            "target": row.target,
            "shot_id": row.shot_id,
            "asset_id": row.asset_id,
            "asset_kind": row.asset_kind,
            "match": row.match,
            "candidates": list(row.candidates),
            "landed": detail,
            "ok": r.ok,
            "error": r.error,
            "staged": bool(detail.get("staged", False)),
            "review": review,
            "note": "",
        })
    data = {
        "batch": batch_id,
        "created": created.isoformat(timespec="seconds"),
        "actor": actor,
        "source": source,
        "items": items,
    }
    path = batch_path(project, batch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_yaml(path, data)


def list_batches(project: Project) -> list[dict[str, Any]]:
    """Every persisted batch, NEWEST first (sorted by ``created``, not
    filename — a caller-supplied ``batch_id`` need not sort lexicographically
    even though the generated default does), each summarized with a
    per-review-state item count so a list view never has to load every
    item of every batch just to render a badge. A malformed/unreadable batch
    file is skipped, never fatal to the whole listing (mirrors
    ``qc.agent_review``'s stance on a torn ``qc_agent.jsonl`` line)."""
    d = batches_dir(project)
    if not d.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.yaml")):
        try:
            data = read_yaml(p)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        items = data.get("items") or []
        counts: dict[str, int] = {}
        for it in items:
            state = str((it or {}).get("review", "pending"))
            counts[state] = counts.get(state, 0) + 1
        out.append({
            "batch": data.get("batch", p.stem),
            "created": data.get("created", ""),
            "actor": data.get("actor", ""),
            "source": data.get("source", ""),
            "items": len(items),
            "counts": counts,
        })
    out.sort(key=lambda b: (str(b["created"]), str(b["batch"])), reverse=True)
    return out


def load_batch(project: Project, batch_id: str) -> dict[str, Any]:
    path = batch_path(project, batch_id)
    if not path.exists():
        raise BatchError(f"批次不存在: {batch_id} — 用 `manju ingest-batches` 查看已有批次")
    data = read_yaml(path)
    if not isinstance(data, dict):
        raise BatchError(f"批次记录损坏(不是合法映射): {batch_id}")
    return data


def review_item(
    project: Project, batch_id: str, index: int, *, decision: str, note: str = "", actor: str,
) -> dict[str, Any]:
    """Confirm/flag/discard ONE item of a persisted ingest batch — updates
    its ``review``/``note`` in place, appends an ``ingest_review`` event,
    atomic write (``core.yamlio.write_yaml`` — temp file + ``os.replace``,
    same crash-safety as every other truth file, §14).

    ``discard`` on an item that auto-staged a take (``build.ingest``'s
    empty-shot auto-select, goal item 2 — ``item["staged"]`` is ``True``)
    ADDITIONALLY undoes that selection: clears ``status.selected_take`` via
    ``core.writes.checked_shot_write``, but ONLY when the shot's CURRENT
    selection still equals the take this item staged — someone may have
    re-selected (by hand, or a later `manju build` gap-fill) since; that
    later, human-observable decision is never silently clobbered by an old
    review action. Either way the returned ``detail["undo"]`` explains what
    happened (undone / left alone because it changed / undo itself refused
    by a lock or check regression — the item is still marked discarded even
    when the undo fails, since the review decision itself always succeeds).

    LOCKING CONTRACT (mirrors ``core.writes``'s own docstring): this
    function does NOT take the project's cross-process build lock itself —
    two concurrent reviewers (or a reviewer racing a build) writing the same
    batch YAML, or the same shot file via the discard-undo path, is exactly
    the hazard §9 exists to close. Every CALLER must hold
    ``runtime.buildlock.BuildLock`` for the full duration of this call —
    see ``cli.py``'s ``manju ingest-confirm``/``ingest-flag``/
    ``ingest-discard`` (``cli._write_lock``, the same helper ``select``/
    ``import``/``gc`` already use) for the reference call site. A future
    GUI/MCP entrance must do the same."""
    if decision not in REVIEW_DECISIONS:
        raise BatchError(f"未知 decision: {decision!r} — 只能是 {'/'.join(REVIEW_DECISIONS)}")
    data = load_batch(project, batch_id)
    items = data.get("items")
    if not isinstance(items, list) or not (0 <= index < len(items)):
        raise BatchError(f"批次 {batch_id} 中没有第 {index} 项")
    item = dict(items[index] or {})

    undo_note: str | None = None
    if decision == "discard" and item.get("staged"):
        landed = item.get("landed") or {}
        shot_id = item.get("shot_id")
        take = landed.get("take")
        if shot_id and take:
            try:
                current_raw = project.load_shot_raw(shot_id)
            except Exception as exc:
                undo_note = f"撤销失败(无法读取镜头 {shot_id}): {exc}"
            else:
                current = (current_raw.get("status") or {}).get("selected_take") \
                    if isinstance(current_raw.get("status"), dict) else None
                if current != take:
                    undo_note = (
                        f"镜头 {shot_id} 的选中 take 已变为 {current!r}"
                        f"(不再是自动选用的 {take!r}),未撤销"
                    )
                else:
                    def _clear(d: dict, *, _take=take) -> None:
                        status = d.get("status")
                        if isinstance(status, dict) and status.get("selected_take") == _take:
                            del status["selected_take"]

                    try:
                        checked_shot_write(project, shot_id, _clear, guard_paths=_SELECTED_TAKE_GUARD)
                    except WriteRejected as exc:
                        undo_note = f"撤销失败(自动选用未撤销): {exc}"
                    else:
                        undo_note = f"已撤销自动选用的 take {take}(镜头 {shot_id} 恢复为未选定)"

    item["review"] = _DECISION_TO_STATE[decision]
    if undo_note:
        item["note"] = f"{note}; {undo_note}" if note else undo_note
    else:
        item["note"] = note
    items[index] = item
    data["items"] = items
    write_yaml(batch_path(project, batch_id), data)

    event_detail: dict[str, Any] = {
        "batch": batch_id, "index": index, "decision": decision, "note": note,
    }
    if undo_note is not None:
        event_detail["undo"] = undo_note
    append_event(project.root, actor, "ingest_review", event_detail)

    return {"batch": batch_id, "index": index, "decision": decision, "item": item, "undo": undo_note}
