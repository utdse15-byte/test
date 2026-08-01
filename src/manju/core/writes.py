"""The ONE checked write path for ``status.selected_take`` (round W, #39/#28/#41).

Round-V review (#19/#39) found that CLI ``select``, MCP ``select_take``, board
``select``, GUI ``select`` and build's auto-select each wrote
``status.selected_take`` with their own — inconsistent — safety net: some
skipped the value-hash lock check entirely, none ran a post-write check
scoped to the touched shot, none reverted a regression. ``mentions --apply``
(#41) had the same shape of gap for ``characters``/``scene``.

This module gives every one of those entrances the SAME three-step pipeline:

1. **lock guard** — a value-hash lock (§5) covering the field about to move
   refuses the write outright, loudly (:class:`WriteRejected`), never a
   silent no-op and never a partial write.
2. **write** — through :meth:`Project.update_shot_raw` (the existing atomic
   text path, §3) — nothing here invents a second write mechanism.
3. **post-write check** — ``manju check`` re-run, scoped to errors mentioning
   THIS shot's file; any error the pre-write baseline did not have reverts
   the edit (the original bytes, verbatim) and raises.

Eventing is left to the caller on purpose: some entrances want one event per
call (`select`), others want one event for a whole batch (`mentions
--apply` records one ``mentions_apply`` per changed shot from the CLI layer,
already de-duplicated there) — :func:`select_take_checked` records its own
(with ``actor``/``via`` so the ledger can tell entrances apart, #39);
:func:`checked_shot_write` never touches ``events.jsonl``.

Process-level mutual exclusion (the cross-process ``.manju/build.lock``, §9)
is a SEPARATE concern from the per-field value-hash lock guarded here, and is
deliberately NOT acquired inside this module: `build/graph.py`'s auto-select
loop and a single-shot `redo`'s inline fill already run inside their own
``build_lock`` (BuildLock is not reentrant — a second ``acquire()`` in the
same process raises immediately), so a lock taken here would self-deadlock
those callers. Every OTHER entrance (CLI ``select``, MCP ``select_take``,
board/GUI select) takes the process lock itself before calling in — see each
call site.

``history.rollback_shot`` is the one exception that does NOT route through
here — see its own docstring in ``core/history.py`` for why: it restores a
PREVIOUSLY recorded human decision from the event ledger, not a fresh one,
and undo must not be blockable by the very lock a mistaken forward selection
would trip (that would turn a recovery path into one more way to get stuck).

Round AA item 5 (#1) adds a FOURTH, OPTIONAL guard on top of the three above:
**CAS** (optimistic concurrency). A GUI form or MCP client that LOADED a shot,
then SAVES after some other entrance edited it in between, would otherwise
silently last-writer-win the whole file — nobody is at fault, and nothing
above (lock guard / write / post-write check) catches it, because the write
itself is perfectly valid content. Pass ``expected_text_hash`` (the
:func:`shot_text_hash` a caller saw when it loaded the shot) and a write
against a file that moved underneath it since is refused — :class:`WriteRejected`,
BEFORE anything is written — instead of silently overwriting. ``None`` (the
default) skips the check entirely, so every EXISTING call site (select,
lock, ``mentions --apply``, none of which round-trip through a client that
could go stale) is byte-for-byte unaffected.

The CLI never passes ``expected_text_hash``: a CLI command's read-modify-write
is a single call inside a single process (and, for the writers that mutate
more than a value-hash-locked field, already inside the process's own
``build_lock`` hold — see ``cli.py``'s ``_write_lock``), so there is no window
for a second entrance to move the file between the CLI's read and its write.
Threading CAS through the CLI would only add a parameter nothing there ever
needs — see each GUI/MCP call site (gui/server.py, mcp/tools.py) for where it
actually threads through.
"""

from __future__ import annotations

from typing import Any, Callable

import yaml

from .check import run_check
from .container import Project
from .events import append_event
from .hashing import hash_text
from .yamlio import atomic_write_text, write_yaml


class WriteRejected(RuntimeError):
    """A checked write was refused — a locked field would move, or the write
    introduced a NEW `manju check` error on the touched shot. Either way the
    shot file is left exactly as it was (reverted, if the write already
    landed) before this is raised."""


def _coerce_locked(value: Any) -> dict[str, str]:
    """Normalize a shot's ``locked`` field (dict, bare list, or absent) to a
    dict — the same coercion ``core/check.py`` and the MCP tools use."""
    if value is None:
        return {}
    if isinstance(value, list):
        return {str(p): "" for p in value}
    if isinstance(value, dict):
        return {str(k): str(v) for k, v in value.items()}
    return {}


def _lock_blocking(locked: dict[str, str], candidate_paths: tuple[str, ...]) -> str | None:
    """The first locked dotted-path that would be violated by writing any of
    ``candidate_paths`` — an exact match, a locked ANCESTOR (``status`` locks
    ``status.selected_take`` too) or a locked DESCENDANT. ``None`` when none
    of the recorded locks touch this write."""
    for lp in locked:
        for cp in candidate_paths:
            if lp == cp or lp.startswith(cp + ".") or cp.startswith(lp + "."):
                return lp
    return None


