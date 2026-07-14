# 便利性计划审计合卷 (2026-07-14) — the 7-hour convenience program

Mandate: "更方便、更好用", ~7 hours continuous. Two dedicated auditors, two
lenses; every cited site personally re-verified before landing. Waves landed
as DECISIONS #47 / #48 / #48a / #48b (+ gate fixes ba5348a); all green and
fast-forwarded into the default branch at 4ef7d83.

## Disposition ledger

| # | finding (short) | verdict |
|---|-----------------|---------|
| CLI-0 | shot/take ids accepted exactly one spelling (typed dozens of times daily) | LANDED #47 — _resolve_shot_arg/_resolve_take_arg, 18 commands |
| CLI-0b | select without take named no candidates | LANDED #47 |
| CLI-0c | CLI startup latency (4 s --help) | DISMISSED with measurement — cold FS cache; warm 0.84 s; remainder = pydantic models nearly every command needs |
| A2-1 | redo success dead-ends | LANDED #48 (single + batch tail) |
| A2-2 | select success dead-ends | LANDED #48 |
| A2-3 | tasks failed rows lack verbatim retry | LANDED #48 |
| A2-4 | exports rows lack their one command | LANDED #48 (kind→command map) |
| A2-5/6 | voice / align successes dead-end | LANDED #48 |
| A2-7 | align plan-only never names --apply | LANDED #48 |
| A2-8 | transcribe never names the align chain | LANDED #48 (actual paths; as_posix after gate red) |
| A2-9 | repair --auto dead-ends | LANDED #48 (silent at zero work) |
| A2-10 | build failure lacks a retry echo | REJECTED — manju failures pointer already printed; verb self-evident |
| A2-s1 | build ok lacks next step | REJECTED — status owns the 下一步 ladder |
| A2-s2 | 跳过 tooltip "快捷键 j" wrong | REJECTED — auditor misread; skip IS setActive(+1) = j |
| A2-s3 | review 通过 has no key | LANDED #48a (a = approve; redo stays keyless — spend never behind one keystroke) |
| A2-s4 | package/masters lack next step | LANDED #48a (→ manju exports) |
| A2-s5 | board subtitle panels point at terminal | REJECTED — bounded-board design for spendy ops |
| A3-1 | review stale filter but no batch redo | LANDED #48b (rv-redo-stale → /api/redo-batch, shots-only, no assume_yes) |
| A3-2 | board redo/build/package unconfirmed | LANDED #48b for redo only (server §8.3 gate verified intact); build/package REJECTED — not spend-by-default, confirm = nagging |
| A3-3 | board select reloads away parked state | LANDED #48b (in-place flip; ★ button carries identity) |
| A3-4 | storyboard bar lacks redo/voice batch | LANDED #48b (wired the two existing endpoints) |
| A3-5 | caption clip drops its cue index | LANDED #48b (#cue-N + scroll/flash) |
| A3-6 | review shot id not linked to lab | LANDED #48b |
| A3-7 | exports lacks generate-all-stale | DEFERRED — medium size, weekly/rare frequency, below the wave line |
| A3-8 | director confirms reject not run | REJECTED — discarded-proposal recoverability unproven; the confirm stays |

Completeness check at close: lenses covered = CLI typing/messages, keyboard,
GUI click-flow, startup latency (measured), shell completion (absent by
design — adding it would thaw the frozen CLI surface). Gate reds during the
program: 2 (Windows path separator — fixed as_posix; ubuntu board-select
flake — non-recurring on rerun, assertion now carries the refusal payload).
Declared DRY for this mandate.

---

# 附:审计员原始报告 1(CLI/流程透镜)

# Manju daily-loop friction audit #2 — "type/click more than necessary; output that dead-ends"

Lens: where the owner types/clicks more than needed, or an output states a fact but not the next keystroke.
All findings verified against src at the cited lines. CLI surface is frozen — every fix below is one
message-string / one echo / one default, never a new command, page, or signature change.
Excluded per brief: status next-step/todo, per-shot next-action on review/board, shorthand resolvers,
select-without-take listing, structured errors, spend-gate "确认后重试" hints (already print verbatim retry),
new() bridge, doctor rows, empty states, stale-tab guard, grouped help.

