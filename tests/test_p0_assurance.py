"""P0-B (WP0 H–I, WP5 J) — DR02 assurance fail-closed hardening.

Three proven gaps in the already-shipped DR02 assurance layer, each pinned
red-first (the assertions state the CONTRACT; against the pre-fix code they
fail, proving the gap):

    H  deterministic-QC unavailability (run_qc raised / report unreadable) was
       silently treated as "no policy block" -> a shot whose promises all PASS
       was ACCEPTED even though acceptance could not be confirmed. Contract:
       QC-unavailable is the honest non-accepting state, reason visible.
    I  a v2 verdict payload listing the SAME expectation_id twice (conflicting
       results) was accepted last-one-wins. Contract: the WHOLE intake is
       rejected as payload-invalid with ZERO writes.
    J  expectation-compile failure was swallowed into an empty set -> the shot
       read as `no_explicit_expectations` (a benign, non-blocking state) =
       vacuous acceptance. Contract: surface `expectation_compile_error`, hold
       the shot in a non-accepting state.

These reuse the existing 8-state vocabulary (H and J land on the honest
`unknown` state) and the existing payload-invalid -> zero-write path (I).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess

import pytest

from manju.core.models import TakeSidecar
from manju.qc.agent_review import (
    VerdictError,
    qc_brief,
    read_v2_records,
    record_verdicts,
)
from manju.qc.assurance import assurance_for_all, compute_assurance

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg + ffprobe required (accepted state needs a real, probe-able take)",
)

V2 = "manju.qc.verdict/v2"


# --------------------------------------------------------------- helpers
# (mirror tests/test_dr02_assurance.py + tests/test_dr02_intake.py patterns)


def _select(project, shot_id, take_name):
    project.update_shot_raw(
        shot_id,
        lambda d: d.setdefault("status", {}).__setitem__("selected_take", take_name),
    )


def _real_take(project, shot_id, *, color="red", select=True):
    tmp = project.root / f"_src_{shot_id}_{color}.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"color=c={color}:s=160x120:d=1:r=24", "-pix_fmt", "yuv420p", str(tmp)],
        check=True, capture_output=True,
    )
    take = project.register_take(shot_id, tmp, TakeSidecar(provider="test", spec_hash="h"))
    tmp.unlink()
    if select:
        _select(project, shot_id, take.name)
    return take


def _take_with_bytes(project, shot_id, data: bytes, *, select=True):
    tmp = project.root / f"_src_{shot_id}.mp4"
    tmp.write_bytes(data)
    take = project.register_take(shot_id, tmp, TakeSidecar(provider="test", spec_hash="h"))
    tmp.unlink()
    if select:
        _select(project, shot_id, take.name)
    return take


def _row(project, shot_id):
    return next(r for r in qc_brief(project, [shot_id])["shots"] if r["shot"] == shot_id)


def _v2(row, observed=None, *, findings=None):
    exps = row["expectations"]
    if observed is None:
        observed = ["present"] * len(exps)
    return {
        "schema": V2, "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": row["shot"]},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [{"expectation_id": e["id"], "observed": o}
                         for e, o in zip(exps, observed)],
        "findings": findings or [],
        "reviewer": {"kind": "model_visual", "name": "t"},
    }


def _accepted_shot(project, add_shot, shot_id="S001"):
    add_shot(project, shot_id, quality={"must_show": ["红色雨伞出现"]})
    _real_take(project, shot_id)
    row = _row(project, shot_id)
    record_verdicts(project, _v2(row, ["present"]))
    return row


def _tree_hashes(root):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


# ======================================================================
# H — deterministic-QC unavailability must NOT be a silent pass
# ======================================================================


@needs_ffmpeg
def test_H_qc_unavailable_is_not_accepted(tmp_project, add_shot, monkeypatch):
    """All promises PASS, but the deterministic-QC layer the derivation consumes
    RAISES. Pre-fix it degraded to "no policy error" -> ACCEPTED. Contract: the
    honest non-accepting state, with the QC-unavailable reason visible."""
    _accepted_shot(tmp_project, add_shot)  # S001, every expectation PASS

    # sanity: with QC available this shot IS accepted (isolates the QC axis).
    baseline = compute_assurance(tmp_project, "S001")
    assert baseline["assurance_state"] == "accepted", baseline["reasons"]

    # INJECTION: the deterministic-QC layer raises.
    import manju.qc.checks as checks

    def _boom(*a, **k):
        raise RuntimeError("run_qc exploded (disk/probe/config)")

    monkeypatch.setattr(checks, "run_qc", _boom)

    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] != "accepted", a
    assert a["assurance_state"] == "unknown", a["assurance_state"]
    # the failure reason is VISIBLE in the derived block, never invisible.
    assert a["qc"] == {"status": "unavailable", "reason": "qc_run_failed"}, a["qc"]
    assert any("QC" in r or "unavailable" in r for r in a["reasons"]), a["reasons"]


@needs_ffmpeg
def test_H_bulk_surface_propagates_unavailable(tmp_project, add_shot, monkeypatch):
    """The bulk surface (`assurance_for_all`) shares ONE run_qc across shots; if
    that shared pass fails, every shot's QC is unavailable — never a silent pass
    (and never re-run per shot)."""
    _accepted_shot(tmp_project, add_shot)

    import manju.qc.checks as checks

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("run_qc exploded")

    monkeypatch.setattr(checks, "run_qc", _boom)

    states = {x["subject"]["id"]: x for x in assurance_for_all(tmp_project)}
    assert states["S001"]["assurance_state"] == "unknown"
    assert states["S001"]["qc"]["status"] == "unavailable"
    # one shared attempt, not one-per-shot (unavailability threads via a sentinel)
    assert calls["n"] == 1, calls


# ======================================================================
# I — duplicate expectation_id inside one v2 payload -> reject, zero writes
# ======================================================================


def test_I_duplicate_expectation_id_rejected_zero_write(tmp_project, add_shot):
    """A v2 payload lists the SAME expectation_id twice with CONFLICTING results.
    Pre-fix intake accepted it (assurance.diff() is last-one-wins). Contract: the
    entire intake is rejected as payload-invalid with ZERO writes."""
    add_shot(tmp_project, "S001", quality={"must_show": ["红伞出现"]})
    _take_with_bytes(tmp_project, "S001", b"AAAA-media-bytes")
    row = _row(tmp_project, "S001")
    eid = row["expectations"][0]["id"]

    payload = {
        "schema": V2, "packet_id": row["packet_id"],
        "subject": {"kind": "shot", "id": "S001"},
        "media_sha256": row["media"]["sha256"],
        "spec_hash": row["spec_hash"],
        "expectation_digest": row["expectation_digest"],
        "observations": [
            {"expectation_id": eid, "observed": "present"},
            {"expectation_id": eid, "observed": "absent"},  # conflicting duplicate
        ],
        "findings": [],
        "reviewer": {"kind": "model_visual", "name": "t"},
    }

    before = _tree_hashes(tmp_project.root)
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, payload)
    assert "expectation_id" in str(exc.value)

    # ZERO writes: ledger + packet files byte-identical before/after.
    assert _tree_hashes(tmp_project.root) == before
    assert read_v2_records(tmp_project)[0] == []


def test_I_duplicate_in_batch_rejects_whole_batch(tmp_project, add_shot):
    """The duplicate is in ONE verdict of a batch; the whole batch (including the
    clean sibling verdict) must be rejected with zero writes — all-or-nothing."""
    add_shot(tmp_project, "S001", quality={"must_show": ["A出现"]})
    add_shot(tmp_project, "S002", quality={"must_show": ["B出现"]})
    _take_with_bytes(tmp_project, "S001", b"aaaa-1")
    _take_with_bytes(tmp_project, "S002", b"bbbb-2")
    r1 = _row(tmp_project, "S001")
    r2 = _row(tmp_project, "S002")
    eid = r2["expectations"][0]["id"]

    good = _v2(r1)
    bad = dict(_v2(r2), observations=[
        {"expectation_id": eid, "observed": "present"},
        {"expectation_id": eid, "observed": "uncertain"},  # duplicate
    ])

    before = _tree_hashes(tmp_project.root)
    with pytest.raises(VerdictError) as exc:
        record_verdicts(tmp_project, {"schema": V2, "verdicts": [good, bad]})
    assert "expectation_id" in str(exc.value)
    assert _tree_hashes(tmp_project.root) == before
    assert read_v2_records(tmp_project)[0] == []


# ======================================================================
# J — expectation-compile failure is an error, not a vacuous empty set
# ======================================================================


@needs_ffmpeg
def test_J_expectation_compile_failure_is_error_not_empty_set(
        tmp_project, add_shot, monkeypatch):
    """Compiling the shot's expectations RAISES (malformed quality block, etc.).
    Pre-fix `_safe_compile` swallowed it to {} -> `no_explicit_expectations` (a
    benign, non-blocking state) = vacuous acceptance. Contract: surface
    `expectation_compile_error`, hold the shot in a non-accepting state."""
    add_shot(tmp_project, "S001", quality={"must_show": ["红伞出现"]})
    _real_take(tmp_project, "S001")  # has media -> passes `not_reviewable`

    # INJECTION: expectation compilation raises for this shot.
    import manju.qc.assurance as assurance_mod

    def _boom(project, shot_id):
        raise ValueError("malformed quality block")

    monkeypatch.setattr(assurance_mod, "compile_expectations", _boom)

    a = compute_assurance(tmp_project, "S001")
    assert a["assurance_state"] != "accepted", a
    assert a["assurance_state"] != "no_explicit_expectations", a
    assert a["assurance_state"] == "unknown", a["assurance_state"]
    # the compile error is surfaced on the derived block, never invisible.
    assert a["expectation_compile_error"], a
    assert "malformed quality block" in a["expectation_compile_error"]
    assert any("compil" in r.lower() or "promise" in r.lower() for r in a["reasons"]), \
        a["reasons"]
