"""AI_IDE_09_11G — the anti-overbuild gates, run honestly.

Part A (09 resume): can a FRESH agent, after a crash/restart, safely take over
using only the EXISTING read-only surfaces (status/tasks/exports/events/
agent_surface/director)? These are characterization tests — they prove the
current surfaces answer every §4/§6 safety question, so the ResumeCapsule /
SkillLock / status-resume-section candidates stay SKIPPED/REJECTED with this
file as the evidence.

Part B (11 single-host): do two independent OS processes ever double-own paid
work (G11-1), does the limiter cap hold (G11-2), is a crash explainable and
recoverable (G11-3), and is cancellation honest (G11-4)? The two-process
fixtures use real ``subprocess`` children synchronized by a file barrier —
SQLite CAS and O_EXCL are only meaningful claims across real processes.

Everything is ffmpeg-free and network-free (ScriptedCloud from the DR06
sandbox). No test here writes production code expectations that do not already
hold at HEAD — a RED here would be the contract's trigger for a narrow fix.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from manju.build import attempts as A
from manju.build import director as D
from manju.build.attempts import build_run_manifest, materialize_run_manifest
from manju.build.shotpackage import project_revision
from manju.cli import app
from manju.core.events import tail_events
from manju.mcp import policy as P
from manju.mcp.tools import TOOL_DEFS
from manju.providers import submission as S
from manju.providers.base import ProviderCanceled, ProviderFailure
from manju.runtime.state import RuntimeState
from tests.test_dr06_admission import ScriptedCloud, _req, _states

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ helpers


def _cli(project, *args):
    """Run one read-only CLI command from inside the project (the fresh-agent
    transcript primitive).

    Uses ``pytest.MonkeyPatch.context()`` (the fixture's context-manager form,
    since a module-level helper cannot take the ``monkeypatch`` fixture arg) so
    the cwd is always restored on exit — even if ``invoke`` raises — and never
    leaks into a sibling test under ``pytest -n auto``."""
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(project.root)
        return runner.invoke(app, list(args))


def _cli_json(project, *args):
    res = _cli(project, *args)
    assert res.exit_code == 0, res.output
    return json.loads(res.output)


def _seed_interrupted_run(project, run_id="run_killed"):
    """A run that STARTED and then hard-crashed: lifecycle start + one attempt
    start, no terminals of any kind (the exact WP0-G shape)."""
    A.append_run_started(project, run_id, target="final", gen="missing")
    A.append_attempt_started(project, run_id, "att_lost", stage="generate",
                             provider="cloud_test")
    return run_id


def _seed_unknown_submission(project, sid="sub_unknown", shot="S001"):
    with RuntimeState(project.root) as st:
        st.open_intent(provider="cloud_test", shot=shot, submission_id=sid,
                       request_digest="sha256:d", state=S.PREPARED)
        st.set_submission_state(sid, S.OUTCOME_UNKNOWN)
    return sid


def _seed_chain(project, sid, *pairs, digest="sha256:d", shot="S001"):
    """Append a VALID hash chain (from tests/test_p0_recovery.py's pattern)."""
    prev_digest = None
    prev_state = None
    for to_state, job in pairs:
        rec = A.append_submission_event(
            project, submission_id=sid, request_digest=digest,
            from_state=prev_state, to_state=to_state, provider_id="cloud_test",
            shot=shot, remote_job_id=job, prev_event_digest=prev_digest)
        prev_digest = S.submission_event_digest(rec)
        prev_state = to_state


def _spawn(script_path, *argv):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, str(script_path), *[str(a) for a in argv]],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _finish(proc, timeout=60):
    out, err = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, f"child failed: {err or out}"
    return out.strip().splitlines()


# =====================================================================
# Part A — 09 resume gate (§4 experiment + §6 tests 1-10)
# =====================================================================


