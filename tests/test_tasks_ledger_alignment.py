"""The run ledger's columns must survive reaching row 10.

`manju tasks` printed a bare ``#{id}``. As soon as the ledger crossed ten rows
the ids stopped being the same width, and every column after them — shot,
provider, status, cost, timestamp — shifted by one character on rows 1-9. The
table broke precisely on the long histories that are the reason to read it.

Ids are right-aligned as a whole token (``  #9`` under ``#12``, not ``# 9``),
so the ``#`` stays attached to its number.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from manju.cli import app

runner = CliRunner()


def _ledger(project, n: int) -> None:
    """n succeeded rows in the disposable run ledger, ids 1..n."""
    from manju.runtime.state import RuntimeState

    st = RuntimeState(project.root)
    for i in range(1, n + 1):
        st.record_run(shot=f"S{i:03d}", provider="caption_card",
                      status="succeeded", cost=0.0, currency="CNY",
                      take=f"take_{i:02d}")


def _rows(out: str) -> list[str]:
    return [ln for ln in out.splitlines() if re.match(r"^\s+#\d+\s", ln)]


def _run(project, monkeypatch) -> str:
    monkeypatch.chdir(project.root)
    res = runner.invoke(app, ["tasks", "-n", "50"])
    assert res.exit_code == 0, res.output
    return res.output


def test_the_shot_column_lines_up_across_the_ten_row_boundary(
        tmp_project, monkeypatch) -> None:
    _ledger(tmp_project, 12)
    rows = _rows(_run(tmp_project, monkeypatch))
    assert len(rows) == 12, f"expected 12 ledger rows, got {len(rows)}"
    starts = {ln.index("S0") for ln in rows}
    assert len(starts) == 1, f"shot column ragged across {sorted(starts)}"


def test_the_hash_stays_attached_to_its_number(
        tmp_project, monkeypatch) -> None:
    """Padding BETWEEN # and the digit ("# 9") aligns but reads as a typo."""
    _ledger(tmp_project, 12)
    for ln in _rows(_run(tmp_project, monkeypatch)):
        assert re.search(r"#\d", ln), f"# separated from its id: {ln!r}"


def test_ids_are_right_aligned_not_left(tmp_project, monkeypatch) -> None:
    _ledger(tmp_project, 12)
    rows = _rows(_run(tmp_project, monkeypatch))
    ends = {ln.index("#") + len(re.match(r"#\d+", ln[ln.index("#"):]).group())
            for ln in rows}
    assert len(ends) == 1, f"ids not right-aligned; digits end at {sorted(ends)}"


def test_a_single_digit_ledger_wastes_no_column(
        tmp_project, monkeypatch) -> None:
    """Width comes from the rows present, so a short ledger stays tight."""
    _ledger(tmp_project, 3)
    rows = _rows(_run(tmp_project, monkeypatch))
    assert rows and all(ln.startswith("  #") for ln in rows), rows


def test_an_empty_ledger_still_renders(tmp_project, monkeypatch) -> None:
    """max() over no rows must not raise."""
    out = _run(tmp_project, monkeypatch)
    assert "任务 / tasks" in out
    assert _rows(out) == []


# ---------------------------------------------------- the same class elsewhere


def test_the_skills_table_widens_to_its_longest_id(tmp_project, monkeypatch) -> None:
    """`manju skills` hardcoded `:<22`, but `continue-from-accepted-take` is 27
    characters — that row's description started five columns right of every
    other one. Width comes from the rows present, like the ledger's ids do."""
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["skills"])
    assert res.exit_code == 0, res.output

    rows = [ln for ln in res.output.splitlines()
            if ln.startswith("  ") and not ln.startswith("  →")
            and not ln.startswith("   ") and " " in ln.strip()]
    assert len(rows) >= 5, f"expected the bundled skill list, got {rows}"

    starts = set()
    for ln in rows:
        sid = ln.strip().split(" ", 1)[0]
        starts.add(ln.index(sid) + len(sid) + (len(ln.split(sid, 1)[1])
                                               - len(ln.split(sid, 1)[1].lstrip())))
    assert len(starts) == 1, f"description column ragged across {sorted(starts)}"


def test_a_long_skill_id_exists_to_make_that_check_real(
        tmp_project, monkeypatch) -> None:
    """Guard the guard: with every id under 22 chars the old code passed too."""
    monkeypatch.chdir(tmp_project.root)
    res = runner.invoke(app, ["skills"])
    ids = [ln.strip().split(" ", 1)[0] for ln in res.output.splitlines()
           if ln.startswith("  ") and not ln.startswith("  →")]
    assert max(len(i) for i in ids) > 22, f"no id long enough: {sorted(ids)[-3:]}"
