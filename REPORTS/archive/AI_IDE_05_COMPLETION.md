# AI IDE 05 Completion

Build report for **Manju Deep Research 05 — agent workspace surface, ToolPolicy &
the unattended MCP boundary**. Paired baseline: `REPORTS/AI_IDE_05_BASELINE.md`.
Final-run numbers and git state are filled by the orchestrator after the last
full-suite run.

Core principle held throughout: **policy metadata is a DESCRIPTION, not a
security boundary.** The ToolPolicy projects effects a profile can decline to
list/call; the actual enforcement stays where it already lived — call dispatch
plus the engine guards (value-hash locks, CAS, the cross-process build lock, the
ask_before/budget spend gate, the director confirm gate). Collaborative is
byte/semantics-identical to today; `unattended` is opt-in, reachable ONLY through
the `serve-mcp --agent-profile` flag, and never selectable from project content.

---

## Repository

| Field | Value |
|---|---|
| Base commit | `d3c6084` (DR06 landed; registry grown through DR01–DR06) |
| DR05 landed | `src/manju/mcp/policy.py` (new, pure): ToolPolicy validation (`manju.agent-tool-policy/v1`), the single resolver `resolve_agent_surface`, the `AgentSurfaceManifestV1` projection + stable digest. `mcp/tools.py`: an inline `policy` dict on all 25 tools + the new read-only `agent_surface` tool; the load-time integrity gate; profile-aware `list_tools(profile)` / `call_tool(…, profile)` + `AgentProfileDenied`. `mcp/server.py`: `--agent-profile` (argparse) threaded into list/call + structured `agent_profile_denied` rendering. `cli.py`: the `serve-mcp --agent-profile` passthrough. `skills/manju/SKILL.md`: one appended `expected_rev`/CAS discipline line. `tests/test_dr05_characterization.py` + `test_dr05_policy.py` + `test_dr05_unattended.py`; these two reports |
| Production files touched | **4 of the 9-file cap** (spec expected 3-4): `mcp/policy.py` (new), `mcp/tools.py`, `mcp/server.py`, `cli.py`. `skills/manju/SKILL.md` is a conditional-deliverable skill/doc edit (ruling 8), not production code |
| Line deltas | `mcp/policy.py` **+427** (new) · `mcp/tools.py` **+169** (25 inline policies + agent_surface + profile threading + load-time gate) · `mcp/server.py` **+27** · `cli.py` **+14** · `skills/manju/SKILL.md` **+2** |
| Tests | `test_dr05_characterization.py` (15) · `test_dr05_policy.py` (30) · `test_dr05_unattended.py` (19) — **64 DR05 tests**, all GREEN |
| Final full-suite result | **4 failed, 2537 passed, 12 skipped** (896.22s / 0:14:56) — the 4 are exactly the pre-existing `include_unindexed` set; zero new failing test names; +64 vs DR06's 2473 = the 64 DR05 tests. Run AFTER the orchestrator's redo-gate review fix, so it covers the final registry |
| Final git state | base `d3c6084` → `e8889b9` (DR05 code + docs, incl. the redo SPEND_GATE review fix) → `f7d8e27` (baseline report) → the reports commit adding this file; working tree clean after it |

---

## Baseline (WP0 verdict)

Gate PASSED → **BUILD**. Every enforcement primitive the policy describes already
exists in the engine; DR05 adds the projection + the opt-in boundary, never a new
mechanism. Two realities were verified at WP0 so the unattended rules bind to the
truth: (a) `get_shot.rev` is a STABLE CAS token (`shot_text_hash` =
`hash_text(file text)` = `sha256:…`, writes.py:225→hashing.py:34; `update_shot`
checks it inside the build lock, tools.py:265) → `update_shot` ALLOW_WITH_CAS wires
to the real token (**PRESENT, not PARTIAL**); (b) `run_build(dry_run=True)` returns
after planning (graph.py:916-922) BEFORE `_phase("generate")` (graph.py:941) → build
DRY_RUN_ONLY rests on a provably-free path. Full §6.3 per-tool inventory (25 tools,
from handler source) + the §6.4 gate table + the 15 characterization pins in
`REPORTS/AI_IDE_05_BASELINE.md`.

---

## Red-first (per WP)

