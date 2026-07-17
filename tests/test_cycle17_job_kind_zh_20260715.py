"""Cycle-17: job strip shows Chinese kind labels.

Rewritten (Priority 1 item 2/4): the job-kind labels now live in exactly one
place — the authoritative registry ``manju.core.jobkinds`` — and the GUI serves
them at ``GET /api/meta/job-kinds``. This test validates that registry BEHAVIOR
(the label a kind actually resolves to) instead of scanning ``gui/page.py`` for
a hard-coded ``KIND_ZH`` string, which no longer exists.
"""

from manju.core import jobkinds


def test_job_kind_zh_map() -> None:
    labels = jobkinds.display_labels()
    # Every registered kind resolves to a non-empty 中文 label.
    assert labels
    assert all(v for v in labels.values())
    # The specific label the old source-scan pinned.
    assert labels["redo_batch"] == "批量重做"
