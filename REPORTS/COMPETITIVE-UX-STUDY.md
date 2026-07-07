# Competitive UX study — mature-product craft → adoption plan

Round R14 of continuous development. Companion to
[COMPETITIVE-STUDY.md](COMPETITIVE-STUDY.md), which studied AI-video
*generator pipelines* (MoneyPrinterTurbo / ShortGPT / NarratoAI / edge-tts)
for **what to build**; this study looks at *mature products* for **how the
workbench should feel**. It also completes and supersedes the shortlist in
[UX-STUDY.md](UX-STUDY.md) (round R9): the two study legs lost to the API
session limit there (Figma multiplayer, Obsidian/VS Code) are now in, and
every pattern is re-scored against what rounds R10–R13 already shipped
(see PROGRESS.md).

## 1. Scope & method

Seven product families, studied for interaction craft, not features. Each
study ran against live web sources in 2026-07 except where flagged:

- **Frame.io V4 (Adobe)** — the industry cloud review-and-approval platform:
  keyboard-first player, time-anchored comments, version stacks, share links.
  Web-verified against the live V4 Knowledge Center.
- **DaVinci Resolve (Blackmagic)** — monolithic NLE; its Cut page is the best
  worked example of review-throughput-as-a-feature (take selector, fast
  review, proxy badges, pre-flighted render queue). Web-verified
  (blackmagicdesign.com, 18.6 manual mirror, practitioner reviews).
- **Descript** — text-based video/podcast editor: transcript as the editing
  surface, checkpointed AI co-editor, deliberate publish. *Caveat:*
  help.descript.com blocks direct fetch (Cloudflare); mechanics came from
  search extracts of the official articles plus two fully-fetched
  third-party reviews.
- **剪映 / CapCut Desktop (ByteDance)** — beginner-first pro NLE: draft
  shelf, template slots, per-feature price tags, script-matched captions.
  Web-verified (help center + Chinese tutorials).
- **Runway ML (Gen-4 era)** — cloud generative video at consumer scale; the
  most battle-tested spend-disclosure and provenance UX. *Caveat:*
  help.runwayml.com returns 403; facts via search extracts of the cited
  articles; docs.dev.runwayml.com/guides/pricing and the changelog were
  fetched directly.
- **Nx Cloud / BuildBuddy (Bazel BEP) / GitHub Actions** — viewers over
  deterministic task engines, exactly manju's shape: input-hash diffs,
  cache-status chips, failure auto-expand, scoped retries. Web-verified.
- **Figma multiplayer + Obsidian / VS Code** — the references for
  presence-over-locking and for "local folder is the product" ergonomics
  (vaults, palettes, keymaps-as-text, workspace trust). Web-verified
  (figma.com blog/help, code.visualstudio.com, obsidian.md/help, 2026-07-06).

Filter applied throughout: every adoption must survive "does it move truth
out of text?", "does it put an LLM in the engine?", and "does it need a
cloud service?" — the same tests COMPETITIVE-STUDY.md applied.

## 2. Cross-cutting principles (deduplicated)

Seven principles recur across otherwise unrelated products:

1. **Cost before commitment.** Runway prints the exact price on the Generate
   button; CapCut price-tags every Pro asset and live-estimates export size;
   Resolve's Deliver queue pre-flights missing media and redundant re-renders
   before spend. The spend decision and the action must be one gesture.
   *Manju: mostly landed (R10 price-on-the-trigger, §8.3 ask_before gate);
   the missing halves are the savings side and estimate-vs-actual.*
2. **Explain the cache.** Nx's "compare to similar tasks" hash diff,
   BuildBuddy's invocation diffs, Resolve's PXY/HQ badges, Nx's replayed
   cache hits: a deterministic engine is trusted exactly insofar as "why did
   this rebuild / what am I looking at" has a mechanical, per-input answer.
   *Manju: R11 landed the spec_snapshot field diff; rendition provenance and
   "what the cache saved" remain.*