def checked_shot_write(
    project: Project,
    shot_id: str,
    mutate: Callable[[dict], None],
    *,
    guard_paths: tuple[str, ...] = (),
    on_locked: str | None = None,
    expected_text_hash: str | None = None,
) -> dict[str, Any]:
    """Write ``shot_id``'s raw file via ``mutate`` through the shared pipeline
    (CAS check → lock guard → write → post-write check → revert-on-regression).

    ``guard_paths`` are dotted paths this write is ABOUT to move; if any is
    (or is nested under, or is an ancestor of) a recorded value-hash lock,
    the write is refused before anything touches disk. Pass ``()`` to skip
    the guard (the caller has already filtered what it will touch, e.g.
    ``mentions.apply_to_shot`` — which still gets the post-write check).

    ``expected_text_hash`` (round AA item 5, #1): the optional optimistic-
    concurrency (CAS) token — :func:`shot_text_hash` as the caller saw it at
    LOAD time. When set and it no longer matches the file's CURRENT text, the
    write is refused before anything touches disk (someone else edited this
    shot since the caller loaded it). ``None`` (the default) skips the check.

    Returns ``{"ok": True, "shot": shot_id, "check_warnings": [...]}``.
    Raises :class:`WriteRejected` — never partially applies a mutation.

    The mutate target is parsed from the CAS'd ``original_text`` bytes (not a
    second disk reload), so without an outer ``build_lock`` the write still
    binds to the text the CAS decision observed (P1-4).
    """
    path = project.shot_path(shot_id)
    label = project.relpath(path)
    original_text = path.read_text(encoding="utf-8")

    if expected_text_hash is not None and hash_text(original_text) != expected_text_hash:
        raise WriteRejected(
            f"{shot_id}: 该镜头在你加载后已被其他入口修改(乐观锁校验失败)——请刷新后重试"
        )

    try:
        raw = yaml.safe_load(original_text)
    except yaml.YAMLError as exc:
        raise WriteRejected(f"{shot_id}: shot YAML unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raw = {}

    if guard_paths:
        locked = _coerce_locked(raw.get("locked"))
        blocking = _lock_blocking(locked, guard_paths)
        if blocking is not None:
            raise WriteRejected(on_locked or (
                f"{shot_id}: '{blocking}' is locked — 该字段已锁定,自动写入口拒绝覆盖"
                f"(人工 `manju unlock {shot_id} {blocking}` 或改走 proposals/)"
            ))

    before = run_check(project)
    before_errors = {e for e in before.errors if label in e}

    mutate(raw)
    write_yaml(path, raw)

    after = run_check(project)
    new_errors = [e for e in after.errors if label in e and e not in before_errors]
    if new_errors:
        atomic_write_text(path, original_text)  # revert — verbatim bytes back
        raise WriteRejected(
            f"{shot_id}: write rejected — it introduces new check error(s): "
            + "; ".join(new_errors)
        )

    return {"ok": True, "shot": shot_id, "check_warnings": after.warnings}


def ensure_mapping(d: dict, key: str) -> dict:
    """``d[key]`` as a dict, coercing a non-mapping value to ``{}`` in place.

    Text is truth: hand-edited shot YAML is a first-class input, and a bare
    ``status:`` / ``camera:`` key parses to ``None`` — ``setdefault(key, {})``
    then returns that ``None`` and the caller crashes. Every raw-dict writer
    that needs a nested mapping goes through here (the one owner)."""
    cur = d.get(key)
    if not isinstance(cur, dict):
        cur = {}
        d[key] = cur
    return cur


# One dotted-path family guards every selected_take write: a lock recorded as
# either the leaf ("status.selected_take") or its parent ("status") blocks.
_SELECTED_TAKE_GUARD = ("status", "status.selected_take")


def select_take_checked(
    project: Project, shot_id: str, take: str, *, actor: str, via: str, action: str = "select"
) -> dict[str, Any]:
    """THE checked selected_take write (#39/#28/#19-adjacent) — every entrance
    (CLI ``select``, MCP ``select_take``, board select, GUI select, build
    auto-select) calls this instead of poking ``status.selected_take`` by
    hand, so lock verification / post-write check / revert / eventing never
    drift apart between call sites again.

    ``actor`` is the events.jsonl actor (human|ai|engine); ``via`` names the
    entrance (cli|mcp|board|gui|build_auto_select|redo) so the ledger can
    tell them apart even though they share one action name; ``action`` lets
    a caller record under a different action (build's gap-filler uses
    ``"auto_select"``, matching its pre-existing event name).

    Raises :class:`WriteRejected` when the take does not exist, the
    selection is locked, or the write regresses `manju check` — the project
    is left exactly as it was in every rejection case.
    """
    if project.get_take(shot_id, take) is None:
        raise WriteRejected(f"{shot_id} has no take '{take}'")

    def _set_selected(d: dict) -> None:
        ensure_mapping(d, "status")["selected_take"] = take

    result = checked_shot_write(
        project,
        shot_id,
        _set_selected,
        guard_paths=_SELECTED_TAKE_GUARD,
        on_locked=(
            f"{shot_id}.status.selected_take is locked (已锁定) — 拒绝自动/写入口改选;"
            f"人工运行 `manju unlock {shot_id} status.selected_take` 解锁后再选,"
            "或改走 proposals/ 提案"
        ),
    )
    append_event(project.root, actor, action, {"shot": shot_id, "take": take, "via": via})
    result["take"] = take
    return result


def selected_take_lock_block(project: Project, shot_id: str) -> str | None:
    """Read-only: the locked dotted-path (if any) that would block a
    ``select_take_checked`` call for this shot right now, or ``None``. Used
    by call sites that want a cheap pre-check without paying for the write +
    two full `manju check` runs (build/graph.py's redo inline auto-fill)."""
    raw = project.load_shot_raw(shot_id)
    return _lock_blocking(_coerce_locked(raw.get("locked")), _SELECTED_TAKE_GUARD)


def shot_text_hash(project: Project, shot_id: str) -> str:
    """The CAS token every READ entrance exposes (round AA item 5, #1): MCP
    ``get_shot``'s ``rev``, the GUI shot editor / storyboard cell / take-note
    forms embed this in what they render, then send it back on save so
    :func:`checked_shot_write`'s ``expected_text_hash`` (or an equivalent
    inline check at a write path that does not go through it, e.g. the GUI's
    raw-text shot editor) can refuse a save that would clobber someone else's
    edit instead of silently last-writer-winning.

    ``sha256:<hex>`` — the SAME canonical form :func:`core.hashing.hash_text`
    uses everywhere else a text hash is rendered. Returns ``""`` for a shot
    that does not exist yet: there is no prior text to collide with, and a
    client that loaded "not found" naturally round-trips that as its rev.
    """
    path = project.shot_path(shot_id)
    if not path.exists():
        return ""
    return hash_text(path.read_text(encoding="utf-8"))


def set_cut_order(project: Project, new_order: list[str], *,
                  actor: str = "engine", via: str = "engine") -> list[str]:
    """Set the cut — its ORDER **and** its membership (2026-08-01).

    ``permute_index`` below deliberately accepts only a permutation: that
    invariant is what keeps two browser tabs from silently dropping shots
    when neither carries a CAS token (GUI-INDEX-P1-001). Membership changes
    therefore get their OWN entrance rather than a relaxation of that guard,
    and callers that expose it (the GUI) require the token.

    Semantics, chosen for a workflow that reworks constantly: **dropping is
    not deleting.** A shot left out of ``new_order`` leaves the cut; its
    YAML stays on disk untouched, `manju check` names it as EXCLUDED with
    both ways back, and passing it again restores it. Nothing is ever lost,
    so no drop is a one-way door.

    Guards: every id must be a shot that exists (no inventing rows), and no
    duplicates (an id twice would silently double a clip). An empty cut is
    allowed — it is fully reversible, and refusing it would block the
    legitimate "start this sequence over" move.
    """
    if not isinstance(new_order, list) or not all(isinstance(s, str) for s in new_order):
        raise WriteRejected("order must be a list of shot ids")
    known = project.shot_ids()
    unknown = [s for s in new_order if s not in known]
    if unknown:
        raise WriteRejected(
            f"没有这些镜头 unknown shots: {', '.join(unknown)} — "
            f"现有镜头 available: {', '.join(known)}")
    seen: set[str] = set()
    dupes = [s for s in new_order if s in seen or seen.add(s)]  # type: ignore[func-returns-value]
    if dupes:
        raise WriteRejected(f"重复的镜头 duplicate ids: {', '.join(sorted(set(dupes)))}")

    before = list(project.load_index().order)
    index = project.load_index()
    index.order = list(new_order)
    project.save_index(index)
    dropped = [s for s in before if s not in new_order]
    restored = [s for s in new_order if s not in before]
    append_event(project.root, actor, "cut",
                 {"order": list(new_order), "dropped": dropped,
                  "restored": restored, "via": via})
    return list(new_order)


def permute_index(project: Project, new_order: list[str], *,
                  actor: str = "engine", via: str = "engine") -> list[str]:
    """WP6 shared write path for shot-order permutation (GUI ``/api/index``,
    CLI roundtrip, etc.).

    ``new_order`` must be a permutation of the CURRENT visible shot set
    (``project.shot_ids()``: index + on-disk extras). Raises
    :class:`WriteRejected` on mismatch. Returns the applied order.
    Caller holds ``build_lock`` when required (roundtrip/GUI already do).
    """
    if not isinstance(new_order, list) or not all(isinstance(s, str) for s in new_order):
        raise WriteRejected("order must be a list of shot ids")
    current = project.shot_ids()
    if sorted(new_order) != sorted(current):
        raise WriteRejected(
            "order must be a permutation of the current shots "
            f"(expected {len(current)} ids: {', '.join(current)})"
        )
    index = project.load_index()
    index.order = list(new_order)
    project.save_index(index)
    append_event(project.root, actor, "reorder",
                 {"order": list(new_order), "via": via})
    return list(new_order)
