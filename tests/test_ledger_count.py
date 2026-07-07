"""Run-ledger COUNT API + truncated-final honesty in `manju status`.

Optimization assessment 2.10-5/-8: status needs the NUMBER of ledger runs, not
up to 100k fetched rows (`RuntimeState.count_runs`, §8.3); and a latest final
lacking its FIX-A ``.key.json`` sidecar (written only at render completion,
``media/render.py``) must be flagged as possibly crash-truncated instead of
being presented as the project's finished 成片 (§10 takeover honesty).
"""

from __future__ import annotations

import json
from pathlib import Path

from manju.build.status import project_status
from manju.runtime.state import RuntimeState

# ------------------------------------------------------------- count_runs()


def test_count_runs_empty_ledger_is_zero(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        assert st.count_runs() == 0


def test_count_runs_matches_recorded_runs(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        for i in range(3):
            st.record_run(shot=f"S00{i + 1}", provider="p", status="succeeded",
                          cost=0.1, currency="CNY")
        st.record_run(shot="S004", provider="p", status="failed",
                      failure_kind="timeout", error="超时")
        assert st.count_runs() == 4
        # exactly what status used to derive by fetching up to 100k rows
        assert st.count_runs() == len(st.run_log(100000))


def test_status_run_count_comes_from_ledger(tmp_project):
    with RuntimeState(tmp_project.root) as st:
        st.record_run(shot="S001", provider="p", status="succeeded", cost=0.32, currency="CNY")
        st.record_run(shot="S001", provider="p", status="succeeded", cost=0.08, currency="CNY")
    info = project_status(tmp_project)
    assert info["run_log"]["runs"] == 2


# ------------------------------------------------- latest_final honesty note

NOTE = "final may be incomplete (no content-key sidecar; crashed render?)"


def _write_key_sidecar(final_path: Path) -> None:
    """Fabricate the completion sidecar exactly where render.py writes it:
    ``final_vN.mp4`` -> ``final_vN.key.json`` (with_suffix, FIX-A)."""
    final_path.with_suffix(".key.json").write_text(
        json.dumps({"final_key": "sha256:deadbeef", "target": "final"},
                   ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_no_final_yields_no_note(tmp_project):
    info = project_status(tmp_project)
    assert info["latest_final"] is None
    assert info["latest_final_note"] is None


def test_final_without_key_sidecar_is_flagged_incomplete(tmp_project):
    (tmp_project.final_dir / "final_v1.mp4").write_bytes(b"truncated-by-crash")
    info = project_status(tmp_project)
    # latest_final itself is unchanged (the file IS the latest on disk) ...
    assert info["latest_final"] == "renders/final/final_v1.mp4"
    # ... but the takeover entry point says it may be a crashed render.
    assert info["latest_final_note"] == NOTE


def test_final_with_key_sidecar_has_no_note(tmp_project):
    final = tmp_project.final_dir / "final_v1.mp4"
    final.write_bytes(b"complete-final")
    _write_key_sidecar(final)
    info = project_status(tmp_project)
    assert info["latest_final"] == "renders/final/final_v1.mp4"
    assert info["latest_final_note"] is None


def test_note_tracks_only_the_latest_final(tmp_project):
    # v1 completed (has sidecar); v2 crash-truncated (no sidecar) -> flagged.
    v1 = tmp_project.final_dir / "final_v1.mp4"
    v1.write_bytes(b"complete")
    _write_key_sidecar(v1)
    (tmp_project.final_dir / "final_v2.mp4").write_bytes(b"truncated")
    info = project_status(tmp_project)
    assert info["latest_final"] == "renders/final/final_v2.mp4"
    assert info["latest_final_note"] == NOTE