**WP0 characterization first (15 pins, GREEN before any production change).**
`tests/test_dr05_characterization.py` captured HEAD's exact collaborative
behavior: the 25-tool registry + absent dangerous tools, the `{name, description,
inputSchema}`-only list payload, the load-bearing schemas, the `get_shot.rev`
CAS reality (stable / tracks-content / round-trips / stale-refused), the write
tool result+error shapes, the free dry-run path, the director human-only confirm
gate, the status field inventory, and the dispatch contract. All 15 GREEN on the
untouched tree.

**Pure module RED→GREEN.** With `mcp/policy.py` absent, `test_dr05_policy.py` and
`test_dr05_unattended.py` failed at import (`ImportError: cannot import name
'policy'`). The 29 policy/manifest/compat tests + the 19 unattended/server tests
went from RED to GREEN as WP1-WP3 landed — the pure validation/resolver/digest
logic is pinned independently of the wiring.

**No characterization flip.** Unlike a fail-closed batch, DR05 changes NO default
behavior, so ZERO characterization pins flipped. The one additive collaborative
delta (the `agent_surface` tool) is pinned by a NEW feature test
(`test_dr05_policy.py::test_compat_21`), not by rewriting a pin. `test_mcp` /
`test_mcp_copilot_e2e` / `test_auto_agent` / `test_cli` stayed GREEN unchanged.

---

## Existing systems reused (not duplicated)

| System | file:line | How reused |
|---|---|---|
| `TOOL_DEFS` — the ONE registry | mcp/tools.py:568 | each def gains an inline `policy` dict; NO second dict, NO parallel allowlist |
| `hash_value` / `canonical_json` | core/hashing.py:23-30 | the manifest digest (stable, excludes description/handler/writes/time) |
| `shot_text_hash` (CAS token) / `hash_text` | core/writes.py:225 / hashing.py:34 | `update_shot` ALLOW_WITH_CAS binds to the real `get_shot.rev`; no new CAS field invented |
| `select_take_checked` (checked write) | core/writes.py:178 | select_take stays ALLOW with its existing lock-guard+revert; no stale-overwrite failure exists to warrant a new CAS field |
| director `confirm` / `execute` human+confirmed+current gates | build/director.py:631, 683-700 | the profile's DENY (confirm) / CONFIRMED_PROPOSAL_ONLY (execute) is belt-and-suspenders; the engine gate re-checks |
| `run_build(dry_run=True)` plan-only path | build/graph.py:916-922 | build DRY_RUN_ONLY admits only `dry_run:true` — the engine returns before any spend |
| `project_status` rich snapshot | build/status.py:163-191 | the evidence AgentWorkspaceSnapshot is SKIPPED against |
| MCP server dispatch + `isError` envelope | mcp/server.py:120-152 | the structured `agent_profile_denied` rides the SAME isError result; no new error channel |

---

## WP1 — `mcp/policy.py` — the pure ToolPolicy / resolver / manifest module

`SCHEMA_POLICY = "manju.agent-tool-policy/v1"`, `SCHEMA_MANIFEST =
"manju.agent-surface/v1"`. Pure — no I/O, no `Project`, no network, no time.

```python
# enums (§7.3) — EXACTLY the contract's six policed fields + writes[]
EFFECTS = {READ, READ_RUNTIME, WRITE_TRUTH, WRITE_PROPOSAL, WRITE_DERIVED, NETWORK, SPEND}
NETWORK_LEVELS = SPEND_LEVELS = {NEVER, POSSIBLE}
GATES = {NONE, CAS, CHECKED_WRITE, BUILD_LOCK, SPEND_GATE, CONFIRMED_PROPOSAL, PROPOSAL_APPEND}
CONCURRENCY = {NONE, BUILD_LOCK, ATOMIC_APPEND}
UNATTENDED_RULES = {ALLOW, DENY, ALLOW_WITH_CAS, DRY_RUN_ONLY, CONFIRMED_PROPOSAL_ONLY}
PROFILES = {COLLABORATIVE, UNATTENDED};  DENIAL_CODE = "agent_profile_denied"

policy(*, effects, network=NEVER, spend=NEVER, gate=(NONE,), concurrency=NONE,
       unattended=ALLOW, writes=())            # inline builder (no 2nd registry)
validate_policy(name, pol) / validate_registry(tool_defs)   # raise PolicyError
resolve_agent_surface(tool_defs, profile, call_arguments=None) -> Surface
Surface.listed_names() / .decide(name, args) -> CallDecision / .manifest() / .digest()
CallDecision.admitted / .denial_payload() -> {error, code, tool, profile, required_path}
```