def test_a0_resume_transcript_read_only_calls_answer_every_safety_question(
        tmp_project, add_shot):
    """§4 gate experiment — THE interrupted-session transcript.

    World: a run was hard-killed mid-generate (no terminals), it left an
    unresolved paid submission (outcome unknown), a stale build lock from the
    dead process, and no final yet. A FRESH agent now takes over using ONLY
    read-only calls. Transcript (recorded for the baseline report):

        1. manju status  --json      → lock holder, run ledger, next_step
        2. manju tasks   --json      → unresolved submission + recovery actions
        3. manju events  --json      → the interrupted run's run_id
        4. manju tasks manifest <id> --json → INCOMPLETE + dangling attempt
        5. manju exports --json      → release blockers + ToolPolicy next actions

    Verdict the test enforces: after these five READ-ONLY calls every §4.4
    safety risk is decided — no mis-resubmit, no completed-misjudgment, no
    stale-proposal continuation, surface digest available. The §4 trigger
    ("even after these calls the next safe action is undecidable") does NOT
    hold, so 09 stays SKIPPED_WITH_EVIDENCE."""
    add_shot(tmp_project, "S001")
    run_id = _seed_interrupted_run(tmp_project)
    _seed_unknown_submission(tmp_project)
    # the dead process also left its build lock behind
    lock = tmp_project.runtime_dir / "build.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 99999999, "actor": "ai",
                                "started": "2026-07-11T00:00:00+00:00",
                                "hostname": "dead-host"}), encoding="utf-8")

    # -- call 1: status --json ------------------------------------------------
    status = _cli_json(tmp_project, "status", "--json")
    assert status["build_lock"] is not None          # the crash is visible
    assert status["build_lock"]["pid"] == 99999999
    assert status["latest_final"] is None            # nothing pretends done
    assert status["next_step"]                       # a suggested step exists

    # -- call 2: tasks --json -------------------------------------------------
    tasks = _cli_json(tmp_project, "tasks", "--json")
    unresolved = tasks["unresolved_submissions"]
    assert [u["submission_id"] for u in unresolved] == ["sub_unknown"]
    u = unresolved[0]
    assert u["automatic_resubmit"] is False          # mis-resubmit risk: decided
    assert u["possible_remote_side_effect"] is True
    assert set(u["actions"]) == {"attach_remote_job", "abandon_with_duplicate_risk"}

    # -- call 3: events --json (run_id discovery, read-only) ------------------
    events = _cli_json(tmp_project, "events", "--json")
    started = [e for e in events if e.get("action") == "run_started"]
    assert started and started[-1]["detail"]["run_id"] == run_id

    # -- call 4: tasks manifest <run_id> --json --------------------------------
    manifest = _cli_json(tmp_project, "tasks", "manifest", run_id, "--json")["manifest"]
    assert manifest["terminal_status"] == "INCOMPLETE"   # never mis-completed
    assert manifest["dangling_attempts"] == [
        {"attempt_id": "att_lost", "stage": "generate", "provider": "cloud_test"}]

    # -- call 5: exports --json (composed blockers + next safe actions) -------
    exports = _cli_json(tmp_project, "exports", "--json")
    ra = exports["release_assessment"]
    codes = {b["code"] for b in ra["blockers"]}
    assert "SUBMISSION_OUTCOME_UNKNOWN" in codes
    assert "CURRENT_FINAL_MISSING" in codes
    assert ra["ready"] is False
    for act in ra["next_actions"]:                   # every action policy-stamped
        assert {"command", "fix_owner", "safe_to_auto_run",
                "requires_confirmation", "may_network", "may_spend"} <= set(act)
    sub_cmds = [a for a in ra["next_actions"]
                if a["reason_code"] == "SUBMISSION_OUTCOME_UNKNOWN"]
    assert sub_cmds and sub_cmds[0]["command"] == "manju tasks"
    assert sub_cmds[0]["safe_to_auto_run"] is False  # human resolves unknowns

    # surface digest is one more read-only call away (agent_surface) — pure.
    digest = P.resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest()
    assert digest.startswith("sha256:")


def test_a1_interrupted_run_recognized_after_restart(tmp_project, add_shot):
    """§6.1: a process restart cannot mis-read a killed run as completed — the
    manifest derives INCOMPLETE from evidence alone (no in-memory state)."""
    add_shot(tmp_project, "S001")
    run_id = _seed_interrupted_run(tmp_project, "run_a1")
    m = build_run_manifest(tmp_project, run_id)      # a FRESH reader, any process
    assert m["terminal_status"] == "INCOMPLETE"
    assert m["dangling_attempts"]
    # and a run nobody ever saw is NOT_FOUND — never guessed from files on disk
    assert build_run_manifest(tmp_project, "run_never")["terminal_status"] == "NOT_FOUND"


def test_a2_unresolved_submission_blocks_before_any_build_or_redo(tmp_project, add_shot):
    """§6.2: the unresolved paid submission out-prioritizes building — the
    ENGINE refuses a fresh paid submit (transport 0) regardless of what a
    resuming agent chooses, and both read surfaces say so."""
    shot = add_shot(tmp_project, "S001")
    _seed_unknown_submission(tmp_project)

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0                # no paid transport
    assert exc.value.detail.get("code") == "submission_outcome_unknown"
    assert exc.value.detail.get("automatic_resubmit") is False

    ra = _cli_json(tmp_project, "exports", "--json")["release_assessment"]
    blocker = next(b for b in ra["blockers"]
                   if b["code"] == "SUBMISSION_OUTCOME_UNKNOWN")
    assert blocker["blocking"] is True and blocker["scope"] == "shot:S001"


def test_a3_stale_proposal_is_not_an_executable_next_action(tmp_project, add_shot):
    """§6.3: a proposal reasoned against an older project state EXPIRES at
    confirm — a resuming agent cannot continue on it, by engine refusal."""
    add_shot(tmp_project, "S001")
    proposal = D.propose(tmp_project, [{"type": "snapshot", "label": "before"}],
                         why="resume test", actor="human")
    add_shot(tmp_project, "S002")                    # the project moves on
    with pytest.raises(D.DirectorError, match="expired"):
        D.confirm(tmp_project, proposal.id, actor="human")
    reloaded = D.load_proposal(tmp_project, proposal.id)
    assert reloaded.state == "expired"               # honestly recorded, kept


