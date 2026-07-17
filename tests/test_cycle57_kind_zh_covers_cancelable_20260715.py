"""Cycle-57: every cancelable job kind has a 中文 chip label.

Rewritten (Priority 1 item 2/4): coverage is now a property of the single
authoritative registry (``manju.core.jobkinds``) and the metadata it serves at
``GET /api/meta/job-kinds`` — not of a hard-coded ``KIND_ZH`` map in
``gui/page.py`` (which was removed; the frontend consumes the endpoint). We
assert the registry BEHAVIOR: every cancelable-while-running kind has a label.
"""

from manju.core import jobkinds


def test_kind_zh_covers_all_cancelable_kinds() -> None:
    labels = jobkinds.display_labels()
    missing = [k for k in sorted(jobkinds.cancelable_running_kinds())
               if not labels.get(k)]
    assert not missing, f"registry missing labels for cancelable kinds: {missing}"


def test_endpoint_metadata_covers_all_cancelable_kinds() -> None:
    # The serialized snapshot the GUI serves must carry a label for every
    # cancelable kind, so the frontend never has to fall back to raw snake_case.
    meta = {row["kind"]: row for row in jobkinds.job_kinds_metadata()}
    for k in jobkinds.cancelable_running_kinds():
        assert meta[k]["display_name_zh"], k
