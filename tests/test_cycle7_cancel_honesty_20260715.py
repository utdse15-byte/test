"""Cycle-7: cancel honesty when job finishes after cancel request."""

from __future__ import annotations

import time

from manju.gui.jobs import Job, JobRunner


def test_done_after_cancel_sets_honest_error(tmp_path):
    runner = JobRunner(runtime_dir=tmp_path, project_id="p")

    def work(job):
        # Simulate cancel mid-flight then finish successfully.
        job.cancel_event.set()
        time.sleep(0.05)
        return {"ok": True}

    job = runner.submit("redo", {}, work)
    # Wait until terminal
    deadline = time.time() + 5
    while time.time() < deadline:
        j = runner.get(job.id)
        if j and j.state in ("done", "failed", "canceled"):
            break
        time.sleep(0.05)
    j = runner.get(job.id)
    assert j is not None
    assert j.state == "done"
    assert j.error and "取消" in j.error
    runner.shutdown()
