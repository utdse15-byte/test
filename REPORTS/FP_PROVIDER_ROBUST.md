# FP Loop M1 — provider-fleet robustness batch (audit F1–F13)

The provider-robustness findings from the 2026-07-12 optimization audit
(`REPORTS/OPTIMIZATION_AUDIT_20260712.md` + the OPT-PROVIDERS detail), landed as
one coherent pass over the adapter fleet. Every change is INTERNAL to
`src/manju/providers/*` — the paid submit path, idempotency, DR06
disposition/admission and OUTCOME_UNKNOWN semantics are UNTOUCHED (proven by the
frozen greens below); `providers/base.py` grew exactly one additive helper. No
new dependencies. Red-first: each fix pin was run RED at HEAD (source stashed)
before the adapter was touched.

| Field | Value |
|---|---|
| Branch | `claude/cost-optimization-strategy-cjfmn5` |
| HEAD at intake | `202065a` |
| Source touched | `providers/{local_cmd,generic_cloud,comfyui,tts,asr,stock,caption_card}.py` + `providers/base.py` (ADDITIVE only) |
| New tests | `tests/test_fp_provider_floor.py` (8) · `tests/test_fp_provider_robust.py` (8) |
| Extended tests | `test_local_cmd.py` · `test_comfyui.py` · `test_stock.py` · `test_asr.py` · `test_voice.py` |
| Guards held (untouched, green) | `test_fp_plugin_api` 17/17 · `test_p0_paid_safety` · `test_dr06_identity` · `test_dr06_admission` · `test_dr06_recovery` · `test_dr06_characterization` |
| Frozen surfaces | PLUGIN API v1 (Provider/CloudProvider ABCs, GenerationRequest ctor, ProviderFailure, registry entry points) — NOT moved; `base.py` change is a new module-level function only |
| Deps | none added |
| CONTRACTS.yaml / DECISIONS.md / CI | not edited |
| Deferred (own loops) | F7 manifest poll knobs · F10 poll-loop consolidation · F12 comfyui cooperative-cancel |

## Scope ruling

In scope: seven robustness fixes (F1–F6, F11) + the fallback-floor test gap (F9)
+ the caption_card silent-swallow diagnostic (F13). Explicitly OUT (recorded,
not built): F7 (additive plugin-API manifest knobs — needs a schema/snapshot
decision), F10 (poll-loop consolidation — no live defect, four-call-site churn
risk touching the paid poll), F12 (comfyui cooperative cancel — a behavior
change deserving its own GUI-cancel-integrated charter). PAID-SAFETY was NOT
"improved" — the audit verified it PASS and it stays byte-frozen here.

## The fixes

### F1 — local_cmd no longer orphans grandchildren on timeout
`subprocess.run(timeout=…)` reaps only the DIRECT child; a wrapper command
(`svd-cli …`, a Wan/CogVideo launcher) that forks the real GPU worker leaked
that grandchild past `timeout_s`, exhausting VRAM across a build. Now:
`Popen(start_new_session=True)` → child in its own session/process group →
`communicate(timeout)` → on `TimeoutExpired`, `os.killpg(os.getpgid(pid),
SIGKILL)` then reap. Guarded for platforms without `killpg` (degrade to
`proc.kill()`). The `ProviderFailure(timeout)` message/detail and every
success/nonzero-exit/no-output/missing-binary semantic is byte-identical.
Pin: a wrapper forks a `sleep 30` grandchild and records its pid — after the
timeout that pid must be DEAD (RED at HEAD: it survived, reparented to init).

### F6 — a transient poll 5xx no longer kills an in-flight (paid) job
`generic_cloud.poll` and `comfyui._poll` treated ANY `>=400` during a poll GET
as a TERMINAL failure, so one 502/503 blip failed an otherwise-good job whose
remote side may still be running/billing. Now a 5xx returns `("running", {})`
(generic_cloud) / keeps looping (comfyui) so polling continues WITHIN the
existing timeout budget; 4xx (429 already handled) stays terminal, and a 5xx
that never clears becomes the ordinary poll-timeout — never an infinite loop.
Idempotent read only; no resubmit (pins assert exactly one submit).

### F5 — comfyui 400 bad-graph is user-fixable `invalid`, not `provider_error`
A rejected graph (`400 {"error":…,"node_errors":{…}}`) is a misconfigured
`workflow_file`/`input_map` — user-fixable INPUT, whose `invalid` hint points at
the manifest fields. 5xx (a real server error) stays `provider_error`. The
error + node_errors is still surfaced verbatim.

