"""FINAL_ACCEPTANCE — F1/F2/F3/F5 red-first tests (contract tests 1-12).

F1  malformed paid evidence must fail closed (consult + release gate);
F2  a run_id-less final is RUN_NOT_PROVEN (reverses the H1 skip: the absence
    itself is the signal — no schema marker needed);
F3  a final whose bytes cannot be proven (no output_sha256 / unhashable /
    candidate sha None) is never ready;
F5  valid-baseline+torn-line blocks, baseline path escape never falls back,
    NLE project file without a hash blocks, and the bundle consumes ONE byte
    snapshot (stream-hash-once — checksums/manifest/ZIP agree by construction).

ffmpeg-free / network-free (ScriptedCloud + fabricated finals, the c07/c13
stance). F4/D (CI red — the BuildLock mid-acquire steal) was fixed at f416167
and is deliberately not re-tested here beyond the existing c0911 suite.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from manju.build import attempts as A
from manju.build import baseline as BL
from manju.build import delivery as D
from manju.core.hashing import hash_file
from manju.providers import submission as S
from manju.providers.base import ProviderFailure
from manju.runtime.state import RuntimeState

from tests.test_dr06_admission import ScriptedCloud, _req
from tests.test_c07_baseline import (
    _fab_final,
    _final_key,
    _manual_timeline,
    _seed_completed_run,
)


# --------------------------------------------------------------- helpers


def _seed_chain(project, sid, *pairs, digest="sha256:d", shot="S001",
                provider="cloud_test"):
    prev_digest = None
    prev_state = None
    for to_state, job in pairs:
        rec = A.append_submission_event(
            project, submission_id=sid, request_digest=digest,
            from_state=prev_state, to_state=to_state, provider_id=provider,
            shot=shot, remote_job_id=job, prev_event_digest=prev_digest)
        prev_digest = S.submission_event_digest(rec)
        prev_state = to_state


def _torn_line(project) -> None:
    """One torn/unparseable line in events.jsonl — no sid is recoverable from
    it, so honesty demands it taints the whole submission-evidence stream."""
    with open(project.root / "events.jsonl", "a", encoding="utf-8") as f:
        f.write('{"action": "submission_state", "detail": {TORN-MID-WRITE\n')


def _proven_final(project, *, version=1, run_id="run_fa", data=b"final-bytes"):
    """A run-proven, byte-proven clean current final."""
    tl = _manual_timeline(project)
    _seed_completed_run(project, run_id)
    p = _fab_final(project, _final_key(project, tl), version=version, data=data,
                   run_id=run_id, output_sha256="auto")
    return p, tl


def _codes(assessment):
    return {b["code"] for b in assessment["blockers"]}


# =====================================================================
# F1 — malformed paid evidence (contract tests 1-4)
# =====================================================================


def test_f1_malformed_only_evidence_fresh_sqlite_blocks_transport(
        tmp_project, add_shot):
    """Contract test 1+3: events.jsonl holds ONLY a torn submission line and the
    runtime projection is fresh/empty. RED at HEAD: read_submission_events'
    malformed count is dropped, restore sees zero parseable chains, the consult
    reads that as clean history and a fresh paid submit fires."""
    shot = add_shot(tmp_project, "S001")
    _torn_line(tmp_project)
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0                       # transport 0
    assert exc.value.detail.get("code") == "submission_recovery_unavailable"
    assert exc.value.detail.get("stage") == "evidence_malformed"


def test_f1_valid_terminal_prefix_plus_malformed_tail_blocks(tmp_project, add_shot):
    """Contract test 2: a VALID resolved chain followed by a torn tail. RED at
    HEAD: the terminal chain restores nothing, the torn tail counts for
    nothing, and a fresh paid submit fires past evidence that provably lost at
    least one line."""
    shot = add_shot(tmp_project, "S001")
    _seed_chain(tmp_project, "sub_done", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.ADMITTED, "job_done"), (S.TERMINAL_SUCCESS, None))
    _torn_line(tmp_project)
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_recovery_unavailable"


def test_f1_admitted_resume_with_torn_stream_fail_closes_not_polls(
        tmp_project, add_shot):
    """A resumable ADMITTED row + a torn line elsewhere in the stream: the
    consult must not trust ANY chain read from a stream that provably lost a
    line — fail closed, no poll, no submit."""
    shot = add_shot(tmp_project, "S001")
    provider = ScriptedCloud()
    _identity, digest = provider._build_identity(_req(tmp_project, shot))
    _seed_chain(tmp_project, "sub_ok", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.ADMITTED, "job_resume"), digest=digest)
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_ok",
                       request_digest=digest, state=S.PREPARED)
        st.set_submission_state("sub_ok", S.ADMITTED, remote_job_id="job_resume")
    _torn_line(tmp_project)

    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0 and provider.poll_calls == 0
    assert exc.value.detail.get("code") == "submission_recovery_unavailable"
    assert exc.value.detail.get("stage") == "evidence_malformed"


def test_f1_release_gate_blocks_on_malformed_evidence(tmp_project, add_shot):
    """Contract test 4: the release gate must surface stream corruption as a
    blocking ATTEMPT_EVIDENCE_CORRUPT (or SUBMISSION_RECOVERY_UNAVAILABLE) —
    RED at HEAD: the malformed count is discarded and the gate reads clean."""
    add_shot(tmp_project, "S001")
    _proven_final(tmp_project)
    _torn_line(tmp_project)
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    hit = [b for b in a["blockers"]
           if b["code"] in ("ATTEMPT_EVIDENCE_CORRUPT",
                            "SUBMISSION_RECOVERY_UNAVAILABLE")]
    assert hit and all(b["blocking"] for b in hit)


def test_f1_unpaid_local_project_stays_unblocked(tmp_project, add_shot):
    """F1 guardrail (green before AND after): no submission events, no torn
    lines, no runtime DB — nothing paid ever happened, so the gate stays free
    and a clean run-proven final is ready."""
    add_shot(tmp_project, "S001")
    _proven_final(tmp_project)
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)

    a = BL.release_assessment(tmp_project)
    assert a["blockers"] == []
    assert a["ready"] is True


# =====================================================================
# F2 — run proof (contract test 5)
# =====================================================================


def test_f2_final_without_run_id_is_run_not_proven(tmp_project, add_shot):
    """Contract test 5: a byte-clean final whose sidecar has NO run_id can no
    longer pass silently — blocking RUN_NOT_PROVEN, ready false. RED at HEAD:
    _run_blockers returned [] ("nothing to prove"). This reverses the H1
    SKIPPED_WITH_EVIDENCE: the run_id's absence IS the signal."""
    add_shot(tmp_project, "S001")
    tl = _manual_timeline(tmp_project)
    _fab_final(tmp_project, _final_key(tmp_project, tl), output_sha256="auto")

    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "RUN_NOT_PROVEN" in _codes(a)
    blocker = next(b for b in a["blockers"] if b["code"] == "RUN_NOT_PROVEN")
    assert blocker["blocking"] is True