**Integrity rules (§7.8), enforced by `validate_policy`:** every tool has a
policy; every enum value legal; `SPEND ∈ effects ⇔ spend≠NEVER`; `NETWORK ∈
effects ⇔ network≠NEVER`; `WRITE_TRUTH ⇒ gate≠NONE`; `ALLOW_WITH_CAS ⇒ CAS ∈
gate`; `CONFIRMED_PROPOSAL_ONLY ⇔ CONFIRMED_PROPOSAL ∈ gate`; `DRY_RUN_ONLY ⇒
SPEND|NETWORK ∈ effects`; a pure-read tool is network/spend NEVER; `writes[]` are
logical project-relative (no absolute/`~`/`..`); every policy value is a string /
bool / list-of-strings (no callables, Paths, credentials, or time). `test_dr05_policy`
exercises each rejection.

**Digest** (`Surface.digest`) = `hash_value` over `{schema, profile,
raw_filesystem_enforced, project_can_override, [(name, sorted effects, network,
spend, sorted gate, concurrency, unattended, listed) …]}` — **excludes** description
text, handler identity, `writes[]` paths and time. Stable across calls; moves with
the profile and with any semantic policy change; unchanged by a reworded
description or a swapped handler (all four pinned).

## WP2 — `mcp/tools.py` — the policed registry + agent_surface + profile threading

Each of the 25 `TOOL_DEFS` entries gains one inline `policy` dict (via `_P.policy`,
not a second registry). The new `agent_surface` tool (effects `[READ_RUNTIME]`,
network/spend NEVER, unattended ALLOW) returns the CURRENT profile's manifest.
`list_tools(profile=COLLABORATIVE)` filters by `resolve_agent_surface(...).
listed_names()`; `call_tool(project, name, arguments, profile=COLLABORATIVE)`
resolves the surface, raises `AgentProfileDenied(decision.denial_payload())` on a
refusal, and routes `agent_surface` with the live profile. The module runs
`validate_registry(TOOL_DEFS)` at import — a malformed policy fails the import
loudly (the load-time gate). `AgentProfileDenied(ToolError)` carries the structured
`.payload`.

## WP3 — `mcp/server.py` — the flag, threaded

`MCPServer(project_start, agent_profile=COLLABORATIVE)` validates the profile once
(a bad value is a hard config error — it can never come from project content).
`tools/list` → `tools.list_tools(self._profile)`; `tools/call` →
`call_tool(…, self._profile)`, with an `AgentProfileDenied` caught BEFORE the
generic handler so the refusal renders as a STRUCTURED isError result
(`agent_profile_denied`), never a JSON-RPC error and never unknown-tool. `main()`
adds `--agent-profile {collaborative,unattended}` (default collaborative).

## WP4 — `cli.py` — the one user-facing flag

`serve-mcp --agent-profile` (default collaborative) validates then forwards
`["--project", root, "--agent-profile", profile]` to the server `main`. This is
the ONLY way to select `unattended`.

---

## The unattended decision table (as implemented)

| Tool(s) | Policy `unattended` | Behavior under `unattended` | Enforced by |
|---|---|---|---|
| status, explain, impact, check, list_shots, get_shot, events, qc_coverage, director_suggest, funnel_status, skill_list, skill_show, qc_brief, **agent_surface** | ALLOW | listed + admitted | resolver |
| update_shot | ALLOW_WITH_CAS | admitted **iff `expected_rev` present**, else denied (listed); write still passes value-lock + CAS + build_lock + revert | resolver arg-check + engine CAS |
| select_take | ALLOW | admitted; existing checked-write (lock guard + post-write check + revert) | resolver + `select_take_checked` |
| build | DRY_RUN_ONLY | admitted **iff `dry_run:true`** (listed); any non-dry / `gen=off` call denied — no gen=off whitelist | resolver arg-check + free dry-run path |
| redo | **DENY** | **hidden** from tools/list; called by name → structured `agent_profile_denied` | resolver |
| director_confirm | **DENY** | **hidden**; called by name → denial ("a human confirms") | resolver (+ engine priced-human gate underneath) |
| director_execute | CONFIRMED_PROPOSAL_ONLY | listed + admitted; the ENGINE re-checks confirmed+current+human (unconfirmed → ordinary ToolError, NOT a profile denial) | resolver admits, engine gates |
| director_propose, propose | ALLOW | listed + admitted (the legitimate proposal channel) | resolver |
| qc, export, board, qc_verdict | ALLOW | listed + admitted (write-derived / append evidence) | resolver |