Ranked by frequency × smallness (biggest, cheapest wins first).

---

## 1. `redo <shot>` success dead-ends — never names review/select as the next keystroke  [DAILY]
(a) After generating fresh takes the owner ALWAYS reviews or selects next, but the success line stops at the take names.
(b) src/manju/cli.py:1336 `typer.secho(f"{shot_id}: new takes {', '.join(takes)}", ...)` — nothing after it.
    Batch path identical: `_print_batch_result` ends at cli.py:1350-1353 with a ran/skipped/failed tally, no next verb.
(c) Append one clause: `... — 看/选下一条: manju select {shot_id} <take>(先在 manju board 里看)`.
(d) daily.

## 2. `select <shot> <take>` success dead-ends — never names `manju build` to re-render  [DAILY]
(a) Selecting a new take changes the film but the owner isn't told the render that makes it real is `manju build`.
(b) src/manju/cli.py:1413 `typer.secho(f"{shot_id}: selected {take}", ...)` — terminal line.
(c) Append: `... — 让改动落到成片: manju build`.
(d) daily.

## 3. `manju tasks` failed rows omit the verbatim retry command — yet the sibling section prints it  [WEEKLY]
(a) A failed job row shows `#id shot provider failed ↳ reason` but never the exact `manju tasks retry #id`,
    even though the adjacent "unresolved submissions" block DOES print its recovery commands verbatim.
(b) Failed rows: src/manju/cli.py:6678-6686 (head + `↳ reason`, no command).
    Contrast unresolved: cli.py:6700-6704 prints `manju tasks attach-remote-job … / manju tasks abandon …` verbatim.
(c) In the `if t["reason"]:` block add, when `t["status"]=="failed"`: `↳ 重试: manju tasks retry #{t['id']}`.
(d) weekly. (Answers brief Q2: for genuine job failures the retry command is NOT printed verbatim anywhere.)

## 4. `manju exports` names what's missing but not the one CLI command to make it — punts to the GUI  [WEEKLY]
(a) Each deliverable row prints 缺失/待更新 + basis + path, then a single footer sends the owner to the GUI;
    the per-row "one command to make it" (which is deterministic per kind) is never printed on the CLI.
(b) Row render src/manju/cli.py:3069-3082; GUI-only footer cli.py:3101
    `（生成/更新与标记已人工确认见 manju gui → 导出中心）`. (Release-level next_actions DO print commands at 3097,
    proving the idiom exists — it's just missing at the per-row level.)
(c) On a missing/stale row, print a kind→command hint using the known static map:
    free exporters → `→ manju export --srt|--otio|--jianying|…`; final/proxy → `→ manju build`;
    封面/预告 → `→ manju package`. One dict + one echo line.
(d) weekly.

## 5. `voice <shot>` success dead-ends — never names `manju build` (the "newest take wins on next build")  [WEEKLY]
(a) The docstring says the new voice only takes effect on the next build, but the success line doesn't say so.
(b) src/manju/cli.py:3810 `typer.secho(f"{shot_id}: 新配音 {media.stem} ({tts.id})", ...)`; batch via _print_batch_result (1350-1353).
(c) Append: `... — 让新配音进成片: manju build`.
(d) weekly.

## 6. `align <shot>` single-shot success dead-ends — never names `manju build`  [WEEKLY]
(a) After writing timing.json the caption change only lands on a rebuild, but the owner isn't told.
(b) src/manju/cli.py:3949-3953 (`timing → …` + advisories, then ends).
(c) Append to the success secho: `... — 重编字幕/成片: manju build`.
(d) weekly.

## 7. `align --media … --shots …` prints a plan but never says "add --apply to apply it"  [WEEKLY]
(a) Plan-only is the DEFAULT; the plan table dead-ends without naming the flag that executes it, so the
    owner must recall `--apply` (and `--rows`) from --help.
(b) src/manju/cli.py:3919-3929 prints rows + unmatched with no apply hint; `--apply` is the gate at cli.py:3891.
(c) After the rows loop add: `echo("加 --apply 应用此计划(--rows 1,3-5 选行)")`.
(d) weekly.

## 8. `transcribe` prints the SRT path but not the `manju align … --from-srt <srt>` it exists to feed  [WEEKLY]
(a) transcribe's whole purpose (per align's own docstring: "run manju transcribe first, then --from-srt") is to
    hand an SRT to align, yet it prints the path as a fact and stops — the next keystroke is left to memory.
