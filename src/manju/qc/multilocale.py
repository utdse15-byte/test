"""True multi-locale QC (Priority 1 item 7).

QC must not silently inspect only ONE locale of a multi-language project. This
module owns the locale-SELECTION policy and the per-locale aggregation:

* single-locale projects check that locale;
* multi-locale projects check EVERY declared locale by default;
* ``--lang X`` checks only the explicitly selected locale;
* ``--all-locales`` checks every declared locale;
* each locale gets an INDEPENDENT result; the aggregate fails if any fails;
* a declared locale with no rendered final is a ``missing_final`` failure — we
  never fall back to the base final for it (that would be a silent lie).

The forbidden shortcut ``sorted(locales)[0]`` is never used unless the user
explicitly selected that one locale. Per-locale probing reuses ``run_qc`` with
the locale's own ``final_path`` (final assets/sidecars/caches stay
locale-specific); the ``qc_runner`` seam keeps the policy unit-testable without
ffmpeg.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..core.container import Project
from ..core.locale import list_locales, validate_lang

# Sentinel locale key for a project that declares no locales at all — QC still
# runs once, against the base final.
class _BaseSentinel:
    """Marker for the unlocalized base final. A DISTINCT object, never a locale
    id string — so it can never collide with a real locale, even one literally
    named "base" (``validate_lang('base')`` is legal). Its ``str`` is "base" so
    it reads naturally as a result key."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "base"

    def __str__(self) -> str:
        return "base"


# Sentinel (identity-compared with ``is``) for "check the project base final,
# not a locale overlay". Only ever selected when NO locale is declared.
BASE = _BaseSentinel()


def _result_key(lg: "str | _BaseSentinel") -> str:
    """The JSON/display key for a selected locale — 'base' for the sentinel,
    else the locale id itself."""
    return "base" if lg is BASE else str(lg)


@dataclass(frozen=True)
class LocaleQCResult:
    lang: str
    status: str  # "pass" | "fail"
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "issues": list(self.issues)}


@dataclass(frozen=True)
class MultiLocaleQCReport:
    aggregate: str  # "pass" | "fail"
    locales: dict[str, LocaleQCResult]
    # the FULL per-locale run_qc report objects (result key → QCReport) — NOT
    # serialized in to_dict (the --json envelope stays byte-stable); the CLI
    # merges them so reports/qc.json + repair_plan.yaml keep being written on
    # the multi-locale path (the command's documented contract).
    reports: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "aggregate": self.aggregate,
            "locales": {lg: r.to_dict() for lg, r in self.locales.items()},
        }

    def merged_report(self):
        """One union QCReport across every checked locale: each locale's items
        (messages prefixed ``[lang]`` for non-base locales, so qc.md/repair
        rows say which film they are about), plus a synthetic error item per
        ``missing_final`` locale. The aggregate stays exactly ``aggregate``
        (an error item exists iff some locale failed)."""
        from .checks import QCItem, QCReport

        merged = QCReport()
        for key in self.locales:
            rep = self.reports.get(key)
            prefix = "" if key == "base" else f"[{key}] "
            if rep is not None:
                for it in rep.items:
                    merged.items.append(QCItem(
                        it.level, it.area, it.subject,
                        f"{prefix}{it.message}" if prefix else it.message,
                        suggestion=it.suggestion, auto_safe=it.auto_safe,
                    ))
            elif "missing_final" in self.locales[key].issues:
                merged.items.append(QCItem(
                    "error", "existence", "final",
                    f"{prefix}locale 无成片(missing_final)— "
                    f"`manju build --lang {key}` 渲染后重跑 qc",
                ))
        return merged


def select_locales(project: Project, *, lang: str | None = None,
                   all_locales: bool = False) -> "list[str | _BaseSentinel]":
    """Which locales this QC run must check — the anti-``sorted()[0]`` policy.

    Order of precedence: an explicit ``--lang`` wins (check only that one);
    ``--all-locales`` checks every declared locale; otherwise a single-locale
    project checks its locale and a multi-locale project checks EVERY declared
    locale. A project with no declared locales yields ``[BASE]``.
    """
    declared = list_locales(project)  # sorted langs that have lines.yaml
    if lang is not None:
        return [validate_lang(str(lang).strip())]
    if all_locales:
        return declared or [BASE]
    if len(declared) <= 1:
        return declared or [BASE]
    return declared  # every declared locale, never just the first


def _locale_final_path(project: Project, lang: "str | _BaseSentinel") -> Path | None:
    """Newest ``final_vN.mp4`` for ``lang`` (base final for the BASE sentinel)."""
    if lang is BASE:
        return project.newest_final_path()
    d = project.final_dir / "locales" / lang
    if not d.exists():
        return None
    versions = [
        (int(m.group(1)), p)
        for p in d.glob("final_v*.mp4")
        if (m := re.match(r"final_v(\d+)$", p.stem))
    ]
    return max(versions, key=lambda t: t[0])[1] if versions else None


def run_multilocale_qc(
    project: Project,
    *,
    lang: str | None = None,
    all_locales: bool = False,
    deep: bool = False,
    qc_runner: Callable[..., object] | None = None,
    timeline: object | None = None,
) -> MultiLocaleQCReport:
    """Run QC across the selected locales and aggregate the results.

    ``qc_runner(project, timeline, *, deep, final_path)`` defaults to
    :func:`manju.qc.checks.run_qc`; it is injectable so the selection/aggregation
    logic is testable without a real ffmpeg probe. ``timeline`` lets the CLI
    pass its already-guarded timeline (a corrupt timeline.json then fails with
    the same clean message as the single-locale path, not a raw traceback);
    ``None`` loads it here exactly as before.
    """
    if qc_runner is None:
        from .checks import run_qc
        qc_runner = run_qc

    langs = select_locales(project, lang=lang, all_locales=all_locales)
    if timeline is None:
        timeline = project.load_timeline()
    results: dict[str, LocaleQCResult] = {}
    reports: dict[str, object] = {}

    for lg in langs:
        key = _result_key(lg)
        final_path = _locale_final_path(project, lg)
        if lg is not BASE and final_path is None:
            # A declared locale with no rendered final: honest failure, never a
            # silent fall-back to the base final.
            results[key] = LocaleQCResult(key, "fail", ["missing_final"])
            continue
        report = qc_runner(project, timeline, deep=deep, final_path=final_path)
        status = "pass" if report.ok else "fail"
        issues = sorted({
            f"{it.area}:{it.subject}" for it in report.items if it.level == "error"
        })
        results[key] = LocaleQCResult(key, status, issues)
        reports[key] = report

    aggregate = "fail" if any(r.status == "fail" for r in results.values()) else "pass"
    return MultiLocaleQCReport(aggregate=aggregate, locales=results, reports=reports)