def test_f2_run_proven_final_stays_ready(tmp_project, add_shot):
    """The sibling of c07 test_23 under F2: run-proven + byte-proven final on a
    clean project is READY — the gate demands proof, not paperwork."""
    add_shot(tmp_project, "S001")
    _proven_final(tmp_project)
    a = BL.release_assessment(tmp_project)
    assert a["blockers"] == [] and a["ready"] is True


def test_f2_legacy_final_approval_requires_human_risk_acceptance(tmp_project, add_shot):
    """F2's legacy/manual escape hatch is HUMAN-ONLY risk acceptance that binds
    the exact final SHA and is appended to the existing verification log —
    never a silent pass."""
    add_shot(tmp_project, "S001")
    tl = _manual_timeline(tmp_project)
    final = _fab_final(tmp_project, _final_key(tmp_project, tl), output_sha256="auto")

    with pytest.raises(BL.BaselineError) as exc:
        BL.approve_baseline(tmp_project, None, reason="legacy import")
    assert "RUN_NOT_PROVEN" in str(exc.value)

    res = BL.approve_baseline(tmp_project, None, reason="legacy import, risk accepted",
                              accept_known_risk=True)
    ev = res["event"]
    assert ev["risk_accepted"] is True
    assert "RUN_NOT_PROVEN" in {b["code"] for b in ev["known_blockers"]}
    assert ev["artifact"]["sha256"] == hash_file(final)     # exact-SHA binding
    # append-only: the event landed in the existing verification log
    lines = (tmp_project.reports_dir / "verifications.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert any(json.loads(l).get("event_id") == ev["event_id"]
               for l in lines if l.strip())


