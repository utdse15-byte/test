# AI IDE 05 Baseline

WP0 audit for **Manju Deep Research 05 — agent workspace surface, ToolPolicy &
the unattended MCP boundary**. Paired completion: `REPORTS/AI_IDE_05_COMPLETION.md`.
Base commit: `d3c6084` (DR06 landed; the MCP registry has grown through
DR01–DR06 — `explain --graph`, qc assurance in results, run_id in build results).

The gate **PASSED → BUILD** (not `ALREADY_IMPLEMENTED`). No `mcp/policy.py`, no
`ToolPolicy` / `manju.agent-tool-policy/v1`, no profiles, no
`resolve_agent_surface`, no `AgentSurfaceManifestV1` / `manju.agent-surface/v1`,
no `agent_surface` tool and no `--agent-profile` flag exist anywhere in
`src/manju` (grep-zero). `mcp/tools.py`'s `TOOL_DEFS` is the single registry but
carries NO per-tool policy; `list_tools()` / `call_tool()` take no profile;
`serve-mcp` (cli.py:5004) forwards only `--project`. The *facts* a policy needs
exist in handler source (locks, CAS, build lock, spend gate, director
human-confirm gate) but nothing PROJECTS them, and there is no opt-in boundary a
self-driving agent can be pointed at.

---

## §6.3 per-tool inventory (from HANDLER SOURCE, not descriptions — HEAD)

The ACTUAL current registry is **25 tools** (`TOOL_DEFS`, mcp/tools.py). No
`shot_package` / `pack` / `unpack` / `unlock` / `gc` tool is on MCP (confirmed
grep-zero — prior batches never exposed them). Reads/Writes/Net/Spend/Gate/Lock
read off each handler's engine call, not its prose:

| Tool | Handler → engine | Writes | Net | Spend | Gate / lock (engine) | Unattended verdict |
|---|---|---|---|---|---|---|
| status | `_h_status`→`project_status` status.py:18 | — | — | — | — | ALLOW (read) |
| explain | `_h_explain`→`explain`(+`diagnose_project` on `graph=true`) | — | — | — | — | ALLOW (read) |
| impact | `_h_impact`→`impact_report` | — | — | — | — | ALLOW (read) |
| check | `_h_check`→`run_check` | — | — | — | — | ALLOW (read) |
| list_shots | `_h_list_shots`→`evaluate_all` | — | — | — | — | ALLOW (read) |
| get_shot | `_h_get_shot`→`shot_text_hash` writes.py:225 | — | — | — | — | ALLOW (read); **returns `rev` (CAS token)** |
| update_shot | `_h_update_shot` tools.py | shots/*.yaml | — | — | value-lock verify + **CAS `expected_rev`** (hash_text, tools.py:265) + build_lock + post-write check + revert | **ALLOW_WITH_CAS** |
| select_take | `_h_select_take`→`select_take_checked` writes.py:178 | shots/*.yaml (`status.selected_take`) | — | — | checked-write (lock guard + post-write check + revert) + build_lock | ALLOW (keeps checked-write) |
| build | `_h_build`→`run_build` graph.py:561 | shots/, renders/, media/gen/, timeline.json, captions/, reports/ | **yes** | **yes** | ask_before/budget breaker (graph.py:929) + build_lock; auto-selects via checked-write | **DRY_RUN_ONLY** |
| redo | `_h_redo`→`redo_shot` graph.py:1650 | media/gen/, (shots/ only if unselected) | **yes** | **yes** | ask_before + build_lock | **DENY** |
| qc | `_h_qc`→`run_qc`+`write_reports` | reports/qc.json/qc.md/repair_plan.yaml | — | — | build_lock | ALLOW (write-derived) |
| qc_brief | `_h_qc_brief`→`qc_brief` agent_review.py:311 | — (populates rebuildable `.manju/frames` cache) | — | — | — | ALLOW (read) |
| qc_coverage | `_h_qc_coverage`→`qc_coverage` | — | — | — | — | ALLOW (read) |
| qc_verdict | `_h_qc_verdict`→`record_verdicts` agent_review.py | reports/qc_agent.jsonl (append evidence) | — | — | append-only | ALLOW (append evidence) |
| export | `_h_export`→`export_captions/otio/jianying` | captions/, exports/ | — | — | build_lock | ALLOW (write-derived) |
| events | `_h_events`→`tail_events` | — | — | — | — | ALLOW (read) |
| board | `_h_board`→`generate_board` board.py:1134 | board.html (root) | — | — | atomic write | ALLOW (write-derived) |
| propose | `_h_propose`→`_claim_proposal_path` tools.py:86 | proposals/NNNN_*.md | — | — | O_CREAT\|O_EXCL atomic claim | ALLOW (propose) |
| director_propose | `_h_director_propose`→`propose` director.py:564 | reports/proposals/<id>.yaml | — | — | shared dry-run estimators (no network) | ALLOW (propose) |
| director_confirm | `_h_director_confirm`→`confirm` director.py:611 | reports/proposals/<id>.yaml (state) | — | — | **priced ⇒ human-only** (director.py:631) | **DENY** |
| director_execute | `_h_director_execute`→`execute` director.py:666 | shots/, renders/, media/gen/, proposals/ | **yes** | **yes** | **confirmed+current+human** (director.py:683-700) + spend gate + build_lock | **CONFIRMED_PROPOSAL_ONLY** |
| director_suggest | `_h_director_suggest`→`suggest_next` | — | — | — | — | ALLOW (read) |
| funnel_status | `_h_funnel_status`→`funnel_status` | — | — | — | — | ALLOW (read) |
| skill_list | `_h_skill_list`→`list_skills` | — | — | — | — | ALLOW (read) |
| skill_show | `_h_skill_show`→`load_skill` | — (best-effort `skill_used` telemetry to events.jsonl) | — | — | never blocks | ALLOW (read) |

**26th (to add):** `agent_surface` — read-only manifest of the CURRENT profile;
effects `[READ_RUNTIME]`, network/spend NEVER, unattended ALLOW; callable in
BOTH profiles.

---

## §6.4 gate verdict (what the contract asks vs. where it lives today)

| # | Capability | Where today | Verdict |
|---|---|---|---|
| 1 | `mcp/policy.py` (ToolPolicy validation, resolver, manifest, digest) | — | **MISSING** |
| 2 | Per-tool `policy` dict on `TOOL_DEFS` (the ONE registry) | `TOOL_DEFS` exists (mcp/tools.py:568), no policy key | **PARTIAL** (registry present, unpoliced) |
| 3 | Integrity rules (§7.8: enums, SPEND⇔spend, WRITE_TRUTH gate, CAS, CONFIRMED) | facts exist in handlers, no validator | **MISSING** |
| 4 | Two profiles (collaborative default / unattended opt-in) | none — one implicit surface | **MISSING** |
| 5 | `serve-mcp --agent-profile` flag (the ONLY source) | serve-mcp forwards `--project` only (cli.py:5004) | **MISSING** |
| 6 | `list_tools(profile)` / `call_tool(…, profile)` share the resolver | both take no profile (mcp/tools.py:934/942) | **MISSING** |
| 7 | Structured `agent_profile_denied` denial | server renders `{"error": str}` only (server.py:151) | **MISSING** |
| 8 | Unattended decision table (confirm/redo DENY, build dry-run-only, execute confirmed-only, update_shot CAS) | — | **MISSING** |
| 9 | `agent_surface` tool + `raw_filesystem_enforced:false` honesty | — | **MISSING** |
| 10 | **get_shot `rev` is a stable CAS token** (the ALLOW_WITH_CAS anchor) | `shot_text_hash` = `hash_text(file text)` = `sha256:…` (writes.py:225→hashing.py:34); `update_shot` checks `hash_text(original)!=expected_rev` inside the build lock (tools.py:265) | **PRESENT** — stable, deterministic; ALLOW_WITH_CAS wires to the REAL token (no PARTIAL) |
| 11 | Director human-confirm gate the profile complements | `confirm` refuses priced+non-human (director.py:631); `execute` re-checks confirmed/current/human (director.py:683-700) | **PRESENT** — the profile's DENY/CONFIRMED_PROPOSAL_ONLY is belt-and-suspenders, never a replacement |
| 12 | build dry-run is provably free | `run_build(dry_run=True)` returns after planning (graph.py:916-922), BEFORE `_phase("generate")` (graph.py:941) | **PRESENT** — DRY_RUN_ONLY rests on this |

**Conclusion → BUILD.** Every enforcement primitive the policy will *describe*
already exists in the engine (locks, CAS, build lock, spend gate, director
human gate); DR05 adds the **projection + the opt-in boundary**, never a new
security mechanism. The CAS token (item 10) is verified stable, so
`update_shot`'s unattended `expected_rev` requirement binds to the real
`get_shot.rev` — reported PRESENT, not PARTIAL.

---

## §6.4 characterization program (`tests/test_dr05_characterization.py`, 15 pins, GREEN on the untouched tree)

Captured BEFORE any production change so each DR05 delta is provable:

1. **all 25 baseline tool names present** + dangerous ones (unlock/gc/pack/import) absent.
2. **list payload shape** = `{name, description, inputSchema}` only (no policy/handler leak).
3. **load-bearing schemas byte-identical** — build dry_run/gen/target enums, update_shot required + expected_rev, explain graph, get_shot required.
4. **get_shot `rev` is a stable content hash** — identical across repeated reads, `== shot_text_hash == hash_text(file)`.
5. **`rev` tracks content** — moves after an `update_shot` write.
6. **`expected_rev` round-trips + stale refused** — the HEAD rev is accepted, the now-stale rev is refused (the CAS reality ALLOW_WITH_CAS leans on).
7. update_shot happy result `{ok, check_warnings}`.
8. update_shot locked-field rejected + file unchanged.
9. update_shot adding-a-lock rejected + file unchanged.
10. select_take `{ok, shot, take}`; unknown take → ToolError.
11. **build dry_run is free** — `{ok, plan, estimated_cost==0}`, generates nothing (shot stays `missing`).
12. director_propose (free action) → `{state: proposed, id: prop_…}`.
13. **priced-proposal ai-confirm refused engine-side** (director human-only gate).
14. status snapshot field inventory (the takeover keys a workspace-snapshot would duplicate, in ONE call).
15. dispatch contract — unknown tool → ToolError; a known read dispatches; every entry addressable.

Pins 1-3 stay GREEN throughout (the 25 tools are never changed — collaborative
byte-identity). Pins 4/6/11/13 are the load-bearing anchors the unattended rules
bind to. The only intended additive delta — the `agent_surface` tool — is pinned
separately in `test_dr05_policy.py` (item 21), not by flipping a characterization
pin.

---

## Conditional-item pre-decisions (evidence gathered at WP0)

- **AgentWorkspaceSnapshotV1 → SKIPPED_WITH_EVIDENCE.** `status --json`
  (`project_status`, status.py:163-191) already composes in ONE call:
  `shots_by_state` + `shot_notes` (gaps), `next_step` (next step), `total_cost` /
  `spend_by_currency` / `budget_limit` (spend), `recent_events` (who did what),
  `timeline` / `qc` / `build_lock` / `mode`. `funnel_status`, `director_suggest`,
  `skill_list` are cheap JSON reads already on MCP. The §10.1 bar ("a takeover
  needs ≥3 calls whose composition status cannot carry") is NOT met. Decision:
  add NO `workspace_snapshot`, NO `agent_context`.
- **`manju auto` playbook (§11.2) → 4/5 lines ALREADY_IMPLEMENTED, 1 appended.**
  skills/manju/SKILL.md covers: *status first* (§1 step 1 `manju status --json`),
  *proposals for locked* (§4), *director flow for spend* (§0 导演循环 + §5),
  *never overwrite imports/takes* (§3 禁区). The `expected_rev`/CAS discipline is
  present as *semantic intent* (§1 step 2: don't overturn others' work) + fully
  documented at the tool surface (get_shot/update_shot), but not named as a
  playbook line → a SHORT resolver-derived line is appended (completion report).

---

## Must-preserve invariants (pinned)

- **Collaborative tools/list / result / error byte-identity** — the 25 existing
  tools' names/descriptions/schemas, their result shapes and their error
  envelope are unchanged; the sole additive delta is the read-only
  `agent_surface` tool. `test_mcp` + `test_mcp_copilot_e2e` + the char pins hold it.
- **Policy metadata is not a security boundary** — enforcement stays at call
  dispatch (the resolver) + the existing engine guards; description text is never
  parsed for permissions. No parallel allowlist: list/call/agent_surface all read
  the ONE resolver over the ONE registry.
- **The profile comes only from the flag** — project.yaml / skills / shots / tool
  arguments can never select or escalate it (`resolve_agent_surface` rejects an
  unknown profile; the server sets it once from `--agent-profile`).
- **All projections derived + rebuildable** — the manifest is a pure function of
  (registry, profile); no persistence, no time/paths in the digest.

Full red-first outcomes, the delivered module API, the unattended decision table
as implemented, the resolver single-source proof and the conditional decisions
are in `REPORTS/AI_IDE_05_COMPLETION.md`.