def test_a4_surface_digest_visible_moves_on_tools_and_not_on_skills(tmp_project):
    """§6.4: the tool-surface digest is read-only-visible and moves when a tool
    POLICY changes. Skill FILE content is deliberately outside the digest —
    skills are readable content (skill_list/skill_show), not tool policy; this
    pin is the §5.2 evidence that no recovery error follows from that split
    (REJECTED_WITH_REASON for SkillLock; no reproduced mis-execution)."""
    base = P.resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest()
    assert base == P.resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest()

    # a REAL policy change moves the digest…
    import copy

    changed = copy.deepcopy(TOOL_DEFS)
    for t in changed:
        if t["name"] == "status":
            t["policy"]["unattended"] = P.DENY
    assert P.resolve_agent_surface(changed, P.COLLABORATIVE).digest() != base

    # …while a skill file changing does not (documented split, not a bug):
    skills_dir = tmp_project.root / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "SKILL.md").write_text("# drifted", encoding="utf-8")
    assert P.resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).digest() == base


def test_a5_source_revision_is_accurate_and_read_only(tmp_project, add_shot):
    """§6.5: the current source revision moves exactly when source moves."""
    add_shot(tmp_project, "S001")
    r1 = project_revision(tmp_project)
    assert r1.startswith("sha256:")
    assert project_revision(tmp_project) == r1       # stable across reads
    add_shot(tmp_project, "S002")
    assert project_revision(tmp_project) != r1       # moves with the index


def test_a6_resume_read_set_is_pure_no_writes_no_network_no_spend(
        tmp_project, add_shot):
    """§6.6: the full takeover read set mutates nothing. The run ledger is
    pre-created (a real interrupted project has one); after that, five reads
    leave the tree byte-set identical and no cloud transport exists at all."""
    add_shot(tmp_project, "S001")
    _seed_interrupted_run(tmp_project)
    _seed_unknown_submission(tmp_project)
    _cli_json(tmp_project, "status", "--json")       # settle any lazy init once

    before = {str(p) for p in tmp_project.root.rglob("*")}
    _cli_json(tmp_project, "status", "--json")
    _cli_json(tmp_project, "tasks", "--json")
    _cli_json(tmp_project, "events", "--json")
    _cli_json(tmp_project, "exports", "--json")
    tail_events(tmp_project.root, 50)
    P.resolve_agent_surface(TOOL_DEFS, P.COLLABORATIVE).manifest()
    after = {str(p) for p in tmp_project.root.rglob("*")}
    assert before == after


def test_a7_next_actions_carry_toolpolicy_truth(tmp_project):
    """§6.7: next-action safety metadata equals the declared ToolPolicy —
    never a hand-written table (pinned for the resume read too)."""
    ra = _cli_json(tmp_project, "exports", "--json")["release_assessment"]
    build_actions = [a for a in ra["next_actions"] if a.get("tool") == "build"]
    assert build_actions, "a missing final must map to the build tool"
    pol = {t["name"]: t["policy"] for t in TOOL_DEFS}["build"]
    act = build_actions[0]
    assert act["may_spend"] == (pol["spend"] != P.NEVER)
    assert act["may_network"] == (pol["network"] != P.NEVER)
    assert act["safe_to_auto_run"] is False and act["fix_owner"] == "human"


def test_a8_missing_or_corrupt_evidence_fails_closed_on_resume(tmp_project, add_shot):
    """§6.8: a corrupt submission chain surfaces as corrupt on the read side
    AND fail-closes the paid path — resume never trusts broken evidence."""
    shot = add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_bad",
                       request_digest="sha256:d", state=S.PREPARED)
        st.set_submission_state("sub_bad", S.DISPATCHING)
    # one chain-breaking event (bad prev digest)
    A.append_submission_event(tmp_project, submission_id="sub_bad",
                              request_digest="sha256:d", from_state=None,
                              to_state=S.DISPATCHING, provider_id="cloud_test",
                              shot="S001", prev_event_digest="sha256:bogus")

    tasks = _cli_json(tmp_project, "tasks", "--json")
    row = next(u for u in tasks["unresolved_submissions"]
               if u["submission_id"] == "sub_bad")
    assert row["evidence_chain_ok"] is False         # visible, never hidden

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "RECOVERY_EVIDENCE_CORRUPT"


def test_a9_derived_projections_are_deletable_without_losing_resume(tmp_project):
    """§6.9: the RunManifest file is a DERIVED projection — delete it and the
    same truth re-derives from evidence; resume never depended on the file."""
    run_id = _seed_interrupted_run(tmp_project, "run_a9")
    path = materialize_run_manifest(tmp_project, run_id)
    assert path.exists()
    first = json.loads(path.read_text(encoding="utf-8"))["terminal_status"]
    path.unlink()                                    # deleting costs nothing
    assert build_run_manifest(tmp_project, run_id)["terminal_status"] == first == "INCOMPLETE"


