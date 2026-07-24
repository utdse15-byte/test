"""Sampled-P1 spot fixes from the merged external audit.

Three fail-open shapes on the tool's OWN data path (no adversary needed):
EVENTS-P1-003 (a huge-int line bricked the whole events tail),
RUNMANIFEST-P1-004 (an output row with no recorded hash read as verified),
VOICE-P1-002 (duplicate ids in an explicit batch list became duplicate paid
generations — same shape pinned for the redo batch).
"""

from __future__ import annotations

import json
from pathlib import Path

from manju.build import attempts as A
from manju.core.events import append_event, tail_events


def test_huge_int_line_never_bricks_the_events_tail(tmp_path: Path) -> None:
    append_event(tmp_path, "human", "note", {"ok": 1})
    with open(tmp_path / "events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"n": ' + "9" * 5000 + "}\n")  # ValueError, not JSONDecodeError
    append_event(tmp_path, "human", "note", {"ok": 2})
    records = tail_events(tmp_path)
    assert [r["detail"]["ok"] for r in records if r.get("action") == "note"] == [1, 2]


def test_output_row_without_recorded_hash_is_unverifiable_not_verified(tmp_path: Path) -> None:
    run_id = "run_unrec"
    rel = "renders/out.mp4"
    (tmp_path / "renders").mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"payload")
    ev = A.RunEvidence(tmp_path, run_id)
    # a hand-written/torn manifest row can carry a path with no hash — the
    # typed output_ref API cannot produce this, which is exactly why the
    # verifier must not silently pass it
    ev.attempt("render", {"kind": "render"}, "render").succeeded(
        outputs=[{"role": "final", "path": rel}])
    ev.run_succeeded()
    mism = A.verify_outputs(tmp_path, run_id)
    assert len(mism) == 1
    assert mism[0]["path"] == rel
    assert mism[0]["expected_sha256"] == "<unrecorded>"
    assert mism[0]["actual_sha256"] == "<unverifiable>"


def test_explicit_batch_shot_lists_are_deduped_order_preserving() -> None:
    # the two batch entry points share the selector shape; pin the de-dup at
    # the source-text level AND the semantic level via the helper expression
    src = Path(A.__file__).parent.joinpath("graph.py").read_text(encoding="utf-8")
    assert src.count('considered = list(dict.fromkeys(shots)) if mode == "shots"') == 2
    assert list(dict.fromkeys(["S001", "S002", "S001"])) == ["S001", "S002"]
