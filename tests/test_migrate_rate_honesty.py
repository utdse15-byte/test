"""``manju migrate`` rate-change honesty — two confirmed defects from the
hourly scan of core/migrate.py, reproduced and fixed here.

7. ``migrate_plan`` accepted a whole-integer target (24/1) OVER an already-
   declared genuine rational rate (24000/1001) as a plain "apply" — silently
   discarding the exact NTSC timing with no loss report and no acknowledgement,
   the very loss the acknowledged downgrade path exists to gate. Its only guard
   was ``nominal_int == fps``, and both 24000/1001 and 24/1 have nominal_int 24.

8. ``downgrade_loss_report`` / ``downgrade_plan`` fabricated the full NTSC /
   drift-free-grid / interchange loss rows for a WHOLE-INTEGER declared rate
   (24/1) and forced ``--acknowledge-loss`` for a semantic no-op — even though
   ``migrate_inspect`` already reports ``downgrade_available == False`` for it.

Both fixes are conservative refusals derived from the module's own contracts
(no silent precision loss; agree with migrate_inspect). Legitimate migrations
(int→NTSC gain, exact passthrough on a fresh project, acknowledged NTSC
downgrade, whole→NTSC gain) are unaffected — pinned below too.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from manju.core.container import Project
from manju.core.migrate import (
    MigrateError,
    apply_plan,
    downgrade_loss_report,
    downgrade_plan,
    migrate_inspect,
    migrate_plan,
)
from manju.core.timebase import Rate

NTSC24 = Rate.from_fraction(24000, 1001)
WHOLE24 = Rate.from_fraction(24, 1)


def _fresh(tmp_path: Path, name: str) -> Project:
    return Project.create(tmp_path / name, git_init=False)


# --------------------------------------------------------------------------- #
# (7) migrate_plan must not silently overwrite a genuine rational rate          #
# --------------------------------------------------------------------------- #


def test_migrate_plan_refuses_whole_int_over_declared_ntsc(tmp_path):
    project = _fresh(tmp_path, "p7")
    apply_plan(project, migrate_plan(project, NTSC24))  # declare NTSC
    assert project.load_config().frame_rate.is_ntsc

    with pytest.raises(MigrateError) as exc:
        migrate_plan(project, WHOLE24)  # whole-int over NTSC — a silent loss
    assert "downgrade" in str(exc.value).lower()
    # the NTSC rate is untouched — nothing was discarded
    assert project.load_config().frame_rate == NTSC24


# --------------------------------------------------------------------------- #
# (8) downgrade must not fabricate losses for a whole-integer rate              #
# --------------------------------------------------------------------------- #


def test_downgrade_of_whole_integer_rate_is_refused(tmp_path):
    project = _fresh(tmp_path, "p8")
    apply_plan(project, migrate_plan(project, WHOLE24))  # declare exact 24/1
    rate = project.load_config().frame_rate
    assert rate.exact_int == 24 and rate.is_ntsc is False
    # migrate_inspect already advertises the downgrade as unavailable...
    assert migrate_inspect(project)["downgrade_available"] is False

    # ...so the downgrade functions must agree, not fabricate NTSC losses.
    with pytest.raises(MigrateError):
        downgrade_loss_report(project)
    with pytest.raises(MigrateError):
        downgrade_plan(project)


# --------------------------------------------------------------------------- #
# regression: every legitimate migration still works                           #
# --------------------------------------------------------------------------- #


def test_exact_passthrough_apply_on_fresh_project_still_works(tmp_path):
    project = _fresh(tmp_path, "reg1")
    apply_plan(project, migrate_plan(project, WHOLE24))
    assert project.load_config().frame_rate == WHOLE24


def test_int_to_ntsc_apply_still_works(tmp_path):
    project = _fresh(tmp_path, "reg2")
    apply_plan(project, migrate_plan(project, NTSC24))
    assert project.load_config().frame_rate == NTSC24


def test_genuine_ntsc_downgrade_with_ack_still_works(tmp_path):
    project = _fresh(tmp_path, "reg3")
    apply_plan(project, migrate_plan(project, NTSC24))
    report = downgrade_loss_report(project)  # a real loss report, not fabricated
    assert report["acknowledgment_required"] is True
    result = apply_plan(project, downgrade_plan(project), acknowledged=True)
    assert result["changed"] is True
    assert project.load_config().frame_rate.exact_int == 24


def test_whole_int_to_ntsc_gain_still_applies(tmp_path):
    project = _fresh(tmp_path, "reg4")
    apply_plan(project, migrate_plan(project, WHOLE24))       # state: whole 24/1
    apply_plan(project, migrate_plan(project, NTSC24))        # gain precision -> NTSC
    assert project.load_config().frame_rate == NTSC24