# =====================================================================
# F3 — final byte proof (contract tests 6-8)
# =====================================================================


def test_f3_missing_output_sha256_blocks(tmp_project, add_shot):
    """Contract test 6: a sidecar with NO output_sha256 proves nothing about
    the bytes — blocking, ready false. RED at HEAD: `if recorded:` silently
    skipped the whole check."""
    add_shot(tmp_project, "S001")
    tl = _manual_timeline(tmp_project)
    _seed_completed_run(tmp_project, "run_ns")
    _fab_final(tmp_project, _final_key(tmp_project, tl), run_id="run_ns",
               output_sha256=None)

    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "CURRENT_FINAL_UNVERIFIABLE" in _codes(a)


def test_f3_unreadable_final_bytes_block(tmp_project, add_shot, monkeypatch):
    """Contract test 7: the final's bytes cannot be hashed (read failure) —
    blocking, ready false. RED at HEAD: `actual is None` skipped the check."""
    add_shot(tmp_project, "S001")
    _proven_final(tmp_project)
    monkeypatch.setattr(BL, "_safe_hash", lambda p: None)

    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "CURRENT_FINAL_UNVERIFIABLE" in _codes(a)


def test_f3_candidate_sha_none_is_never_ready(tmp_project, add_shot, monkeypatch):
    """Contract test 8: whatever else holds, a candidate whose sha256 is None
    must not be ready."""
    add_shot(tmp_project, "S001")
    _proven_final(tmp_project)
    monkeypatch.setattr(BL, "_safe_hash", lambda p: None)

    a = BL.release_assessment(tmp_project)
    assert a["candidate"]["sha256"] is None
    assert a["ready"] is False


def test_f3_hash_mismatch_still_blocks_pin(tmp_project, add_shot):
    """Existing behaviour pinned (green before and after): bytes that changed
    after render keep blocking as CURRENT_FINAL_HASH_MISMATCH."""
    add_shot(tmp_project, "S001")
    p, _tl = _proven_final(tmp_project)
    p.write_bytes(b"tampered-after-render")
    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "CURRENT_FINAL_HASH_MISMATCH" in _codes(a)


# =====================================================================
# F5.1 / F5.2 — baseline evidence P1s (contract tests 9-10)
# =====================================================================


def test_f5_valid_baseline_plus_torn_line_release_blocks(tmp_project, add_shot):
    """Contract test 9: a torn line in the verification log blocks the release
    EVEN when a valid baseline event exists — the torn line could BE the
    superseding approval. current_baseline may stay VALID for display; the
    assessment must carry a blocking evidence blocker. RED at HEAD."""
    add_shot(tmp_project, "S001")
    _proven_final(tmp_project)
    BL.approve_baseline(tmp_project, None, reason="baseline")
    assert BL.current_baseline(tmp_project)["status"] == "VALID"

    with open(tmp_project.reports_dir / "verifications.jsonl", "a",
              encoding="utf-8") as f:
        f.write('{"kind": "release_baseline_approved", "target": "final", TORN\n')

    base = BL.current_baseline(tmp_project)
    assert base["status"] == "VALID"                        # display unchanged
    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "BASELINE_EVIDENCE_CORRUPT" in _codes(a)