3. **Review is a first-class mode.** Frame.io's keyboard player, Resolve's
   Cut page and Fast Review, Runway's one-key favorites, Figma's
   unread-first comment triage: review throughput comes from a bounded,
   filterable queue plus one-key verdicts, never from browsing.
   *Manju: chips, j/k review mode, and 好/弃 verdicts landed in R10; the
   queue is not yet unseen-first and has no time-boxed pass.*
4. **Keyboard is the pro path — and it is discoverable.** Frame.io's NLE
   keymap with an in-player overlay; VS Code/Obsidian palettes showing
   hotkeys inline; keymaps living in editable text files. Muscle memory is
   borrowed, not invented, and the bindings teach themselves.
   *Manju: review keys + "?" hint bar shipped; bindings are hardcoded in
   page.py (acceptable at current scale).*
5. **Presence beats locking.** Figma rejected baton-passing locks: show who
   is acting on what and true conflicts become rare and small. When a
   conflict does happen, surface it — never silently merge, never silently
   discard. *Manju: the engine's single-writer lock and check-gated
   auto-revert are correct but silent — the revert throws the director's
   buffer away with a bare 409.*
6. **Verdicts are data every downstream picker respects.** Frame.io status
   chips feeding Collections; Runway favorites honored by asset pickers;
   Descript's per-item accept/reject batches. A verdict captured at viewing
   time must cost one key and compound later. *Manju: take_notes verdicts
   landed (R10); nothing sorts or filters by them yet on the board.*
7. **Publishing is deliberate; staleness is worn on the button.** Descript's
   Export control relabels to "Update" when the share page is behind;
   Frame.io scopes shares at creation time. The stale-indicator lives on the
   primary control, not in a notification. *Manju: board.html is exactly
   this shape and currently carries no staleness signal.*

Meta-lesson (unchanged from UX-STUDY.md, now with two more product families
confirming it): the engine already computes every number these patterns
display — the gap is presentational.

## 3. Adoption table

Already landed, therefore **not** rows below (R10–R13, verified in
`src/manju/gui/page.py` / `build/stale.py` / `board/board.py`): price on the
trigger, why-stale field diff (`spec_snapshot` + `diff_spec_fields`), state
filter chips, one-key 好/弃 verdicts, timecode-note click-to-seek, failed-job
auto-expand, finals version stack with content-key honesty flags,
recipe-reuse redo (⟳ same provider+seed), doctor's build-lock/cache facts.
Structural equivalents that predate this study: events.jsonl already *is*
the BEP-style single event log; `--readonly` is the Workspace-Trust analog;
directory-as-project is Obsidian's vault model.