def test_a10_status_json_shape_is_backward_compatible(tmp_project, add_shot):
    """§6.10: the takeover payload still carries every pre-0911 key an old
    client reads — this batch adds NOTHING, so the pin is the whole proof."""
    add_shot(tmp_project, "S001")
    status = _cli_json(tmp_project, "status", "--json")
    assert {"project", "mode", "resolution", "shots_total", "shots_by_state",
            "timeline", "latest_final", "qc", "total_cost", "currency",
            "run_log", "budget_limit", "recent_events", "next_step",
            "build_lock", "failures_since_build"} <= set(status)


# =====================================================================
# Part B — 11 single-host gate (§8 G11-1..5, §10 tests 1-15)
# =====================================================================


_CLAIM_SCRIPT = """
import sys, time
from pathlib import Path
from manju.runtime.state import RuntimeState

root, barrier = Path(sys.argv[1]), Path(sys.argv[2])
deadline = time.monotonic() + 15
while not barrier.exists():
    if time.monotonic() > deadline:
        print("BARRIER_TIMEOUT"); sys.exit(0)
    time.sleep(0.005)
with RuntimeState(root) as st:
    try:
        won = st.claim_dispatching("sub_race")
    except Exception as exc:  # a locked write refuses -> fail-closed, no submit
        print("ERROR_FAIL_CLOSED", type(exc).__name__); sys.exit(0)
print("CLAIMED" if won else "LOST")
"""


def test_b1_g11_1_two_processes_same_submission_exactly_one_claims(tmp_project):
    """G11-1 / §10.1 — two INDEPENDENT OS processes race the DISPATCHING claim
    for the SAME submission identity. The SQLite CAS admits exactly one; the
    loser fail-closes before any transport. Transport count for the identity
    can therefore never reach 2. GREEN at HEAD → no ExecutionAdmission."""
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_race",
                       request_digest="sha256:d", state=S.PREPARED)

    script = tmp_project.root / "_claim_race.py"
    script.write_text(_CLAIM_SCRIPT, encoding="utf-8")
    barrier = tmp_project.root / "_go"
    p1 = _spawn(script, tmp_project.root, barrier)
    p2 = _spawn(script, tmp_project.root, barrier)
    time.sleep(0.3)                                  # both children at the barrier
    barrier.write_text("go")
    out1, out2 = _finish(p1), _finish(p2)

    verdicts = [out1[-1].split()[0], out2[-1].split()[0]]
    assert verdicts.count("CLAIMED") == 1, verdicts  # exactly one owner
    assert set(verdicts) <= {"CLAIMED", "LOST", "ERROR_FAIL_CLOSED"}
    with RuntimeState(tmp_project.root) as st:
        row = st.get_submission("sub_race")
    assert row["state"] == S.DISPATCHING             # single transition applied


_RESUME_SCRIPT = """
import sys
from pathlib import Path
from manju.core.container import Project
from tests.test_dr06_admission import ScriptedCloud, _req

root = Path(sys.argv[1])
project = Project(root)
shot = project.load_shot("S001")
provider = ScriptedCloud()
takes = provider.generate(_req(project, shot))
print("SUBMITS", provider.submit_calls)
print("POLLS", provider.poll_calls)
print("TAKES", len(takes))
"""


def test_b2_g11_1_second_process_resumes_admitted_job_poll_only(tmp_project, add_shot):
    """G11-1 / §10.6 — process A crashed after ADMITTED (job id on record). A
    SECOND OS process running the same generate RESUMES the job poll-only:
    its transport submit count is 0 and the SAME submission closes. Known
    remote work is never resubmitted across processes. GREEN at HEAD."""
    shot = add_shot(tmp_project, "S001")
    provider = ScriptedCloud()
    _identity, digest = provider._build_identity(_req(tmp_project, shot))
    _seed_chain(tmp_project, "sub_ok", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.ADMITTED, "job_resume"), digest=digest)
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_ok",
                       request_digest=digest, state=S.PREPARED)
        st.set_submission_state("sub_ok", S.ADMITTED, remote_job_id="job_resume")

    script = tmp_project.root / "_resume.py"
    script.write_text(_RESUME_SCRIPT, encoding="utf-8")
    lines = _finish(_spawn(script, tmp_project.root))
    facts = dict(l.split() for l in lines)
    assert facts["SUBMITS"] == "0"                   # poll-only in the 2nd process
    assert int(facts["POLLS"]) >= 1 and facts["TAKES"] == "1"
    assert _states(tmp_project, "sub_ok")[-1] == S.TERMINAL_SUCCESS


def test_b3_g11_1_dispatching_owned_elsewhere_fail_closes_here(tmp_project, add_shot):
    """G11-1 (the consult layer) — a DISPATCHING row owned by another process
    is side-effect ambiguous: a concurrent generate here refuses (transport 0)
    instead of double-submitting past it."""
    shot = add_shot(tmp_project, "S001")
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_mid",
                       request_digest="sha256:d", state=S.PREPARED)
        st.set_submission_state("sub_mid", S.DISPATCHING)
    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("code") == "submission_outcome_unknown"


