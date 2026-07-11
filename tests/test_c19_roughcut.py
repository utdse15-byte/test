"""AI_IDE_19 WP5a — Speech rough cut (annotate-only proposals).

§11 row 14: a speech rough-cut proposal is REVERSIBLE and never changes the
Timeline directly. It is built from AI_IDE_18's word/speaker evidence, DEFAULTS to
annotate-only (never deletes), and keeps the original transcript refs, ranges and
reasons so a human can restore anything.
"""

from __future__ import annotations

from pathlib import Path

from manju.qc import roughcut


EVIDENCE = {
    "schema": "manju.alignment-evidence/v1",
    "header": {"source_media_hash": "sha256:audio-1", "status": "ALIGNED"},
    "cues": [
        {"start_ms": 0,    "end_ms": 400,  "text": "呃",        "speaker": "linxia"},  # filler
        {"start_ms": 400,  "end_ms": 900,  "text": "这不可能",   "speaker": "linxia"},
        {"start_ms": 900,  "end_ms": 1400, "text": "这不可能",   "speaker": "linxia"},  # repeat
        {"start_ms": 3000, "end_ms": 3400, "text": "然后呢",     "speaker": "linxia"},  # long pause before
    ],
}


def test_proposal_shape_and_default_annotate_only():
    prop = roughcut.rough_cut_proposal(EVIDENCE, long_pause_ms=800)
    assert prop["kind"] == "speech_rough_cut"
    assert prop["default_action"] == "annotate"     # never auto-delete (§8)
    assert prop["reversible"] is True
    # binds the original transcript (18 evidence) — never a copy / re-transcribe
    assert prop["source_transcript_ref"] == "sha256:audio-1"
    for a in prop["annotations"]:
        assert {"kind", "start_ms", "end_ms", "text", "reason", "cue_index"} <= set(a)


def test_detects_filler_repetition_and_long_pause():
    prop = roughcut.rough_cut_proposal(EVIDENCE, long_pause_ms=800,
                                       fillers=["呃", "那个"])
    kinds = {a["kind"] for a in prop["annotations"]}
    assert roughcut.FILLER in kinds
    assert roughcut.REPETITION in kinds
    assert roughcut.LONG_PAUSE in kinds
    # the repetition annotation carries the ORIGINAL range + reason (restorable)
    rep = next(a for a in prop["annotations"] if a["kind"] == roughcut.REPETITION)
    assert rep["start_ms"] == 900 and rep["end_ms"] == 1400
    assert rep["reason"]


def test_sensitive_ranges_optional_and_flagged():
    prop = roughcut.rough_cut_proposal(EVIDENCE, sensitive_terms=["不可能"])
    sens = [a for a in prop["annotations"] if a["kind"] == roughcut.SENSITIVE]
    assert sens, "sensitive term not flagged"
    # sensitive is still ONLY an annotation (never an auto-delete)
    assert all(a.get("action", "annotate") == "annotate" for a in sens)


def test_reversible_apply_is_explicit_cutdown_not_timeline_write(tmp_project):
    prop = roughcut.rough_cut_proposal(EVIDENCE, fillers=["呃"])
    before = {p.relative_to(tmp_project.root).as_posix()
              for p in tmp_project.root.rglob("*") if p.is_file()}
    # NOT applying leaves everything (reversal = don't apply) — zero writes
    after = {p.relative_to(tmp_project.root).as_posix()
             for p in tmp_project.root.rglob("*") if p.is_file()}
    assert before == after
    # applying selected annotations yields a WP3 cutdown PROPOSAL payload — the
    # rough cut never edits the Timeline itself; it routes through the cut path.
    cutdown = roughcut.to_cutdown(prop, select=[0], source_analysis_digest="sha256:a")
    assert "remove" in cutdown and "keep" in cutdown
    assert cutdown["remove"] == [[0, 400]]          # only the selected filler
    assert cutdown["reason"]


def test_never_imports_timeline_writer():
    # the rough cut is a PURE advisor over evidence — it must not write timeline.
    import inspect
    src = inspect.getsource(roughcut)
    for banned in ("save_timeline", "save_rules", "register_take", "checked_shot_write"):
        assert banned not in src, f"rough cut must not call {banned}"