| # | Pattern | Source(s) | Manju landing spot | Effort | Prio |
|---|---------|-----------|--------------------|--------|------|
| 1 | Unread-first triage: per-workspace "last reviewed" mark, `new` badges on takes/proposals, j/k walks unseen first | Figma comments | `src/manju/gui/state.py` (add take created-ts to the card; proposals already carry `mtime`) + `page.py` localStorage keyed by project | S | P0 |
| 2 | Cache hits show what they saved: "fresh — skipped, saved ~$X" per shot and a run total | Nx replayed hits, BuildBuddy | `src/manju/build/graph.py` plan (ledger in `runtime/state.py` has real per-take costs) + build panel in `gui/page.py` + `manju build` output | S | P0 |
| 3 | Board staleness on the button: stamp the project fingerprint into board.html; header shows "重新生成 board.html (behind)" vs up-to-date | Descript Update relabel | `src/manju/board/board.py` (stamp) + `gui/state.py` fp compare + `gui/page.py` header; `manju board` in `cli.py` | S | P0 |
| 4 | Schema validation at keystroke time in the truth editors, field docs on hover; same schemas published over MCP so the agent pre-validates proposals | VS Code settings.json | `manju schema` already exports JSON Schemas (`cli.py`, `core/models.py`); wire into the shot/bible/rules editors in `gui/page.py`; expose via `mcp/tools.py` | M | P0 |
| 5 | Conflict banner instead of silent revert: keep the 409 auto-revert, but return check errors + fresh disk text; render buffer-vs-truth diff with one-click re-apply onto the new base | Figma visibility-over-locking; VS Code | `src/manju/gui/server.py` `/api/shot/<id>` POST + `gui/page.py` editor dialog (also covers the editor-vs-build race GUI.md names an honest limit) | M | P0 |
| 6 | Presence strip: Director / Agent / Engine chips in the header — agent lit by recent `events.jsonl` actor, engine by `build_lock`/active job | Figma avatar stack | `/api/state` already carries `events` tail + `build_lock{actor,pid}` (`gui/state.py`); render in `gui/page.py` header | S | P1 |
| 7 | Estimate-vs-actual spend table: per-shot/per-day rows with the delta column that calibrates trust in dry-run pricing | Runway spend history | new `manju spend` in `cli.py` reading the `runs` ledger (`runtime/state.py`); spend section on the state page (`gui/page.py`) | S | P1 |
| 8 | Rendition provenance badge on every player: 原件 vs 转码预览 (and on board takes) | Resolve PXY/HQ badges | `gui/state.py` already picks `/media/` vs `/preview/` per take — surface the choice as a badge in `gui/page.py` + `board/board.py` | S | P1 |
| 9 | Compare two takes with a linked scrubber (one timebar drives two `<video>`s); takes are append-only so the pairing is stable | Frame.io comparison viewer | shot-card 对比 action, `gui/page.py` only (client-side `currentTime` sync) | M | P1 |
| 10 | Voice audition before generation: 试听 per character synthesizes the first sentence, cached by (voice, text-hash); `preview_tts` over MCP | CapCut TTS preview | `providers/edge_tts.py` (keyless, so audition is free) + `/api/voice` preview flag in `gui/server.py` + `mcp/tools.py`; bible editor button | M | P1 |
| 11 | Follow-the-build: click the running job's phase chip → auto-scroll/flash the shot being rendered, sticky "跟随构建 — 停止" banner | Figma follow mode | jobs already publish coarse phases (`gui/jobs.py` `progress`); pure `gui/page.py` wiring | M | P1 |
| 12 | Fast-review pass: `f` in review mode auto-advances through unreviewed takes, each capped ~5 s (playbackRate scaled), any verdict key interrupts; shows "N takes, ~Xs" up front | Resolve Fast Review | review keyboard mode in `gui/page.py` | S | P2 |
| 13 | Share-scoped board export: `manju board --latest-only\|--all-takes --notes shared\|none`; take_notes marked internal never render in board.html | Frame.io share scoping + lock icon | `src/manju/board/board.py` + `board` command in `cli.py` (board currently renders no take_notes at all — additive) | S | P2 |
| 14 | Workspace recents list shared across surfaces: known projects in `~/.manju/workspaces.yaml`; "remove from list" never touches the project dir | Obsidian vault switcher | `discover_workspace` in `gui/server.py` + `manju gui` in `cli.py`; same file readable by a future `manju workspace list` | S | P2 |
| 15 | Per-workspace session restore: kb focus, filter chips, open panels persisted per project and restored on `/api/switch` and reopen | VS Code / Obsidian workspaces | `gui/page.py` localStorage keyed by workspace slug (UI state stays out of versioned truth, as VS Code does) | S | P2 |

## 4. Rejections (doctrine conflicts)

- **In-engine AI assists** (Resolve Close Up / Smooth Cut, Descript
  Underlord, Runway Chat Mode, CapCut 一键成片 / 智能剪口播 recognition
  halves) — §0: intelligence stays outside; Claude Code behind CLI/MCP with
  the proposals channel *is* the adoptable form, already shipped.
- **Live share-link machinery** — revocation, passphrase auth, view
  counters, activity feeds (Frame.io), stable-URL cloud pages (Descript) —
  all require a serving backend with per-request auth/telemetry; board.html
  is a static file, and once sent it is the recipient's.
- **Anonymous inbound comments** (Frame.io zero-account write) — an
  unattended write channel into truth files; events.jsonl requires an actor.