_LOCK_SCRIPT = """
import sys, time
from pathlib import Path
from manju.runtime.buildlock import BuildLock, BuildLocked

root, barrier = Path(sys.argv[1]), Path(sys.argv[2])
deadline = time.monotonic() + 15
while not barrier.exists():
    if time.monotonic() > deadline:
        print("BARRIER_TIMEOUT"); sys.exit(0)
    time.sleep(0.005)
lock = BuildLock(root, actor="ai")
try:
    lock.acquire()
except BuildLocked:
    print("LOCKED"); sys.exit(0)
t_acq = time.time()          # wall clock: comparable across sibling processes
time.sleep(0.4)              # hold, attempting real overlap with the sibling
lock.release()
print(f"ACQUIRED {t_acq:.6f} {time.time():.6f}")
"""


def test_b4_g11_5_two_processes_race_build_lock_single_owner(tmp_project):
    """G11-5 — the dual-ownership probe: two OS processes race the project
    build lock simultaneously. THE invariant is that the two hold intervals
    NEVER OVERLAP (an overlap is dual ownership). Under CI scheduling
    starvation the loser may legitimately acquire AFTER the winner released
    (sequential ACQUIRED/ACQUIRED) — that is not dual ownership, and the old
    assertion that exactly one process must lose was what made this test
    flake red on GitHub CI while ALSO exposing a real steal race (b16).
    Lease/fencing stays REJECTED_WITH_REASON: the lock itself, fixed, is the
    single-owner mechanism."""
    script = tmp_project.root / "_lock_race.py"
    script.write_text(_LOCK_SCRIPT, encoding="utf-8")
    barrier = tmp_project.root / "_go_lock"
    p1 = _spawn(script, tmp_project.root, barrier)
    p2 = _spawn(script, tmp_project.root, barrier)
    time.sleep(0.3)
    barrier.write_text("go")
    out1, out2 = _finish(p1), _finish(p2)
    lines = sorted([out1[-1], out2[-1]])
    assert "BARRIER_TIMEOUT" not in lines, lines
    acquired = [l for l in lines if l.startswith("ACQUIRED")]
    assert acquired, lines                       # at least one always wins
    if len(acquired) == 1:
        assert lines.count("LOCKED") == 1, lines  # the simultaneous case
    else:
        # sequential double-acquire: legal IFF the hold intervals are disjoint
        spans = sorted(tuple(map(float, l.split()[1:3])) for l in acquired)
        (a_start, a_end), (b_start, b_end) = spans
        assert a_end <= b_start, (
            f"DUAL OWNERSHIP: hold intervals overlap "
            f"[{a_start:.6f},{a_end:.6f}] vs [{b_start:.6f},{b_end:.6f}]")


def test_b5_g11_2_limiter_cap_holds_under_concurrency(monkeypatch):
    """G11-2 / §10.2 — a manifest max_concurrent=2 is a HARD cap: 8 concurrent
    workers through the shared per-provider semaphore never exceed 2 in
    flight, and all 8 complete. GREEN at HEAD → limiter untouched."""
    from manju.build import graph as G

    class _Limits:
        max_concurrent = 2

    class _Manifest:
        limits = _Limits()

    monkeypatch.setattr("manju.providers.registry.get_manifest",
                        lambda name: _Manifest())
    cache: dict = {}
    sem = G._provider_semaphore("cloud_capped", cache)
    assert sem is not None
    assert G._provider_semaphore("cloud_capped", cache) is sem  # shared object

    in_flight = 0
    peak = 0
    guard = threading.Lock()
    done = []

    def work(i):
        nonlocal in_flight, peak
        with sem:
            with guard:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.05)
            with guard:
                in_flight -= 1
        done.append(i)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak <= 2 and len(done) == 8


def test_b5b_g11_2_limiter_releases_after_exception_no_leak(monkeypatch):
    """§10.4 — a worker raising INSIDE the gated section releases its permit
    (context-manager release); subsequent workers still acquire. No leak."""
    from manju.build import graph as G

    class _Limits:
        max_concurrent = 1

    class _Manifest:
        limits = _Limits()

    monkeypatch.setattr("manju.providers.registry.get_manifest",
                        lambda name: _Manifest())
    sem = G._provider_semaphore("cloud_leaky", {})

    def boom():
        with sem:
            raise RuntimeError("provider exploded")

    for _ in range(3):
        with pytest.raises(RuntimeError):
            boom()
    acquired = sem.acquire(timeout=1.0)              # still free after 3 failures
    assert acquired
    sem.release()