(b) src/manju/cli.py:4101-4102 `typer.secho(f"{rel}: {len(segments)} segments (source: {source})", ...)`.
    The chain is spelled out in align's docstring at cli.py:3850-3851.
(c) Append: `... — 用它对齐: manju align --media <media> --shots S001-… --from-srt {rel}`.
(d) weekly.

## 9. `repair --auto` success dead-ends — never names build/qc as the next step  [WEEKLY]
(a) "repaired N, remaining M" tells the owner nothing about re-rendering the repaired takes or where the
    remaining M live.
(b) src/manju/cli.py:2247 `typer.echo(f"repaired {done}, remaining for human review: {left}")`.
(c) Append: `... — 重渲染看效果: manju build;剩余见 manju failures`.
(d) weekly.

## 10. `manju build` failure points to diagnosis but never prints the retry verb  [DAILY-when-failing]
(a) On failure the owner is told to read `manju failures`, but the actual retry (re-run `manju build`, or the
    spend-gated `--yes` variant) is never printed — one extra recall step every failed build.
(b) src/manju/cli.py:1222 `失败 {n} — manju failures 看原因/证据/建议`; then cli.py:1246 prints "build failed" and exits.
    (Contrast: the waiting_user spend stops elsewhere DO print `确认后重试: manju … --yes`.)
