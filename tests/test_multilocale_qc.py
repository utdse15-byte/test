"""Behavioral tests for true multi-locale QC (P1 item 7).

Covers item-2 bullet #8 (multi-locale QC checks EVERY declared locale) and
acceptance #7 (N locales -> N results + one aggregate). The per-locale probe is
injected (``qc_runner``) so the SELECTION + AGGREGATION policy is tested without
a real ffmpeg probe. The forbidden ``sorted(locales)[0]`` shortcut is pinned out.
"""

from __future__ import annotations

from pathlib import Path

from manju.core.container import Project
from manju.core.locale import add_locale
from manju.qc.checks import QCReport
from manju.qc.multilocale import (
    BASE,
    run_multilocale_qc,
    select_locales,
)


def _make_final(project: Project, lang: str | None) -> None:
    d = project.final_dir if lang is None else project.final_dir / "locales" / lang
    d.mkdir(parents=True, exist_ok=True)
    (d / "final_v1.mp4").write_bytes(b"fake-final")


def _runner(fail_langs: set[str]):
    """Fake run_qc: fails a locale iff its final lives under locales/<lang>/ for
    a lang in ``fail_langs`` (base final => 'base')."""
    calls: list[Path | None] = []

    def _run(project, timeline, *, deep=False, final_path=None):
        calls.append(final_path)
        rep = QCReport()
        lang = final_path.parent.name if final_path is not None else BASE
        if lang in fail_langs:
            rep.add("error", "technical", "final", "boom")
        return rep

    _run.calls = calls  # type: ignore[attr-defined]
    return _run


# --------------------------------------------------------------------------- #
# select_locales: the anti-sorted()[0] policy
# --------------------------------------------------------------------------- #

def test_base_only_project_selects_base(tmp_project) -> None:
    assert select_locales(tmp_project) == [BASE]


def test_single_declared_locale_selects_that_locale(tmp_project) -> None:
    add_locale(tmp_project, "en")
    assert select_locales(tmp_project) == ["en"]


def test_multi_locale_default_selects_every_declared_locale(tmp_project) -> None:
    for lg in ("en", "ja", "zh-CN"):
        add_locale(tmp_project, lg)
    got = select_locales(tmp_project)
    assert set(got) == {"en", "ja", "zh-CN"}
    # It must NOT collapse to sorted(locales)[0].
    assert got != ["en"]
    assert len(got) == 3


def test_explicit_lang_selects_only_that_locale(tmp_project) -> None:
    for lg in ("en", "ja", "zh-CN"):
        add_locale(tmp_project, lg)
    assert select_locales(tmp_project, lang="ja") == ["ja"]


def test_all_locales_selects_every_declared_locale(tmp_project) -> None:
    for lg in ("en", "ja"):
        add_locale(tmp_project, lg)
    assert set(select_locales(tmp_project, all_locales=True)) == {"en", "ja"}


# --------------------------------------------------------------------------- #
# run_multilocale_qc: N locales -> N results + one aggregate
# --------------------------------------------------------------------------- #

def test_n_locales_produce_n_results_and_one_aggregate(tmp_project) -> None:
    for lg in ("en", "ja", "zh-CN"):
        add_locale(tmp_project, lg)
        _make_final(tmp_project, lg)
    runner = _runner(fail_langs={"ja"})
    report = run_multilocale_qc(tmp_project, qc_runner=runner)

    assert set(report.locales) == {"en", "ja", "zh-CN"}
    assert report.locales["en"].status == "pass"
    assert report.locales["zh-CN"].status == "pass"
    assert report.locales["ja"].status == "fail"
    # aggregate fails iff any locale fails.
    assert report.aggregate == "fail"


def test_aggregate_passes_when_every_locale_passes(tmp_project) -> None:
    for lg in ("en", "ja"):
        add_locale(tmp_project, lg)
        _make_final(tmp_project, lg)
    report = run_multilocale_qc(tmp_project, qc_runner=_runner(fail_langs=set()))
    assert report.aggregate == "pass"
    assert all(r.status == "pass" for r in report.locales.values())


def test_declared_locale_without_final_is_missing_final_not_base_fallback(tmp_project) -> None:
    add_locale(tmp_project, "en")
    add_locale(tmp_project, "ja")
    _make_final(tmp_project, "en")           # en rendered, ja NOT
    _make_final(tmp_project, None)           # a base final exists...
    runner = _runner(fail_langs=set())
    report = run_multilocale_qc(tmp_project, qc_runner=runner)

    assert report.locales["ja"].status == "fail"
    assert report.locales["ja"].issues == ["missing_final"]
    # ja must NOT have been probed against the base final.
    assert (tmp_project.final_dir / "final_v1.mp4") not in runner.calls  # type: ignore[attr-defined]
    assert report.aggregate == "fail"


def test_each_locale_is_probed_with_its_own_final(tmp_project) -> None:
    for lg in ("en", "ja"):
        add_locale(tmp_project, lg)
        _make_final(tmp_project, lg)
    runner = _runner(fail_langs=set())
    run_multilocale_qc(tmp_project, qc_runner=runner)
    probed = {p.parent.name for p in runner.calls}  # type: ignore[attr-defined]
    assert probed == {"en", "ja"}


def test_explicit_lang_checks_only_that_locale(tmp_project) -> None:
    for lg in ("en", "ja"):
        add_locale(tmp_project, lg)
        _make_final(tmp_project, lg)
    runner = _runner(fail_langs={"ja"})
    report = run_multilocale_qc(tmp_project, lang="en", qc_runner=runner)
    assert set(report.locales) == {"en"}
    assert report.aggregate == "pass"  # ja's failure is not in scope