def test_b5c_g11_2_local_and_unknown_providers_are_uncapped_by_design(monkeypatch):
    """The limiter reads the 04 manifest as the ONE authority: a provider with
    no manifest (every built-in local) has no vendor permit at all — cached /
    local work never contends with cloud dispatch slots."""
    from manju.build import graph as G

    monkeypatch.setattr("manju.providers.registry.get_manifest",
                        lambda name: (_ for _ in ()).throw(KeyError(name)))
    assert G._provider_semaphore("kenburns", {}) is None
    assert G._provider_semaphore(None, {}) is None


def test_b6_g11_2_cache_hit_never_enters_the_dispatch_plan(tmp_project, add_shot, make_take):
    """§10.3 — a shot whose selected take already exists is not MISSING, so it
    never enters the generation plan at all: a cache hit cannot consume a
    vendor dispatch permit by construction."""
    from manju.build.graph import _plan_generation
    from manju.build.stale import evaluate_all
    from manju.core.spec import compute_spec_hash

    done = add_shot(tmp_project, "S001")
    add_shot(tmp_project, "S002")                    # stays missing
    h = compute_spec_hash(done, tmp_project.load_bible())
    info = make_take(tmp_project, "S001", h)
    tmp_project.update_shot_raw(
        "S001", lambda d: d.setdefault("status", {}).__setitem__("selected_take", info.name))

    statuses = evaluate_all(tmp_project)
    plan = _plan_generation(tmp_project, statuses, gen="missing", regen_stale=False)
    assert [p["shot"] for p in plan] == ["S002"]     # the cached shot is absent


def test_b7_g11_3_process_gone_is_never_success_and_final_honestly_flagged(
        tmp_project, add_shot):
    """§10.5 — after a hard kill: the run is INCOMPLETE (A1), and a final
    missing its key sidecar (crash-truncated render) is flagged at the
    takeover surface instead of presented as the finished 成片."""
    add_shot(tmp_project, "S001")
    _seed_interrupted_run(tmp_project, "run_b7")
    tmp_project.final_dir.mkdir(parents=True, exist_ok=True)
    (tmp_project.final_dir / "final_v1.mp4").write_bytes(b"truncated")  # no .key.json

    status = _cli_json(tmp_project, "status", "--json")
    assert status["latest_final"] is not None
    assert "incomplete" in (status["latest_final_note"] or "")
    assert build_run_manifest(tmp_project, "run_b7")["terminal_status"] == "INCOMPLETE"


def test_b8_g11_3_classification_survives_ledger_loss_and_rebuild(tmp_project, add_shot):
    """§10.12 — delete the ENTIRE disposable runtime dir; rebuild restores the
    same OUTCOME_UNKNOWN classification from the evidence chain, and the paid
    path still refuses. Crash reconciliation is derived, not persisted twice."""
    import shutil

    shot = add_shot(tmp_project, "S001")
    _seed_chain(tmp_project, "sub_lost", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.OUTCOME_UNKNOWN, None))
    shutil.rmtree(tmp_project.root / ".manju")

    with RuntimeState(tmp_project.root) as st:
        st.rebuild(tmp_project)
        rows = st.unresolved_submissions()
    assert [r["submission_id"] for r in rows] == ["sub_lost"]
    assert rows[0]["state"] == S.OUTCOME_UNKNOWN     # identical classification

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure):
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0


def test_b9_g11_4_cancel_before_dispatch_has_no_remote_side_effect(tmp_project, add_shot):
    """§10.8 — a cancel that trips BEFORE any dispatch: the build unwinds as
    canceled (never a failure), NO submission evidence exists at all, and the
    run terminal records remote_may_continue=False."""
    from manju.build.graph import run_build

    add_shot(tmp_project, "S001")
    res = run_build(tmp_project, target="final", should_cancel=lambda: True)
    assert res.ok is False and res.canceled is True

    assert _states(tmp_project) == []                # zero submission events
    events = tail_events(tmp_project.root, 200)
    cancels = [e for e in events if e.get("action") == "stage_attempt"
               and (e.get("detail") or {}).get("stage") == "build"
               and (e.get("detail") or {}).get("state") == "CANCELED"]
    assert cancels, "the canceled run terminal must be on evidence"
    decision = (cancels[-1]["detail"].get("decision") or {})
    assert decision.get("remote_may_continue") is False


def test_b10_g11_4_stop_polling_is_not_remote_cancel(tmp_project, add_shot):
    """§10.10/11 — canceling a poll STOPS WAITING only: the exception says the
    remote may continue billing, the submission stays ADMITTED (never a
    terminal 'canceled'), and the next generate resumes poll-only. Nothing
    anywhere claims REMOTE_CANCEL_CONFIRMED."""
    shot = add_shot(tmp_project, "S001")

    running = ScriptedCloud(poll_result=("running", {}))
    req = _req(tmp_project, shot)
    req.should_cancel = lambda: True                 # trip at the first poll gap
    with pytest.raises(ProviderCanceled) as exc:
        running.generate(req)
    assert running.submit_calls == 1                 # it DID dispatch once
    assert "可能仍在进行" in str(exc.value)          # remote-may-continue, in words
    assert _states(tmp_project)[-1] == S.ADMITTED    # NOT canceled-final
    with RuntimeState(tmp_project.root) as st:
        row = st.unresolved_submissions()[0]
    assert row["state"] == S.ADMITTED and row["remote_job_id"] == "job_fresh_1"

    resumed = ScriptedCloud()                        # a later build picks it up
    takes = resumed.generate(_req(tmp_project, shot))
    assert takes and resumed.submit_calls == 0       # resumed, never resubmitted