### F4 — one `status_to_kind` classifier kills the rate_limited-drift
Added `providers.base.status_to_kind(status)` (ADDITIVE): `429 → rate_limited`,
everything else → `provider_error`. Wired into `tts` submit+poll and `asr`
submit+poll. ASR now records a 429 as retryable `rate_limited` like its tts
sibling (was always `provider_error`), and tts's poll matches its own submit.
generic_cloud left as-is (its 429/content-rejected/disposition logic is not a
trivial `status_to_kind` case).

### F2 — stock failures now land in `reports/failures.jsonl`
`PexelsStockProvider` is a plain `Provider` on the shot fallback chain (like
comfyui/local_cmd) but never recorded, so `manju failures` was blind to a stock
outage. `generate` now wraps `_generate` in the same record-and-reraise idiom.
tts/asr/edge were checked and SKIPPED: their build/repair callers
(`build/graph.py` `_record`, `media/voicefix.py` `_fail`) already record —
adding adapter-level recording would double-record; and `asr.transcribe(media)`
has no project/shot context to record from.

### F3 — generic_cloud stops leaking `_results`/`_submitted`
The registry caches ONE `GenericCloudProvider` per manifest; both job-keyed
dicts grew monotonically for the process lifetime. `download()` — the terminal
read of a job's poll info — now pops both entries. After generate+download the
dicts are empty.

### F11 — stock rejects an HTML error page instead of poisoning a take
A 200 HTML CDN error page was written as `stock_NN.mp4` and registered. Now
`reject_html_error_page` (the SAME guard comfyui/generic_cloud/tts apply) runs
before the write, raising `provider_error` with a verbatim snippet.

### F9 — the always-available FLOOR now has direct tests
kenburns/caption_card/manual — the §8.4 network-independent safety floor — had
only incidental e2e coverage. New offline pins: kenburns no-ref → `invalid` and
per-candidate zoom index-determinism (`zoom_to = round(1.10 + 0.03*i, 4)`);
caption_card records the renderer that ACTUALLY ran (`html` on success,
`drawtext` on fallback) and surfaces an EXPLICIT `renderer: html` failure; the
manual `NeedsHumanInput` branch and the file-supplied `.generate` path
(`spec_hash = MANUAL_HASH`, source COPIED not consumed).

### F13 — caption_card no longer swallows an html-renderer failure silently
Auto-mode html→drawtext degradation now records a best-effort `level="info"`
DEGRADATION (the documented "a card skipped" shape — "why did this shot become a
drawtext card?" answerable from the record alone), wrapped so the diagnostic can
NEVER break the fallback floor. `level="info"` keeps it out of error counts and
the status nudge; chromium being present means normal builds never trip it.

## Evidence

Red-first (source stashed to HEAD, pins present): **16 failed, 6 passed** — the
6 green-at-HEAD are F9 characterization pins for already-correct floor behavior.

Charter verification surface (frozen suites included):
```
python -m pytest test_local_cmd test_comfyui test_stock test_asr test_voice \
  test_edge_tts test_providers_routing test_fp_plugin_api test_dr06_identity \
  test_dr06_admission test_p0_paid_safety test_fp_provider_floor \
  test_fp_provider_robust test_fp_ratemig1
=> 240 passed, 1 skipped
```
Broader provider-consumer sweep (generic_cloud, failures, dr06 recovery/char,
dr04/dr05, qualification, voicefix, cloud-estimate, ledger, jobs, refbudget,
batch, director): **300 passed**. Fallback-floor in REAL e2e builds
(`test_round_a` / `test_content_qc` / `test_e2e_m0` / `test_dr03c_lifecycle`):
**37 passed, 4 skipped**.

`test_fp_plugin_api` (17/17), `test_p0_paid_safety`, and every `test_dr06_*`
are GREEN and UNMODIFIED — the proof no frozen or paid-safety surface moved.

## One corrected assertion (not a weakening)

`test_comfyui.py::test_workflow_validation_error_is_surfaced` pinned the 400 to
`provider_error`; F5 makes it `invalid`, so that single assertion was corrected
(with a docstring for the why). It still asserts the 400 is surfaced with its
`node_errors` detail verbatim — coverage is unchanged, only the now-correct
classification moved.