Collaborative admits ALL of the above unconditionally (byte-identical to today).
`unattended` hides **exactly** `{redo, director_confirm}` and nothing else
(pinned, `test_un_40`).

---

## Resolver single-source proof

`resolve_agent_surface(TOOL_DEFS, profile)` is the ONE decision function.
`tools/list` reads `Surface.listed_names()`; the call gate reads
`Surface.decide(name, args)`; the `agent_surface` tool reads `Surface.manifest()`
/ `.digest()`. There is no second dict and no parallel allowlist: the policy is an
inline field on the single `TOOL_DEFS`, and `list`/`call`/`agent_surface` all
resolve from it. `test_dr05_policy::test_surf_11` asserts the manifest's listed
set IS the resolver's `listed_names()`; `test_un_40` asserts the list delta
equals the resolver's DENY set; the real-server test asserts `tools/list` and
`tools/call` agree through one `MCPServer(profile)`.

---

## Conditional-item decisions (with evidence)

| Item | Decision | Evidence |
|---|---|---|
| AgentWorkspaceSnapshotV1 | **SKIPPED_WITH_EVIDENCE** — no `workspace_snapshot`, no `agent_context` added | `status --json` (status.py:163-191) already composes stage/gaps/next-step/spend/recent-events/locks in ONE call; `funnel_status`/`director_suggest`/`skill_list` are cheap reads already on MCP → the §10.1 "≥3 calls status cannot carry" bar is NOT met (`test_snap_45/46`) |
| `manju auto` playbook (§11.2) | **ALREADY_IMPLEMENTED (4/5) + one appended** | SKILL.md covers status-first (§1), proposals-for-locked (§4), director-flow-for-spend (§0/§5), never-overwrite-imports/takes (§3). The `expected_rev`/CAS line was present only as semantic intent (§1 step 2) + at the tool surface → one SHORT resolver-derived line appended to §1 (kept under the `<300`-line core-skill budget, test_skill_content GREEN) |
| select_take new CAS field | **NOT ADDED** | no proven stale-overwrite failure exists; `select_take_checked` (writes.py:178) already lock-guards + post-write-checks + reverts. select_take stays ALLOW |
| build `gen=off` whitelist | **NOT ADDED** | the engine cannot provably guarantee `gen=off` never renders/long-runs; only `dry_run:true` is provably free (graph.py:916). Unattended build is dry-run-only |
| agent_surface honesty flags | **STATED IN EVERY MANIFEST** | `raw_filesystem_enforced:false` + `project_can_override:false` in both profiles (`test_surf_12`) — MCP policy governs only this server's tool calls |

---

## Enforcement & compatibility notes (deliberate + documented)

1. **Enforcement is at call dispatch + the engine, never description text.** The
   resolver decides admission; the engine guards (locks, CAS, build lock, spend
   gate, director confirm) run on top unchanged. A denied paid tool never reaches
   its handler — proven by the monkeypatched spend counter staying 0 across a full
   unattended session (`test_un_43_44`).
2. **The denial is structured AND worded.** `{error, code:
   "agent_profile_denied", tool, profile, required_path}` — never NL-only, never
   unknown-tool. A genuinely unknown tool still raises the plain unknown-tool
   ToolError (`test_un_41`).
3. **The profile is flag-only.** A planted `agent_profile: unattended` in
   project.yaml, a tool argument, or a skill can never escalate; `resolve_agent_
   surface` rejects an unknown profile and the server sets it once from the flag
   (`test_un_42`).
4. **Collaborative is byte/semantics-identical.** The 25 existing tools' names,
   descriptions, schemas, result and error shapes are unchanged; the sole
   additive delta is the read-only `agent_surface` tool. `test_mcp` /
   `test_mcp_copilot_e2e` / `test_auto_agent` / `test_cli` unchanged and GREEN.

---

## Tests

- **New suites (64 DR05 tests):** `test_dr05_characterization.py` (15, the WP0
  pins), `test_dr05_policy.py` (30: integrity 1-10 + the review-added 08b
  spend⇒SPEND_GATE invariant, manifest/digest 11-20, default compat 21-27),
  `test_dr05_unattended.py` (19: the decision table 28-44, real-`MCPServer`
  list/call parity, snapshot 45-46). All GREEN.