def test_b11_g11_4_no_remote_cancel_hook_and_cli_refuses_to_pretend(tmp_project):
    """G11-4 audit pin — there IS no provider remote-cancel hook in the
    codebase, so a REMOTE_CANCEL_CONFIRMED state would be unrecordable truth:
    REJECTED_WITH_REASON. The CLI cancel honestly refuses instead of faking a
    CANCELED_FINAL."""
    from manju.providers.base import CloudProvider
    from manju.providers.generic_cloud import GenericCloudProvider

    assert not hasattr(CloudProvider, "cancel")
    assert not hasattr(GenericCloudProvider, "cancel")

    res = _cli(tmp_project, "tasks", "cancel", "1")
    assert res.exit_code != 0                        # refuses, never pretends
    assert "GUI" in res.output


def test_b12_g11_shared_ids_across_tasks_manifest_failures(tmp_project, add_shot):
    """§10.13 — one failed cloud attempt is ONE identity everywhere: the
    ledger row's failure_id resolves in reports/failures.jsonl and its
    submission chain carries the same submission_id tasks shows."""
    shot = add_shot(tmp_project, "S001")
    provider = ScriptedCloud(
        poll_result=("failed", {"failure_kind": "provider_error", "reason": "boom"}))
    with pytest.raises(ProviderFailure):
        provider.generate(_req(tmp_project, shot))

    with RuntimeState(tmp_project.root) as st:
        runs = st.run_log(5)
    failed = [r for r in runs if r.get("status") == "failed"]
    assert failed and failed[0].get("failure_id")
    failures_file = tmp_project.reports_dir / "failures.jsonl"
    recorded_ids = [json.loads(l).get("id")
                    for l in failures_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert failed[0]["failure_id"] in recorded_ids   # same identity, both surfaces

    chain_sids = {e.get("submission_id") for e in A.read_submission_events(tmp_project)[0]}
    assert failed[0].get("remote_job_id") == "job_fresh_1"
    assert len(chain_sids) == 1                      # one submission identity total


def test_b13_g11_bad_task_never_blocks_unrelated_work(tmp_project, add_shot):
    """§10.15 — S001's corrupt chain fail-closes S001+provider ONLY: S002
    still generates through the same provider (transport exactly 1)."""
    s1 = add_shot(tmp_project, "S001")
    s2 = add_shot(tmp_project, "S002")
    with RuntimeState(tmp_project.root) as st:
        st.open_intent(provider="cloud_test", shot="S001", submission_id="sub_c",
                       request_digest="sha256:d", state=S.PREPARED)
        st.set_submission_state("sub_c", S.DISPATCHING)
    A.append_submission_event(tmp_project, submission_id="sub_c",
                              request_digest="sha256:d", from_state=None,
                              to_state=S.DISPATCHING, provider_id="cloud_test",
                              shot="S001", prev_event_digest="sha256:bogus")

    blocked = ScriptedCloud()
    with pytest.raises(ProviderFailure):
        blocked.generate(_req(tmp_project, s1))
    assert blocked.submit_calls == 0                 # S001: fail-closed

    fine = ScriptedCloud()
    takes = fine.generate(_req(tmp_project, s2))
    assert takes and fine.submit_calls == 1          # S002: unaffected


# =====================================================================
# Part B (POST_COMPLETION WP6) — the two proof-scope corrections
# =====================================================================


_FULL_GEN_SCRIPT = """
import sys, time
from pathlib import Path
from manju.core.container import Project
from manju.runtime.buildlock import BuildLock, BuildLocked
from tests.test_dr06_admission import ScriptedCloud, _req

root, barrier = Path(sys.argv[1]), Path(sys.argv[2])
deadline = time.monotonic() + 15
while not barrier.exists():
    if time.monotonic() > deadline:
        print("SUBMITS 0"); print("VERDICT BARRIER_TIMEOUT"); sys.exit(0)
    time.sleep(0.005)
project = Project(root)
shot = project.load_shot("S001")
lock = BuildLock(root, actor="ai")
try:
    lock.acquire()
except BuildLocked:
    print("SUBMITS 0"); print("VERDICT LOCKED"); sys.exit(0)  # loser: no transport
try:
    provider = ScriptedCloud()
    provider.generate(_req(project, shot))            # the FULL paid generate path
    time.sleep(0.4)                                   # hold so the sibling reliably loses
    print("SUBMITS", provider.submit_calls)
    print("VERDICT WON")
finally:
    lock.release()
"""


def test_b14_wp6_two_full_subprocess_generates_single_transport(tmp_project, add_shot):
    """WP6 §1 — two INDEPENDENT OS processes each run the FULL paid generate
    path for the same shot+provider. The cross-process single-owner property for
    a full build comes from the BUILD LOCK (O_EXCL), not a submission-level CAS:
    exactly one process wins the lock and its scripted transport fires once; the
    loser fail-closes (BuildLocked) with transport 0. Transport across both is
    therefore exactly 1 — the honest end-to-end characterization behind the
    report's "layered with the build lock, transport 2 is not reachable"."""
    add_shot(tmp_project, "S001")
    script = tmp_project.root / "_full_gen.py"
    script.write_text(_FULL_GEN_SCRIPT, encoding="utf-8")
    barrier = tmp_project.root / "_go_full"
    p1 = _spawn(script, tmp_project.root, barrier)
    p2 = _spawn(script, tmp_project.root, barrier)
    time.sleep(0.3)                                   # both children at the barrier
    barrier.write_text("go")
    out1, out2 = _finish(p1), _finish(p2)

    def _facts(lines):
        d = {}
        for ln in lines:
            parts = ln.split()
            if parts and parts[0] in ("SUBMITS", "VERDICT"):
                d[parts[0]] = parts[1] if len(parts) > 1 else ""
        return d

    f1, f2 = _facts(out1), _facts(out2)
    verdicts = sorted([f1.get("VERDICT"), f2.get("VERDICT")])
    assert verdicts == ["LOCKED", "WON"], (out1, out2)
    total = int(f1.get("SUBMITS", 0)) + int(f2.get("SUBMITS", 0))
    assert total == 1                                 # exactly one transport across both


def test_b15_wp6_delete_dot_manju_without_rebuild_still_fail_closes(tmp_project, add_shot):
    """WP6 §2 / §5 — deleting `.manju` and NOT running an explicit rebuild is
    still safe: the paid consult AUTO-projects the unresolved submission from
    evidence and fail-closes (transport 0). This is the automatic guard the
    report now distinguishes from an EXPLICIT `rebuild()` (test_b8) — the
    classification is identical, but here no rebuild command was run."""
    import shutil

    shot = add_shot(tmp_project, "S001")
    _seed_chain(tmp_project, "sub_nr", (S.PREPARED, None), (S.DISPATCHING, None),
                (S.OUTCOME_UNKNOWN, None))
    shutil.rmtree(tmp_project.root / ".manju", ignore_errors=True)   # NO rebuild

    provider = ScriptedCloud()
    with pytest.raises(ProviderFailure) as exc:
        provider.generate(_req(tmp_project, shot))
    assert provider.submit_calls == 0
    assert exc.value.detail.get("automatic_resubmit") is False


# ================= b16: the mid-acquire empty-lock steal window (CI's find)


def test_b16_fresh_empty_lock_is_not_stolen(tmp_project):
    """FINAL_ACCEPTANCE F4/D root cause — the race GitHub CI reproduced that
    our container never did: BuildLock._create() used to open O_EXCL and THEN
    write the holder JSON, while _is_stale() treated an empty/unparsable lock
    as stale OUTRIGHT. A sibling landing in the open->write window read the
    empty file, judged it stale, deleted the winner's lock and acquired —
    ['ACQUIRED', 'ACQUIRED'], real dual ownership.

    Deterministic reproduction: an EMPTY lock file that is FRESH (a writer
    mid-acquire) must NOT be stealable; once it is genuinely old it is a
    corpse and MAY be stolen. RED at HEAD: the fresh empty lock was stolen
    immediately."""
    import os as _os
    import time as _time

    from manju.runtime.buildlock import BuildLock, BuildLocked

    lock_path = tmp_project.root / ".manju" / "build.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"")                 # a writer between open and write

    with pytest.raises(BuildLocked):           # fresh empty => NOT stealable
        BuildLock(tmp_project.root, actor="ai").acquire()
    assert lock_path.exists()                  # the winner's lock survived

    # ...but a genuinely OLD empty lock is a corpse: stealable via the
    # existing staleness machinery (backdate far past any grace).
    old = _time.time() - 3600
    _os.utime(lock_path, (old, old))
    lk = BuildLock(tmp_project.root, actor="ai", stale_after_s=600).acquire()
    try:
        assert lock_path.read_text(encoding="utf-8")  # never exists empty now
    finally:
        lk.release()


def test_b17_lock_file_never_visible_empty(tmp_project):
    """With write-then-hardlink creation the lock file's first visible state
    already carries the full holder JSON — the steal window cannot exist."""
    from manju.runtime.buildlock import BuildLock

    lk = BuildLock(tmp_project.root, actor="ai").acquire()
    try:
        lock_path = tmp_project.root / ".manju" / "build.lock"
        holder = json.loads(lock_path.read_text(encoding="utf-8"))
        assert holder.get("pid") == os.getpid()
        # no stray temp siblings left behind
        strays = [p for p in lock_path.parent.iterdir()
                  if p.name.startswith("build.lock.") and p != lock_path]
        assert strays == []
    finally:
        lk.release()