(c) On the error branch add: `修复后重试: manju build`.
(d) daily (only when a build fails, but that's a core inner-loop event).

---

## Also-verified, smaller (below the top 10)

- `manju build` GREEN success ("build ok", cli.py:1246-1247) names no next step (审片 manju board / 交付 manju exports);
  the build output is already long, so MEDIUM value. [daily]
- Review page: the 跳过 button carries `title="快捷键 j"` (pages.py:560) but the header documents j as 上下 navigation
  (pages.py:581) and the keydown handler binds j to `setActive(active+1)` (pages.py:2006) — the tooltip mislabels a
  nav key as "skip". Tooltip-only fix. [rare]
- Review page: 重做 (redo) and 通过✓ (qapprove) buttons (pages.py:556-557) have no keyboard binding and aren't in the
  header key legend — the g/x/j/k/space set stops short of the two most common verdict actions. [rare]
- `package` success (cli.py:2898-2900) and `masters` success (cli.py:3999-4004) print paths with no next step
  (`manju exports` to see delivery readiness). [rare]
- board serve subtitles panel dead-ends to a terminal command ("先 manju build", board.py:2163; "或运行
  manju build --target qc", board.py:2281) with no in-page action, though these are heavy/spendy ops so a
  terminal hop is defensible. [rare]


---

# 附:审计员原始报告 2(GUI 点击流透镜)

# Manju GUI Audit — Lens: DAILY CLICK-FLOW FRICTION (bulk gaps / lost state / two-page round-trips / confirm consistency)

Scope: src/manju/gui/{pages,page,edit,storyboard,lab_page,exports_page,pages_t,director_page,ingest_page,series_page}.py + src/manju/board/board.py
Engine batch capability proven in cli.py: `redo_batch` (--shots/--all-stale/--all-missing/--all, cli.py:1259-1316), `voice_batch` (--shots/--all/--missing, cli.py:3658-3789), ingest `--all-matched` (cli.py:1056-1099), export multi-profile (board/server.py:299-312). GUI endpoints already exist: `/api/redo-batch` + `/api/voice-batch` (server.py:785-786, 1431-1483).

Ranked by frequency x smallness.

---

## 1. /review has a "待更新/stale" filter but no batch redo — per-shot redo + a confirm on EACH  [BULK GAP · DAILY]
(a) Filtering the review queue to STALE surfaces every shot that needs regen, yet the only redo is one button per card that fires `/api/redo` and pops `window.confirm` once per shot — the exact multi-select the workbench already ships.
(b) Lacking side: pages.py:606 (`("stale","待更新")` filter chip), pages.py:557 (per-shot `data-act="redo"`), pages.py:1926-1934 (`/api/redo` one shot, `window.confirm` each), pages.py:610-625 (`_queue_bar_html` toolbar has filter chips + prev/next but no batch action).
    Proof capability exists: page.py:4716-4759 (workbench `selectAllStale()` + floating bulk bar → `/api/redo-batch`/`/api/voice-batch` with a plan modal), server.py:1431-1460 (`_act_redo_batch`), cli.py:1260 (`--all-stale`).
(c) Fix: add one "批量重做待更新" button to `_queue_bar_html`; its handler collects `.rv-shot[data-buildstate="stale"]:not(.rv-filtered-out)` `data-shot` ids, single confirm, POST `/api/redo-batch {shots, assume_yes:true}` (copy page.py:4740-4746). No new endpoint/page.
(d) DAILY.

## 2. Board redo / build / package SPEND with one click and NO confirm — main GUI confirms the same actions  [CONFIRM CONSISTENCY (missing) · DAILY]
(a) On the review board every `data-act` (including money-spending `redo` and multi-minute `build`) is dispatched straight to `/api/<act>` with zero confirmation, while the identical redo on /review and the paid rebuild in /edit both gate on a spend/cost confirm.
(b) Lacking side: board.py:901-907 (delegated handler `post(btn.getAttribute("data-act"), body)` — no confirm branch), board.py:1519 (redo button), board.py:2046 (build button), board/server.py:279-289 (`_api_redo` really calls `redo_shot` = generation spend).
    Other side (pattern to copy): pages.py:1929 (`window.confirm("重做镜头 … 将产生新的生成花费。")`), edit.py:2520-2523 (`window.confirm` with `estimated_cost`), page.py:4742 (batch redo behind plan modal).
(c) Fix: in board.py `post()`, gate `action in {"redo","build","package"}` behind a `window.confirm` carrying the spend/time warning (mirror pages.py:1929). ~4 lines, no engine change.
(d) DAILY (redo) / WEEKLY (build).

## 3. Board "选用/select" does a full page reload, dropping compare mode + parked players  [LOST STATE · DAILY]
(a) Picking a take reloads the whole board, discarding any open compare wraps, parked frames and playback — even though the board already has an in-place success path that was added precisely to stop reloads clobbering that state.
(b) Lacking side: board.py:486 (`post()` default branch `location.reload()`), reached by the `select` button (board.py:1398-1399) which passes no `onOk`.
    Same-page pattern to copy (already in the file): board.py:482-485 (F16: `annotate` passes an `onOk` that patches the DOM in place "instead of location.reload(), which dropped every parked player / compare mode / active tab").
(c) Fix: give the select click an `onOk(d)` that moves the "已选用" badge / updates the selected chip in place (same mechanism annotate uses), instead of falling through to reload.
(d) DAILY.

## 4. /storyboard already has full multi-select but the batch bar offers only approve/lock — no redo/voice  [BULK GAP · WEEKLY, tiny]
(a) After bulk-editing shot text (which marks rows STALE, shown in the 状态 column) the owner has checkboxes, select-all and a floating bar right there — but must leave for the workbench to regenerate, because the bar wires only approve-all and lock-all.
(b) Lacking side: storyboard.py:413-424 (`sb-batchbar` = approve-all + lock-all + clear only), storyboard.py:756-783 (batch handlers cover only `/api/storyboard/approve` + `/api/storyboard/lock-batch`); selection helper already exists at storyboard.py:701-705 (`selected()`).
    Proof: `/api/redo-batch` + `/api/voice-batch` already live (server.py:785-786); identical button+post shape at page.py:4738-4759.
(c) Fix: add "批量重做"/"批量配音" buttons to the existing `sb-batchbar`, handlers POST `selected()` to `/api/redo-batch`/`/api/voice-batch` (with a confirm). Reuses existing selection + endpoints; two buttons.
(d) WEEKLY.

## 5. Timeline caption clip links to /subtitles but drops the cue index it already knows  [TWO-PAGE ROUND-TRIP · WEEKLY]
(a) Clicking a caption block in the editor to fix its wording lands you at the TOP of the subtitles table with no scroll/highlight, even though the link carries the cue index and every cue row has a matching index attribute.
(b) Page A: edit.py:1073-1076 (`<a … href="/subtitles" data-cue="{i}" …>` — index computed but not put in the href/hash).
    Page B: pages_t.py:157-160 (cue rows carry `data-index="{idx}"`; the page never reads `location.hash` to jump to one).
(c) Fix: emit `href="/subtitles#cue-{i}"`; add a tiny onload in pages-t.js that scrolls to + flashes `.cue-row[data-index]` matching the hash — mirror the hash-restore already in board.py:508-515 (`restoreTab`).
(d) WEEKLY.

## 6. /review shot header shows the shot id as plain text — no link into its per-shot lab  [TWO-PAGE ROUND-TRIP · WEEKLY]
(a) To regenerate a reviewed shot at a chosen quality (not the flat inline redo) the owner opens /lab and re-finds the shot by hand, though the review card header already prints the id.
(b) Lacking side: pages.py:545 (`<h2>{sid} …` rendered as text; card actions are inline good/reject/redo/repair only, no deep-dive link).
    Proof link pattern exists: lab_page.py:356 (`href="/lab?shot={quote(sid)}"`) — /lab is the per-shot quality-choice regen surface.
(c) Fix: wrap the id in `<a href="/lab?shot={sid}">` (or add a small "实验室" chip in `rv-head`). One f-string edit, existing route.
(d) WEEKLY.

## 7. /exports generates one deliverable per click — no "generate all stale", though the export core is multi-profile  [BULK GAP · WEEKLY/RARE]
(a) With several free exporters stale (srt/otio/剪映/CapCut/封面/预告 are separate cards) the owner clicks 生成/更新 on each, even though the export core happily takes a list of profiles in one call.
(b) Lacking side: exports_page.py:89 (one 生成/更新 button per card), server.py:3390-3411 (`_act_exports_generate` accepts a single `kind`).
    Proof capability exists: board/server.py:299-312 (`_api_export` takes a `profiles` LIST) and CLI `export` accepts multiple profiles.
(c) Fix: add a "生成全部待更新" button in the summary bar whose handler loops the `.xc-card` whose freshness is stale/missing and fires the existing `/api/exports/generate` per kind (pure JS, no endpoint change), or extend the endpoint to accept `kinds:[…]` reusing the same exporter branch.
(d) WEEKLY/RARE.

## 8. /director puts a confirm on the safe "否决" (reject) while the paid "执行" (run) has none  [CONFIRM CONSISTENCY (over-confirm) · RARE]
(a) Rejecting a proposal (a reversible, non-spend state change — you can re-propose) pops a confirm, but the money-spending 执行/run does not — the guard sits on the cheap action, not the costly one.
(b) director_page.py:396 (`if (!confirm("否决提案 … ?")) return;`) vs director_page.py:384-388 (`/api/director/run` posts with no confirm — it relies on the separate 确认→执行 two-step, §8.3, which is fine; the reject confirm is the redundant one).
(c) Fix: drop the `confirm` on reject (toast-undo is already the house style), keeping the confirm budget for spend actions only.
(d) RARE.

---
### Notes / non-findings verified
- /ingest batch review already has the bulk action ("全部确认已匹配" → `--all-matched`, ingest_page.py:122). Not a gap.
- /series new-episode preserves eid/title inputs on failed submit (series_page.py:696-701). Not a gap.
- pages_t.py mixer/packaging "value=''" clears are on freshly-cloned blank rows only (correct), and applyMixer updates in place without reload. Not a gap.
- exports_page.py priced final/proxy correctly refuse to spend and link to the workbench plan modal (exports_page.py:85-88). Consistent.