- **Auto-publish** — already rejected in COMPETITIVE-STUDY.md; exports are
  the boundary.
- **Silent last-writer-wins merging** (Figma's sync core) — truth is text:
  conflicts must surface as explicit diffs the director approves (row 5 is
  the adoptable half).
- **Ephemeral channels** (Figma cursor chat) — decisions with no textual
  trace violate truth-is-text; the collaborator is an async agent, not a
  co-present human.
- **Settings/vault cloud sync** (VS Code Settings Sync, Obsidian Sync) — no
  cloud; because keymap/config/workspace lists are plain text, "sync" is
  git or a dotfiles repo, deliberately the director's problem.
- **Fleet analytics** (Nx Cloud flaky scatter plots, BuildBuddy Trends) —
  single director, dozens-not-thousands of runs: no sample size, no tenant,
  near-zero decision value; the one number worth keeping is row 2/7.
- **Template marketplace + cloud backup** (CapCut) — no cloud; a template,
  if ever wanted, is a plain YAML file in-repo.

## 5. Top-5 recommendation

Chosen against what the GUI already has (R10–R13 closed the display side of
"cost before commitment" and "explain the cache"); these five close the
remaining trust loops rather than add surface:

1. **Schema validation in the truth editors (row 4).** The check-gated
   auto-revert is today's most trust-damaging moment: submit-time rejection
   throws typed work away. `manju schema` already exports the models —
   wiring it into the editors converts rejection into keystroke-time
   guidance, and publishing it over MCP gives the agent the same guardrails.
2. **Conflict banner with re-apply (row 5).** Completes row 4: when the
   revert *does* fire (or the file moved under the editor mid-build — a
   documented honest limit), the director sees a buffer-vs-truth diff and
   keeps their work. Together, 4+5 make the editors feel as safe as the
   engine actually is.
3. **Unread-first triage (row 1).** The single highest-leverage review
   feature for one director returning to a long-running project: "what
   changed since I last looked" becomes a bounded queue. Composes with the
   already-shipped chips, j/k walk, and 好/弃 verdicts at S effort.
4. **Cache savings + spend delta (rows 2+7, one small wave).** R10 put the
   estimate on the button; these show what fresh targets *didn't* cost and
   how estimates track actuals. The delta column is what teaches the
   director to trust the ask_before gate — the ledger data already exists.
5. **Board staleness on the button (row 3).** One fingerprint stamp plus a
   header compare turns board.html — the primary sharing artifact — into
   Descript's "Update" pattern: viewers never silently see a stale board,
   and regeneration stays a deliberate act. Smallest item on the list;
   disproportionate honesty payoff.

Key sources (full lists in UX-STUDY.md and the study inputs):
Frame.io <https://help.frame.io/en/articles/9105337-keyboard-shortcuts>,
<https://help.frame.io/en/articles/9952618-comparison-viewer>;
Resolve <https://www.blackmagicdesign.com/products/davinciresolve/cut>;
Descript <https://help.descript.com/hc/en-us/articles/10164106619405-Version-history>,
<https://help.descript.com/hc/en-us/articles/10255817744653-Export-and-publish-content-with-Descript-web-links>;
CapCut <https://www.capcut.com/help/use-and-export-templates-in-capcut>,
<https://zhuanlan.zhihu.com/p/639816219>;
Runway <https://docs.dev.runwayml.com/guides/pricing/>,
<https://runwayml.com/changelog>;
build viewers <https://nx.dev/docs/troubleshooting/troubleshoot-cache-misses>,
<https://www.buildbuddy.io/ui/>, <https://bazel.build/remote/bep>,
<https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs>;
Figma <https://www.figma.com/blog/multiplayer-editing-in-figma/>,
<https://help.figma.com/hc/en-us/articles/360039825314-Guide-to-comments-in-Figma>;
local-first <https://code.visualstudio.com/docs/editing/workspaces/workspace-trust>,
<https://code.visualstudio.com/docs/configure/settings>,
<https://obsidian.md/help/Files+and+folders/Manage+vaults>.