- **§12 coverage:** registry integrity (every tool policed; enums legal;
  SPEND/NETWORK⇔level; WRITE_TRUTH gate; ALLOW_WITH_CAS⇒CAS; CONFIRMED⇔gate;
  logical writes; no impure values; each rejection) → `test_dr05_policy` ints.
  Surface manifest (schema, honesty flags in both profiles, field projection,
  stable digest, digest excludes description/handler, digest moves with
  profile/semantics, no-time, agent_surface read-runtime) → surf 11-20. Default
  compat (all baseline + agent_surface, payload shape, all admitted, byte-identity,
  default=collaborative, redo/confirm admitted, schemas preserved) → compat 21-27.
  Unattended (reads ALLOW, update_shot CAS + denial payload, select_take ALLOW,
  build dry-run-only + free, redo/confirm DENY+hidden+structured, propose/director
  ALLOW, execute confirmed-only engine-gated, derived-writes ALLOW, exact hide set,
  structured-not-unknown, flag-only source, zero-spend session, server parity) →
  un 28-44 + server. Snapshot SKIPPED_WITH_EVIDENCE → snap 45-46.
- **Regression (GREEN, unchanged):** `test_mcp` (33), `test_mcp_copilot_e2e`,
  `test_auto_agent`, `test_cli`, `test_skill_content` / `test_skills` /
  `test_ask_before` (the SKILL.md append stays under the <300-line core budget),
  `test_director` (bar the known drift), `test_qc_agent`, `test_funnel`,
  `test_explain`, `test_dr01_run_evidence`, `test_dr02_surfaces`,
  `test_dr03b_characterization`, `test_interconnection`, `test_events_follow`.
- **Known baseline drift (untouched, OUTSIDE this batch):** the 4 pre-existing
  `include_unindexed` failures — `test_director::test_execute_build_passes_assume_
  yes_from_confirmed_state`, `test_director::test_mcp_driven_roundtrip_build_
  mocked`, `test_round_w_agent_wb::test_build_gen_off_still_skips_generation`,
  `test_write_consistency::test_execute_locked_internally_action_type_no_double_
  acquire` — all the same `fake_run_build() got an unexpected keyword argument
  'include_unindexed'` gui/plan.py:125 test-double drift, none touched by DR05.

---

## Deviations (with reasons)

| Deviation | Reason |
|---|---|
| `skills/manju/SKILL.md` touched (a 5th file) | conditional deliverable (ruling 8): the `expected_rev` discipline line was genuinely absent from the playbook prose — one SHORT resolver-derived append. A skill/doc file, not a production code file; the 4 production files stay within the 3-4 estimate |
| `agent_surface` added to the collaborative tools/list | mandated (ruling 6: callable in BOTH profiles). The "byte-identical tools/list" invariant is read as "the 25 pre-existing tools unchanged" — the additive read-only tool is the intended delta, pinned separately (`test_compat_21`) |
| `WRITE_PROPOSAL` effect token | proposals are neither shot truth nor a rebuildable render output; a distinct honest token keeps propose/director_propose classified precisely (append-only, never edits truth) without overloading WRITE_DERIVED |
| `gate` / `effects` modeled as lists (sets) | update_shot genuinely passes several engine guards (CAS + checked-write + build lock); a scalar gate would force a dishonest single-guard claim. The integrity rules read membership (`WRITE_TRUTH ⇒ NONE ∉ gate`, `ALLOW_WITH_CAS ⇒ CAS ∈ gate`, `CONFIRMED_PROPOSAL_ONLY ⇔ CONFIRMED_PROPOSAL ∈ gate`) |
| `network`/`spend` levels are two-valued `NEVER\|POSSIBLE` (contract sketched `NEVER\|CONDITIONAL\|ALWAYS`) | a static per-tool declaration cannot honestly distinguish CONDITIONAL from ALWAYS — whether build actually spends depends on runtime state (cache hits, `gen` mode, regen flags) the registry cannot see. Claiming ALWAYS would be false on a fully-cached build; the honest static statement is "spending is POSSIBLE on this path". The integrity rules (`SPEND ∈ effects ⇔ spend ≠ NEVER`) are unaffected |

**Review fix (orchestrator, post-agent):** `redo`'s declared `gate` list omitted
`SPEND_GATE` even though `redo_shot` runs the same §8.3 ask_before spend gate as
build (`cli.py` redo's `--yes` approves ask_before-gated spend) — the metadata
under-described a real engine guard (§7.4 violation, caught in review). Fixed to
`[SPEND_GATE, BUILD_LOCK]` and pinned structurally: new invariant
`test_int_08b_spend_possible_declares_spend_gate` requires every `spend:
POSSIBLE` tool to declare `SPEND_GATE` (red-first — the test failed on the
unfixed registry, then the one-line policy fix turned it green).
