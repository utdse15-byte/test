"""The single authoritative job-kind registry (Priority 1 item 4).

Before this module, job-kind metadata was duplicated across the codebase:

* ``gui/jobs.py`` ``RETRYABLE_KINDS`` (which kinds the retry button rebuilds),
* ``gui/jobs.py`` ``CANCELABLE_RUNNING_KINDS`` (which honor mid-run cancel),
* ``gui/server.py`` ``_build_retry_fn`` (the per-kind retry factory allow-list),
* ``gui/page.py`` ``const KIND_ZH`` (the frontend's hard-coded 中文 chip labels),
* the assorted spend-gate / locale-aware knowledge spread through the engine.

Each of those is now DERIVED from :data:`JOB_KINDS` here. Define a kind once,
in one place, with all of its metadata; every secondary structure is a view
onto this registry. The GUI serves it read-only at ``GET /api/meta/job-kinds``
and the frontend consumes that instead of keeping its own copy.

This module is PURE metadata: frozen dataclasses and plain strings, no engine
imports, no provider SDKs (it lives in ``core`` and is scanned by the
``xp.core_no_sdk`` contract). Retry FACTORY implementations necessarily live in
the GUI layer (they close over the session/project); the registry declares
*which* kinds are retryable and a consistency test pins that the GUI's factory
dispatch covers exactly :func:`retryable_kinds` — see
``tests/test_jobkinds_registry.py``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobKindSpec:
    """Everything the system needs to know about one job kind, declared once.

    Fields:

    - ``kind``: the wire identifier used by ``JobRunner.submit`` and the API.
      MUST match the kind strings the GUI actually submits.
    - ``display_name_zh``: the user-facing 中文 label (owner-facing UI is 中文).
    - ``cancelable_while_running``: the kind actually honors ``should_cancel`` /
      ``cancel_event`` mid-run. Kinds that are False still accept cancel on a
      *queued* job, but calling running-cancel a success would be a lie.
    - ``retryable``: the GUI can rebuild the exact work closure for a failed
      job of this kind (``gui/server.py`` ``_build_retry_fn``).
    - ``spend_endpoint``: coarse provider-spend/billing bucket a paid kind bills
      against, or ``None`` for kinds that never call a paid provider. A kind is
      "paid" iff this is not ``None`` (see :func:`paid_kinds`).
    - ``locale_aware``: the kind reads/writes per-locale and must receive an
      explicit locale (build/voice/qc in a multi-language project).
    - ``destructive``: the kind irreversibly overwrites or deletes source truth.
      In this append-only architecture (media is append-only, text is truth) no
      current kind is destructive; the field exists so a future one must opt in
      explicitly rather than be silently treated as safe.
    """

    kind: str
    display_name_zh: str
    cancelable_while_running: bool = False
    retryable: bool = False
    spend_endpoint: str | None = None
    locale_aware: bool = False
    destructive: bool = False

    @property
    def paid(self) -> bool:
        """A kind is paid iff it declares a spend/billing bucket."""
        return self.spend_endpoint is not None


# ---------------------------------------------------------------------------
# THE registry. Every job kind is declared here EXACTLY once. Adding a kind or
# changing its semantics is a one-line edit that flows to every derived view.
#
# The cancelable / retryable columns MUST stay byte-identical to the frozensets
# the GUI and its pins expect (tests/test_project_bugfix_20260715.py pins
# RETRYABLE_KINDS by equality; the cycle-* tests pin CANCELABLE membership).
# ---------------------------------------------------------------------------
_SPECS: tuple[JobKindSpec, ...] = (
    # Paid, retryable generation kinds (the spend-gated core pipeline).
    JobKindSpec("build", "构建", cancelable_while_running=True, retryable=True,
                spend_endpoint="build.pipeline", locale_aware=True),
    JobKindSpec("redo", "重做", cancelable_while_running=True, retryable=True,
                spend_endpoint="image.generate"),
    JobKindSpec("voice", "配音", cancelable_while_running=True, retryable=True,
                spend_endpoint="tts.synthesize", locale_aware=True),
    JobKindSpec("redo_batch", "批量重做", cancelable_while_running=True,
                retryable=True, spend_endpoint="image.generate"),
    JobKindSpec("voice_batch", "批量配音", cancelable_while_running=True,
                retryable=True, spend_endpoint="tts.synthesize", locale_aware=True),
    # Free, non-retryable operational kinds.
    JobKindSpec("qc", "质检", cancelable_while_running=True, locale_aware=True),
    JobKindSpec("repair", "修复", cancelable_while_running=True),
    JobKindSpec("export", "导出", cancelable_while_running=True),
    JobKindSpec("ingest_plan", "导入计划", cancelable_while_running=True),
    JobKindSpec("ingest", "导入应用", cancelable_while_running=True),
    JobKindSpec("series_sync_bible", "同步设定", cancelable_while_running=True),
    JobKindSpec("edit_preview_batch", "批量预览", cancelable_while_running=True),
    JobKindSpec("voice_preview", "试听", cancelable_while_running=True),
    JobKindSpec("handle_rebuild", "补拍手柄", cancelable_while_running=True),
    JobKindSpec("roundtrip", "外部回写", cancelable_while_running=True),
    # Kinds that do NOT honor mid-run cancel (queued-cancel only).
    JobKindSpec("series_new_episode", "新建集"),
    JobKindSpec("edit_preview", "剪辑预览"),
)

JOB_KINDS: dict[str, JobKindSpec] = {s.kind: s for s in _SPECS}


def spec(kind: str) -> JobKindSpec | None:
    """The spec for ``kind``, or ``None`` if it is not a registered kind."""
    return JOB_KINDS.get(kind)


def all_kinds() -> frozenset[str]:
    return frozenset(JOB_KINDS)


def retryable_kinds() -> frozenset[str]:
    return frozenset(k for k, s in JOB_KINDS.items() if s.retryable)


def cancelable_running_kinds() -> frozenset[str]:
    return frozenset(k for k, s in JOB_KINDS.items() if s.cancelable_while_running)


def paid_kinds() -> frozenset[str]:
    return frozenset(k for k, s in JOB_KINDS.items() if s.paid)


def locale_aware_kinds() -> frozenset[str]:
    return frozenset(k for k, s in JOB_KINDS.items() if s.locale_aware)


def destructive_kinds() -> frozenset[str]:
    return frozenset(k for k, s in JOB_KINDS.items() if s.destructive)


def display_labels() -> dict[str, str]:
    """kind → 中文 label, for the GUI chip map."""
    return {k: s.display_name_zh for k, s in JOB_KINDS.items()}


def job_kinds_metadata() -> list[dict[str, object]]:
    """JSON-serializable registry snapshot for ``GET /api/meta/job-kinds``.

    Read-only view; the frontend consumes this instead of a hard-coded map.
    Ordered as declared so the UI is deterministic across requests.
    """
    return [
        {
            "kind": s.kind,
            "display_name_zh": s.display_name_zh,
            "cancelable_while_running": s.cancelable_while_running,
            "retryable": s.retryable,
            "paid": s.paid,
            "spend_endpoint": s.spend_endpoint,
            "locale_aware": s.locale_aware,
            "destructive": s.destructive,
        }
        for s in _SPECS
    ]


def known(kinds: Iterable[str]) -> bool:
    """True iff every kind in ``kinds`` is registered (no unknown references)."""
    return all(k in JOB_KINDS for k in kinds)
