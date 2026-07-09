"""Structured failure records — make EVERY failure debuggable (§10, goal 10).

When any build step fails, the reason has to be clear to BOTH the AI and the
human: what step, on what subject, the root cause in one human line, a short
verbatim slice of evidence, one actionable hint, and a pointer to the fuller
log. That is the whole contract of this module.

The shape borrows from the tools that got diagnostics right:

- rustc / cargo — an error line, a labelled span, and a ``help:`` that tells you
  the one thing to try next;
- GitHub Actions annotations — every failure pinned to a step and a subject;
- Sentry breadcrumbs — a short, structured, verbatim trail rather than a wall of
  log.

So every :class:`Failure` answers, in a fixed order: **what step**, **on what
subject**, **root cause** (one line), **evidence** (a short verbatim tail:
ffmpeg stderr + argv head / HTTP status + body head / traceback last frame),
**one hint**, and **where the full log lives**. Degradations (a fallback fired,
QC gated, a card skipped) record in the SAME shape at ``level="info"`` — so
"why did this shot become a caption card?" is answerable from the record alone.

Records append to ``reports/failures.jsonl`` (append-only, one JSON object per
line, UTF-8, rotating at ~1MB) and every record also drops an ``events.jsonl``
entry (``action="failure"``) so the collaboration log tail shows failures inline
with everything else the two parties did (§3, §10).

Stdlib only; the record schema is frozen truth the GUI (wave-2) renders from.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

try:
    import fcntl
except ImportError:  # Windows: no fcntl — the lock degrades to a no-op below
    fcntl = None  # type: ignore[assignment]

# reports/ is the project's report surface (qc.md/json live here too); failures
# ride alongside as an append-only jsonl.
FAILURES_FILE = "reports/failures.jsonl"
ROTATE_BYTES = 1_000_000  # ~1MB: rename the active log aside, start a fresh one
_LOCK_TIMEOUT_S = 15.0


@contextlib.contextmanager
def _ledger_lock(reports_dir: Path, *, timeout_s: float = _LOCK_TIMEOUT_S) -> Iterator[None]:
    """Cross-process mutex around the rotate-then-append sequence (round W,
    #50). A single ``write()`` of one short JSON line is already POSIX-atomic
    (append-mode, under PIPE_BUF) on a local filesystem, but the ROTATE check
    (stat size → maybe ``os.replace`` the active file aside) is NOT: two
    processes racing the same rotation boundary can both decide to rotate,
    or one can append to a file the other is mid-rename on. ``fcntl.flock``
    on a sibling ``failures.lock`` serializes the whole thing, so a
    multi-process build (CLI + MCP + a GUI job) never interleaves or drops a
    line. Same degrade-not-hang stance as ``core/library.py``'s index lock:
    a platform without ``fcntl`` (Windows) no-ops rather than blocking
    forever, and a stuck holder times out to a clear error instead of a wedge.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    lock_path = reports_dir / "failures.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    # Best-effort: a wedged lock must never lose the failure
                    # record itself — proceed unlocked rather than drop it.
                    break
                time.sleep(0.02)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


# The build steps a failure can be pinned to (goal 10). "check" is the hard
# gate; the rest track the build graph (§0, §11). Kept as a frozenset so a typo
# in a caller is caught by _norm_step rather than silently accepted.
STEPS = frozenset(
    {
        "check",
        "generate",
        "voice",
        "compile",
        "captions",
        "render",
        "qc",
        "export",
        "package",
        "repair",
        # round W (#50): a step string that matches none of the above (a
        # typo in a caller — "reneder", "qcc", ...) must never be silently
        # attributed to "generate", the busiest/most-expected step. It reads
        # as "unknown" instead — loud, greppable, and never miscounted
        # against a step that did not actually fail.
        "unknown",
    }
)

LEVELS = frozenset({"error", "info"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_id() -> str:
    """A short, sortable-ish id: the GUI keys failure cards by it and the ledger
    row cross-references it (runtime/state.py ``failure_id``)."""
    return "F-" + uuid.uuid4().hex[:12]


def _norm_step(step: str) -> str:
    """Normalize a step string, never inventing a wrong attribution (#50): an
    empty/unrecognized value reads as ``"unknown"`` — never silently coerced
    to ``"generate"``, which would misattribute a render/qc/export typo to
    the busiest step and make it invisible in a by-step failure count."""
    step = str(step or "").strip().lower()
    return step if step in STEPS else "unknown"


def _clip(text: Any, limit: int) -> str:
    """A short verbatim tail, kept human: trailing whitespace trimmed, capped to
    ``limit`` chars with a leading ellipsis when it overflowed. Newlines are
    PRESERVED (an ffmpeg stderr tail / traceback frame reads better multi-line)."""
    if text is None:
        return ""
    s = str(text).rstrip()
    if len(s) <= limit:
        return s
    return "…" + s[-limit:]


@dataclass
class Failure:
    """One debuggable failure (or, at ``level="info"``, one degradation).

    Field order mirrors how a human reads a diagnostic: step → subject → cause →
    evidence → hint → where the log is. ``ts``/``id`` are filled by
    :func:`record_failure` when absent, so callers construct with just the
    meaningful fields.
    """

    step: str  # one of STEPS: check|generate|voice|compile|captions|render|qc|export|package|repair|unknown
    subject: str  # shot id / take / "final" / provider id / "timeline" — what the failure is ABOUT
    cause: str  # ONE human line: the root cause, no stack, no jargon
    evidence: str = ""  # short verbatim tail: ffmpeg stderr+argv / HTTP status+body head / last frame
    hint: str = ""  # ONE actionable line: the next thing to try
    log_path: str | None = None  # project-relative path to the fuller log, when one exists
    actor: str = "engine"  # who was driving: engine | human | ai
    level: str = "error"  # "error" (a step failed) | "info" (a degradation in the same shape)
    detail: dict[str, Any] = field(default_factory=dict)  # structured extras (kind/status/job_id/node_id)
    ts: str | None = None
    id: str | None = None

    def to_record(self) -> dict[str, Any]:
        """The frozen on-disk / on-wire shape (wave-2 GUI renders this)."""
        rec = asdict(self)
        rec["step"] = _norm_step(self.step)
        rec["level"] = self.level if self.level in LEVELS else "error"
        rec["ts"] = self.ts or _now_iso()
        rec["id"] = self.id or _new_id()
        return rec

    def summary(self) -> dict[str, Any]:
        """The compact form for a BuildResult / the AI path — id + the one-liners,
        never the full evidence blob."""
        return {
            "id": self.id or "",
            "level": self.level if self.level in LEVELS else "error",
            "step": _norm_step(self.step),
            "subject": self.subject,
            "cause": self.cause,
        }


def _root(project: Any) -> Path:
    """Accept a Project (has ``.root``) or a raw path — callers deep in the
    provider stack hold a Project, ffmpeg holds a bare root.

    A bare ``Path`` is matched FIRST: ``pathlib.Path`` itself exposes a ``.root``
    attribute (the anchor, e.g. ``"/"``), so a naive ``getattr(project, "root")``
    would silently redirect every write to the filesystem root."""
    if isinstance(project, (str, Path)):
        return Path(project)
    root = getattr(project, "root", project)
    return Path(root)


def next_options_for(failure: Failure) -> list[dict[str, str]]:
    """WP5: deterministic "what now" choices after a generation/voice failure.

    Returns ``[{action, label_zh, why}, …]``. Computed at record time for
    ``step in {generate, voice}``; empty for other steps.
    """
    step = _norm_step(failure.step)
    if step not in ("generate", "voice"):
        return []
    detail = failure.detail or {}
    kind = str(detail.get("kind") or detail.get("failure_kind") or "").lower()
    cause = (failure.cause or "").lower()
    # Infer kind from cause text when detail is sparse
    if not kind:
        if "rate" in cause or "429" in cause or "限流" in cause:
            kind = "rate_limited"
        elif "timeout" in cause or "超时" in cause:
            kind = "timeout"
        elif "content" in cause or "rejected" in cause or "审核" in cause or "nsfw" in cause:
            kind = "content_rejected"
        else:
            kind = "provider_error"

    opts: list[dict[str, str]] = []
    subject = failure.subject or ""
    fid = failure.id or ""

    if kind in ("rate_limited", "timeout"):
        opts.append({
            "action": "retry",
            "label_zh": "重试",
            "why": f"manju tasks retry {fid}" if fid else "稍后重试同一任务",
        })
    if kind == "content_rejected":
        if subject:
            opts.append({
                "action": "rewrite_prompt",
                "label_zh": "改写提示词",
                "why": f"manju prompt {subject}",
            })
        # Name the next fallback provider when known
        chain = detail.get("fallback_chain") or detail.get("chain") or []
        if isinstance(chain, list) and len(chain) > 1:
            # current is [0]; next is [1]
            nxt = str(chain[1])
            opts.append({
                "action": "fallback_provider",
                "label_zh": f"换供应商 {nxt}",
                "why": f"下一回退供应商: {nxt}",
            })
        elif detail.get("next_provider"):
            nxt = str(detail["next_provider"])
            opts.append({
                "action": "fallback_provider",
                "label_zh": f"换供应商 {nxt}",
                "why": f"下一回退供应商: {nxt}",
            })
        if subject:
            opts.append({
                "action": "manual_import",
                "label_zh": "手动导入素材",
                "why": f"manju select {subject} --file …",
            })
    if kind == "provider_error":
        chain = detail.get("fallback_chain") or detail.get("chain") or []
        if isinstance(chain, list) and len(chain) > 1:
            nxt = str(chain[1])
            opts.append({
                "action": "fallback_provider",
                "label_zh": f"换供应商 {nxt}",
                "why": f"下一回退供应商: {nxt}",
            })
        elif detail.get("next_provider"):
            nxt = str(detail["next_provider"])
            opts.append({
                "action": "fallback_provider",
                "label_zh": f"换供应商 {nxt}",
                "why": f"下一回退供应商: {nxt}",
            })
        opts.append({
            "action": "doctor",
            "label_zh": "检查环境",
            "why": "manju doctor",
        })
    # Always offer retry as a soft last option when nothing more specific
    if not opts:
        opts.append({
            "action": "retry",
            "label_zh": "重试",
            "why": f"manju tasks retry {fid}" if fid else "稍后重试",
        })
    return opts


def record_failure(project: Any, failure: Failure) -> dict[str, Any]:
    """Persist one :class:`Failure` and return its on-disk record.

    WP5: for generate/voice steps, attach deterministic ``next_options`` into
    ``detail`` at record time so ``manju failures`` and the GUI failure cards
    show the same "what now" choices.

    Two sinks, both append-only:

    1. ``reports/failures.jsonl`` — the durable failure log, one JSON object per
       line. Rotates when it crosses ~1MB: the active file is renamed aside with
       a timestamp suffix (``failures.<ts>.jsonl``) and a fresh one is started,
       so the recent view stays cheap to read while history is never dropped.
    2. ``events.jsonl`` — an ``action="failure"`` entry with ``{step, subject,
       cause, level, id}`` so the collaboration-log tail surfaces failures inline
       (§3, §10). Best-effort: an events hiccup never loses the failure record.

    Both ``failure.ts`` and ``failure.id`` are assigned here when absent. The
    returned record is what the caller threads onto a BuildResult / a ledger row.
    """
    failure.ts = failure.ts or _now_iso()
    failure.id = failure.id or _new_id()
    # WP5: attach next_options for generate/voice before the on-disk write
    if _norm_step(failure.step) in ("generate", "voice"):
        if not isinstance(failure.detail, dict):
            failure.detail = {}
        if "next_options" not in failure.detail:
            failure.detail["next_options"] = next_options_for(failure)
    record = failure.to_record()

    root = _root(project)
    path = root / FAILURES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    # #50: rotate-check + rotate + append is ONE critical section across
    # processes — an exclusive lock on a sibling failures.lock (best-effort,
    # times out to unlocked rather than ever dropping the record; see
    # _ledger_lock) so two processes never both decide to rotate the same
    # boundary or interleave a line mid-rotate.
    with _ledger_lock(path.parent):
        # Rotate BEFORE appending so the active file never grows unbounded. A
        # rename is atomic on the same filesystem; a same-second second
        # rotation gets a numeric suffix so we never clobber an
        # already-rotated slice.
        try:
            if path.exists() and path.stat().st_size >= ROTATE_BYTES:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
                rotated = path.with_name(f"failures.{stamp}.jsonl")
                n = 1
                while rotated.exists():
                    rotated = path.with_name(f"failures.{stamp}.{n}.jsonl")
                    n += 1
                os.replace(path, rotated)
        except OSError:
            pass  # a rotation hiccup must never lose the failure we came to record

        line = json.dumps(record, ensure_ascii=False)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    # Mirror to the collaboration log — best-effort, never fatal.
    try:
        from .events import append_event

        append_event(
            root,
            failure.actor,
            "failure",
            {
                "step": record["step"],
                "subject": failure.subject,
                "cause": failure.cause,
                "level": record["level"],
                "id": record["id"],
            },
        )
    except Exception:
        pass

    return record


def _iter_active(project: Any):
    """Yield parsed records from the ACTIVE failures.jsonl (rotated history is
    left on disk but not read here — the recent view is what both surfaces need,
    and it stays O(active file))."""
    path = _root(project) / FAILURES_FILE
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue  # a torn write must not brick the reader (mirrors events)


def read_failures(project: Any, n: int = 10, *, level: str | None = None) -> list[dict[str, Any]]:
    """The most recent failures, NEWEST FIRST — what ``manju failures`` renders.

    ``level`` filters to just errors or just degradations when given; ``None``
    returns both. ``n`` caps the result after filtering."""
    records = [r for r in _iter_active(project) if level is None or r.get("level") == level]
    records.reverse()  # newest first
    if n is not None and n >= 0:
        return records[:n]
    return records


def failures_since_last_build(project: Any) -> list[dict[str, Any]]:
    """Failure events recorded since the last SUCCESSFUL build (errors only).

    Powers the one-line ``manju status`` nudge. Computed from the append-ORDERED
    events.jsonl, not by timestamp: every failure mirrors itself there as an
    ``action="failure"`` entry (carrying ``{step, subject, cause, level, id}``),
    and a build mirrors as ``action="build"`` with ``detail.ok``. Walking events
    in order and counting failures after the last green build is exact even when
    a failure and the build land in the same wall-clock second — a boundary a
    seconds-granularity ts comparison could not resolve.

    Returns each failure's event ``detail`` (id/step/subject/cause/level), newest
    LAST (event order). Degradations (``level="info"``) never count — the status
    line is for things that actually broke. Empty when no green build precedes
    the failures too: with no successful build on record, every recorded error
    since the log began counts."""
    root = _root(project)
    try:
        from .events import tail_events

        events = tail_events(root, 2000)
    except Exception:
        return []
    last_ok = -1
    for i, ev in enumerate(events):
        if ev.get("action") == "build" and (ev.get("detail") or {}).get("ok"):
            last_ok = i
    out: list[dict[str, Any]] = []
    for ev in events[last_ok + 1:]:
        if ev.get("action") != "failure":
            continue
        detail = ev.get("detail") or {}
        if detail.get("level", "error") == "error":
            out.append(detail)
    return out