def test_f5_baseline_path_escape_is_damaged_never_fallback(tmp_project, add_shot):
    """Contract test 10: a baseline event whose artifact path escapes the
    project root must be DAMAGED — never resolved through the raw-join
    fallback. RED at HEAD: `except: abspath = project.root / path` follows the
    escape, and matching outside bytes read back VALID."""
    add_shot(tmp_project, "S001")
    _proven_final(tmp_project)

    outside = tmp_project.root.parent / "outside.bin"
    outside.write_bytes(b"outside-bytes")
    event = {
        "kind": BL.BASELINE_KIND, "target": "final", "ts": "2026-07-11T00:00:00+00:00",
        "artifact": {"path": "../outside.bin", "sha256": hash_file(outside),
                     "bytes": outside.stat().st_size, "final_key": "sha256:k"},
        "source_revision": None, "run_id": None, "assurance_digest": None,
        "technical_report_digest": None, "actor": {"kind": "human"},
        "reason": "escape", "supersedes_event_id": None,
    }
    event["event_id"] = BL._event_id(event)                 # passes self-verification
    vpath = tmp_project.reports_dir / "verifications.jsonl"
    vpath.parent.mkdir(parents=True, exist_ok=True)
    with open(vpath, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

    base = BL.current_baseline(tmp_project)
    assert base["status"] == "DAMAGED"                      # never VALID via escape
    a = BL.release_assessment(tmp_project)
    assert a["ready"] is False
    assert "BASELINE_DAMAGED" in _codes(a)


# =====================================================================
# F5.3 / F5.4 — delivery P1s (contract tests 11-12)
# =====================================================================


def _nle_fixture(tmp_project, add_shot):
    from tests.test_c13_delivery import (
        _fab_final as fab13,
        _final_key as key13,
        _manual_timeline as tl13,
        _register as reg13,
        _write_otio,
    )

    add_shot(tmp_project, "S001")
    tname, src = reg13(tmp_project, "S001")
    from manju.core.models import VideoClip

    tl = tl13(tmp_project, [VideoClip(shot="S001", take=tname, source=src,
                                      start_ms=0, duration_ms=2000)])
    fab13(tmp_project, key13(tmp_project, tl))
    otio = _write_otio(tmp_project, tmp_project.load_config().name)
    return Path(otio)


def test_f5_nle_project_file_without_hash_blocks(tmp_project, add_shot, monkeypatch):
    """Contract test 11: the NLE project file row exists but its bytes cannot
    be hashed → a blocking manifest diagnostic. RED at HEAD: sha256 stayed None
    with no diagnostic at all."""
    otio_abs = _nle_fixture(tmp_project, add_shot)
    real = D.hash_file

    def selective(path, *a, **k):
        if Path(path) == otio_abs:
            raise OSError("unreadable NLE project file")
        return real(path, *a, **k)

    monkeypatch.setattr(D, "hash_file", selective)
    man = D.build_manifest(tmp_project, "master")
    assert man["nle"] is not None
    assert man["nle"]["project_file"]["sha256"] is None
    diag = [d for d in man["diagnostics"] if d["code"] == "NLE_PROJECT_UNVERIFIABLE"]
    assert diag and diag[0]["severity"] == "blocking"


def _c13_clean_manifest(tmp_project, add_shot):
    from tests.test_c13_delivery import _clean_final as clean13, _write_srt

    add_shot(tmp_project, "S001")
    final, _tl = clean13(tmp_project)
    _write_srt(tmp_project)
    man = D.build_manifest(tmp_project, "master")
    return final, man


def test_f5_bundle_members_are_read_exactly_once(tmp_project, add_shot, monkeypatch):
    """Contract test 12 (mechanism): stream-hash-once — every member file is
    opened/read exactly ONCE while its bytes flow into the ZIP, so there is no
    validate→zip window for a concurrent writer. RED at HEAD: each member was
    read three times (CAS revalidate, SHA256SUMS, zip writestr)."""
    final, man = _c13_clean_manifest(tmp_project, add_shot)

    import builtins

    member_paths = {Path(p).resolve() for p in [final]}
    opens: dict[str, int] = {}
    real_open = builtins.open

    def counting_open(file, *a, **k):
        try:
            rp = Path(file).resolve()
            if rp in member_paths:
                opens[str(rp)] = opens.get(str(rp), 0) + 1
        except (TypeError, OSError):
            pass
        return real_open(file, *a, **k)

    monkeypatch.setattr(builtins, "open", counting_open)
    # hash_file also opens — count those too via the same builtins.open seam.
    D.write_bundle(tmp_project, man, output=tmp_project.root / "once.zip")
    monkeypatch.setattr(builtins, "open", real_open)

    assert opens == {str(final.resolve()): 1}, opens        # ONE read per member


def test_f5_mutation_between_manifest_and_bundle_refused_cleanly(
        tmp_project, add_shot):
    """Contract test 12 (between): bytes mutated after the manifest → the write
    refuses with DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST, no new ZIP appears
    and no temp leaks."""
    final, man = _c13_clean_manifest(tmp_project, add_shot)
    final.write_bytes(b"FINAL-BYTES")                       # same length, new bytes
    dest = tmp_project.root / "mut.zip"

    with pytest.raises(D.DeliveryManifestError) as exc:
        D.write_bundle(tmp_project, man, output=dest)
    assert "DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST" in str(exc.value)
    assert not dest.exists()                                # nothing half-written
    assert not list(dest.parent.glob(".mut.zip.tmp-*"))     # no temp leak


def test_f5_racing_writer_never_yields_internally_inconsistent_bundle(
        tmp_project, add_shot, monkeypatch):
    """Contract test 12 (during): a writer that lands right after any
    validation read must either be caught (refusal) or the produced bundle must
    still be internally consistent (SHA256SUMS == embedded manifest == actual
    ZIP bytes). RED at HEAD: the racing write slid between CAS revalidation and
    the zip read — the bundle packed mutated bytes whose SHA256SUMS disagreed
    with the embedded manifest's artifact sha256."""
    final, man = _c13_clean_manifest(tmp_project, add_shot)
    dest = tmp_project.root / "race.zip"
    real = D.hash_file
    state = {"mutated": False}

    def racing(path, *a, **k):
        digest = real(path, *a, **k)
        if Path(path) == final and not state["mutated"]:
            state["mutated"] = True
            final.write_bytes(b"FINAL-BYTES")               # same length, new bytes
        return digest

    monkeypatch.setattr(D, "hash_file", racing)
    try:
        out, _man2 = D.write_bundle(tmp_project, man, output=dest)
    except D.DeliveryManifestError as exc:
        assert "DELIVERY_ARTIFACT_CHANGED_AFTER_MANIFEST" in str(exc.value)
        assert not dest.exists()
        return

    with zipfile.ZipFile(out) as zf:
        sums = {}
        for line in zf.read("SHA256SUMS").decode().splitlines():
            hexd, name = line.split("  ", 1)
            sums[name] = hexd
        embedded = json.loads(zf.read("delivery-manifest.json"))
        for name in zf.namelist():
            if name in ("SHA256SUMS", "delivery-manifest.json"):
                continue
            actual_hex = hashlib.sha256(zf.read(name)).hexdigest()
            assert sums[name] == actual_hex, name           # sums == zip bytes
        for art in embedded.get("artifacts") or []:
            if art.get("path") in sums and art.get("sha256"):
                assert art["sha256"].endswith(sums[art["path"]]), art["path"]
