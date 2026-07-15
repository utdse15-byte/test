"""Local Web GUI front-end (§1-⑦ revisited, ``manju gui``) — v3.

The browser side of the ``manju gui`` command: three pure functions returning
the page, the stylesheet and the app script that the local HTTP server serves
as ``/``, ``/app.css`` and ``/app.js``. The GUI is a STRICT CLIENT of the
engine API — truth stays in the project's text files; the page never computes
build state itself, it only renders what ``GET /api/state`` (and friends)
report and posts intents back (`select`, `build`, `redo`, `voice`, `qc`).

v2 added, on the same contract: a check-gated shot YAML editor (+ new-shot
template), a drag-and-drop importer (``/api/upload``), a proportional
timeline strip (``/api/timeline``, fingerprint-cached), a doctor panel
(``/api/doctor``), a collapsible git panel (``/api/git/*``, lazily fetched)
and the build-lock chip (``state.build_lock``).

v3 adds: fingerprint-driven refresh (``/api/watch`` long-poll replaces the
idle 5 s interval; the 1.5 s poll survives only while a job is active, since
job state transitions don't all bump the fingerprint), the §8.3 spend-confirm
banner (a build job finishing with ``result.waiting_user`` offers 「确认花费
并构建」 = resend the same params + ``assume_yes``), readonly awareness
(``state.readonly`` shows a chip and disables every mutating control), a
collapsible proposals panel (``/api/proposals``, fetched at most once per
fingerprint change), bible/rules editors reusing the shot-editor dialog
(``/api/bible/<name>``, ``/api/rules``), ↑/↓ shot reordering
(``POST /api/index``), the workspace switcher chip (``/api/projects`` +
``POST /api/switch``), a review keyboard mode (?, j/k, e, 1-9, space),
``<img>`` rendering for image takes and the ``latest_final_note`` honesty
line under the header spend row.

v3.1 adds three patterns from REPORTS/COMPETITIVE-UX-STUDY.md: the editor
conflict banner (a save 409 now carries ``current`` — the reverted-to disk
text — so the dialog renders a collapsible, approximate buffer-vs-truth line
diff plus a 以真相为底重填 reload button; the buffer itself is never lost),
unread-first triage (a per-project ``manju-reviewed-<name>`` localStorage
snapshot of take names: takes absent from the last snapshot get a 新 chip,
and the shots bar gains 标记已阅 plus a live 未阅 N count chip) and the
cache-savings display (``saved_cost``/``skipped`` on dry-run estimates and
done build jobs render as 缓存命中省 ≈X / 跳过 N — the Nx replayed-hits
pattern).

Design stance:

  * CSP-friendly by construction (``script-src 'self'; style-src 'self'``):
    CSS and JS live in external files, there are no inline handlers and no
    inline ``style=`` attributes (dynamic geometry — budget bar, timeline
    clip widths — goes through the CSSOM, which strict CSP permits); every
    mutating request carries the ``X-Manju-Token`` header read from the
    ``manju-token`` meta tag;
  * XSS-safe by construction: ALL server-supplied text (dialogue, YAML,
    git diffs are arbitrary user text) enters the DOM via ``textContent`` /
    ``createTextNode`` — the script never assigns ``innerHTML``; the diff
    view splits lines and sets textContent per line; the two values embedded
    server-side (project name, token) are HTML-escaped here;
  * zero external assets, zero frameworks — vanilla ES2020 on the dark board
    palette (:mod:`manju.board.board`), fully offline;
  * degrade section-by-section: a 500 from /api/timeline, /api/doctor or
    /api/git/* renders one muted line in its own section and never breaks
    the poll loop or its neighbours;
  * pure functions only: no I/O and no imports beyond the stdlib, so the
    server (and tests) can render without a project on disk.
"""

from __future__ import annotations

import html

__all__ = ["render_page", "render_css", "render_js"]

# --------------------------------------------------------------------- CSS --
# Extends the static board's palette and CJK-aware font stack (§1-⑦); the
# animations are the queued/running job-chip pulse, the amber build-lock pulse
# and the one-shot flash when a timeline clip is clicked.

_CSS = """
/* manju gui — dark workbench stylesheet (served as /app.css). */
:root {
  --bg: #14161a; --panel: #1d2027; --panel2: #24272f; --line: #333844;
  --fg: #e8eaed; --muted: #9aa0aa; --accent: #6ea8fe; --star: #ffcf5c;
  --ok: #7ee2a8; --warn: #ffcf5c; --err: #ff8a90;
  /* the info/hover tint behind accent-coloured text (next-step bar, unread
   * chip, drag highlight …) — was hand-copied as #202b40 across modules. */
  --accent-bg: #202b40;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas,
    "Noto Sans Mono CJK SC", monospace;
  /* Native UA widgets (scrollbars, form controls, <video> chrome) follow the
   * dark palette — without this, Windows renders bright-grey scrollbars into
   * every overflow panel of the dark workbench. */
  color-scheme: dark;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
html { scrollbar-gutter: stable; }
body {
  background: var(--bg); color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
    "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", "Source Han Sans SC",
    "WenQuanYi Micro Hei", sans-serif;
  line-height: 1.5; padding-bottom: 4rem;
}
main { padding: 0 1.2rem 1.2rem; max-width: 1600px; margin: 0 auto; }
a { color: var(--accent); }
/* THE one owner of "hidden means hidden". Both the class and the attribute
 * lose to any later `display:` rule at equal specificity (the /create skill
 * modal shipped broken exactly that way: `.cw-modal{display:flex}` beat
 * [hidden] and the overlay permanently covered the page). !important retires
 * the whole conflict class — per-selector `.foo.hidden{display:none}` patches
 * are no longer needed and must not be re-introduced. */
.hidden { display: none !important; }
[hidden] { display: none !important; }
.muted { color: var(--muted); }
.loading { color: var(--muted); margin: 0; animation: mj-breathe 1.2s ease-in-out infinite alternate; }
@keyframes mj-breathe { from { opacity: .5; } to { opacity: 1; } }
::selection { background: #2b4a75; color: var(--fg); }
/* thin dark scrollbars on inner overflow panels (Chromium + Firefox). */
* { scrollbar-width: thin; scrollbar-color: #3d434f transparent; }
*::-webkit-scrollbar { width: 10px; height: 10px; }
*::-webkit-scrollbar-track { background: transparent; }
*::-webkit-scrollbar-thumb {
  background: #3d434f; border-radius: 999px;
  border: 2px solid transparent; background-clip: padding-box;
}
*::-webkit-scrollbar-thumb:hover { background: #4d5563; background-clip: padding-box; }
h1 { margin: 0; font-size: 1.35rem; }
h2 {
  margin: 0 0 .6rem; font-size: 1.02rem; border-bottom: 1px solid var(--line);
  padding-bottom: .3rem;
}
h3 { margin: .2rem 0 .4rem; font-size: .92rem; }

/* ------------------------------------------------------------- panels -- */
.panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: .9rem 1.1rem; margin: 1rem 0;
}
#header {
  margin: 0 0 1rem; border-radius: 0; border-width: 0 0 1px; padding: 1.1rem 1.4rem;
}
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; }
.cols .panel { margin: 0; }

/* ------------------------------------------------------------- header -- */
.head-top { display: flex; align-items: center; gap: .9rem; flex-wrap: wrap; }
.chips { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; }
.chip {
  background: var(--panel2); border: 1px solid var(--line); border-radius: 999px;
  padding: .1rem .6rem; font-size: .78rem; white-space: nowrap;
}
.chip.lock { color: var(--warn); border-color: #4a3a12; }
.chip.build-lock {
  color: var(--warn); border-color: #6b5518; background: #4a3a12;
  animation: mj-pulse 1.4s ease-in-out infinite;
}
.chip.readonly { color: var(--warn); border-color: #6b5518; background: #4a3a12; font-weight: 700; }
button.chip { cursor: pointer; font: inherit; font-size: .78rem; color: var(--fg); }
button.chip:hover { filter: brightness(1.15); }

/* --------------------------------------------------- workspace switcher -- */
.ws-wrap { position: relative; display: inline-block; }
.ws-menu {
  position: absolute; top: calc(100% + 4px); left: 0; z-index: 70;
  background: var(--panel2); border: 1px solid var(--line); border-radius: 8px;
  padding: .3rem; min-width: 240px; max-width: min(420px, 90vw);
  box-shadow: 0 8px 24px rgba(0, 0, 0, .55);
  display: flex; flex-direction: column; gap: 2px;
}
.ws-menu.hidden { display: none; }
.ws-item {
  background: transparent; border: 0; color: var(--fg); text-align: left;
  padding: .35rem .6rem; border-radius: 6px; cursor: pointer; font: inherit;
  font-size: .84rem; display: flex; gap: .6rem; justify-content: space-between;
  align-items: baseline; white-space: nowrap;
}
.ws-item:hover:not(:disabled) { background: var(--accent-bg); }
.ws-item:disabled { color: var(--muted); cursor: default; }
.ws-count { color: var(--muted); font-size: .76rem; }
.spend { color: var(--muted); font-size: .9rem; margin-top: .45rem; }
.final-note {
  margin-top: .45rem; padding: .3rem .6rem; border-left: 3px solid var(--warn);
  background: #4a3a12; color: var(--warn); border-radius: 0 6px 6px 0;
  font-size: .86rem;
}
.bar {
  height: 5px; max-width: 420px; background: var(--panel2); border-radius: 999px;
  overflow: hidden; margin: .3rem 0 .1rem;
}
.bar-fill { display: block; height: 100%; background: var(--accent); border-radius: 999px; }
.bar-fill.over { background: var(--err); }
.next-step {
  margin-top: .6rem; padding: .45rem .7rem; border-left: 3px solid var(--accent);
  background: var(--accent-bg); color: #cfe3ff; border-radius: 0 6px 6px 0; font-size: .92rem;
}
.final { margin-top: .6rem; display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; }
.final-link { font-size: .85rem; word-break: break-all; }
.final-video { width: 100%; }
.final-video video {
  max-width: 420px; width: 100%; border-radius: 8px; background: #000;
  display: block; margin-top: .5rem;
}

/* ------------------------------------------------------------ buttons -- */
.btn {
  background: var(--accent); color: #0b1220; border: 0; border-radius: 6px;
  padding: .38rem .85rem; font-size: .84rem; font-weight: 700; cursor: pointer;
  font-family: inherit;
}
.btn.ghost { background: var(--panel2); color: var(--fg); border: 1px solid var(--line); }
.btn.small { width: 100%; margin-top: .45rem; padding: .28rem .6rem; font-size: .76rem; }
.btn.mini { padding: .06rem .5rem; font-size: .76rem; font-weight: 700; line-height: 1.3; }
.btn:hover:not(:disabled) { filter: brightness(1.12); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
/* interaction feel: hovers ease instead of snapping, presses acknowledge. */
.btn, button.chip, .chip, .pnav a, .ws-item, .tl-clip, .dropzone {
  transition: filter .12s ease, background-color .12s ease,
    border-color .12s ease, color .12s ease, opacity .12s ease;
}
.btn:active:not(:disabled) { transform: translateY(1px); }
.btn:focus-visible, select:focus-visible, input:focus-visible, textarea:focus-visible,
a:focus-visible, button:focus-visible, summary:focus-visible,
[role="button"]:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 1px;
}
.btnrow { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .7rem; }

/* ----------------------------------------------------- import dropzone -- */
.dropzone {
  border: 1px dashed var(--line); border-radius: 8px; color: var(--muted);
  padding: .35rem .8rem; margin: 1rem 0 0; font-size: .8rem; text-align: center;
  cursor: pointer; user-select: none;
}
.dropzone.drag { border-color: var(--accent); color: var(--accent); background: var(--accent-bg); }
.dropzone.busy { border-style: solid; border-color: var(--accent); color: var(--fg); }

/* -------------------------------------------------------- build panel -- */
.controls { display: flex; flex-wrap: wrap; gap: .5rem 1.4rem; align-items: center; }
.ctl {
  color: var(--muted); font-size: .86rem; display: inline-flex;
  align-items: center; gap: .35rem;
}
.ctl select {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .25rem .45rem; font-family: inherit; font-size: .84rem;
}
.ctl input[type="checkbox"] { accent-color: var(--accent); }
.panel-out { margin-top: .8rem; max-height: 320px; overflow: auto; font-size: .86rem; }
.panel-out:empty { display: none; }
.panel-out p { margin: .25rem 0; }
.tablewrap { overflow-x: auto; }
.panel-out table { width: 100%; border-collapse: collapse; font-size: .82rem; }
.panel-out th, .panel-out td {
  text-align: left; padding: .25rem .55rem; border-bottom: 1px solid var(--line);
}
.panel-out th { color: var(--muted); font-weight: 600; }
.panel-out td.num, .panel-out th.num { text-align: right; white-space: nowrap; }
.panel-out tr.total td { font-weight: 700; border-bottom: 0; }
.panel-out tr.total td.grand { color: var(--star); font-size: .95rem; }
.panel-out tr.voice-row td { color: #cbb8ff; }

/* --------------------------------------------- spend-confirm banner §8.3 -- */
.spendbanner {
  margin-top: .8rem; border: 1px solid #6b5518; background: #4a3a12;
  color: var(--warn); padding: .6rem .8rem; border-radius: 8px;
}
.spendbanner .sb-text { white-space: pre-wrap; word-break: break-word; font-size: .9rem; }
.spendbanner .btnrow { margin-top: .55rem; }
.btn.confirm { background: var(--warn); color: #241a02; }

/* ------------------------------------------------- truth files (bible) -- */
.tfrow {
  display: flex; flex-wrap: wrap; gap: .4rem; align-items: center;
  margin-top: .7rem; padding-top: .6rem; border-top: 1px dashed var(--line);
}
.tfrow .lbl { color: var(--muted); font-size: .82rem; margin-right: .2rem; }
.vtag { margin-left: .45rem; }
.why { color: var(--muted); font-size: .84rem; }
.doc-line { font-family: var(--mono); font-size: .8rem; white-space: pre-wrap; padding: .08rem 0; }

/* ------------------------------------------------- badges (board §11) -- */
.badge {
  display: inline-block; font-size: .72rem; font-weight: 700; padding: .12rem .5rem;
  border-radius: 999px; text-transform: uppercase; letter-spacing: .03em;
  white-space: nowrap;
}
.st-fresh  { background: #17402a; color: #7ee2a8; }
.st-stale  { background: #4a3a12; color: #ffcf5c; }
.st-missing{ background: #3a3d44; color: #c4c9d2; }
.st-manual { background: #23324d; color: #8fb8ff; }
.st-needs  { background: #4a2f12; color: #ffb27a; }
.st-broken { background: #4d1f22; color: #ff8a90; }

/* --------------------------------------------------------- jobs strip -- */
.jb-queued    { background: #3a3d44; color: #c4c9d2; animation: mj-pulse 1.6s ease-in-out infinite; }
.jb-running   { background: #23324d; color: #8fb8ff; animation: mj-pulse 1.1s ease-in-out infinite; }
.jb-canceling { background: #4a3a12; color: #ffcf5c; animation: mj-pulse 0.9s ease-in-out infinite; }
.jb-canceled  { background: #3a3d44; color: #ffcf5c; }
.jb-done      { background: #17402a; color: #7ee2a8; }
.jb-failed    { background: #4d1f22; color: #ff8a90; }
/* round AA item 6: interrupted is an HONESTY signal, not a failure — the
 * same info-blue pairing jb-running/st-manual already use, not error red. */
.jb-interrupted { background: #23324d; color: #8fb8ff; }
.jnote { color: var(--muted); font-size: .8rem; }
@keyframes mj-pulse { 0%, 100% { opacity: 1; } 50% { opacity: .4; } }
.job {
  display: flex; align-items: baseline; gap: .6rem; flex-wrap: wrap;
  font-size: .86rem; padding: .22rem 0; border-bottom: 1px dashed var(--line);
}
.job:last-child { border-bottom: 0; }
.job .btn.mini { flex: 0 0 auto; }
.jkind { font-weight: 700; min-width: 3.5rem; }
.jsum { color: var(--muted); font-size: .8rem; word-break: break-all; }
.job details { width: 100%; font-size: .8rem; }
.job summary { cursor: pointer; color: var(--err); }
.jerr {
  white-space: pre-wrap; color: var(--err); background: #0f1114; border-radius: 6px;
  padding: .4rem .6rem; margin-top: .3rem; word-break: break-all;
}

/* ------------------------------------------------------ timeline strip -- */
.tl-strip {
  display: flex; flex-wrap: nowrap; overflow-x: auto; border-radius: 6px;
  background: var(--panel2); border: 1px solid var(--line);
}
.tl-clip {
  flex: 0 0 auto; min-width: 26px; padding: .28rem .25rem; font-size: .7rem;
  font-weight: 700; text-align: center; overflow: hidden; white-space: nowrap;
  text-overflow: ellipsis; cursor: pointer; border-right: 1px solid #14161a;
}
.tl-clip:last-child { border-right: 0; }
.tl-clip:hover { filter: brightness(1.25); }
.tl-h0 { background: #23324d; color: #8fb8ff; }
.tl-h1 { background: #17402a; color: #7ee2a8; }
.tl-h2 { background: #3a2a4d; color: #cf9bff; }
.tl-h3 { background: #4a3a12; color: #ffcf5c; }
.tl-h4 { background: #1c3f44; color: #7fd9e6; }
.tl-h5 { background: #4a2f12; color: #ffb27a; }
.tl-caps {
  position: relative; height: 10px; margin-top: 4px; background: var(--panel2);
  border: 1px solid var(--line); border-radius: 4px; overflow: hidden;
}
.tl-cap {
  position: absolute; top: 2px; height: 4px; min-width: 3px;
  background: var(--accent); border-radius: 2px; opacity: .85;
}
.tl-ruler {
  display: flex; justify-content: space-between; color: var(--muted);
  font-size: .7rem; margin-top: .25rem; font-variant-numeric: tabular-nums;
}
.tl-note { font-size: .8rem; }
@keyframes mj-flash {
  0% { outline: 3px solid var(--star); outline-offset: 2px; }
  100% { outline: 3px solid transparent; outline-offset: 2px; }
}
.shot.flash { animation: mj-flash 1.5s ease-out 1; }

/* --------------------------------------------------------- shots grid -- */
.shotsbar { display: flex; align-items: center; gap: .6rem; margin: 1.2rem 0 0; flex-wrap: wrap; }
.shotsbar h2 { flex: 1; margin: 0; border: 0; padding: 0; }
.shotsbar:empty { display: none; }
.ns-form { display: inline-flex; gap: .4rem; align-items: center; }
.ns-form.hidden { display: none; }
.ns-input {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .3rem .55rem; font-family: var(--mono);
  font-size: .84rem; width: 9rem;
}
#shots {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(430px, 1fr));
  gap: 1rem; margin: 1rem 0;
}
.shot {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 1rem 1.1rem;
}
.shot.kb-focus { outline: 2px solid var(--accent); outline-offset: 2px; }
.shot-head { display: flex; align-items: baseline; gap: .55rem; flex-wrap: wrap; }
.mvbtns { margin-left: auto; display: inline-flex; gap: .3rem; }
.sid { font-size: 1.05rem; font-weight: 700; }
.action { margin-top: .3rem; font-size: .92rem; }
.dialogue { color: var(--muted); margin-top: .25rem; font-size: .9rem; }
.speaker { color: var(--accent); }
.note { color: var(--muted); font-size: .82rem; margin-top: .2rem; font-style: italic; }
.takes { display: flex; flex-wrap: wrap; gap: .7rem; margin-top: .7rem; }
.take {
  background: var(--panel2); border: 1px solid var(--line); border-radius: 8px;
  padding: .5rem; width: 196px;
}
.take.selected { border: 2px solid var(--star); }
.take video, .take img { width: 100%; height: auto; border-radius: 5px; background: #000; display: block; }
.nomedia {
  width: 100%; aspect-ratio: 9/16; display: flex; align-items: center;
  justify-content: center; color: var(--muted); font-size: .78rem; background: #000;
  border-radius: 5px; text-align: center;
}
.tname { font-weight: 700; margin: .4rem 0 .1rem; font-size: .88rem; }
.star { color: var(--star); }
.tmeta { color: var(--muted); font-size: .74rem; word-break: break-all; }
.hint { color: var(--warn); font-size: .72rem; margin-top: .25rem; }
.voice-takes { margin-top: .6rem; display: flex; flex-direction: column; gap: .35rem; }
.voice-take { display: flex; align-items: center; gap: .6rem; flex-wrap: wrap; }
.voice-take audio { height: 30px; max-width: 260px; }
.vname { color: var(--muted); font-size: .8rem; }
.empty {
  background: var(--panel); border: 1px dashed var(--line); border-radius: 10px;
  padding: 1.4rem; text-align: center; grid-column: 1 / -1;
}

/* -------------------------------------------------------- shot editor -- */
dialog.editor { display: none; }
dialog.editor[open] {
  display: block; position: fixed; top: 50%; left: 50%;
  transform: translate(-50%, -50%); z-index: 60; margin: 0;
  width: min(760px, 94vw); max-height: 92vh; overflow: auto;
  background: var(--panel); color: var(--fg); border: 1px solid var(--line);
  border-radius: 10px; padding: 1rem 1.2rem;
  box-shadow: 0 12px 48px rgba(0, 0, 0, .6);
}
dialog.editor::backdrop { background: rgba(8, 9, 12, .72); }
.ed-head h3 { margin: 0 0 .4rem; font-size: 1rem; }
.ed-hint { font-size: .78rem; margin: .3rem 0 0; }
.ed-ta {
  width: 100%; font-family: var(--mono); font-size: .82rem; line-height: 1.45;
  background: #0f1114; color: var(--fg); border: 1px solid var(--line);
  border-radius: 8px; padding: .6rem .7rem; margin-top: .6rem; resize: vertical;
}
.ed-errors { margin-top: .5rem; }
.ed-errors:empty { display: none; }
.ed-err-title { color: var(--err); font-weight: 700; margin: .2rem 0; font-size: .88rem; }
.ed-err { color: var(--err); margin: .15rem 0; font-size: .84rem; white-space: pre-wrap; }
/* live validation strip (debounced /api/validate): a persistent, advisory
   status line that sits ABOVE the gated Save's own error box. The ✓ is a muted
   green — softer than the loud badge --ok — because a pure validate pass is not
   a promise that the check-gated Save will succeed. */
.ed-valid { margin-top: .5rem; font-size: .84rem; }
.ed-valid:empty { display: none; }
.ed-valid-ok { color: #77b596; }   /* muted green: advisory pass */
.ed-valid-err { color: var(--err); margin: .15rem 0; white-space: pre-wrap; }
.ed-valid-wait { color: var(--muted); }
.ed-valid-na { color: var(--muted); }
/* WP1 impact strip (debounced /api/impact): advisory chain-reaction preview */
.ed-impact { margin-top: .4rem; font-size: .84rem; color: var(--warn);
             background: #3a3010; border-radius: 4px; padding: .35rem .55rem; }
.ed-impact:empty { display: none; }
.ed-impact-ok { color: #77b596; background: transparent; padding: 0; }

/* ----------------------------------------------------------- QC panel -- */
.qc-err  { background: #4d1f22; color: #ff8a90; }
.qc-warn { background: #4a3a12; color: #ffcf5c; }
.badge.lvl-error, .badge.lvl-fail { background: #4d1f22; color: #ff8a90; }
.badge.lvl-warn, .badge.lvl-warning { background: #4a3a12; color: #ffcf5c; }
.badge.lvl-info, .badge.lvl-ok, .badge.lvl-pass { background: #17402a; color: #7ee2a8; }
p.lvl-error { color: var(--err); }
p.lvl-warning { color: var(--warn); }
p.lvl-ok { color: var(--ok); }
.qc-item { padding: .3rem 0; border-bottom: 1px dashed var(--line); font-size: .86rem; }
.qc-item:last-child { border-bottom: 0; }
.qmsg { margin-left: .45rem; }
.qshot { color: var(--accent); font-weight: 600; }
.qsug { font-size: .78rem; margin-top: .1rem; }

/* -------------------------------------------------------- events feed -- */
.list { max-height: 300px; overflow: auto; }
.event {
  display: flex; align-items: baseline; gap: .55rem; flex-wrap: wrap;
  font-size: .84rem; padding: .2rem 0; border-bottom: 1px dashed var(--line);
}
.event:last-child { border-bottom: 0; }
.etime { color: var(--muted); font-variant-numeric: tabular-nums; }
.ac-human  { background: #23324d; color: #8fb8ff; }
.ac-ai     { background: #3a2a4d; color: #cf9bff; }
.ac-engine { background: #3a3d44; color: #c4c9d2; }
.eaction { font-weight: 600; }
.edetail { color: var(--muted); font-size: .76rem; word-break: break-all; }

/* ----------------------------------------------------------- git panel -- */
.git-head { display: flex; align-items: center; gap: .6rem; cursor: pointer; flex-wrap: wrap; }
.git-head h2 { margin: 0; border: 0; padding: 0; }
.git-head .chips { flex: 1; }
.git-arrow { color: var(--muted); font-size: .8rem; }
.gs-row {
  display: flex; align-items: center; gap: .55rem; padding: .2rem 0;
  font-size: .84rem; cursor: pointer; border-bottom: 1px dashed var(--line);
  flex-wrap: wrap;
}
.gs-chip {
  font-family: var(--mono); font-size: .72rem; background: var(--panel2);
  border: 1px solid var(--line); border-radius: 4px; padding: 0 .35rem;
  min-width: 2.1em; text-align: center; white-space: pre;
}
.gs-path { word-break: break-all; }
.diff {
  background: #0f1114; border: 1px solid var(--line); border-radius: 6px;
  padding: .45rem .6rem; margin: .3rem 0 .5rem; max-height: 340px; overflow: auto;
}
.diff div { font-family: var(--mono); font-size: .76rem; white-space: pre; line-height: 1.4; }
.dl-add { color: var(--ok); }
.dl-del { color: var(--err); }
.dl-hunk { color: var(--accent); }
.dl-meta { color: var(--muted); font-weight: 700; }
.git-log-row { color: var(--muted); font-size: .8rem; padding: .12rem 0; display: flex; gap: .5rem; flex-wrap: wrap; }
.git-hash { font-family: var(--mono); color: var(--accent); }
.commit-row { display: flex; gap: .5rem; margin-top: .6rem; flex-wrap: wrap; }
.commit-msg {
  flex: 1; min-width: 220px; background: var(--panel2); color: var(--fg);
  border: 1px solid var(--line); border-radius: 6px; padding: .3rem .55rem;
  font-family: inherit; font-size: .84rem;
}

/* ------------------------------------------------------ proposals panel -- */
/* same collapsible-head pattern as the git panel (arrow + chips stay live) */
.prop-row {
  display: flex; align-items: baseline; gap: .6rem; padding: .25rem 0;
  border-bottom: 1px dashed var(--line); cursor: pointer; flex-wrap: wrap;
  font-size: .84rem;
}
.prop-name { font-family: var(--mono); font-size: .8rem; word-break: break-all; }
.prop-date { color: var(--muted); font-size: .76rem; font-variant-numeric: tabular-nums; }
.prop-pre {
  background: #0f1114; border: 1px solid var(--line); border-radius: 6px;
  padding: .5rem .7rem; margin: .25rem 0 .5rem; white-space: pre-wrap;
  word-break: break-word; font-family: var(--mono); font-size: .78rem;
  max-height: 320px; overflow: auto;
}

/* --------------------------------------------------- keyboard hint bar -- */
#kbdhint {
  position: fixed; left: 50%; transform: translateX(-50%); bottom: .8rem;
  z-index: 55; background: var(--panel2); border: 1px solid var(--line);
  border-radius: 999px; padding: .35rem 1rem; font-size: .8rem;
  color: var(--muted); box-shadow: 0 6px 18px rgba(0, 0, 0, .5);
  white-space: nowrap; max-width: 94vw; overflow-x: auto;
}
#kbdhint.hidden { display: none; }
#kbdhint b { color: var(--fg); font-weight: 700; }

/* --------------------------------------------------------------- toasts -- */
#toast {
  position: fixed; right: 1rem; bottom: 1rem; z-index: 50;
  display: flex; flex-direction: column; gap: .5rem; max-width: min(380px, 90vw);
}
.toast {
  background: var(--panel2); border: 1px solid var(--line);
  border-left: 4px solid var(--accent); border-radius: 8px; padding: .55rem .8rem;
  font-size: .86rem; box-shadow: 0 6px 18px rgba(0, 0, 0, .5); cursor: pointer;
  word-break: break-word;
  animation: mj-rise .18s ease-out;
}
.toast-ok { border-left-color: var(--ok); }
.toast-warn { border-left-color: var(--warn); }
.toast-err { border-left-color: var(--err); }
/* shared entrance for both toast systems (.toast here, .toast-item in
 * pages.css) and any future overlay chrome. */
@keyframes mj-rise {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}

/* ----------------------------------------------------------- responsive -- */
@media (max-width: 900px) {
  #shots { grid-template-columns: 1fr; }
  .cols { grid-template-columns: 1fr; }
  main { padding: 0 .7rem .9rem; }
  #header { padding: .9rem .9rem; }
}
/* ---- R10: take verdicts/notes, filter chips, spendy trigger ---- */
.tacts { display: flex; gap: .3rem; margin-top: .35rem; flex-wrap: wrap; align-items: center; }
.btn.tiny { padding: .1rem .45rem; font-size: .85rem; line-height: 1.4; }
.btn.tiny.on { border-color: var(--star); color: var(--star); background: rgba(255, 207, 92, .12); }
.tnote { max-width: 190px; word-break: break-all; }
/* inline take-note editor (#49a — replaces the page-freezing prompt dialog) */
.tnote-edit { margin-top: .35rem; width: 100%; }
.tnote-edit textarea {
  width: 100%; font-family: inherit; font-size: .8rem; line-height: 1.4;
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .3rem .45rem; resize: vertical;
}
.tnote-edit .btnrow { margin-top: .3rem; }
.tnote.seekable { cursor: pointer; text-decoration: underline dotted; }
.tnote.seekable:hover { color: var(--accent); }
.fchips { display: flex; gap: .4rem; flex-wrap: wrap; margin: .3rem 0 .7rem; }
button.fchip { cursor: pointer; }
.fchip.on { border-color: var(--accent); color: var(--accent); background: rgba(110, 168, 254, .12); }
.btn.spendy { border-color: var(--star); }
.vstack { margin-top: .45rem; display: flex; flex-direction: column; gap: .25rem; }
.vrow { display: flex; align-items: center; gap: .5rem; font-size: .85rem; }
.chip.warn { border-color: #e0a030; color: #e0a030; }

/* ---- v3.1: conflict banner — buffer-vs-truth diff inside the editor ---- */
.ed-truth { margin-top: .6rem; }
.ed-truth summary { cursor: pointer; color: var(--warn); font-size: .84rem; font-weight: 700; }
.ed-truth .diff { max-height: 260px; }
.ed-truth-note { color: var(--muted); font-size: .76rem; margin: .3rem 0 .1rem; }
.ed-truth .btnrow { margin-top: .5rem; }

/* ---- v3.1: unread-first triage — mark-reviewed + new-take chips ---- */
.chip.unread { color: var(--accent); border-color: #2b4a7a; background: var(--accent-bg); font-weight: 700; }
.chip.tk-new {
  color: var(--accent); border-color: #2b4a7a; background: var(--accent-bg);
  font-size: .68rem; padding: 0 .4rem; margin-left: .35rem; vertical-align: middle;
}

/* ---- v3.1: cache savings (Nx pattern) — estimates + done-build rows ---- */
.savings {
  margin-top: .5rem; display: flex; gap: .5rem; flex-wrap: wrap;
  align-items: center; font-size: .84rem;
}
.jsave { color: var(--ok); font-size: .78rem; white-space: nowrap; }

/* ---- R14: A/B take compare overlay (Frame.io comparison viewer) ---- */
/* A plain fixed div on <body> (NOT inside #shots), so a poll re-render can
   never destroy it; palette-only, no inline styles. */
.cmp-btn { margin-left: .2rem; }
.cmp-overlay {
  position: fixed; inset: 0; z-index: 80; background: rgba(8, 9, 12, .82);
  display: flex; align-items: flex-start; justify-content: center;
  padding: 2.4vh 1.1rem; overflow: auto;
}
.cmp-panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 12px;
  width: min(1400px, 96vw); padding: 1rem 1.2rem;
  box-shadow: 0 16px 56px rgba(0, 0, 0, .65);
}
.cmp-head { display: flex; align-items: center; gap: .9rem; flex-wrap: wrap; }
.cmp-head h3 { margin: 0; flex: 1; font-size: 1.05rem; min-width: 10rem; }
.cmp-toggles { display: flex; gap: 1rem; flex-wrap: wrap; align-items: center; }
.cmp-toggle {
  color: var(--muted); font-size: .86rem; display: inline-flex;
  align-items: center; gap: .35rem; cursor: pointer; user-select: none;
}
.cmp-toggle input[type="checkbox"] { accent-color: var(--accent); }
.cmp-rate { color: var(--star); font-variant-numeric: tabular-nums; font-weight: 700; }
.cmp-close {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; width: 2rem; height: 2rem; cursor: pointer;
  font-size: 1rem; line-height: 1; font-family: inherit;
}
.cmp-close:hover { filter: brightness(1.2); }
.cmp-videos { display: grid; grid-template-columns: 1fr 1fr; gap: 1rem; margin-top: 1rem; }
.cmp-side { display: flex; flex-direction: column; gap: .5rem; min-width: 0; }
.cmp-side select {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .3rem .5rem; font-family: inherit;
  font-size: .84rem; width: 100%;
}
.cmp-side video {
  width: 100%; max-height: 60vh; background: #000; border-radius: 8px; display: block;
}
.cmp-cap { color: var(--muted); font-size: .8rem; word-break: break-all; }
.cmp-cap b { color: var(--fg); }
.cmp-side .btn.small { margin-top: 0; }

/* ---- R14: events 更多 (show more) ---- */
.ev-more { margin-top: .6rem; }

/* ---------------------------------------- S8a: onboarding checklist ---- */
#onboarding { border-left: 3px solid var(--accent); }
.ob-head { display: flex; align-items: center; gap: .8rem; flex-wrap: wrap; }
.ob-head h2 { margin: 0; border: 0; padding: 0; flex: 1; min-width: 12rem; }
.ob-prog { color: var(--muted); font-size: .82rem; font-variant-numeric: tabular-nums; }
.ob-intro { color: var(--muted); font-size: .86rem; margin: .3rem 0 .7rem; }
.ob-step {
  display: flex; align-items: baseline; gap: .6rem; padding: .45rem .2rem;
  border-top: 1px solid var(--line);
}
.ob-mark { font-size: .95rem; width: 1.3rem; flex: none; }
.ob-step.done .ob-mark { color: var(--ok); }
.ob-step.todo .ob-mark { color: var(--muted); }
.ob-main { flex: 1; min-width: 0; }
.ob-title { font-weight: 700; font-size: .9rem; }
.ob-step.done .ob-title { color: var(--muted); font-weight: 400; }
.ob-hint { color: var(--muted); font-size: .82rem; margin-top: .15rem; }
.ob-cli {
  font-family: var(--mono); font-size: .78rem; color: #cfe3ff; background: var(--accent-bg);
  border-radius: 4px; padding: .05rem .4rem; margin-top: .25rem; display: inline-block;
  word-break: break-all;
}

/* ---------------------------------------------- S8a: failure cards ----- */
#failures { border-left: 3px solid var(--err); }
.fail-head { display: flex; align-items: center; gap: .7rem; flex-wrap: wrap; }
.fail-head h2 { margin: 0; border: 0; padding: 0; }
.fail-card {
  border: 1px solid var(--line); border-radius: 8px; margin-top: .5rem;
  background: var(--panel2); overflow: hidden;
}
.fail-card.err { border-color: #5a2b2e; }
.fail-sum {
  display: flex; align-items: baseline; gap: .55rem; padding: .45rem .65rem;
  cursor: pointer; flex-wrap: wrap;
}
.fail-sum:hover { background: #262a33; }
.fail-arrow { color: var(--muted); width: 1rem; flex: none; }
.fail-step {
  font-family: var(--mono); font-size: .74rem; border-radius: 999px;
  padding: .02rem .5rem; border: 1px solid var(--line);
}
.fail-card.err .fail-step { color: var(--err); border-color: #5a2b2e; }
.fail-card.info .fail-step { color: var(--muted); }
.fail-subj { font-weight: 700; font-size: .84rem; }
.fail-cause { color: var(--muted); font-size: .84rem; }
.fail-body { padding: 0 .65rem .6rem 1.65rem; }
.fail-ev {
  font-family: var(--mono); font-size: .76rem; white-space: pre-wrap;
  background: #14161a; border: 1px solid var(--line); border-radius: 6px;
  padding: .45rem .6rem; margin: .35rem 0; max-height: 15rem; overflow: auto;
  word-break: break-word;
}
.fail-hint { color: #cfe3ff; font-size: .82rem; margin: .25rem 0; }
.fail-meta { color: var(--muted); font-size: .76rem; margin: .15rem 0; word-break: break-all; }

/* --------------------------------------------- S8a: plan modal (§4.4) -- */
.planmodal { max-width: 720px; }
.pm-head h3 { margin: 0 0 .3rem; }
.pm-badge {
  display: inline-block; font-size: .78rem; border-radius: 999px;
  padding: .1rem .6rem; margin-bottom: .5rem; border: 1px solid var(--line);
}
.pm-badge.free { color: var(--ok); border-color: #2f5a43; background: #14261d; }
.pm-badge.spendy { color: var(--star); border-color: #6b5518; background: #4a3a12; }
.pm-total { margin-top: .6rem; font-size: .88rem; display: flex; gap: .8rem; flex-wrap: wrap; }
.pm-saved { color: var(--ok); }
.pm-skip { margin-top: .3rem; }
.pm-skiprow { display: flex; gap: .5rem; align-items: baseline; padding: .15rem 0; font-size: .82rem; }
.pm-err { color: var(--err); font-size: .86rem; }
.planmodal h4 { margin: .7rem 0 .2rem; font-size: .84rem; color: var(--muted); }
.btn.confirm { background: var(--star); color: #2a2000; }

/* --------------------------------------------- S8a: batch select bar --- */
.shot-check { accent-color: var(--accent); width: 1.05rem; height: 1.05rem; cursor: pointer; }
.batchbar {
  position: fixed; left: 50%; bottom: 1rem; transform: translateX(-50%);
  z-index: 80; background: var(--panel2); border: 1px solid var(--accent);
  border-radius: 999px; padding: .45rem .9rem; display: flex; gap: .6rem;
  align-items: center; box-shadow: 0 10px 30px rgba(0, 0, 0, .6);
}
.batchbar.hidden { display: none; }
.bb-count { font-weight: 700; font-size: .86rem; }
.bb-res { display: flex; gap: .4rem; flex-wrap: wrap; margin-top: .3rem; }
.bb-res .badge { font-size: .74rem; }

/* -------------------------------------------------- S8a: tasks panel --- */
.tk-row {
  display: flex; gap: .6rem; align-items: baseline; padding: .3rem 0;
  border-top: 1px solid var(--line); font-size: .82rem; flex-wrap: wrap;
}
.tk-id { font-family: var(--mono); color: var(--muted); }
.tk-reason { color: var(--err); font-size: .78rem; width: 100%; padding-left: 1rem; }
.badge.tk-succeeded { color: var(--ok); }
.badge.tk-failed, .badge.tk-moderation-rejected { color: var(--err); }
.badge.tk-polling { color: var(--star); }

/* ------------------------------------------ S8a: git snapshot/rollback - */
.snap-row { display: flex; gap: .5rem; align-items: center; margin: .5rem 0; flex-wrap: wrap; }
.snap-input {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .3rem .5rem; font-family: inherit; font-size: .82rem;
  flex: 1; min-width: 8rem;
}
.gs-rollback { margin-left: auto; }
.btn.tiny.gs-rollback { padding: .04rem .45rem; }

@media (max-width: 900px) {
  .cmp-videos { grid-template-columns: 1fr; }
}

/* ============================================ round V: project cockpit ==== */
/* The one-glance home. Pre-attentive by design (NN/g dashboards): colour is
   spent ONLY on exceptions — the state strip reuses the board's st-* badges,
   the risk banner is the only red block and it renders only when non-empty. */
.cockpit {
  margin: 0 0 1rem; padding: 1.05rem 1.4rem; background: var(--panel);
  border-bottom: 1px solid var(--line);
}
.ck-hero {
  display: flex; align-items: center; gap: 1rem 1.4rem; flex-wrap: wrap;
  justify-content: space-between;
}
.ck-state { min-width: 12rem; flex: 1; }
.ck-phase {
  display: inline-block; font-size: .72rem; font-weight: 700; letter-spacing: .04em;
  color: var(--accent); text-transform: uppercase; margin-bottom: .15rem;
}
.ck-sentence { font-size: 1.2rem; font-weight: 700; line-height: 1.35; }
.ck-human { color: var(--muted); font-size: .84rem; margin-top: .2rem; }
.ck-cta { display: flex; flex-direction: column; align-items: flex-end; gap: .3rem; }
.btn.ck-primary {
  font-size: .98rem; padding: .6rem 1.3rem; border-radius: 8px; max-width: 30rem;
  white-space: normal; text-align: center;
}
a.btn.ck-primary { text-decoration: none; }
.ck-cta .muted { font-size: .74rem; }

/* state strip — shot counts as chips; exception states carry the loud badge,
   就绪/人工 stay quiet (colour only where it means "act"). */
.ck-strip { display: flex; flex-wrap: wrap; gap: .4rem; margin-top: .7rem; align-items: center; }
.ck-scount {
  display: inline-flex; align-items: baseline; gap: .3rem; font-size: .78rem;
  padding: .12rem .55rem; border-radius: 999px; border: 1px solid var(--line);
  background: var(--panel2); color: var(--muted);
  /* #50: the counts are clickable queues now (buttons, not spans) */
  font-family: inherit; cursor: pointer;
}
.ck-scount:hover { border-color: var(--accent); color: var(--fg); }
.ck-scount b { color: var(--fg); font-size: .84rem; }
.ck-scount.exc { border-color: #5a4718; }
/* 继续上次工作 (#50) — the first thing the returning owner sees */
.ck-continue { margin: 0 0 .7rem; }
a.btn.ck-continue-btn { text-decoration: none; display: inline-block; }
.ck-final { color: var(--muted); font-size: .8rem; }
.ck-final .badge { margin-left: .35rem; }

/* risk banner — the ONLY red block, shown by exception (Linear "at risk"). */
.ck-risks {
  margin-top: .8rem; border: 1px solid #5a2b2e; border-left-width: 4px;
  background: #2a1618; border-radius: 8px; padding: .5rem .75rem;
}
.ck-risk { display: flex; align-items: baseline; gap: .5rem; font-size: .86rem; padding: .15rem 0; }
.ck-risk-dot { flex: none; font-size: .7rem; line-height: 1.6; }
.ck-risk.lvl-error .ck-risk-dot { color: var(--err); }
.ck-risk.lvl-warn .ck-risk-dot { color: var(--warn); }
.ck-risk.lvl-info .ck-risk-dot { color: var(--muted); }
.ck-risk-text { word-break: break-word; }

/* the supporting block grid (deliverables / spend / queue / approvals /
   activity / suggestions) — calm, one-click-deep detail below the fold. */
.ck-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  gap: .8rem; margin-top: .9rem;
}
.ck-block { background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: .55rem .75rem; }
.ck-block h3 { margin: 0 0 .35rem; font-size: .8rem; color: var(--muted); font-weight: 600; }
.ck-block.wide { grid-column: 1 / -1; }
.ck-chips { display: flex; flex-wrap: wrap; gap: .35rem; }
.ck-dv { display: inline-flex; align-items: baseline; gap: .3rem; font-size: .76rem;
  padding: .08rem .5rem; border-radius: 999px; border: 1px solid var(--line); background: var(--panel); }
.ck-dv .badge { font-size: .64rem; padding: .04rem .4rem; }
.ck-line { font-size: .84rem; margin: .12rem 0; }
.ck-line .muted { font-size: .78rem; }
.ck-mini-bar { height: 4px; max-width: 100%; background: var(--panel); border-radius: 999px; overflow: hidden; margin: .35rem 0 .1rem; }
.ck-mini-fill { display: block; height: 100%; background: var(--accent); border-radius: 999px; }
.ck-mini-fill.over { background: var(--err); }
.ck-ev { display: flex; align-items: baseline; gap: .45rem; font-size: .8rem; padding: .12rem 0; }
.ck-ev .etime { font-size: .72rem; }
.ck-sugg { display: flex; align-items: baseline; gap: .4rem; font-size: .82rem; padding: .12rem 0; }
.ck-err { color: var(--muted); font-size: .8rem; font-style: italic; }
.ck-empty { color: var(--muted); font-size: .82rem; }

/* --------------------------------------- evaluate block (round AA item 8) */
.ck-eval-sub { margin: .5rem 0; }
.ck-eval-sub h4 { margin: 0 0 .25rem; font-size: .78rem; color: var(--muted); font-weight: 600; }
/* info callout, not a warning — same info-blue pairing jb-running/st-manual
   already use elsewhere, deliberately NOT the amber/red risk colours: the
   honesty section is a permanent, calm disclosure, not an exception alert. */
.ck-honesty {
  margin-top: .6rem; padding: .5rem .7rem; border-radius: 8px;
  background: #23324d; border: 1px solid #345a91;
}
.ck-honesty h4 { margin: 0 0 .3rem; font-size: .8rem; color: #8fb8ff; font-weight: 600; }
.ck-honesty-summary { font-size: .82rem; margin: 0 0 .3rem; }
.ck-honesty ul { margin: .2rem 0 0; padding-left: 1.15rem; }
.ck-honesty li { font-size: .78rem; margin: .18rem 0; color: var(--muted); }

/* onboarding-lead: when a fresh project has nothing yet, the checklist leads
   and the supporting grid steps back (NN/g empty state → one clear next step). */
.cockpit.fresh .ck-grid { margin-top: .6rem; }

@media (max-width: 900px) {
  .cockpit { padding: .9rem .9rem; }
  .ck-cta { align-items: stretch; width: 100%; }
  .btn.ck-primary { max-width: none; }
}

/* Honour the OS-level motion preference: every animation/transition above is
 * decorative (pulse, flash, toast rise, breathe, hover easing) — none carries
 * state, so collapsing them to a single instant frame loses nothing. */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: .01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: .01ms !important;
  }
}
""".strip() + "\n"

# ---------------------------------------------------------------------- JS --
# Vanilla ES2020, no frameworks, no external assets. Server data only ever
# enters the DOM through textContent/createTextNode (dialogue, YAML and git
# diffs are arbitrary user text). Dynamic styles (budget-bar width, timeline
# clip geometry) are set through the CSSOM (`el.style.* = ...`), which strict
# `style-src 'self'` permits — unlike `style=` attributes, which this app
# never uses.

_JS = r"""
/* manju gui — front-end app (served as /app.js; source: manju/gui/page.py).
 *
 * Strict client of the engine HTTP API: the page never computes build state
 * itself — it renders /api/state verbatim and posts intents back. CSP is
 * `script-src 'self'`, so there are no inline handlers; every request sends
 * the X-Manju-Token header read from the <meta name="manju-token"> tag.
 */
"use strict";
(() => {
  const tokenMeta = document.querySelector('meta[name="manju-token"]');
  const TOKEN = tokenMeta ? (tokenMeta.getAttribute("content") || "") : "";
  /* stale-tab guard: the project this document rendered for.
   * Session is immutable — PROJECT is only rewritten when the server returns
   * next_action.kind === "reload_current" (first bind). Never on open/create
   * of another project. */
  const projMeta = document.querySelector('meta[name="manju-project"]');
  let PROJECT = projMeta ? (projMeta.getAttribute("content") || "") : "";
  const $ = (id) => document.getElementById(id);

  /* Per-project UI memory (external-review round, #49a): the shot filter and
   * panel-open states survive a reload. Keyed by the STABLE project identity
   * (#45's root-derived token) — the display NAME collides across same-named
   * projects. Best-effort: blocked/corrupt storage never breaks the page. */
  const uiKey = () => "manju-ui-" + (PROJECT || "unbound");
  const loadUI = () => {
    try { return JSON.parse(localStorage.getItem(uiKey())) || {}; }
    catch (e) { return {}; }
  };
  const saveUI = (patch) => {
    try {
      const cur = loadUI();
      Object.keys(patch).forEach((k) => { cur[k] = patch[k]; });
      localStorage.setItem(uiKey(), JSON.stringify(cur));
    } catch (e) { /* storage disabled — memory just stays off */ }
    /* Durable per-workspace restore (server ~/.manju/gui_state.json). */
    try {
      if (PROJECT && typeof requestJson === "function") {
        const serverPatch = {};
        if (patch.filter !== undefined) serverPatch.shot_status_filter = patch.filter;
        if (patch.lastShot !== undefined) serverPatch.last_shot_id = patch.lastShot;
        if (patch.reviewPos !== undefined) serverPatch.review_position = patch.reviewPos;
        if (Object.keys(serverPatch).length) {
          requestJson("POST", "/api/ui-state", { ui: serverPatch },
            { token: TOKEN, projectId: PROJECT || "" }).catch(() => {});
        }
      }
    } catch (e2) { /* never block UI */ }
  };

  /* One-shot migrate localStorage → server UI state (best-effort). */
  try {
    if (PROJECT && typeof requestJson === "function") {
      const loc = loadUI();
      const opts = { token: TOKEN, projectId: PROJECT || "" };
      requestJson("GET", "/api/ui-state", undefined, opts).then((data) => {
        const ui = (data && data.ui) || {};
        if (ui.migrated_from_localstorage) return;
        const patch = {
          migrated_from_localstorage: true,
          shot_status_filter: loc.filter || ui.shot_status_filter || "",
          last_shot_id: loc.lastShot || ui.last_shot_id || "",
        };
        return requestJson("POST", "/api/ui-state", { ui: patch }, opts);
      }).catch(() => {});
    }
  } catch (e) { /* */ }

  /* #50a: the review page deep-links /?compare=<shot> into the A/B overlay */
  let pendingCompare = (() => {
    try { return new URLSearchParams(location.search).get("compare") || ""; }
    catch (e) { return ""; }
  })();

  /* A click-driven header/zone becomes keyboard-operable. A real <button>
   * would be invalid around its heading/input children, so the pattern is
   * role+tabindex+keydown — Enter/Space route to the SAME click handler. */
  const actAsButton = (node, label) => {
    node.tabIndex = 0;
    node.setAttribute("role", "button");
    if (label) node.setAttribute("aria-label", label);
    node.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        e.stopPropagation();  /* the global Space=play shortcut must not fire */
        node.click();
      }
    });
  };

  /* another tab switched the server's project — block this one (its media
   * URLs now resolve inside the NEW project); reload follows the switch. */
  const projectSwitchedOverlay = (name) => {
    if (document.getElementById("mj-proj-switched")) return;
    const ov = document.createElement("div");
    ov.id = "mj-proj-switched";
    ov.style.cssText = "position:fixed;inset:0;z-index:9999;background:rgba(15,18,24,.92);" +
      "color:#fff;display:flex;flex-direction:column;align-items:center;" +
      "justify-content:center;gap:1rem;text-align:center;padding:2rem";
    const msg = document.createElement("div");
    msg.style.cssText = "font-size:1.05rem;max-width:34em";
    msg.textContent = name
      ? ("服务器已切换到项目「" + name + "」— 本页属于另一个项目,已停止读写。")
      : "服务器已切换/关闭项目 — 本页属于另一个项目,已停止读写。";
    const btn = document.createElement("button");
    btn.textContent = "刷新,跟随当前项目 (reload)";
    btn.style.cssText = "font-size:1rem;padding:.5em 1.2em;cursor:pointer";
    btn.addEventListener("click", () => location.reload());
    ov.appendChild(msg);
    ov.appendChild(btn);
    document.body.appendChild(ov);
  };

  /* ------------------------------------------------------------- DOM --- */
  /* Every dynamic node gets its text via textContent — never innerHTML. */
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null && text !== "") n.textContent = String(text);
    return n;
  };
  const clear = (node) => { while (node.firstChild) node.removeChild(node.firstChild); };
  const errMsg = (err) => String((err && err.message) || err);

  /* ------------------------------------------------------ formatting --- */
  const fmtDur = (ms) => {                       /* 90500 -> "01:30.50" */
    if (typeof ms !== "number" || !isFinite(ms) || ms <= 0) return "—";
    const total = ms / 1000;
    const m = Math.floor(total / 60);
    const s = total - m * 60;
    return String(m).padStart(2, "0") + ":" + s.toFixed(2).padStart(5, "0");
  };
  const fmtMMSS = (ms) => {                      /* 90500 -> "01:30" (ruler) */
    const t = Math.max(0, Math.round((typeof ms === "number" && isFinite(ms) ? ms : 0) / 1000));
    return String(Math.floor(t / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0");
  };
  const fmtMoney = (v) =>
    (typeof v === "number" && isFinite(v)) ? String(Number(v.toFixed(4))) : "?";
  const fmtClock = (ts) => {                     /* ISO ts -> local HH:MM:SS */
    const d = new Date(ts);
    if (!isNaN(d.getTime())) {
      const p = (x) => String(x).padStart(2, "0");
      return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
    }
    const m = /\d{2}:\d{2}:\d{2}/.exec(String(ts || ""));
    return m ? m[0] : String(ts || "");
  };
  const jobSeconds = (job) => {
    if (!job.started || !job.finished) return "";
    const a = Date.parse(job.started);
    const b = Date.parse(job.finished);
    if (isNaN(a) || isNaN(b) || b < a) return "";
    return ((b - a) / 1000).toFixed(1) + "s";
  };

  /* ---------------------------------------------------------- toasts --- */
  const toast = (msg, kind) => {
    const box = $("toast");
    if (!box.hasAttribute("aria-live")) {
      /* announce state changes to assistive tech without stealing focus */
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
    }
    const cls = kind === "err" ? "toast-err" : (kind === "warn" ? "toast-warn" : "toast-ok");
    const t = el("div", "toast " + cls, msg);
    t.addEventListener("click", () => t.remove());
    box.appendChild(t);
    /* F20 discipline (server pages had it since #42; the SPA missed it):
     * an error is often a long engine sentence — it stays until clicked. */
    if (kind === "err") { t.textContent = msg + "  ✕"; }
    else { setTimeout(() => t.remove(), 4000); }
  };

  /* ------------------------------------------------------------- API --- */
  /* Uses /webclient.js requestJson when available; falls back to local
   * implementation that still preserves the full error JSON body. */
  const apiOptions = () => ({ token: TOKEN, projectId: PROJECT || "" });
  const apiRaw = async (method, path, body) => {
    try {
      if (typeof requestJson === "function") {
        const data = await requestJson(method, path, body, apiOptions());
        return { ok: true, status: 200, data };
      }
    } catch (err) {
      if (err && err.name === "ManjuApiError") {
        if (err.status === 409 && err.data && err.data.code === "project_switched") {
          projectSwitchedOverlay(err.data.project);
        }
        return { ok: false, status: err.status, data: err.data || {} };
      }
      throw err;
    }
    const opts = { method, headers: { "X-Manju-Token": TOKEN, "Accept": "application/json" } };
    if (PROJECT) opts.headers["X-Manju-Project"] = PROJECT;
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = {};
    try {
      const text = await res.text();
      if (text) data = JSON.parse(text);
    } catch (e) { data = {}; }
    if (res.status === 409 && data && data.code === "project_switched") {
      projectSwitchedOverlay(data.project);
    }
    return { ok: res.ok, status: res.status, data };
  };
  const api = async (method, path, body) => {
    const r = await apiRaw(method, path, body);
    if (!r.ok) {
      if (typeof ManjuApiError === "function") {
        throw new ManjuApiError(r.status, r.data || {}, path);
      }
      const err = new Error(r.data && r.data.error ? r.data.error : "HTTP " + r.status + " (" + path + ")");
      err.status = r.status;
      err.code = r.data && r.data.code;
      err.data = r.data || {};
      throw err;
    }
    return r.data;
  };

  /* POST wrapper: disables the button in flight, toasts success/error,
   * refreshes state after. Errors never propagate — polling must survive. */
  const post = async (btn, path, body, okMsg) => {
    if (btn) btn.disabled = true;
    try {
      const data = await api("POST", path, body);
      if (okMsg) toast(okMsg, "ok");
      await refresh();
      return data;
    } catch (err) {
      toast(errMsg(err), "err");
      return null;
    } finally {
      if (btn) btn.disabled = false;
      updateGates();  /* keep Build/QC locked if a job is now active */
    }
  };

  /* ---------------------------------------------------- polling/watch --- */
  /* Idle refreshes ride the /api/watch long-poll (seeded from state.fp):
   * changed -> fetch state, unchanged -> re-arm. While any job is active the
   * old 1.5s state polling stays — job state transitions don't all bump the
   * fingerprint. A watch failure degrades to the old 5s interval for ~60s,
   * then watch is tried again. document.hidden and the editor dialog pause
   * everything (the in-flight watch is ABORTED so a stale response can never
   * clobber the page). */
  let timer = null;
  let anyActive = false;   /* any job queued/running: fast poll + build lock */
  let pollFailed = false;  /* toast once per outage, not once per retry */
  let editorOpen = false;  /* editor dialog pauses the loop entirely: a
                              mid-edit rerender must never eat the shots grid */
  let readonly = false;    /* state.readonly: every mutating control gated */
  let lastFp = null;       /* state.fp — seeds the /api/watch long-poll */
  let lastShots = [];      /* last state.shots (new-shot template needs the
                              first scene id; keyboard mode + reorder too) */
  let lastJobs = [];       /* last state.jobs (spend banner, switch cleanup) */
  let lastDoneCount = -1;  /* done-job count: a finished job invalidates the
                              timeline fingerprint, git panel and proposals */
  let watchCtl = null;            /* AbortController of the in-flight watch */
  let watchFallbackUntil = 0;     /* after a watch failure: 5s polls until then */
  const sigs = {};         /* per-section JSON signatures: skip no-op
                              re-renders so <video> playback survives polls */

  const section = (key, data, fn) => {
    const sig = JSON.stringify(data === undefined ? null : data);
    if (sigs[key] === sig) return;
    sigs[key] = sig;
    fn();
  };

  const stopWatch = () => {
    if (watchCtl) {
      watchCtl.abort();   /* its catch sees .aborted and stays silent */
      watchCtl = null;
    }
  };
  const pauseLive = () => {   /* hidden tab / open editor: full stop */
    if (timer) clearTimeout(timer);
    stopWatch();
  };

  const schedule = () => {
    if (timer) clearTimeout(timer);
    if (document.hidden) return;  /* paused; visibilitychange resumes */
    if (editorOpen) return;       /* paused; editorClosed() resumes */
    if (anyActive) { timer = setTimeout(refresh, 1500); return; }
    if (!lastFp || Date.now() < watchFallbackUntil) {
      timer = setTimeout(refresh, 5000);  /* no fp yet / watch outage window */
      return;
    }
    armWatch();
  };

  async function armWatch() {
    if (watchCtl) return;   /* one in-flight watch only */
    const ctl = new AbortController();
    watchCtl = ctl;
    let changed = false;
    try {
      const res = await fetch(
        "/api/watch?fp=" + encodeURIComponent(lastFp) + "&timeout=25",
        { headers: { "X-Manju-Token": TOKEN }, signal: ctl.signal });
      const data = await res.json();
      if (!res.ok) throw new Error("HTTP " + res.status + " (/api/watch)");
      changed = !!(data && data.changed);
    } catch (err) {
      if (ctl.signal.aborted) return;  /* deliberate pause — stay quiet */
      if (watchCtl === ctl) watchCtl = null;
      watchFallbackUntil = Date.now() + 60000;  /* 5s interval for ~60s */
      schedule();
      return;
    }
    if (watchCtl === ctl) watchCtl = null;
    if (ctl.signal.aborted || document.hidden || editorOpen) return;  /* paused meanwhile */
    if (changed) refresh();  /* refresh() re-arms via schedule() */
    else schedule();         /* timeout lapsed unchanged: just re-arm */
  }

  async function refresh() {
    if (timer) clearTimeout(timer);
    stopWatch();                  /* a direct refresh supersedes the watch */
    if (editorOpen) return;       /* paused; editorClosed() resumes */
    try {
      const s = await api("GET", "/api/state");
      pollFailed = false;
      /* stale-tab guard: another tab switched the server's project. Never
       * silently repaint as the new project — overlay and stop rendering. */
      if (PROJECT && s && typeof s.project_token === "string" &&
          s.project_token && s.project_token !== PROJECT) {
        projectSwitchedOverlay(s.project && s.project.name);
        return;
      }
      render(s);
    } catch (err) {
      if (!pollFailed) {
        toast("刷新失败 (refresh failed): " + errMsg(err), "err");
      }
      pollFailed = true;
    }
    schedule();
  }

  function render(s) {
    const jobs = Array.isArray(s.jobs) ? s.jobs : [];
    anyActive = jobs.some((j) => j.state === "queued" || j.state === "running");
    readonly = s.readonly === true;
    lastFp = (typeof s.fp === "string" && s.fp) ? s.fp : null;
    lastShots = Array.isArray(s.shots) ? s.shots : [];
    lastProjectName = (s.project && s.project.name) ? String(s.project.name) : "";
    lastJobs = jobs;
    const doneCount = jobs.filter((j) => j.state === "done").length;
    if (lastDoneCount >= 0 && doneCount > lastDoneCount) {
      tlForce = true;    /* a build may have recompiled the timeline */
      gitStale = true;   /* … and touched the working tree */
      propStale = true;  /* … and an agent may have filed a proposal */
      cockStale = true;  /* … and moved every number the cockpit shows */
      if (gitOpen) fetchGitPanel();
      if (tasksOpen) fetchTasks();   /* a done job likely wrote a ledger row */
    }
    lastDoneCount = doneCount;
    if (lastFp !== estSeenFp && !anyActive) {
      estSeenFp = lastFp;
      updateEstimate();   /* UX-STUDY #1: price follows the project state */
    }
    section("header",
      [s.project, s.budget, s.next_step, s.timeline, s.latest_final,
       s.latest_final_note, s.build_lock, s.readonly, s.workspace,
       s.finals, (s.shots || []).length],
      () => renderHeader(s));
    section("jobs", jobs, () => renderJobs(jobs));
    section("shots", [s.shots, s.readonly], () => renderShots(s.shots || []));
    section("failures", [s.failures, s.shots], () => renderFailures(s.failures || []));
    section("qc", s.qc, () => renderQC(s.qc));
    section("events", s.events, () => renderEvents(s.events || []));
    renderBatchBar();   /* selection survives polls; bar follows current shots */
    maybeOnboarding(s); /* auto-show once for an empty-ish, undismissed project */
    renderSpend(jobs);  /* §8.3 waiting_user banner in the build panel */
    updateReviewChip(); /* 未阅 N follows the fresh shots + localStorage mark */
    maybeTimeline(s);   /* async, self-contained: a 500 there never cascades */
    maybeProposals(s);  /* async, at most one fetch per fp change */
    maybeCockpit(s);    /* async, fingerprint-gated: the round-V cockpit */
    maybeEvaluate(s);   /* async, fingerprint-gated: round AA item 8 */
    $("dropzone").classList.toggle("hidden", readonly);
    updateGates();
    /* #50a: /?compare=S001 deep-links from /review straight into the A/B
     * takes overlay — consumed once, after the first shots render (the
     * overlay takes the shot OBJECT). <2 takes = a silent no-op by design. */
    if (pendingCompare && lastShots.length) {
      const target = lastShots.find((sh) => sh.id === pendingCompare);
      pendingCompare = "";
      /* #50c: strip the param so F5 / the project-switch 刷新 button can
       * never replay the overlay (worst case: onto a same-named shot in a
       * DIFFERENT project after a switch). */
      try { history.replaceState(null, "", location.pathname); } catch (err) { /* keep */ }
      if (target) {
        try { openCompare(target); } catch (err) { /* stays on the workbench */ }
        if (!cmpOverlay) toast("该镜头可对比的视频 take 不足两个", "warn");
      }
    }
  }

  /* ========================================================= cockpit ===
   * Round V (goal item 4): the one-glance home. Its numbers all come from
   * /api/cockpit (engine reads only — build.status/stale/exportstatus/spend/
   * director), fetched at most once per fingerprint change (mirroring the
   * proposals panel), so it rides the SAME poll with no new machinery. Every
   * node is built via createElement/textContent (CSP + XSS safe); the only
   * dynamic geometry is a budget mini-bar width set through the CSSOM. */
  let cockFp = null;      /* fingerprint the current cockpit was fetched at */
  let cockStale = false;  /* set by a done job; cleared by the next fetch */
  let cockBusy = false;
  let cockData = null;
  let cockErr = null;

  function maybeCockpit(s) {
    if (typeof s.fp === "string" && s.fp && (s.fp !== cockFp || cockStale)) {
      fetchCockpit(s.fp);
    }
  }

  async function fetchCockpit(fp) {
    if (cockBusy) return;
    cockBusy = true;
    if (fp !== undefined) cockFp = fp;   /* recorded up front: an error must
                                            not hammer /api/cockpit */
    cockStale = false;
    try {
      cockData = await api("GET", "/api/cockpit");
      cockErr = null;
    } catch (err) {
      cockErr = errMsg(err);   /* section-local degrade; the loop is untouched */
    } finally {
      cockBusy = false;
      renderCockpit();
    }
  }

  /* ---------------------------------------------------- evaluate (item 8)
   * Round AA goal item 8: the honest usage/rework lens (core/evaluate.py),
   * surfaced in the cockpit as its own block — fetched separately from
   * /api/evaluate (read-only, no lock), same fingerprint-gated shape as the
   * cockpit fetch above so it rides the SAME poll with no new machinery.
   * Independent busy/err/data state: a broken /api/evaluate must never take
   * the rest of the cockpit down with it. */
  let evalFp = null;
  let evalBusy = false;
  let evalData = null;
  let evalErr = null;

  function maybeEvaluate(s) {
    if (typeof s.fp === "string" && s.fp && s.fp !== evalFp) {
      fetchEvaluate(s.fp);
    }
  }

  async function fetchEvaluate(fp) {
    if (evalBusy) return;
    evalBusy = true;
    if (fp !== undefined) evalFp = fp;
    try {
      evalData = await api("GET", "/api/evaluate");
      evalErr = null;
    } catch (err) {
      evalErr = errMsg(err);
    } finally {
      evalBusy = false;
      renderCockpit();  /* re-render the grid with the freshly loaded block */
    }
  }

  const CK_STATE_ZH = {
    missing: "缺失", needs_selection: "待挑选", broken: "损坏",
    stale: "待更新", manual: "人工", fresh: "就绪",
  };
  const CK_STATE_BADGE = {
    missing: "st-missing", needs_selection: "st-needs", broken: "st-broken",
    stale: "st-stale", manual: "st-manual", fresh: "st-fresh",
  };
  const CK_EXC = { missing: 1, needs_selection: 1, broken: 1, stale: 1 };
  const CK_FRESH_BADGE = {
    up_to_date: "st-fresh", stale: "st-stale", missing: "st-missing",
    problematic: "st-broken", needs_manual: "st-manual", verified: "st-fresh",
  };
  const blkErr = (b) => (b && typeof b === "object" && b.error) ? String(b.error) : null;

  function renderCockpit() {
    const root = $("cockpit");
    if (!root) return;
    clear(root);
    const c = cockData;
    if (cockErr && !c) {
      root.appendChild(el("p", "ck-err", "驾驶舱不可用 (cockpit unavailable): " + cockErr));
      return;
    }
    if (!c) { root.appendChild(el("p", "loading", "加载中 (loading)…")); return; }

    const state = c.state || {};
    const na = c.next_action || {};
    const fresh = (state.shots_total === 0)
      || (c.onboarding && c.onboarding.should_show);
    root.classList.toggle("fresh", !!fresh);

    /* --- 继续上次工作 (#50): pure client memory (common.js records every
     * server-page visit per project); /review restores its own position, so
     * this chip only needs to LINK back. The engine keeps no UI state. */
    try {
      const lastRaw = localStorage.getItem("manju-last-" + PROJECT);
      const last = lastRaw ? JSON.parse(lastRaw) : null;
      if (PROJECT && last && last.page && last.page !== "/") {
        const cont = el("div", "ck-continue");
        let lbl = "继续上次工作:" + (last.title || last.page);
        if (last.page === "/review") {
          const pos = localStorage.getItem("manju-rv-pos-" + PROJECT);
          if (pos) lbl += " · " + pos;
        }
        const a = document.createElement("a");
        a.className = "btn ck-continue-btn";
        a.href = last.page;
        a.textContent = lbl;
        cont.appendChild(a);
        root.appendChild(cont);
      }
    } catch (err) { /* storage off — no chip, no noise */ }

    /* --- HERO: the state sentence + the ONE next action ------------------ */
    const hero = el("div", "ck-hero");
    const left = el("div", "ck-state");
    if (!blkErr(state)) {
      if (state.phase_zh) left.appendChild(el("div", "ck-phase", state.phase_zh));
      left.appendChild(el("div", "ck-sentence",
        state.sentence || "驾驶舱 (cockpit)"));
    } else {
      left.appendChild(el("div", "ck-sentence", "状态不可用 (state unavailable)"));
      left.appendChild(el("div", "ck-err", blkErr(state)));
    }
    if (na && na.human_label) left.appendChild(el("div", "ck-human", "下一步 (next): " + na.human_label));
    hero.appendChild(left);
    hero.appendChild(renderHeroCTA(na));
    root.appendChild(hero);

    /* --- 待办箱: 新 take 未阅 (#50a) — the same snapshot the shots bar's
     * 未阅 chip reads; cockpit refetches AFTER the state render, so
     * lastShots is populated by the time this runs. --------------------- */
    let unreadTakes = 0;
    try {
      const snap = loadReviewSnapshot();
      (lastShots || []).forEach((s) => {
        (Array.isArray(s.takes) ? s.takes : []).forEach((t) => {
          if (isNewTake(snap, s.id, String(t.name))) unreadTakes++;
        });
      });
    } catch (err) { unreadTakes = 0; }

    /* --- STATE STRIP: shot counts by state, exceptions coloured ---------- */
    if (!blkErr(state) && state.shots_total > 0) {
      const strip = el("div", "ck-strip");
      const counts = state.shots_by_state || {};
      Object.keys(counts).forEach((k) => {
        /* 待办箱 (#50): a count is a QUEUE, not a statistic — clicking it
         * filters the shots grid to exactly those shots and jumps there. */
        const chip = el("button", "ck-scount" + (CK_EXC[k] ? " exc" : ""));
        chip.type = "button";
        chip.title = "在分镜里筛选:" + (CK_STATE_ZH[k] || k);
        chip.appendChild(el("b", null, String(counts[k])));
        chip.appendChild(document.createTextNode(" " + (CK_STATE_ZH[k] || k)));
        chip.addEventListener("click", () => {
          stateFilter = k;
          saveUI({ filter: stateFilter });
          renderShots(lastShots);
          const sh = $("shots");
          if (sh) sh.scrollIntoView({ behavior: "smooth", block: "start" });
        });
        strip.appendChild(chip);
      });
      if (unreadTakes > 0) {
        const uc = el("button", "ck-scount exc");
        uc.type = "button";
        uc.title = "上次标记已阅之后新增的 take(卡片带「新」章)— 点击跳到分镜";
        uc.appendChild(el("b", null, String(unreadTakes)));
        uc.appendChild(document.createTextNode(" 新 take 未阅"));
        uc.addEventListener("click", () => {
          const sh = $("shots");
          if (sh) sh.scrollIntoView({ behavior: "smooth", block: "start" });
        });
        strip.appendChild(uc);
      }
      const fin = state.final;
      if (fin) {
        const f = el("span", "ck-final",
          "成片 (final)" + (fin.version ? " " + fin.version : ""));
        if (fin.freshness) {
          f.appendChild(el("span", "badge " + (CK_FRESH_BADGE[fin.freshness] || "st-missing"),
            fin.freshness_zh || fin.freshness));
        }
        strip.appendChild(f);
      }
      root.appendChild(strip);
    }

    /* --- RISK BANNER: by exception only (calm when empty) ---------------- */
    /* the engine risks (qc/stale/budget/broken/lock/crashed-render) come from
     * /api/cockpit; failed GUI jobs live only in the runner (lastJobs), so they
     * are appended client-side — the fourth risk class the contract names. */
    const risks = c.risks;
    const riskItems = (risks && !blkErr(risks) && Array.isArray(risks.items))
      ? risks.items.slice() : [];
    const failedJobs = (lastJobs || []).filter((j) => j.state === "failed").length;
    if (failedJobs) {
      riskItems.push({kind: "job", level: "error",
        text: failedJobs + " 个任务失败 (failed jobs) — 见下方任务区"});
    }
    if (blkErr(risks)) {
      root.appendChild(el("p", "ck-err", "风险读取失败 (risks unavailable): " + blkErr(risks)));
    }
    if (riskItems.length) {
      const banner = el("div", "ck-risks");
      riskItems.forEach((r) => {
        const row = el("div", "ck-risk lvl-" + (r.level || "warn"));
        row.appendChild(el("span", "ck-risk-dot", "●"));
        row.appendChild(el("span", "ck-risk-text", r.text || r.kind || "risk"));
        banner.appendChild(row);
      });
      root.appendChild(banner);
    }

    /* --- ONBOARDING lead (fresh project) or the supporting grid ---------- */
    root.appendChild(renderCockGrid(c, fresh));
  }

  function renderHeroCTA(na) {
    const cta = el("div", "ck-cta");
    if (blkErr(na) || !na.verb || na.verb === "done" || na.verb === "none") {
      if (na && na.verb === "done") {
        const a = el("a", "btn ghost ck-primary", "查看成片 · 导出 (exports)");
        a.href = "/exports";
        cta.appendChild(a);
      } else {
        cta.appendChild(el("span", "muted", na && na.text ? na.text : "无待办 (nothing to do)"));
      }
      return cta;
    }
    const label = na.text || "开始 (start)";
    const btn = el("button", "btn primary ck-primary", label);
    btn.type = "button";
    const mutating = na.verb === "build" || na.verb === "redo"
      || na.verb === "repair" || na.verb === "package";
    if (readonly && mutating) {
      btn.disabled = true;
      btn.title = "只读模式 (readonly)";
    } else {
      btn.addEventListener("click", () => heroAction(na));
    }
    cta.appendChild(btn);
    if (na.verb === "build" && na.action && na.action.regen_stale) {
      cta.appendChild(el("span", "muted", "含重做过期 (incl. regen stale)"));
    }
    return cta;
  }

  function heroAction(na) {
    try {
      if (na.verb === "story") { openOnboarding(); return; }
      if (na.verb === "build") { heroBuild(na.action || {}); return; }
      if (na.shot) { focusShot(na.shot); return; }   /* redo/select/repair: link to the shot */
      const bp = $("buildpanel");                      /* package/other: link to the build panel */
      if (bp) bp.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) { toast(errMsg(err), "err"); }
  }

  function heroBuild(action) {
    const params = {
      target: action.target || "final",
      gen: action.gen || "missing",
      regen_stale: !!action.regen_stale,
      force: !!action.force,
    };
    showPlanModal("build", params, {
      title: "构建前计划 (plan before build)",
      onConfirm: async () => {
        const confirmed = Object.assign({}, params, { dry_run: false, assume_yes: true });
        const data = await post(null, "/api/build", confirmed, "构建任务已入队 (build queued)");
        const gate = spendGateOf(data);   /* SYNCHRONOUS waiting_user, if any */
        if (gate) {
          spendSig = "sync:" + Date.now();
          showSpendBanner(spendText(gate), confirmed, spendSig);
        }
      },
    });
  }

  function renderCockGrid(c, fresh) {
    const grid = el("div", "ck-grid");

    /* onboarding leads a fresh project (NN/g: one clear next step) */
    const ob = c.onboarding;
    if (fresh && ob && !blkErr(ob) && Array.isArray(ob.steps)) {
      const b = el("div", "ck-block wide");
      b.appendChild(el("h3", null, "新手引导 (getting started) · "
        + (ob.done_count || 0) + "/" + (ob.total || ob.steps.length)));
      ob.steps.forEach((st) => {
        const line = el("div", "ck-line");
        line.appendChild(el("span", null, (st.done ? "✓ " : "○ ")));
        line.appendChild(el("span", st.done ? "muted" : null, st.title || st.key));
        b.appendChild(line);
      });
      const open = el("button", "btn ghost small", "打开引导 (open guide)");
      open.type = "button";
      open.addEventListener("click", () => openOnboarding());
      b.appendChild(open);
      grid.appendChild(b);
    }

    /* deliverables strip (block 4) */
    grid.appendChild(ckBlock("交付物 (deliverables)", (b) => {
      const dv = c.deliverables;
      if (blkErr(dv)) { b.appendChild(el("p", "ck-err", blkErr(dv))); return; }
      const rows = (dv && Array.isArray(dv.rows)) ? dv.rows : [];
      if (!rows.length) { b.appendChild(el("p", "ck-empty", "暂无 (none)")); return; }
      const chips = el("div", "ck-chips");
      rows.forEach((r) => {
        const chip = el("span", "ck-dv");
        chip.appendChild(el("span", null, (r.label || r.kind) + (r.version ? " " + r.version : "")));
        chip.appendChild(el("span", "badge " + (CK_FRESH_BADGE[r.freshness] || "st-missing"),
          r.freshness_zh || r.freshness));
        chip.title = r.basis || "";
        chips.appendChild(chip);
      });
      b.appendChild(chips);
    }));

    /* spend (block 5) */
    grid.appendChild(ckBlock("花费 (spend)", (b) => {
      const sp = c.spend;
      if (blkErr(sp)) { b.appendChild(el("p", "ck-err", blkErr(sp))); return; }
      const total = Number(sp && sp.total || 0);
      const cur = (sp && sp.currency) || "";
      const limit = sp && sp.budget_limit;
      b.appendChild(el("div", "ck-line",
        "已花 " + fmtMoney(total) + " " + cur
        + (limit ? " / 预算 " + fmtMoney(limit) + " " + cur : " / 预算 ∞")));
      if (typeof limit === "number" && limit > 0) {
        const ratio = Math.max(0, total / limit);
        const bar = el("div", "ck-mini-bar");
        const fill = el("span", "ck-mini-fill" + (ratio > 0.9 ? " over" : ""));
        fill.style.width = Math.min(100, ratio * 100).toFixed(1) + "%";  /* CSSOM */
        bar.appendChild(fill);
        b.appendChild(bar);
      }
      if (sp && typeof sp.delta === "number") {
        b.appendChild(el("div", "ck-line muted",
          "估算差 (est. delta) " + (sp.delta >= 0 ? "+" : "") + fmtMoney(sp.delta)));
      }
    }));

    /* queue (block 6) — cloud pending from the ledger + live GUI jobs */
    grid.appendChild(ckBlock("队列 (queue)", (b) => {
      const q = c.queue;
      const cloud = (q && !blkErr(q) && typeof q.pending_cloud === "number") ? q.pending_cloud : 0;
      const active = (lastJobs || []).filter((j) => j.state === "queued" || j.state === "running").length;
      if (!cloud && !active) { b.appendChild(el("p", "ck-empty", "空闲 (idle)")); return; }
      if (active) b.appendChild(el("div", "ck-line", "本地任务 (GUI jobs) · " + active + " 进行中"));
      if (cloud) b.appendChild(el("div", "ck-line", "云端待轮询 (cloud pending) · " + cloud));
    }));

    /* approvals (block 8) — storyboard 审批 pending */
    grid.appendChild(ckBlock("审批 (approvals)", (b) => {
      const ap = c.approvals;
      if (blkErr(ap)) { b.appendChild(el("p", "ck-err", blkErr(ap))); return; }
      const pending = ap ? (ap.pending || 0) : 0;
      if (!pending) { b.appendChild(el("p", "ck-empty", "无待审 (none pending)")); return; }
      const line = el("div", "ck-line");
      line.appendChild(el("span", null, pending + " 个镜头待审 (pending)"));
      b.appendChild(line);
      const a = el("a", "muted", "去审片 (review) →");
      a.href = "/review";
      b.appendChild(a);
    }));

    /* recent activity (block 7) */
    grid.appendChild(ckBlock("最近动态 (activity)", (b) => {
      const act = c.activity;
      if (blkErr(act)) { b.appendChild(el("p", "ck-err", blkErr(act))); return; }
      const events = (act && Array.isArray(act.events)) ? act.events : [];
      if (!events.length) { b.appendChild(el("p", "ck-empty", "暂无动态 (no events)")); return; }
      events.slice(0, 6).forEach((e) => {
        const row = el("div", "ck-ev");
        row.appendChild(el("span", "badge ac-" + (e.actor || "engine"), e.actor || "?"));
        row.appendChild(el("span", "eaction", e.action || ""));
        if (e.summary) row.appendChild(el("span", "edetail", e.summary));
        if (e.ts) row.appendChild(el("span", "etime muted", fmtClock(e.ts)));
        b.appendChild(row);
      });
    }));

    /* suggestions (block 8, the rest of suggest_next) */
    const sg = c.suggestions;
    const sgItems = (sg && !blkErr(sg) && Array.isArray(sg.items)) ? sg.items : [];
    if (sgItems.length) {
      grid.appendChild(ckBlock("其它建议 (suggestions)", (b) => {
        sgItems.forEach((s) => {
          const row = el("div", "ck-sugg");
          row.appendChild(el("span", "ck-risk-dot muted", "•"));
          const txt = el("span", null, s.text || s.kind || "");
          if (s.shot) {
            txt.classList.add("tnote", "seekable");
            txt.addEventListener("click", () => { try { focusShot(s.shot); } catch (e) {} });
          }
          row.appendChild(txt);
          b.appendChild(row);
        });
      }));
    }

    /* evaluate (round AA item 8) — its own wide block: skill usage / rework
     * hotspots / QC tallies, then the mandatory honesty section VERBATIM. */
    grid.appendChild(renderEvaluateBlock());

    return grid;
  }

  /* ------------------------------------------------- evaluate (item 8) --- */
  function renderEvaluateBlock() {
    const b = el("div", "ck-block wide");
    b.appendChild(el("h3", null, "评估 (evaluate)"));
    if (evalErr && !evalData) {
      b.appendChild(el("p", "ck-err", "评估不可用 (evaluate unavailable): " + evalErr));
      return b;
    }
    if (!evalData) {
      b.appendChild(el("p", "loading", "加载中 (loading)…"));
      return b;
    }
    const ev = evalData;

    /* 技能使用 skills: top used + never-used */
    const skSub = el("div", "ck-eval-sub");
    const skills = ev.skills || {};
    skSub.appendChild(el("h4", null, "技能使用 (skills) · 已用 "
      + (skills.used_total || 0) + "/" + (skills.installed_total || 0)));
    const topUsed = (skills.usage || []).filter((r) => r.count > 0).slice(0, 5);
    if (topUsed.length) {
      const chips = el("div", "ck-chips");
      topUsed.forEach((r) => {
        const chip = el("span", "ck-dv");
        chip.appendChild(el("span", null, r.id + " ×" + r.count));
        if (r.low_n) chip.appendChild(el("span", "badge lvl-warn", "样本少"));
        chips.appendChild(chip);
      });
      skSub.appendChild(chips);
    } else {
      skSub.appendChild(el("p", "ck-empty", "暂无技能调用记录 (no skill usage yet)"));
    }
    const neverUsed = skills.never_used || [];
    if (neverUsed.length) {
      skSub.appendChild(el("div", "ck-line muted",
        "从未使用 (never used): " + neverUsed.join("、")));
    }
    b.appendChild(skSub);

    /* 返工热点 rework hotspots: top redo + repair shots */
    const rhSub = el("div", "ck-eval-sub");
    rhSub.appendChild(el("h4", null, "返工热点 (rework hotspots)"));
    const wf = ev.workflow || {};
    const redo = wf.redo || {};
    const repair = wf.repair || {};
    const redoTop = (redo.hotspots || []).slice(0, 5);
    if (redoTop.length) {
      rhSub.appendChild(el("div", "ck-line", "重做 (redo) 共 " + (redo.total || 0) + " 次 · "
        + redoTop.map((r) => r.shot + " ×" + r.count).join("、")));
    } else {
      rhSub.appendChild(el("p", "ck-empty", "暂无重做记录 (no redo yet)"));
    }
    const repairTop = (repair.hotspots || []).slice(0, 5);
    if (repairTop.length) {
      rhSub.appendChild(el("div", "ck-line", "修复 (repair) 共 " + (repair.total || 0) + " 次 · "
        + repairTop.map((r) => r.shot + " ×" + r.count).join("、")));
    }
    b.appendChild(rhSub);

    /* QC verdict tallies */
    const qcSub = el("div", "ck-eval-sub");
    const qc = ev.qc || {};
    qcSub.appendChild(el("h4", null, "QC 判读 (verdicts) · 共 " + (qc.verdicts_total || 0)));
    if (qc.verdicts_total) {
      const chips = el("div", "ck-chips");
      const by = qc.by_level || {};
      const lvlBadge = { blocker: "lvl-error", issue: "lvl-warn", fyi: "lvl-info" };
      ["blocker", "issue", "fyi"].forEach((lv) => {
        const n = by[lv] || 0;
        if (!n) return;
        chips.appendChild(el("span", "badge " + lvlBadge[lv], lv + " " + n));
      });
      qcSub.appendChild(chips);
    } else {
      qcSub.appendChild(el("p", "ck-empty", "暂无 QC 判读记录 (no QC verdicts yet)"));
    }
    b.appendChild(qcSub);

    /* honesty — VERBATIM, visible, styled as an info callout (not a warning):
     * this section IS the point of the feature (goal item 8), never fine print. */
    const hn = ev.honesty || {};
    const callout = el("div", "ck-honesty");
    callout.appendChild(el("h4", null, "诚实说明 (honesty)"));
    if (hn.summary) callout.appendChild(el("p", "ck-honesty-summary", hn.summary));
    const claims = Array.isArray(hn.cannot_claim) ? hn.cannot_claim : [];
    if (claims.length) {
      const ul = document.createElement("ul");
      claims.forEach((c) => {
        const li = document.createElement("li");
        li.textContent = c;
        ul.appendChild(li);
      });
      callout.appendChild(ul);
    }
    b.appendChild(callout);

    return b;
  }

  function ckBlock(title, fill) {
    const b = el("div", "ck-block");
    b.appendChild(el("h3", null, title));
    try { fill(b); } catch (err) { b.appendChild(el("p", "ck-err", errMsg(err))); }
    return b;
  }

  /* ---------------------------------------------------------- header --- */
  let finalOpen = false;  /* 成片预览 toggle survives re-renders */

  function renderHeader(s) {
    const root = $("header");
    clear(root);
    const p = s.project || {};
    const b = s.budget || {};
    const tl = s.timeline || {};

    const top = el("div", "head-top");
    top.appendChild(el("h1", null, p.name || "manju"));
    const chips = el("div", "chips");
    const chip = (t) => chips.appendChild(el("span", "chip", t));
    if (p.width && p.height) chip(p.width + "×" + p.height);
    if (p.fps) chip(p.fps + " fps");
    if (p.mode) chip("模式 " + p.mode);
    if (tl.exists) chip("时长 " + fmtDur(tl.duration_ms));
    chip("分镜 " + (Array.isArray(s.shots) ? s.shots.length : 0));
    const bl = s.build_lock;
    if (bl && typeof bl === "object") {
      /* another process (or a crashed run) holds the build lock right now;
       * both lock shapes travel here — {pid,actor,started,hostname} and the
       * unreadable-file {note} — so every key is optional */
      let txt = "构建中 (building)";
      if (bl.pid !== undefined && bl.pid !== null) txt += " pid " + bl.pid;
      if (bl.actor) txt += " · " + bl.actor;
      if (bl.pid === undefined && !bl.actor && bl.note) txt += " · " + bl.note;
      const lockChip = el("span", "chip build-lock", txt);
      if (bl.started) lockChip.title = "started " + bl.started + (bl.hostname ? " @ " + bl.hostname : "");
      chips.appendChild(lockChip);
    }
    if (s.readonly === true) {
      const ro = el("span", "chip readonly", "只读 (readonly)");
      ro.title = "只读工作台 — 所有修改操作已停用 (all mutating controls disabled)";
      chips.appendChild(ro);
    }
    if (s.workspace && typeof s.workspace === "object") {
      chips.appendChild(workspaceChip(s.workspace));
    }
    /* re-open the first-run checklist any time (Linear/Notion pattern: the
     * onboarding guide is always one click away, never only at first run) */
    const help = el("button", "chip", "帮助 · 新手引导 (guide)");
    help.type = "button";
    help.title = "打开新手引导清单 (open the onboarding checklist)";
    help.addEventListener("click", () => openOnboarding());
    chips.appendChild(help);
    top.appendChild(chips);
    root.appendChild(top);

    const limitTxt = (b.limit === null || b.limit === undefined) ? "∞" : fmtMoney(b.limit);
    root.appendChild(el("div", "spend",
      "花费 " + fmtMoney(b.total_cost || 0) + " " + (b.currency || "") + " / 预算 " + limitTxt));
    if (typeof b.limit === "number" && b.limit > 0) {
      const ratio = Math.max(0, (b.total_cost || 0) / b.limit);
      const bar = el("div", "bar");
      const fill = el("span", "bar-fill" + (ratio > 0.9 ? " over" : ""));
      fill.style.width = Math.min(100, ratio * 100).toFixed(1) + "%";  /* CSSOM: CSP-safe */
      bar.appendChild(fill);
      root.appendChild(bar);
    }
    if (s.latest_final_note) {   /* crashed-render honesty (§3) */
      root.appendChild(el("div", "final-note", "⚠ 成片提示 (final note): " + s.latest_final_note));
    }

    if (s.next_step) root.appendChild(el("div", "next-step", "下一步 (next): " + s.next_step));

    const fin = s.latest_final;
    if (fin && fin.url) {
      const wrap = el("div", "final");
      const btn = el("button", "btn ghost", finalOpen ? "成片预览 ▾" : "成片预览 ▸");
      btn.type = "button";
      const holder = el("div", "final-video" + (finalOpen ? "" : " hidden"));
      const mkVideo = () => {
        if (holder.firstChild) return;
        const v = document.createElement("video");
        v.controls = true;
        v.preload = "none";
        v.src = fin.url;
        holder.appendChild(v);
      };
      if (finalOpen) mkVideo();
      btn.addEventListener("click", () => {
        finalOpen = !finalOpen;
        btn.textContent = finalOpen ? "成片预览 ▾" : "成片预览 ▸";
        holder.classList.toggle("hidden", !finalOpen);
        if (finalOpen) mkVideo();
      });
      const a = el("a", "final-link", fin.path || fin.url);
      a.href = fin.url;
      wrap.appendChild(btn);
      wrap.appendChild(a);
      wrap.appendChild(holder);
      /* version stack (Frame.io pattern): newest on top, append-only —
       * every final_vN is one click away; a missing key sidecar is flagged */
      const finals = Array.isArray(s.finals) ? s.finals : [];
      if (finals.length > 1) {
        const stack = el("div", "vstack");
        finals.forEach((f, i) => {
          const rowEl = el("div", "vrow");
          const link = el("a", "final-link", f.name);
          link.href = f.url;
          rowEl.appendChild(link);
          if (i === 0) rowEl.appendChild(el("span", "chip", "当前 (current)"));
          if (f.has_key === false) {
            rowEl.appendChild(el("span", "chip warn", "⚠ 无内容键 (no key)"));
          }
          if (f.size) rowEl.appendChild(el("span", "muted",
            (f.size / 1e6).toFixed(1) + " MB"));
          stack.appendChild(rowEl);
        });
        wrap.appendChild(stack);
      }
      root.appendChild(wrap);
    }
  }

  /* ---------------------------------------------- workspace switcher --- */
  /* Only rendered when state.workspace is non-null (--workspace mode). The
   * menu is plain CSP-safe DOM; a document-level click closes any open menu
   * (menu items run first on the bubble, then the menu hides itself). */
  function workspaceChip(ws) {
    const wrap = el("span", "ws-wrap");
    const chipBtn = el("button", "chip ws",
      "项目 (project): " + (ws.active || "?") + " ▾");
    chipBtn.type = "button";
    chipBtn.title = "切换工作区项目 (switch workspace project) · 共 " + (ws.count || 0);
    const menu = el("div", "ws-menu hidden");
    chipBtn.addEventListener("click", async (ev) => {
      ev.stopPropagation();   /* the document listener would close it again */
      closeWsMenus(menu);     /* at most one open menu */
      if (!menu.classList.contains("hidden")) {
        menu.classList.add("hidden");
        return;
      }
      clear(menu);
      menu.appendChild(el("div", "muted ws-item", "加载中 (loading)…"));
      menu.classList.remove("hidden");
      try {
        const data = await api("GET", "/api/projects");
        clear(menu);
        const items = (data && Array.isArray(data.projects)) ? data.projects : [];
        if (!items.length) {
          menu.appendChild(el("div", "muted ws-item", "无项目 (no projects)"));
          return;
        }
        items.forEach((p) => {
          const item = el("button", "ws-item");
          item.type = "button";
          item.appendChild(el("span", null,
            (p.active ? "✓ " : "") + (p.name || p.slug)));
          item.appendChild(el("span", "ws-count", "分镜 " + (p.shots || 0)));
          item.title = p.root || "";
          if (p.active) {
            item.disabled = true;
          } else if (readonly) {
            item.disabled = true;
            item.title = "只读模式 (readonly)";
          } else {
            item.addEventListener("click", () => {
              menu.classList.add("hidden");
              doSwitch(p.slug);
            });
          }
          menu.appendChild(item);
        });
        /* new-project dialog lives in the switcher (S8a): create a sibling
         * project via the same core `manju new` calls, then switch to it */
        if (!readonly) {
          const np = el("button", "ws-item ws-new");
          np.type = "button";
          np.appendChild(el("span", null, "＋ 新建项目 (New project)"));
          np.addEventListener("click", (ev2) => {
            ev2.stopPropagation();
            menu.classList.add("hidden");
            openNewProjectDialog();
          });
          menu.appendChild(np);
        }
      } catch (err) {
        clear(menu);
        menu.appendChild(el("div", "muted ws-item",
          "项目列表不可用 (projects unavailable): " + errMsg(err)));
      }
    });
    wrap.appendChild(chipBtn);
    wrap.appendChild(menu);
    return wrap;
  }

  function closeWsMenus(except) {
    document.querySelectorAll(".ws-menu").forEach((m) => {
      if (m !== except) m.classList.add("hidden");
    });
  }

  async function requestOpenProject(slug) {
    /* Never rewrites PROJECT / resetAfterSwitch unless reload_current. */
    try {
      const data = await api("POST", "/api/workspace/open", { slug });
      handleSpaProjectAction(data);
    } catch (err) {
      if (err && err.data && err.data.next_action) {
        handleSpaProjectAction(err.data);
        return;
      }
      /* Fall back to legacy /api/switch (same immutable response). */
      try {
        const data = await api("POST", "/api/switch", { slug });
        handleSpaProjectAction(data);
      } catch (err2) {
        if (err2 && err2.data && err2.data.next_action) {
          handleSpaProjectAction(err2.data);
          return;
        }
        toast("打开项目失败：" + errMsg(err2 || err), "err");
      }
    }
  }

  function handleSpaProjectAction(data) {
    if (!data) return;
    const next = data.next_action || {};
    if (next.kind === "already_open" || data.code === "already_open") {
      return;  /* no token rewrite, no reset */
    }
    if (next.kind === "reload_current") {
      if (data.project_token) PROJECT = data.project_token;
      else if (data.project && data.project.id) PROJECT = data.project.id;
      resetAfterSwitch();
      refresh();
      return;
    }
    if (typeof handleProjectAction === "function") {
      handleProjectAction(data, {
        onReload: () => {
          if (data.project_token) PROJECT = data.project_token;
          resetAfterSwitch();
          refresh();
        }
      });
      return;
    }
    /* Fallback without project-action.js */
    toast((data.error || "请在新窗口打开其他项目") +
      (data.cli ? (" — " + data.cli) : ""), "err");
  }

  async function doSwitch(slug) {
    await requestOpenProject(slug);
  }

  /* every per-project cache must die with the old project */
  function resetAfterSwitch() {
    try { closeCompare(); } catch (e) { /* a stale compare overlay must not outlive the switch */ }
    Object.keys(sigs).forEach((k) => delete sigs[k]);
    lastFp = null;
    lastDoneCount = -1;
    lastShots = [];
    lastProjectName = "";   /* triage snapshot is per project name */
    reviewSnap = null;
    updateReviewChip();     /* hide 未阅 until the new project renders */
    finalOpen = false;
    kbFocusId = null;
    tlSeenSig = null;
    tlFingerprint = null;
    tlForce = false;
    gitLoaded = false;
    gitStale = true;
    gitStatus = null;
    gitLog = null;
    gitErr = null;
    gitLogErr = null;
    renderGit();
    if (gitOpen) fetchGitPanel();
    propFp = null;
    propStale = false;
    propLoaded = false;
    propItems = [];
    propErr = null;
    propExpanded = {};
    renderProposals();
    cockFp = null;
    cockStale = false;
    cockData = null;
    cockErr = null;
    renderCockpit();
    /* a waiting_user banner from the OLD project must not be confirmable
     * against the new one — the resend would target the active project */
    lastJobs.forEach((j) => {
      if (j.kind === "build" && j.result && j.result.waiting_user === true) {
        spendDismissed[j.id] = true;
      }
    });
    spendSig = null;
    if (spendBox) clear(spendBox);
    obShownFor = null;   /* re-evaluate onboarding auto-show for the new project */
    obForce = false;
    batchSel.clear();    /* selection is per-project */
    renderBatchBar();
    tasksLoaded = false;
    if (tasksOpen && tasksBody) { clear(tasksBody); fetchTasks(); }
  }

  /* new-project dialog (S8a) — reuses the #editor <dialog> as a generic modal
   * (its close handler resumes the poll loop). Name + preset (from `manju
   * presets`) + orientation; the server creates a sibling and switches to it. */
  async function openNewProjectDialog() {
    const dlg = $("editor");
    clear(dlg);
    const head = el("div", "ed-head");
    head.appendChild(el("h3", null, "新建项目 (new project)"));
    dlg.appendChild(head);
    dlg.appendChild(el("p", "muted ed-hint",
      "在工作区里新建一个同级项目 (a sibling project);与 CLI `manju new` 同一套核心。"));

    const nameL = el("label", "ctl", "名称 (name) ");
    const name = document.createElement("input");
    name.type = "text";
    name.className = "snap-input";
    name.placeholder = "my_film";
    name.maxLength = 80;
    nameL.appendChild(name);
    dlg.appendChild(nameL);

    const orientL = el("label", "ctl", "画幅 (orientation) ");
    const orient = document.createElement("select");
    [["vertical", "竖屏 9:16 (vertical)"], ["horizontal", "横屏 16:9 (horizontal)"]]
      .forEach((pair) => {
        const o = document.createElement("option");
        o.value = pair[0];
        o.textContent = pair[1];
        orient.appendChild(o);
      });
    orientL.appendChild(orient);
    dlg.appendChild(orientL);

    const presetL = el("label", "ctl", "预设 (preset) ");
    const preset = document.createElement("select");
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "无 / generic (none)";
    preset.appendChild(none);
    presetL.appendChild(preset);
    dlg.appendChild(presetL);

    const errBox = el("div", "ed-errors");
    dlg.appendChild(errBox);
    const row = el("div", "btnrow ed-btnrow");
    const create = el("button", "btn", "创建 (Create)");
    create.type = "button";
    const cancel = el("button", "btn ghost", "取消 (Cancel)");
    cancel.type = "button";
    cancel.addEventListener("click", () => closeEditor());
    create.addEventListener("click", async () => {
      const nm = name.value.trim();
      if (!nm) { toast("请输入名称 (name required)", "warn"); name.focus(); return; }
      create.disabled = true;
      cancel.disabled = true;
      clear(errBox);
      try {
        const body = { name: nm, vertical: orient.value !== "horizontal" };
        if (preset.value) body.preset = preset.value;
        const d = await api("POST", "/api/new-project", body);
        const pname = (d && d.project && d.project.name) || (d && d.name) || nm;
        toast("项目已创建：" + pname, "ok");
        closeEditor();
        /* Do NOT resetAfterSwitch / rewrite PROJECT — session stays put. */
        handleSpaProjectAction(d);
      } catch (err) {
        if (err && err.data && err.data.next_action) {
          const pname = (err.data.project && err.data.project.name) || nm;
          toast("项目已创建：" + pname, "ok");
          closeEditor();
          handleSpaProjectAction(err.data);
          return;
        }
        errBox.appendChild(el("p", "ed-err-title", "创建失败 (create failed): " + errMsg(err)));
        create.disabled = false;
        cancel.disabled = false;
      }
    });
    row.appendChild(create);
    row.appendChild(cancel);
    dlg.appendChild(row);

    editorOpen = true;   /* pause the poll loop while the dialog is up */
    pauseLive();
    if (typeof dlg.showModal === "function") { if (!dlg.open) dlg.showModal(); }
    else dlg.setAttribute("open", "");
    name.focus();

    /* fill presets after the dialog is up (a slow list must not block opening) */
    try {
      const data = await api("GET", "/api/presets");
      (data && Array.isArray(data.presets) ? data.presets : []).forEach((p) => {
        const o = document.createElement("option");
        o.value = p.name;
        o.textContent = p.name + " · " + (p.title || "") + " (" + (p.aspect || "") + ")";
        preset.appendChild(o);
      });
    } catch (err) { /* presets are optional; generic still works */ }
  }

  /* ----------------------------------------------------- build panel --- */
  let buildBtn = null;
  let qcBtn = null;
  let estBtn = null;
  let tfBtns = [];         /* truth-file editor buttons (bible/rules) */
  let newShotBtn = null;   /* assigned by initShotsBar */
  const RO_TIP = "只读模式 (readonly)";

  /* Build/QC follow the JOB queue (anyActive) — unrelated to the cross-
   * process build-lock chip in the header; readonly gates EVERY mutator
   * (the dry-run Estimate is a POST too, which the server 403s). */
  function updateGates() {
    const gate = (btn, queueLocked) => {
      if (!btn) return;
      btn.disabled = (queueLocked && anyActive) || readonly;
      btn.title = readonly ? RO_TIP : "";
    };
    gate(buildBtn, true);
    gate(qcBtn, true);
    gate(estBtn, false);
    tfBtns.forEach((b) => gate(b, false));
    gate(newShotBtn, false);
  }

  /* --------------------------------------------- spend confirm (§8.3) --- */
  let spendBox = null;   /* stable container at the top of the build panel */
  let spendSig = null;   /* "job:<id>" | "sync:<n>" — key of the shown banner */
  const spendDismissed = {};   /* banner key -> true (dismissed/confirmed) */

  const spendGateOf = (data) => {   /* waiting_user in any envelope shape */
    if (!data) return null;
    if (data.waiting_user === true) return data;
    if (data.result && data.result.waiting_user === true) return data.result;
    return null;
  };
  const spendText = (gate) =>
    (Array.isArray(gate.errors) && gate.errors.length ? gate.errors[0]
      : "waiting_user: 需要确认预估花费 (spend confirmation required)");

  /* jobs-driven: the NEWEST build job finishing with result.waiting_user
   * raises the banner; 确认 resends that job's EXACT params + assume_yes. */
  function renderSpend(jobs) {
    if (!spendBox) return;
    const builds = jobs.filter((j) => j.kind === "build")
      .sort((a, b) => String(b.created).localeCompare(String(a.created)));
    const j = builds[0];
    const waiting = !!(j && j.state === "done" && j.result &&
      j.result.waiting_user === true && !spendDismissed[j.id]);
    if (waiting) {
      if (spendSig !== "job:" + j.id) {
        spendSig = "job:" + j.id;
        showSpendBanner(spendText(j.result), j.params || {}, j.id);
      }
      return;
    }
    /* a job banner clears once its job is dismissed or superseded; a
     * "sync:" banner (future synchronous waiting_user) stays until acted on */
    if (spendSig && spendSig.indexOf("job:") === 0) {
      spendSig = null;
      clear(spendBox);
    }
  }

  function showSpendBanner(text, params, key) {
    clear(spendBox);
    const box = el("div", "spendbanner");
    box.appendChild(el("div", "sb-text", "⚠ " + text));
    const row = el("div", "btnrow");
    if (!readonly) {
      const ok = el("button", "btn confirm", "确认花费并构建 (Confirm & build)");
      ok.type = "button";
      ok.addEventListener("click", async () => {
        const body = Object.assign({}, params, { assume_yes: true, dry_run: false });
        spendDismissed[key] = true;
        spendSig = null;
        const data = await post(ok, "/api/build", body,
          "已确认花费,构建已重新入队 (confirmed — build re-queued)");
        if (data) clear(spendBox);
        else spendDismissed[key] = false;  /* resend failed: keep it retryable */
      });
      row.appendChild(ok);
    }
    const no = el("button", "btn ghost", "取消 (Dismiss)");
    no.type = "button";
    no.addEventListener("click", () => {
      spendDismissed[key] = true;
      spendSig = null;
      clear(spendBox);
    });
    row.appendChild(no);
    box.appendChild(row);
    spendBox.appendChild(box);
  }

  /* ---------------------------------------------- bible/rules editors --- */
  const TRUTH_LABELS = {
    characters: "bible/characters.yaml", scenes: "bible/scenes.yaml",
    props: "bible/props.yaml", style: "bible/style.yaml",
    rules: "timeline/rules.yaml", packaging: "timeline/packaging.yaml",
  };
  const TRUTH_URLS = { rules: "/api/rules", packaging: "/api/packaging" };
  const TRUTH_VKINDS = { rules: "rules", packaging: "packaging" };

  async function openTruthEditor(name, btn) {
    const url = TRUTH_URLS[name] || "/api/bible/" + name;
    const vkind = TRUTH_VKINDS[name] || "bible/" + name;  /* /api/validate kind */
    const label = TRUTH_LABELS[name] || name;
    if (btn) btn.disabled = true;
    try {
      const data = await api("GET", url);
      const hints = [];
      if (!data || data.exists === false) {
        hints.push("文件尚不存在,保存即创建 (file does not exist yet — saving creates it)");
      }
      showEditor({
        title: "编辑 (edit) · " + label,
        yaml: (data && data.yaml) || "",
        saveUrl: url,
        label,
        hints,
        validate: { kind: vkind },   /* live keystroke validation for this truth file */
      });
    } catch (err) {
      toast("无法加载 (cannot load) " + label + ": " + errMsg(err), "err");
    } finally {
      if (btn) btn.disabled = false;
      updateGates();
    }
  }

  let updateEstimate = () => {};  /* bound in initBuildPanel */
  let estSeenFp = null;

  function initBuildPanel() {
    const root = $("buildpanel");
    clear(root);
    root.appendChild(el("h2", null, "构建 (build)"));
    spendBox = el("div");   /* the §8.3 waiting_user banner lands here */
    root.appendChild(spendBox);

    const row = el("div", "controls");
    const mkSelect = (labelTxt, values) => {
      const l = el("label", "ctl", labelTxt + " ");
      const sel = document.createElement("select");
      values.forEach((v) => {
        const o = document.createElement("option");
        o.value = v;
        o.textContent = v;
        sel.appendChild(o);
      });
      l.appendChild(sel);
      row.appendChild(l);
      return sel;
    };
    const mkCheck = (labelTxt) => {
      const l = el("label", "ctl");
      const c = document.createElement("input");
      c.type = "checkbox";
      l.appendChild(c);
      l.appendChild(document.createTextNode(" " + labelTxt));
      row.appendChild(l);
      return c;
    };
    const target = mkSelect("目标 (target)", ["final", "proxy", "exports", "qc"]);
    const gen = mkSelect("生成 (gen)", ["missing", "auto", "off"]);
    const regen = mkCheck("重做过期 (regen stale)");
    const force = mkCheck("强制 (force)");
    root.appendChild(row);

    /* UX-STUDY #1: the price rides ON the trigger — a silent dry-run keeps
     * the Build button honest about what clicking it would spend */
    let estSeq = 0;
    updateEstimate = async () => {
      if (readonly || !buildBtn) return;
      const seq = ++estSeq;
      try {
        const data = await api("POST", "/api/build", buildBody(true));
        if (seq !== estSeq || !data || !data.result) return;
        const cost = Number(data.result.estimated_cost || 0);
        const cur = ((data.result.plan || [])
          .map((it) => it.currency).find(Boolean)) || "";
        buildBtn.textContent = cost > 0
          ? "构建 (Build) ≈" + fmtMoney(cost) + (cur ? " " + cur : "")
          : "构建 (Build)";
        buildBtn.classList.toggle("spendy", cost > 0);
      } catch (err) { /* advisory only: the button stays plain */ }
    };
    [target, gen, regen, force].forEach((c) =>
      c.addEventListener("change", () => updateEstimate()));

    const out = el("div", "panel-out");
    const buildBody = (dry) => ({
      target: target.value,
      gen: gen.value,
      regen_stale: regen.checked,
      force: force.checked,
      dry_run: dry,
    });

    const btns = el("div", "btnrow");
    const mkBtn = (label, cls, fn) => {
      const btn = el("button", "btn " + cls, label);
      btn.type = "button";
      btn.addEventListener("click", () => fn(btn));
      btns.appendChild(btn);
      return btn;
    };
    estBtn = mkBtn("估算 (Estimate)", "ghost", async (btn) => {
      const data = await post(btn, "/api/build", buildBody(true), "估算完成 (dry run)");
      if (data && data.result) renderPlan(out, data.result);
    });
    buildBtn = mkBtn("构建 (Build)", "primary", (btn) => {
      /* §4.4: the plan modal FIRST (dry-run explanation), then an explicit
       * confirm that passes assume_yes — never a silent spend. The spend banner
       * below stays as a belt-and-braces fallback for a synchronous gate. */
      const body = buildBody(false);
      showPlanModal("build", {
        target: body.target, gen: body.gen,
        regen_stale: body.regen_stale, force: body.force,
      }, {
        title: "构建前计划 (plan before build)",
        onConfirm: async () => {
          const confirmed = Object.assign({}, body, { assume_yes: true });
          const data = await post(btn, "/api/build", confirmed, "构建任务已入队 (build queued)");
          const gate = spendGateOf(data);  /* SYNCHRONOUS waiting_user, if any */
          if (gate) {
            spendSig = "sync:" + Date.now();
            showSpendBanner(spendText(gate), confirmed, spendSig);
          }
        },
      });
    });
    /* WP2 先听后看: audition target — voice+captions on slate, no picture gen */
    mkBtn("先听后看 (Audition)", "ghost", (btn) => {
      showPlanModal("build", { target: "audition", gen: "missing" }, {
        title: "先听后看 — 配音+字幕试听片 (no picture generation)",
        onConfirm: async () => {
          const body = { target: "audition", gen: "missing", assume_yes: true };
          const data = await post(btn, "/api/build", body, "试听片任务已入队");
          const gate = spendGateOf(data);
          if (gate) {
            spendSig = "sync:" + Date.now();
            showSpendBanner(spendText(gate), body, spendSig);
          }
        },
      });
    });
    qcBtn = mkBtn("QC", "ghost", (btn) =>
      post(btn, "/api/qc", {}, "QC 任务已入队 (qc queued)"));
    mkBtn("检查 (Check)", "ghost", async (btn) => {
      btn.disabled = true;
      try { renderCheck(out, await api("GET", "/api/check")); }
      catch (err) { toast(errMsg(err), "err"); }
      finally { btn.disabled = false; }
    });
    mkBtn("解释 (Explain)", "ghost", async (btn) => {
      btn.disabled = true;
      try { renderExplain(out, await api("GET", "/api/explain")); }
      catch (err) { toast(errMsg(err), "err"); }
      finally { btn.disabled = false; }
    });
    mkBtn("体检 (Doctor)", "ghost", async (btn) => {
      btn.disabled = true;
      try { renderDoctor(out, await api("GET", "/api/doctor")); }
      catch (err) {
        clear(out);
        out.appendChild(el("h3", null, "体检 (doctor)"));
        out.appendChild(el("p", "muted", "体检不可用 (doctor unavailable): " + errMsg(err)));
      }
      finally { btn.disabled = false; }
    });
    root.appendChild(btns);

    /* truth 文件 (files): bible + rules editors — same dialog flow as shots */
    const tf = el("div", "tfrow");
    tf.appendChild(el("span", "lbl", "truth 文件 (files):"));
    tfBtns = [];
    ["characters", "scenes", "props", "style", "rules", "packaging"].forEach((name) => {
      const b = el("button", "btn ghost mini", name);
      b.type = "button";
      b.title = TRUTH_LABELS[name] || name;
      b.addEventListener("click", () => openTruthEditor(name, b));
      tf.appendChild(b);
      tfBtns.push(b);
    });
    root.appendChild(tf);
    root.appendChild(out);
  }

  function renderPlan(out, result) {
    clear(out);
    out.appendChild(el("h3", null, "试算计划 (plan)"));
    const plan = Array.isArray(result.plan) ? result.plan : [];
    if (!plan.length) {
      out.appendChild(el("p", "muted", "无事可做 (nothing to do)。"));
    } else {
      const wrap = el("div", "tablewrap");
      const table = document.createElement("table");
      const thead = document.createElement("thead");
      const hr = document.createElement("tr");
      hr.appendChild(el("th", null, "shot"));
      hr.appendChild(el("th", null, "reason"));
      hr.appendChild(el("th", null, "provider"));
      hr.appendChild(el("th", "num", "≈cost"));
      thead.appendChild(hr);
      table.appendChild(thead);
      const tbody = document.createElement("tbody");
      plan.forEach((it) => {
        const tr = document.createElement("tr");
        const isVoice = it.kind === "voice";
        if (isVoice) tr.className = "voice-row";
        const shotCell = el("td", null, it.shot || "");
        if (isVoice) shotCell.appendChild(el("span", "badge st-manual vtag", "配音"));
        tr.appendChild(shotCell);
        tr.appendChild(el("td", null, it.reason || ""));
        tr.appendChild(el("td", null, it.provider || ""));
        tr.appendChild(el("td", "num", "≈" + fmtMoney(it.estimated_cost || 0)));
        tbody.appendChild(tr);
      });
      const totalRow = document.createElement("tr");
      totalRow.className = "total";
      const lbl = el("td", null, "合计预估 (estimated total)");
      lbl.colSpan = 3;
      totalRow.appendChild(lbl);
      const cur = (plan.find((p) => p.currency) || {}).currency || "";
      totalRow.appendChild(el("td", "num grand",   /* §8.3: the number a human
        confirms must be impossible to miss */
        "≈" + fmtMoney(result.estimated_cost || 0) + (cur ? " " + cur : "")));
      tbody.appendChild(totalRow);
      table.appendChild(tbody);
      wrap.appendChild(table);
      out.appendChild(wrap);
    }
    /* cache savings (Nx replayed-hits pattern): what fresh targets did NOT
     * cost — saved_cost prices skipped regeneration, skipped lists the hits */
    const skippedShots = Array.isArray(result.skipped) ? result.skipped : [];
    const savedCost = Number(result.saved_cost || 0);
    if ((isFinite(savedCost) && savedCost > 0) || skippedShots.length) {
      const row = el("div", "savings");
      if (isFinite(savedCost) && savedCost > 0) {
        row.appendChild(el("span", "badge st-fresh",
          "缓存命中省 ≈" + fmtMoney(savedCost) + " (saved)"));
      }
      if (skippedShots.length) {
        const skChip = el("span", "chip", "跳过 " + skippedShots.length + " (cache hits)");
        skChip.title = skippedShots.join(", ");
        row.appendChild(skChip);
      }
      out.appendChild(row);
    }
    (result.errors || []).forEach((m) => out.appendChild(el("p", "lvl-error", "错误: " + m)));
    (result.warnings || []).forEach((m) => out.appendChild(el("p", "lvl-warning", "警告: " + m)));
  }

  function renderCheck(out, data) {
    clear(out);
    out.appendChild(el("h3", null, "检查 (check)"));
    out.appendChild(el("p", data.ok ? "lvl-ok" : "lvl-error",
      data.ok ? "✓ 通过 (ok)" : "✗ 未通过 (failed)"));
    (data.errors || []).forEach((m) => out.appendChild(el("p", "lvl-error", "错误: " + m)));
    (data.warnings || []).forEach((m) => out.appendChild(el("p", "lvl-warning", "警告: " + m)));
  }

  function renderExplain(out, data) {
    clear(out);
    out.appendChild(el("h3", null, "解释 (explain)"));
    const chips = el("div", "chips");
    const verdictChip = (label, v) => {
      if (!v) return;
      const cls = String(v).startsWith("skip") ? "st-fresh" : "st-stale";
      chips.appendChild(el("span", "badge " + cls, label + ": " + v));
    };
    const renders = data.renders || {};
    if (renders.final) verdictChip("final", renders.final.verdict);
    if (renders.proxy) verdictChip("proxy", renders.proxy.verdict);
    if (data.timeline) verdictChip("timeline", data.timeline.verdict);
    out.appendChild(chips);
    if (renders.note) out.appendChild(el("p", "muted", renders.note));
    (data.shots || []).forEach((sh) => {
      const v = sh.video || {};
      if (v.state && v.state !== "fresh") {
        out.appendChild(el("p", "why",
          sh.shot + " · " + v.state + (v.why ? " — " + v.why : "")));
      }
      const vo = sh.voice;
      if (vo && vo.state && vo.state !== "fresh") {
        out.appendChild(el("p", "why",
          sh.shot + " · 配音 " + vo.state + (vo.why ? " — " + vo.why : "")));
      }
    });
  }

  /* -------------------------------------------------------- doctor ----- */
  /* GET /api/doctor renders into the same inline-results area Check/Explain
   * use; `line` arrives preformatted (✓/✗/•/⚠) — shown verbatim, monospace. */
  function renderDoctor(out, data) {
    clear(out);
    out.appendChild(el("h3", null, "体检 (doctor)"));
    const chips = el("div", "chips");
    chips.appendChild(el("span", "badge " + (data.ok ? "st-fresh" : "st-broken"),
      data.ok ? "✓ 通过 (ok)" : "✗ 有问题 (problems)"));
    out.appendChild(chips);
    const list = el("div");
    (Array.isArray(data.checks) ? data.checks : []).forEach((c) => {
      list.appendChild(el("div", "doc-line",
        c.line || ((c.name || "?") + ": " + (c.detail || ""))));
    });
    out.appendChild(list);
  }

  /* ------------------------------------------------------ jobs strip --- */
  /* Grouped visibility (GUI repair): never sort-then-slice(0,8) — a long
   * queue of newer queued jobs was hiding the actual running job. */
  function jobTime(j) {
    return j.created || j.started || j.finished || "";
  }
  function pickJobs(jobs, states, limit) {
    return jobs.filter((j) => states.indexOf(j.state) >= 0)
      .sort((a, b) => String(jobTime(b)).localeCompare(String(jobTime(a))))
      .slice(0, limit);
  }
  function renderJobs(jobs) {
    const root = $("jobs");
    clear(root);
    if (!jobs.length) { root.classList.add("hidden"); return; }
    root.classList.remove("hidden");
    root.appendChild(el("h2", null, "任务 (jobs)"));
    const running = pickJobs(jobs, ["running", "canceling"], 99);  /* always show */
    const queued = pickJobs(jobs, ["queued"], 3);
    const need = pickJobs(jobs, ["failed", "interrupted"], 5);
    const recent = pickJobs(jobs, ["done", "canceled"], 5);
    const groups = [
      { title: "正在运行 (running)", items: running },
      { title: "接下来 (queued)", items: queued },
      { title: "需要处理 (needs attention)", items: need },
      { title: "最近完成 (recent)", items: recent },
    ];
    let expandedOne = false;  /* UX-STUDY #5: auto-expand only the newest failure */
    groups.forEach((g) => {
      if (!g.items.length) return;
      root.appendChild(el("div", "muted jgroup", g.title));
      g.items.forEach((j) => renderJobRow(j));
    });

    function renderJobRow(j) {
      const row = el("div", "job");
      row.appendChild(el("span", "jkind", j.kind));
      /* round AA item 6: an interrupted job (a past GUI process's dangling
       * queued/running job — see gui/jobs.py's JobRunner.interrupted()) gets
       * its own 中文 chip, never the raw English state word every other
       * state renders as-is. */
      row.appendChild(el("span", "badge jb-" + j.state,
        j.state === "interrupted" ? "已中断" : j.state));
      const secs = jobSeconds(j);
      if (secs) row.appendChild(el("span", "muted", secs));
      /* goal: honest job cancellation — retry lineage + cancel/retry buttons.
       * cancelable/retryable come straight from Job.to_dict() so the client
       * never re-derives the state-machine rule. */
      if (j.retry_of) {
        row.appendChild(el("span", "muted", "重试自 #" + j.retry_of));
      }
      if (j.state === "interrupted" && j.note) {
        /* visible, not tucked into a <details> — the honesty note IS the
         * point of this chip, never fine print (goal item 6). No cancel/
         * retry buttons render for this state: cancelable/retryable are
         * both false straight off the same dict, so nothing below adds them. */
        row.appendChild(el("span", "jnote", j.note));
      }
      if (j.cancelable) {
        const cancelBtn = el("button", "btn mini ghost", "取消");
        cancelBtn.type = "button";
        roGate(cancelBtn);  /* disabled + tooltip in readonly; click never fires then */
        cancelBtn.addEventListener("click", () =>
          post(cancelBtn, "/api/jobs/cancel", { job_id: j.id }, "已请求取消 (cancel requested)"));
        row.appendChild(cancelBtn);
      }
      if (j.retryable) {
        const retryBtn = el("button", "btn mini ghost", "重试");
        retryBtn.type = "button";
        roGate(retryBtn);
        retryBtn.addEventListener("click", () =>
          post(retryBtn, "/api/jobs/retry", { job_id: j.id }, "已重新提交 (retried)"));
        row.appendChild(retryBtn);
      }
      if (j.state === "done" && j.result) {
        const r = j.result;
        let summary = "";
        if (r.render_path) summary = r.render_path;
        else if (typeof r.qc_ok === "boolean") summary = "qc_ok: " + (r.qc_ok ? "✓" : "✗");
        else if (Array.isArray(r.errors) && r.errors.length) summary = r.errors[0];
        if (summary) row.appendChild(el("span", "jsum", summary));
        /* cache savings on done builds (Nx pattern): mirror renderPlan */
        if (j.kind === "build") {
          const saved = Number(r.saved_cost || 0);
          if (isFinite(saved) && saved > 0) {
            row.appendChild(el("span", "jsave",
              "缓存命中省 ≈" + fmtMoney(saved) + " (saved)"));
          }
          const hits = Array.isArray(r.skipped) ? r.skipped : [];
          if (hits.length) {
            const sk = el("span", "jsave", "跳过 " + hits.length + " (cache hits)");
            sk.title = hits.join(", ");
            row.appendChild(sk);
          }
        }
        /* batch results (goal item 3): per-shot skipped/failed reasons, never
         * a silent exclusion — the BatchResult carries {shot, reason} for each */
        if (j.kind === "redo_batch" || j.kind === "voice_batch") {
          const ran = Array.isArray(r.ran) ? r.ran : [];
          const skipped = Array.isArray(r.skipped) ? r.skipped : [];
          const failed = Array.isArray(r.failed) ? r.failed : [];
          row.appendChild(el("span", "jsum",
            "完成 " + ran.length + " · 跳过 " + skipped.length + " · 失败 " + failed.length));
          if (skipped.length || failed.length) {
            const det = document.createElement("details");
            det.appendChild(el("summary", null, "跳过/失败原因 (skipped / failed)"));
            const box = el("div", "bb-res");
            skipped.forEach((s) => {
              const b = el("div", "pm-skiprow");
              b.appendChild(el("span", "badge st-manual", s.shot || "?"));
              b.appendChild(el("span", "muted", "跳过 (skipped): " + (s.reason || "")));
              box.appendChild(b);
            });
            failed.forEach((f) => {
              const b = el("div", "pm-skiprow");
              b.appendChild(el("span", "badge qc-err", f.shot || "?"));
              b.appendChild(el("span", "muted", "失败 (failed): " + (f.reason || "")));
              box.appendChild(b);
            });
            det.appendChild(box);
            row.appendChild(det);
          }
        }
      }
      if ((j.state === "failed" || j.state === "canceled") && j.error) {
        const det = document.createElement("details");
        if (!expandedOne) { det.open = true; expandedOne = true; }
        det.appendChild(el("summary", null,
          j.state === "canceled" ? "已取消 (canceled)" : "错误 (error)"));
        det.appendChild(el("div", "jerr", j.error));
        row.appendChild(det);
      }
      root.appendChild(row);
    }
  }

  /* --------------------------------------------------- timeline strip --- */
  let tlSeenSig = null;      /* state.timeline signature at the last fetch */
  let tlFingerprint = null;  /* meta.compiled_from of the rendered strip */
  let tlFetching = false;
  let tlForce = false;       /* a done job may have recompiled the timeline */

  function maybeTimeline(s) {
    const t = s.timeline;
    const root = $("timeline");
    if (!t || !t.exists) {
      tlSeenSig = null;
      tlFingerprint = null;
      tlForce = false;
      if (!root.classList.contains("hidden")) { clear(root); root.classList.add("hidden"); }
      return;
    }
    /* /api/state carries no compiled_from, so its cheap timeline sub-object
     * (exists/duration/mode) + job completions are the "maybe changed"
     * signal that gates the fetch; the fetched meta.compiled_from stays
     * cached as the render key, so an unchanged timeline is neither
     * re-fetched on every poll nor ever re-rendered. */
    const sig = JSON.stringify(t);
    if (tlFetching) return;
    if (!tlForce && sig === tlSeenSig) return;
    fetchTimeline(sig, root);
  }

  async function fetchTimeline(sig, root) {
    tlFetching = true;
    tlSeenSig = sig;   /* recorded up-front: an error must not hammer /api/timeline */
    tlForce = false;
    try {
      const data = await api("GET", "/api/timeline");
      const tl = data ? data.timeline : null;
      const fp = (tl && tl.meta) ? (tl.meta.compiled_from || "?") : null;
      if (fp !== null && fp === tlFingerprint) return;  /* strip already current */
      tlFingerprint = fp;
      renderTimeline(tl);
    } catch (err) {
      tlFingerprint = null;  /* next state change (or done job) retries */
      clear(root);
      root.classList.remove("hidden");
      root.appendChild(el("h2", null, "时间线 (timeline)"));
      root.appendChild(el("p", "muted tl-note",
        "时间线不可用 (timeline unavailable): " + errMsg(err)));
    } finally {
      tlFetching = false;
    }
  }

  function renderTimeline(tl) {
    const root = $("timeline");
    clear(root);
    if (!tl) { root.classList.add("hidden"); return; }
    root.classList.remove("hidden");
    root.appendChild(el("h2", null, "时间线 (timeline)"));
    const tracks = tl.tracks || {};
    const clips = Array.isArray(tracks.video) ? tracks.video : [];
    let total = (typeof tl.duration_ms === "number" && tl.duration_ms > 0) ? tl.duration_ms : 0;
    if (!total) clips.forEach((c) => { total = Math.max(total, (c.start_ms || 0) + (c.duration_ms || 0)); });
    if (!clips.length || total <= 0) {
      root.appendChild(el("p", "muted tl-note", "时间线为空 (timeline is empty)"));
      return;
    }
    const strip = el("div", "tl-strip");
    clips.forEach((c, i) => {
      const block = el("div", "tl-clip tl-h" + (i % 6), c.shot || "?");
      /* proportional width via the CSSOM (CSP-safe); .tl-clip min-width in
       * the stylesheet keeps tiny clips clickable (the strip then scrolls) */
      block.style.width = (((c.duration_ms || 0) / total) * 100).toFixed(3) + "%";
      block.title = (c.shot || "?") + " / " + (c.take || "?") + " · " + fmtDur(c.duration_ms);
      block.addEventListener("click", () => focusShot(c.shot));
      strip.appendChild(block);
    });
    root.appendChild(strip);
    /* overlay/music tracks are deliberately NOT drawn in v2: at this strip
     * height they are a handful of near-invisible slivers (low signal), and
     * the captions rail already marks the spoken beats. */
    const caps = Array.isArray(tracks.captions) ? tracks.captions : [];
    if (caps.length) {
      const rail = el("div", "tl-caps");
      caps.forEach((c) => {
        const b = el("div", "tl-cap");
        const start = Math.max(0, c.start_ms || 0);
        const width = Math.max(0, (c.end_ms || 0) - start);
        b.style.left = ((start / total) * 100).toFixed(3) + "%";   /* CSSOM */
        b.style.width = ((width / total) * 100).toFixed(3) + "%";
        b.title = (c.speaker ? c.speaker + ": " : "") + (c.text || "");
        rail.appendChild(b);
      });
      root.appendChild(rail);
    }
    const ruler = el("div", "tl-ruler");
    [0, 0.25, 0.5, 0.75, 1].forEach((f) =>
      ruler.appendChild(el("span", null, fmtMMSS(total * f))));
    root.appendChild(ruler);
  }

  function cardById(id) {
    const cards = document.querySelectorAll("#shots .shot");
    for (const card of cards) {
      if (card.dataset.sid === id) return card;
    }
    return null;
  }

  function flashCard(card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.remove("flash");
    void card.offsetWidth;   /* restart the one-shot CSS animation */
    card.classList.add("flash");
    setTimeout(() => card.classList.remove("flash"), 1600);
  }

  function focusShot(id) {
    let card = cardById(id);
    if (!card && stateFilter) {
      /* bug-hunt #51: the timeline/cockpit navigate by shot id regardless of
       * the grid's state filter — navigation wins, the filter clears. */
      stateFilter = "";
      saveUI({ filter: "" });
      renderShots(lastShots);
      card = cardById(id);
    }
    if (card) flashCard(card);
    else toast("镜头卡片未找到 (shot card not found): " + id, "err");
  }

  /* ------------------------------------------------------ shots grid --- */
  const STATE_CLASS = {
    fresh: "st-fresh", stale: "st-stale", missing: "st-missing",
    manual: "st-manual", needs_selection: "st-needs", broken: "st-broken",
  };
  const PREVIEW_HINT_EXT = { mkv: true, flac: true, m4v: true };
  const IMAGE_EXT = { png: true, jpg: true, jpeg: true, gif: true, webp: true };

  /* readonly gate for a mutating control: disabled + tooltip (a disabled
   * button never dispatches click, so listeners can attach unconditionally) */
  const roGate = (btn) => {
    if (!readonly) return false;
    btn.disabled = true;
    btn.title = RO_TIP;
    return true;
  };

  /* ↑/↓ reorder: build the FULL new order from the current state and POST
   * /api/index. One reorder in flight at a time — the server permutation-
   * checks against the current shots, and racing swaps could undo each
   * other; post() handles the disable + toast + refresh. */
  let reorderBusy = false;

  async function moveShot(i, delta, btn) {
    if (reorderBusy || readonly) return;
    const order = lastShots.map((s) => s.id);
    const j = i + delta;
    if (i < 0 || i >= order.length || j < 0 || j >= order.length) return;
    const moved = order[i];
    order[i] = order[j];
    order[j] = moved;
    reorderBusy = true;
    try {
      await post(btn, "/api/index", { order }, "已移动 (moved) " + moved);
    } finally {
      reorderBusy = false;
    }
  }

  let stateFilter = loadUI().filter || "";  /* '' = all; survives re-renders
    AND reloads (per-project UI memory, #49a) */

  /* most-blocking first — the #44 resolver's ladder, not the alphabet */
  const STATE_FILTER_ORDER = ["broken", "missing", "needs_selection", "stale", "manual", "fresh"];

  function filterChips(shots) {
    const bar = el("div", "fchips");
    const counts = {};
    shots.forEach((s) => { counts[s.state] = (counts[s.state] || 0) + 1; });
    const mk = (label, value, n, tip) => {
      const c = el("button",
        "chip fchip" + (stateFilter === value ? " on" : ""),
        label + (n !== undefined ? " " + n : ""));
      c.type = "button";
      c.setAttribute("aria-pressed", stateFilter === value ? "true" : "false");
      if (tip) c.title = tip;
      c.addEventListener("click", () => {
        stateFilter = stateFilter === value ? "" : value;
        saveUI({ filter: stateFilter });  /* survives reload, per project */
        renderShots(lastShots);  /* state unchanged: re-render directly */
      });
      bar.appendChild(c);
    };
    mk("全部 (all)", "", shots.length);
    STATE_FILTER_ORDER.filter((st) => counts[st])
      .concat(Object.keys(counts).filter((st) => STATE_FILTER_ORDER.indexOf(st) < 0).sort())
      .forEach((st) => mk(CK_STATE_ZH[st] || st, st, counts[st], st));
    return bar;
  }

  /* -------------------------------------- unread-first triage (Figma) --- */
  /* localStorage manju-reviewed-<project>: {ts, takes:{shotId:[takeNames]}}.
   * Take cards carry no created_at, so "new" is approximated as "name absent
   * from the take-name snapshot stored at the last 标记已阅". No snapshot yet
   * (never marked) -> nothing is flagged. Storage failures (blocked/corrupt)
   * silently disable the feature — the grid itself must never break. */
  let lastProjectName = "";   /* state.project.name — keys the snapshot */
  let reviewSnap = null;      /* parsed snapshot for the CURRENT render pass */
  let reviewChipEl = null;    /* 未阅 N chip in the shots bar */

  /* Keyed by the STABLE project identity when the guard meta is present —
   * two projects can share a display NAME and used to share (and clobber)
   * one snapshot. The old name key migrates once, then retires. */
  const reviewKey = () =>
    "manju-reviewed-" + (PROJECT || lastProjectName);

  function loadReviewSnapshot() {
    if (!lastProjectName && !PROJECT) return null;
    try {
      let raw = window.localStorage.getItem(reviewKey());
      if (!raw && PROJECT && lastProjectName) {
        const legacy = window.localStorage.getItem("manju-reviewed-" + lastProjectName);
        if (legacy) {  /* one-time migration off the colliding name key */
          window.localStorage.setItem(reviewKey(), legacy);
          window.localStorage.removeItem("manju-reviewed-" + lastProjectName);
          raw = legacy;
        }
      }
      if (!raw) return null;
      const snap = JSON.parse(raw);
      return (snap && typeof snap === "object" && snap.takes &&
        typeof snap.takes === "object") ? snap : null;
    } catch (err) { return null; }
  }

  function takeNameSnapshot() {
    const takes = {};
    lastShots.forEach((s) => {
      takes[s.id] = (Array.isArray(s.takes) ? s.takes : []).map((t) => String(t.name));
    });
    return takes;
  }

  function isNewTake(snap, shotId, takeName) {
    if (!snap) return false;
    const seen = snap.takes[shotId];
    if (!Array.isArray(seen)) return true;   /* whole shot is post-review */
    return seen.indexOf(String(takeName)) < 0;
  }

  function countNewTakes(snap) {
    if (!snap) return 0;
    let n = 0;
    lastShots.forEach((s) =>
      (Array.isArray(s.takes) ? s.takes : []).forEach((t) => {
        if (isNewTake(snap, s.id, t.name)) n += 1;
      }));
    return n;
  }

  function markReviewed() {
    if (!lastProjectName) {
      toast("项目尚未加载 (project not loaded yet)", "warn");
      return;
    }
    try {
      window.localStorage.setItem(reviewKey(), JSON.stringify({
        ts: new Date().toISOString(),
        takes: takeNameSnapshot(),
      }));
      toast("已标记已阅 (marked reviewed)", "ok");
    } catch (err) {
      toast("标记失败 (mark reviewed failed): " + errMsg(err), "err");
      return;
    }
    try { renderShots(lastShots); } catch (err) { /* chips are advisory */ }
    updateReviewChip();
    /* #50c: the cockpit's 新 take 未阅 row reads the SAME snapshot — repaint
     * it now, or the one-glance home contradicts the shots bar until the
     * next fingerprint change. */
    try { if (cockData) renderCockpit(); } catch (err) { /* advisory */ }
  }

  function updateReviewChip() {
    if (!reviewChipEl) return;
    let n = 0;
    try { n = countNewTakes(loadReviewSnapshot()); } catch (err) { n = 0; }
    reviewChipEl.textContent = "未阅 " + n + " (unread)";
    reviewChipEl.classList.toggle("hidden", n <= 0);
  }

  /* Debug counters for keyed patch (tests / soak read window.__manjuRenderStats). */
  const renderStats = {
    shots_created: 0, shots_reused: 0, shots_replaced: 0, shots_removed: 0,
    media_nodes_created: 0, media_nodes_reused: 0, media_activated: 0,
    render_duration_ms: 0,
  };
  try { window.__manjuRenderStats = renderStats; } catch (e) { /* non-browser */ }

  /* Media IO: activate src when near viewport; keep selected/review takes ready. */
  let mediaIO = null;
  try {
    if (typeof IntersectionObserver === "function") {
      mediaIO = new IntersectionObserver((entries) => {
        entries.forEach((en) => {
          const node = en.target;
          if (!node || !node.dataset) return;
          if (en.isIntersecting) {
            if (node.dataset.lazySrc && !node.src) {
              node.src = node.dataset.lazySrc;
              renderStats.media_activated++;
            }
            if (node.tagName === "VIDEO" && node.preload === "none"
                && node.dataset.wantPreload === "1") {
              node.preload = "metadata";
            }
          } else if (node.tagName === "VIDEO" && !node.dataset.keepAlive) {
            try {
              if (!node.paused) node.pause();
            } catch (err) { /* ignore */ }
          }
        });
      }, { root: null, rootMargin: "200px 0px", threshold: 0.01 });
    }
  } catch (e) { mediaIO = null; }

  function renderShots(shots) {
    const t0 = (typeof performance !== "undefined" && performance.now)
      ? performance.now() : Date.now();
    reviewSnap = loadReviewSnapshot();   /* one parse per grid render */
    const root = $("shots");
    /* #50c (review find #1): a DIRECT re-render (filter chip, cockpit count,
     * 标记已阅, batch bar) while an inline note editor is open destroys the
     * editor node WITHOUT editorClosed() — editorOpen would leak true and
     * silently freeze the whole poll loop + keyboard. The draft is forfeit
     * (the user asked for a repaint); the pause must never leak. */
    if (editorOpen && root.querySelector(".tnote-edit")) {
      editorOpen = false;
      /* bug-hunt #51: clearing the flag alone left the loop DEAD — nothing
       * re-arms it (schedule/refresh only chain off each other). */
      schedule();
    }
    if (!shots.length) {
      clear(root);
      const empty = el("div", "empty");
      empty.appendChild(el("p", null, "还没有分镜 (no shots yet)。"));
      empty.appendChild(el("p", "muted",
        "分镜是创作阶段：请在 shots/*.yaml 中撰写 — 引擎只做确定性构建，从不代你发明分镜。"));
      root.appendChild(empty);
      return;
    }
    if (Object.keys(shots.reduce((a, s) => (a[s.state] = 1, a), {})).length > 1) {
      /* filter chips: keep one host at top */
      let chips = root.querySelector(".shot-filters");
      if (!chips) {
        chips = filterChips(shots);
        chips.classList.add("shot-filters");
        root.insertBefore(chips, root.firstChild);
      }
    } else if (stateFilter) {
      stateFilter = "";  /* single-state grid: a stale filter must not hide it */
      saveUI({ filter: "" });  /* #50c: the reset must reach the memory too */
    }
    const visible = stateFilter ? shots.filter((s) => s.state === stateFilter) : shots;
    if (!visible.length) {
      stateFilter = "";
      saveUI({ filter: "" });  /* #50c: ditto — never restore a dead filter */
      renderShots(shots);  /* the filtered state vanished: reset, re-render */
      return;
    }

    /* Keyed patch by shot.id + ui_rev — never clear+rebuild the whole grid. */
    const scrollTop = root.scrollTop;
    const prevFocus = document.activeElement;
    const byId = new Map();
    Array.from(root.querySelectorAll("section.shot[data-sid]")).forEach((node) => {
      byId.set(node.dataset.sid, node);
    });
    const keep = new Set();
    const frag = document.createDocumentFragment();
    let orderHost = root.querySelector(".shot-list");
    if (!orderHost) {
      orderHost = el("div", "shot-list");
      root.appendChild(orderHost);
    }
    /* Move existing nodes into fragment for reordering without destroying. */
    while (orderHost.firstChild) frag.appendChild(orderHost.firstChild);

    visible.forEach((shot, idx) => {
      const total = shots.length;
      const existing = byId.get(shot.id);
      const rev = shot.ui_rev || "";
      if (existing && existing.dataset.uiRev === rev) {
        /* Reuse node; update keyboard focus class only. */
        existing.classList.toggle("kb-focus", shot.id === kbFocusId);
        /* Batch checkbox may have changed without ui_rev — sync cheaply. */
        const cb = existing.querySelector("input.shot-check");
        if (cb) cb.checked = batchSel.has(shot.id);
        orderHost.appendChild(existing);
        keep.add(shot.id);
        renderStats.shots_reused++;
        return;
      }
      if (existing) {
        /* Capture media playback before replace. */
        const playState = [];
        existing.querySelectorAll("video").forEach((v) => {
          playState.push({
            name: (v.closest(".take") || {}).dataset
              ? (v.closest(".take").dataset.tname || "") : "",
            t: v.currentTime, paused: v.paused, vol: v.volume,
            muted: v.muted, rate: v.playbackRate,
          });
        });
        const card = shotCard(shot, idx, total);
        card.dataset.uiRev = rev;
        orderHost.appendChild(card);
        /* Restore playback on matching take name if present. */
        playState.forEach((ps) => {
          const v = card.querySelector(
            ps.name ? ('.take[data-tname="' + ps.name + '"] video') : "video");
          if (!v) return;
          try {
            v.volume = ps.vol; v.muted = ps.muted; v.playbackRate = ps.rate || 1;
            if (ps.t > 0) v.currentTime = ps.t;
            if (!ps.paused && v.play) v.play().catch(() => {});
          } catch (err) { /* autoplay / seek may fail */ }
        });
        keep.add(shot.id);
        renderStats.shots_replaced++;
        return;
      }
      const card = shotCard(shot, idx, total);
      card.dataset.uiRev = rev;
      orderHost.appendChild(card);
      keep.add(shot.id);
      renderStats.shots_created++;
    });

    /* Remove shots no longer visible. */
    byId.forEach((node, sid) => {
      if (!keep.has(sid)) {
        if (mediaIO) {
          node.querySelectorAll("video, img").forEach((m) => {
            try { mediaIO.unobserve(m); } catch (e) { /* */ }
          });
        }
        if (node.parentNode) node.parentNode.removeChild(node);
        renderStats.shots_removed++;
      }
    });
    /* Drop leftover frag nodes (orphans). */
    while (frag.firstChild) {
      const n = frag.firstChild;
      frag.removeChild(n);
      if (n.dataset && n.dataset.sid && !keep.has(n.dataset.sid)) {
        renderStats.shots_removed++;
      }
    }
    try { root.scrollTop = scrollTop; } catch (e) { /* */ }
    if (prevFocus && document.contains(prevFocus) && prevFocus.focus) {
      try { prevFocus.focus({ preventScroll: true }); } catch (e) {
        try { prevFocus.focus(); } catch (e2) { /* */ }
      }
    }
    const t1 = (typeof performance !== "undefined" && performance.now)
      ? performance.now() : Date.now();
    renderStats.render_duration_ms = Math.round(t1 - t0);
  }

  function shotCard(shot, idx, total) {
    const card = el("section", "shot");
    card.dataset.sid = shot.id;   /* timeline clips scroll to this card */
    if (shot.id === kbFocusId) card.classList.add("kb-focus");  /* survives re-renders */
    const head = el("div", "shot-head");
    /* batch multi-select (goal item 3): the checkbox reflects the persistent
     * selection Set and survives poll re-renders. Read-only hides mutation, so
     * the box is disabled there (viewing stays fine). */
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "shot-check";
    cb.checked = batchSel.has(shot.id);
    cb.title = "多选做批量 (select for batch redo/voice)";
    if (readonly) cb.disabled = true;
    cb.addEventListener("change", () => toggleBatch(shot.id, cb.checked));
    head.appendChild(cb);
    head.appendChild(el("span", "sid", shot.id));
    /* F18 discipline (the board landed it in #42a; the workbench card kept
     * the raw enum): the GUI's own Chinese state word, enum on the title. */
    const stBadge = el("span",
      "badge " + (STATE_CLASS[shot.state] || "st-missing"),
      CK_STATE_ZH[shot.state] || shot.state);
    stBadge.title = shot.state;
    head.appendChild(stBadge);
    if (shot.voice) {
      const vc = el("span",
        "badge " + (STATE_CLASS[shot.voice.state] || "st-missing"),
        "配音 " + shot.voice.state);
      if (shot.voice.why) vc.title = shot.voice.why;
      head.appendChild(vc);
    }
    (shot.locked || []).forEach((f) => head.appendChild(el("span", "chip lock", "🔒 " + f)));
    /* A/B compare (Frame.io): only when >=2 video takes exist to pair. Viewing
     * is read-only, so it is NOT readonly-gated (the select L/R buttons are). */
    if (videoTakesOf(shot).length >= 2) {
      const cmp = el("button", "btn ghost mini cmp-btn", "对比 (Compare)");
      cmp.type = "button";
      cmp.title = "并排对比 take (compare two takes side by side)";
      cmp.addEventListener("click", () => {
        try { openCompare(shot); }
        catch (err) { toast("对比打开失败 (compare failed): " + errMsg(err), "err"); }
      });
      head.appendChild(cmp);
    }
    const mv = el("span", "mvbtns");
    const mkMove = (label, delta, edge, tip) => {
      const b = el("button", "btn ghost mini", label);
      b.type = "button";
      b.title = tip;
      if (!roGate(b) && edge) b.disabled = true;   /* first ↑ / last ↓ stay off */
      b.addEventListener("click", () => moveShot(idx, delta, b));
      mv.appendChild(b);
    };
    mkMove("↑", -1, idx === 0, "上移 (move up)");
    mkMove("↓", 1, idx === total - 1, "下移 (move down)");
    head.appendChild(mv);
    card.appendChild(head);

    if (shot.action) card.appendChild(el("div", "action", shot.action));
    if (shot.speaker || shot.dialogue) {
      const d = el("div", "dialogue");
      if (shot.speaker) d.appendChild(el("span", "speaker", shot.speaker + ": "));
      d.appendChild(document.createTextNode(shot.dialogue || ""));
      card.appendChild(d);
    }
    if (shot.note) card.appendChild(el("div", "note", shot.note));

    const takes = Array.isArray(shot.takes) ? shot.takes : [];
    if (takes.length) {
      const row = el("div", "takes");
      takes.forEach((t) => row.appendChild(takeCard(shot, t)));
      card.appendChild(row);
    } else {
      card.appendChild(el("div", "dialogue", "还没有 take (no takes yet)"));
    }

    const voiceTakes = Array.isArray(shot.voice_takes) ? shot.voice_takes : [];
    if (voiceTakes.length) {
      const vlist = el("div", "voice-takes");
      voiceTakes.forEach((v) => {
        const vrow = el("div", "voice-take");
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.preload = "none";
        audio.src = v.url;
        vrow.appendChild(audio);
        vrow.appendChild(el("span", "vname", v.name + (v.manual ? " (manual)" : "")));
        vlist.appendChild(vrow);
      });
      card.appendChild(vlist);
    }

    const acts = el("div", "btnrow");
    const edit = el("button", "btn ghost", "编辑 (Edit)");
    edit.type = "button";
    roGate(edit);
    edit.addEventListener("click", () => openEditorFor(shot.id, edit));
    acts.appendChild(edit);
    const redo = el("button", "btn ghost", "重做 (Redo)");
    redo.type = "button";
    roGate(redo);
    redo.addEventListener("click", () =>
      showPlanModal("redo", { shot: shot.id }, {
        title: "重做前计划 (plan before redo) · " + shot.id,
        onConfirm: () => post(redo, "/api/redo", { shot: shot.id, assume_yes: true },
          shot.id + " 重做已入队 (redo queued)"),
      }));
    acts.appendChild(redo);
    if (String(shot.dialogue || "").trim()) {
      const voice = el("button", "btn ghost", "配音 (Voice)");
      voice.type = "button";
      roGate(voice);
      voice.addEventListener("click", () =>
        showPlanModal("voice", { shot: shot.id }, {
          title: "配音前计划 (plan before voice) · " + shot.id,
          onConfirm: () => post(voice, "/api/voice", { shot: shot.id, assume_yes: true },
            shot.id + " 配音已入队 (voice queued)"),
        }));
      acts.appendChild(voice);
    }
    card.appendChild(acts);
    return card;
  }

  function takeCard(shot, t) {
    const box = el("div", "take" + (t.selected ? " selected" : ""));
    box.dataset.tname = t.name || "";
    const ext = String(t.ext || "").toLowerCase();
    if (t.url && IMAGE_EXT[ext]) {
      /* image takes render as a real <img> — v2 gave them a dead <video> */
      const img = document.createElement("img");
      img.loading = "lazy";
      img.decoding = "async";
      img.alt = t.name || "take";
      /* Selected: set src immediately; others use lazy dataset + observer. */
      if (t.selected) {
        img.src = t.url;
        renderStats.media_nodes_created++;
      } else {
        img.dataset.lazySrc = t.url;
        img.src = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
        renderStats.media_nodes_created++;
        if (mediaIO) mediaIO.observe(img);
      }
      box.appendChild(img);
    } else if (t.url) {
      const v = document.createElement("video");
      v.controls = true;
      v.width = 180;
      /* Selected / current review take: metadata; off-screen others: none. */
      if (t.selected || (shot.id === kbFocusId)) {
        v.preload = "metadata";
        v.src = t.url;
        v.dataset.keepAlive = "1";
        v.dataset.wantPreload = "1";
      } else {
        v.preload = "none";
        v.dataset.lazySrc = t.url;
        v.dataset.wantPreload = "0";
      }
      if (t.poster) v.poster = t.poster;   /* server thumb rides as poster */
      box.appendChild(v);
      renderStats.media_nodes_created++;
      if (mediaIO && !t.selected) mediaIO.observe(v);
    } else {
      box.appendChild(el("div", "nomedia", "no media"));
    }
    const name = el("div", "tname", t.name);
    if (t.selected) name.appendChild(el("span", "star", " ★"));
    if (isNewTake(reviewSnap, shot.id, t.name)) {
      /* unread-first triage: absent from the last 标记已阅 snapshot */
      name.appendChild(el("span", "chip tk-new", "新 (new)"));
    }
    box.appendChild(name);
    box.appendChild(el("div", "tmeta", (t.provider || "?") + " · " + fmtDur(t.duration_ms)));
    if (PREVIEW_HINT_EXT[ext]) {
      box.appendChild(el("div", "hint", "浏览器可能无法预览 ." + ext));
    }
    /* director note (Frame.io-style): truth text; a mm:ss prefix seeks */
    if (t.note) {
      const noteEl = el("div", "note tnote", "📝 " + t.note);
      noteEl.title = t.note;
      const m = /^(\d{1,2}):(\d{2})/.exec(t.note);
      const vid = box.querySelector("video");
      if (m && vid) {
        noteEl.classList.add("seekable");
        noteEl.title = t.note + " — 点击跳转 (click to seek)";
        noteEl.addEventListener("click", () => {
          vid.currentTime = parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
          if (vid.paused && vid.play) vid.play().catch(() => {});
        });
      }
      box.appendChild(noteEl);
    }
    const acts = el("div", "tacts");
    /* one-key verdicts (UX-STUDY #4): 好/弃 are just take_notes values —
     * one reviewable YAML line, clicking the active verdict clears it */
    const verdict = (label, value, tip) => {
      const b = el("button", "btn tiny" + (t.note === value ? " on" : ""), label);
      b.type = "button";
      b.title = tip;
      b.setAttribute("aria-label", tip);  /* the emoji face needs a name */
      roGate(b);
      b.addEventListener("click", () =>
        post(b, "/api/take-note",
          { shot: shot.id, take: t.name, text: t.note === value ? "" : value },
          (t.note === value ? "已清除评价 " : "已标记" + value + " ")
            + shot.id + "/" + t.name));
      acts.appendChild(b);
    };
    verdict("👍", "好", "标记 好 (approve)");
    verdict("👎", "弃", "标记 弃 (reject)");
    const noteBtn = el("button", "btn tiny", "📝");
    noteBtn.type = "button";
    noteBtn.title = "备注 (note) — 以 mm:ss 开头可点击跳转";
    noteBtn.setAttribute("aria-label", "备注 (note)");
    roGate(noteBtn);
    noteBtn.addEventListener("click", () => {
      /* inline editor, not a blocking prompt dialog — that froze the whole
       * page (playing take included) and Esc silently threw the text away.
       * It joins the ONE editorOpen pause (like the shot-editor dialog):
       * a fingerprint refresh mid-typing would rebuild #shots and eat the
       * draft, so the loop pauses while a note editor is up. */
      const openEd = box.querySelector(".tnote-edit");
      if (openEd) { openEd.querySelector("textarea").focus(); return; }
      document.querySelectorAll(".tnote-edit").forEach((other) => other.remove());
      editorOpen = true;
      const ed = el("div", "tnote-edit");
      const ta = document.createElement("textarea");
      ta.rows = 2;
      ta.value = t.note || "";
      ta.placeholder = "留空保存=删除；mm:ss 开头=可跳转；Ctrl+Enter 保存";
      const row = el("div", "btnrow");
      const ok = el("button", "btn mini", "保存 (save)");
      ok.type = "button";
      roGate(ok);
      const cancel = el("button", "btn ghost mini", "取消");
      cancel.type = "button";
      const save = () => {
        editorOpen = false;  /* BEFORE post — its refresh must not be swallowed */
        post(ok, "/api/take-note",
          { shot: shot.id, take: t.name, text: ta.value },
          ta.value.trim() ? "已备注 " + shot.id + "/" + t.name : "已删除备注"
        ).then((res) => {
          /* #50c (review find #2): a FAILED save runs no refresh, so nothing
           * re-arms the paused loop. Keep the draft + the pause invariant
           * (editor still in the DOM) — 重试/取消 both resume normally. */
          if (res === null) { editorOpen = true; return; }
          /* bug-hunt #51: an UNCHANGED note leaves the shots signature
           * identical — section() skips the repaint that would sweep the
           * editor. Remove it explicitly when it survived the refresh. */
          if (ed.isConnected) ed.remove();
        });
      };
      const closeNote = () => { ed.remove(); editorClosed(); };
      ok.addEventListener("click", save);
      cancel.addEventListener("click", closeNote);
      ta.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); }
        if (e.key === "Escape") { e.stopPropagation(); closeNote(); }
      });
      row.appendChild(ok);
      row.appendChild(cancel);
      ed.appendChild(ta);
      ed.appendChild(row);
      box.appendChild(ed);
      ta.focus();
    });
    acts.appendChild(noteBtn);
    /* the recipe travels with the output (Runway pattern): same provider,
     * same seed, one click — append-only new takes, selection untouched */
    if (t.provider && t.provider !== "manual_import") {
      const rb = el("button", "btn tiny", "⟳");
      rb.type = "button";
      rb.title = "用此参数重做 (redo with these settings"
        + (t.seed !== null && t.seed !== undefined ? ", seed " + t.seed : "") + ")";
      rb.setAttribute("aria-label", "用此参数重做 (redo with these settings)");
      roGate(rb);
      rb.addEventListener("click", () => {
        const planParams = { shot: shot.id, provider: t.provider };
        if (t.seed !== null && t.seed !== undefined) planParams.seed = t.seed;
        showPlanModal("redo", planParams, {
          title: "重做前计划 · " + shot.id + " (" + t.provider + ")",
          onConfirm: () => post(rb, "/api/redo",
            Object.assign({ assume_yes: true }, planParams),
            "已入队重做 " + shot.id + " (" + t.provider + ")"),
        });
      });
      acts.appendChild(rb);
    }
    if (!t.selected) {
      const btn = el("button", "btn small", "选用 (Select)");
      btn.type = "button";
      roGate(btn);
      btn.addEventListener("click", () =>
        post(btn, "/api/select", { shot: shot.id, take: t.name },
          "已选用 " + shot.id + " / " + t.name));
      acts.appendChild(btn);
    }
    box.appendChild(acts);
    return box;
  }

  /* ------------------------------------------- A/B take compare (R14) --- */
  /* Frame.io comparison viewer + Resolve Fast Review, in one overlay. Two
   * <video>s driven from one shot's takes. The overlay is a plain fixed div
   * mounted on <body> — OUTSIDE #shots — so a poll re-render (which only
   * rebuilds #shots) can never destroy it; it is read-only, so unlike the
   * editor it does NOT pause the poll loop. Closed via ✕ or Esc (the global
   * keydown handler routes Esc here while it is up). Every listener is
   * try/caught; all text enters via textContent (el()); no inline styles. */
  let cmpOverlay = null;   /* the mounted overlay element, or null when closed */

  /* takes playable as <video>: has media and is not an image (compare is a
   * side-by-side video scrub, so image takes are excluded from the pairing) */
  function videoTakesOf(shot) {
    return (Array.isArray(shot.takes) ? shot.takes : []).filter((t) =>
      !!(t && t.url) && !IMAGE_EXT[String(t.ext || "").toLowerCase()]);
  }

  function closeCompare() {
    if (!cmpOverlay) return;
    try {
      cmpOverlay.querySelectorAll("video").forEach((v) => {
        try { v.pause(); } catch (e) { /* nothing to stop */ }
      });
    } catch (e) { /* teardown is best-effort */ }
    if (cmpOverlay.parentNode) cmpOverlay.parentNode.removeChild(cmpOverlay);
    cmpOverlay = null;
  }

  function openCompare(shot) {
    closeCompare();   /* at most one overlay at a time */
    const takes = videoTakesOf(shot);
    if (takes.length < 2) return;   /* the button only shows for >=2 anyway */
    const FIT_TARGET = 5;           /* seconds — Resolve Fast Review target */

    let syncOn = false;   /* mirror transport A -> B */
    let fitOn = false;    /* duration-normalized playbackRate */
    const sides = [];     /* [leftState, rightState] */

    /* real duration (s): the <video> metadata is authoritative — fake takes
     * carry no probe, so duration_ms is null; duration_ms is only a fallback */
    const durSec = (st) => {
      const v = st && st.video;
      if (v && isFinite(v.duration) && v.duration > 0) return v.duration;
      const ms = st && st.take && st.take.duration_ms;
      return (typeof ms === "number" && isFinite(ms) && ms > 0) ? ms / 1000 : 0;
    };
    /* fit 5s: playbackRate = duration / 5, clamped to [0.5, 4] (Resolve Cut) */
    const fitRateOf = (st) => {
      const d = durSec(st);
      if (!fitOn || d <= 0) return 1;
      return Math.min(4, Math.max(0.5, d / FIT_TARGET));
    };

    const overlay = el("div", "cmp-overlay");
    const panel = el("div", "cmp-panel");
    overlay.appendChild(panel);

    const head = el("div", "cmp-head");
    head.appendChild(el("h3", null, "对比 (compare) · " + shot.id));
    const toggles = el("div", "cmp-toggles");

    const syncLabel = el("label", "cmp-toggle");
    const syncCk = document.createElement("input");
    syncCk.type = "checkbox";
    syncLabel.appendChild(syncCk);
    syncLabel.appendChild(document.createTextNode(" 同步播放 (sync play)"));
    toggles.appendChild(syncLabel);

    const fitLabel = el("label", "cmp-toggle");
    const fitCk = document.createElement("input");
    fitCk.type = "checkbox";
    fitLabel.appendChild(fitCk);
    fitLabel.appendChild(document.createTextNode(" 等长回放 (fit 5s)"));
    const fitRateLbl = el("span", "cmp-rate");   /* ×N.N applied-rate label */
    fitLabel.appendChild(fitRateLbl);
    toggles.appendChild(fitLabel);
    head.appendChild(toggles);

    const closeBtn = el("button", "cmp-close", "✕");
    closeBtn.type = "button";
    closeBtn.title = "关闭 (close) · Esc";
    closeBtn.addEventListener("click", () => {
      try { closeCompare(); } catch (e) { /* already gone */ }
    });
    head.appendChild(closeBtn);
    panel.appendChild(head);

    const updateFitLabel = () => {
      if (!fitOn || sides.length < 2) { fitRateLbl.textContent = ""; return; }
      const l = fitRateOf(sides[0]);
      const r = fitRateOf(sides[1]);
      fitRateLbl.textContent = (Math.abs(l - r) < 0.05)
        ? " ×" + l.toFixed(1)
        : " 左×" + l.toFixed(1) + " 右×" + r.toFixed(1);
    };
    const applyFit = () => {
      sides.forEach((st) => {
        try { st.video.playbackRate = fitRateOf(st); } catch (e) { /* pre-metadata */ }
      });
      updateFitLabel();
    };

    const grid = el("div", "cmp-videos");
    panel.appendChild(grid);

    const makeSide = (defaultIdx, selectLabel) => {
      const wrap = el("div", "cmp-side");
      const sel = document.createElement("select");
      takes.forEach((t, i) => {
        const o = document.createElement("option");
        o.value = String(i);
        o.textContent = t.name + (t.selected ? " ★" : "");
        sel.appendChild(o);
      });
      const video = document.createElement("video");
      video.controls = true;
      video.preload = "metadata";
      const cap = el("div", "cmp-cap");
      const selBtn = el("button", "btn small", selectLabel);
      selBtn.type = "button";
      roGate(selBtn);   /* /api/select mutates: disabled in readonly */

      const st = { sel, video, cap, selBtn, take: takes[defaultIdx] };
      sides.push(st);

      const renderCap = () => {
        clear(cap);
        const t = st.take;
        const ms = (isFinite(video.duration) && video.duration > 0)
          ? video.duration * 1000
          : (typeof t.duration_ms === "number" ? t.duration_ms : 0);
        cap.appendChild(el("b", null, t.name));
        cap.appendChild(document.createTextNode(
          " · " + (t.provider || "?") + " · " + fmtDur(ms)));
      };
      const setTake = (i) => {
        st.take = takes[i];
        video.src = st.take.url;   /* property assignment — URL is app-built */
        renderCap();               /* rate + label refresh on loadedmetadata */
      };

      sel.addEventListener("change", () => {
        try { setTake(Number(sel.value)); }
        catch (e) { toast("切换失败 (switch failed): " + errMsg(e), "err"); }
      });
      video.addEventListener("loadedmetadata", () => {
        try { renderCap(); video.playbackRate = fitRateOf(st); updateFitLabel(); }
        catch (e) { /* advisory */ }
      });
      selBtn.addEventListener("click", () => {
        try {
          const takeName = st.take.name;
          closeCompare();   /* toast + close + refresh — post() refreshes state */
          post(null, "/api/select", { shot: shot.id, take: takeName },
            "已选用 " + shot.id + " / " + takeName);
        } catch (e) { toast("选用失败 (select failed): " + errMsg(e), "err"); }
      });

      sel.value = String(defaultIdx);
      wrap.appendChild(sel);
      wrap.appendChild(video);
      wrap.appendChild(cap);
      wrap.appendChild(selBtn);
      grid.appendChild(wrap);
      setTake(defaultIdx);
      return st;
    };

    /* default pairing: selected take (or first) on the left, newest OTHER on
     * the right — takes() is oldest-first, so the newest is the last index */
    const selIdx = takes.findIndex((t) => t.selected);
    const leftIdx = selIdx >= 0 ? selIdx : 0;
    let rightIdx = leftIdx;
    for (let i = takes.length - 1; i >= 0; i--) {
      if (i !== leftIdx) { rightIdx = i; break; }
    }
    makeSide(leftIdx, "选用左 (select L)");
    makeSide(rightIdx, "选用右 (select R)");

    /* sync: A (left) is the master; play/pause/seek mirror to B. Position
     * mirroring (seek + >0.25s drift) is suspended while fit 5s is on — fit
     * deliberately runs the clips at different rates, so only transport
     * (play/pause) stays linked in that combined mode. */
    const a = sides[0].video, b = sides[1].video;
    const safePlay = (v) => { const p = v.play(); if (p && p.catch) p.catch(() => {}); };
    a.addEventListener("play", () => {
      try { if (syncOn && b.paused) safePlay(b); } catch (e) { /* transport */ }
    });
    a.addEventListener("pause", () => {
      try { if (syncOn && !b.paused) b.pause(); } catch (e) { /* transport */ }
    });
    a.addEventListener("seeked", () => {
      try { if (syncOn && !fitOn) b.currentTime = a.currentTime; } catch (e) { /* seek */ }
    });
    a.addEventListener("timeupdate", () => {
      try {
        if (syncOn && !fitOn && Math.abs(b.currentTime - a.currentTime) > 0.25) {
          b.currentTime = a.currentTime;   /* drift correction */
        }
      } catch (e) { /* drift */ }
    });

    syncCk.addEventListener("change", () => {
      try {
        syncOn = syncCk.checked;
        if (syncOn && !fitOn) {   /* align B to A the moment sync engages */
          b.currentTime = a.currentTime;
          if (!a.paused) safePlay(b);
        }
      } catch (e) { toast("同步失败 (sync failed): " + errMsg(e), "err"); }
    });
    fitCk.addEventListener("change", () => {
      try { fitOn = fitCk.checked; applyFit(); }
      catch (e) { toast("等长回放失败 (fit failed): " + errMsg(e), "err"); }
    });

    document.body.appendChild(overlay);
    cmpOverlay = overlay;
  }

  /* ------------------------------------------------------ shot editor --- */
  /* The editor edits HUMAN TRUTH: the exact YAML bytes travel both ways.
   * While the dialog is open the poll loop is fully paused (schedule() and
   * refresh() both early-return), so a mid-edit rerender can never eat the
   * user's text; the dialog itself lives outside #shots anyway. Saving is
   * check-gated server-side: a 409 means the file was already reverted. */
  const SHOT_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

  const shotUrl = (id) => "/api/shot/" + encodeURIComponent(id);

  async function newShotTemplate(id) {
    /* scene defaults to the first scene id seen in existing shots: the shots
     * cards do not carry `scene`, so peek at the first shot's raw YAML */
    let scene = "";
    if (lastShots.length) {
      try {
        const d = await api("GET", shotUrl(lastShots[0].id));
        const m = /^scene:[ \t]*(.+)$/m.exec((d && d.yaml) || "");
        if (m) scene = m[1].trim().replace(/^["']|["']$/g, "");
      } catch (err) { /* template scene is a convenience, never a blocker */ }
    }
    return "id: " + id + "\nscene: " + scene +
      "\ncharacters: []\ndialogue: {speaker: \"\", text: \"\"}\nduration: auto\n";
  }

  async function openEditorFor(id, btn) {
    if (btn) btn.disabled = true;
    try {
      const data = await api("GET", shotUrl(id));
      const exists = !!(data && data.exists);
      const yamlText = exists ? data.yaml : await newShotTemplate(id);
      /* round AA item 5 (#1): `rev` is the CAS token GET returned (""
       * for a not-yet-existing shot) — threaded into the shared dialog so
       * Save can prove the buffer still matches what was loaded. */
      showShotEditor(id, yamlText, (data && data.locked) || [], !exists,
        !data || data.in_index !== false, (data && data.rev) || "");
    } catch (err) {
      toast("无法加载镜头 (cannot load shot) " + id + ": " + errMsg(err), "err");
    } finally {
      if (btn) btn.disabled = false;
      updateGates();
    }
  }

  /* shot flavour of the shared dialog (bible/rules reuse the same flow) */
  function showShotEditor(id, yamlText, locked, isNew, inIndex, rev) {
    const chips = (Array.isArray(locked) ? locked : []).map((f) => "🔒 " + f);
    const hints = [];
    if (chips.length) {
      hints.push("锁定字段改动会被 check 拒绝并回滚;解锁请在终端运行 manju unlock");
    }
    if (!isNew && inIndex === false) {
      hints.push("该镜头不在 index 顺序中 (not listed in the shot index)");
    }
    showEditor({
      title: (isNew ? "新建镜头 (new shot) · " : "编辑镜头 (edit shot) · ") + id,
      yaml: yamlText,
      saveUrl: shotUrl(id),
      label: id,
      chips,
      hints,
      validate: { kind: "shot", id },   /* live keystroke validation for this shot */
      impact: { shot: id },   /* WP1: debounced chain-reaction preview on dialogue */
      rev,   /* round AA item 5 (#1): CAS token, echoed back on Save below */
    });
  }

  /* --------------------------------------------- conflict banner (409) --- */
  /* Naive line diff, deliberately LCS-free (multiset membership only):
   * "-" = line on disk (truth) missing from the buffer, "+" = line in the
   * buffer missing from truth. Order-insensitive, hence labelled 近似
   * (approximate) — good enough to see WHAT the check gate reverted. */
  function naiveLineDiff(bufferText, truthText) {
    const bufLines = String(bufferText).split("\n");
    const truthLines = String(truthText).split("\n");
    const countOf = (lines) => {
      const m = Object.create(null);
      lines.forEach((l) => { m[l] = (m[l] || 0) + 1; });
      return m;
    };
    const inBuf = countOf(bufLines);
    const inTruth = countOf(truthLines);
    const rows = [];
    truthLines.forEach((l) => {
      if (inBuf[l] > 0) inBuf[l] -= 1;
      else rows.push({ cls: "dl-del", text: "- " + l });
    });
    bufLines.forEach((l) => {
      if (inTruth[l] > 0) inTruth[l] -= 1;
      else rows.push({ cls: "dl-add", text: "+ " + l });
    });
    return rows;
  }

  /* Collapsible 对比真相 section for the editor's 409 path: the server
   * already reverted the file and sent the on-disk text back as `current`.
   * The user's buffer stays untouched unless they click 重填 (and confirm). */
  function conflictSection(ta, errBox, current) {
    const det = document.createElement("details");
    det.className = "ed-truth";
    det.open = true;   /* the conflict IS the news — start expanded */
    det.appendChild(el("summary", null, "对比真相 (diff vs truth)"));
    det.appendChild(el("p", "ed-truth-note",
      "近似逐行对比 (approximate line diff): - 行仅存在于磁盘真相, + 行仅存在于你的缓冲区 "
      + "(- lines only on disk, + lines only in your buffer)"));
    const box = el("div", "diff");
    const rows = naiveLineDiff(ta.value, current);
    if (!rows.length) {
      box.appendChild(el("div", "dl-ctx", "(无逐行差异 no line-level differences)"));
    } else {
      /* arbitrary user text: one div per line, textContent only */
      rows.forEach((r) => box.appendChild(el("div", r.cls, r.text)));
    }
    det.appendChild(box);
    const row = el("div", "btnrow");
    const reload = el("button", "btn ghost", "以真相为底重填 (reload truth)");
    reload.type = "button";
    reload.title = "用磁盘上的当前内容替换编辑区 (replace the textarea with the on-disk text)";
    reload.addEventListener("click", () => {
      try {
        if (ta.value !== current && !window.confirm(
          "缓冲区与磁盘真相不同,确定用真相覆盖你的编辑?"
          + " (your buffer differs — replace it with the on-disk truth?)")) {
          return;
        }
        ta.value = current;   /* property assignment — arbitrary text is safe */
        clear(errBox);        /* buffer now equals truth: the stale diff goes */
        ta.focus();
      } catch (err) {
        toast("重填失败 (reload failed): " + errMsg(err), "err");
      }
    });
    row.appendChild(reload);
    det.appendChild(row);
    return det;
  }

  /* Generic check-gated YAML dialog: GET-raw text in, POST {"yaml"} out;
   * 400/409 (bad YAML / check failed + reverted) render INSIDE the dialog
   * with the text intact. opts: {title, yaml, saveUrl, label, chips, hints,
   * rev}. `rev` (round AA item 5, #1) is the CAS token — when set, it rides
   * along as `expected_rev` in the save POST so the server can refuse a
   * save whose buffer went stale instead of silently overwriting; only the
   * shot editor sets it today (bible/rules/packaging omit it, unaffected). */
  function showEditor(opts) {
    const dlg = $("editor");
    clear(dlg);
    const head = el("div", "ed-head");
    head.appendChild(el("h3", null, opts.title));
    dlg.appendChild(head);
    if (Array.isArray(opts.chips) && opts.chips.length) {
      const chips = el("div", "chips");
      opts.chips.forEach((c) => chips.appendChild(el("span", "chip lock", c)));
      dlg.appendChild(chips);
    }
    (Array.isArray(opts.hints) ? opts.hints : []).forEach((h) =>
      dlg.appendChild(el("p", "muted ed-hint", h)));
    const ta = document.createElement("textarea");
    ta.className = "ed-ta";
    ta.rows = 22;
    ta.spellcheck = false;
    ta.value = opts.yaml;   /* property assignment — arbitrary text is safe */
    dlg.appendChild(ta);

    /* ---- live validation strip (VS Code settings.json debounce pattern) ----
     * On each typing pause we POST /api/validate {kind, yaml, id?} and render a
     * persistent status line here, between the textarea and the buttons.
     * ADVISORY ONLY: /api/validate is pure — it parses + model-validates the
     * buffer with NO write, NO full `check`, and NO lock / cross-reference
     * verification. So a ✓ here is NOT a promise that Save will succeed: the
     * gated Save below stays the authority and may still 400/409 on things this
     * cannot see (locked-field edits, missing bible refs, on-disk conflicts). */
    const vspec = (opts.validate && opts.validate.kind) ? opts.validate : null;
    const strip = el("div", "ed-valid");
    if (vspec) dlg.appendChild(strip);   /* only mounted when a kind is known */
    let valSeq = 0;          /* newest-wins: a superseded reply must never render */
    let valTimer = null;     /* 600ms input debounce handle */
    let valWaitTimer = null; /* 300ms "validating…" delay (anti-flicker) handle */
    let valOffline = false;  /* network-failure latch: no retry until next input */

    const renderValid = () => {
      clear(strip);
      strip.appendChild(el("span", "ed-valid-ok", "✓ 校验通过 (valid)"));
    };
    const renderInvalid = (errors) => {
      clear(strip);
      const lines = (Array.isArray(errors) ? errors : []).filter(Boolean);
      if (!lines.length) lines.push("校验未通过 (invalid)");
      /* server-provided strings: one node per line, textContent only (el()) */
      lines.forEach((m) => strip.appendChild(el("div", "ed-valid-err", "✗ " + m)));
    };
    const renderWaiting = () => {
      clear(strip);
      strip.appendChild(el("span", "ed-valid-wait", "校验中… (validating)"));
    };
    const renderOffline = () => {
      clear(strip);
      strip.appendChild(el("div", "ed-valid-na", "校验不可用 (validation unavailable)"));
    };

    const runValidate = async () => {
      /* strip.isConnected guards against a closed/replaced dialog (its own
       * closure lives on; a late reply must not touch a detached node) */
      if (!vspec || valOffline || !strip.isConnected) return;
      const seq = ++valSeq;
      const body = { kind: vspec.kind, yaml: ta.value };
      if (vspec.id !== undefined && vspec.id !== null) body.id = vspec.id;
      if (valWaitTimer) clearTimeout(valWaitTimer);
      valWaitTimer = setTimeout(() => {   /* only if this outlives 300ms */
        if (seq === valSeq && strip.isConnected) renderWaiting();
      }, 300);
      let r = null;
      try {
        r = await apiRaw("POST", "/api/validate", body);
      } catch (err) {
        r = null;   /* fetch threw (offline / abort): treat as unavailable */
      }
      if (valWaitTimer) { clearTimeout(valWaitTimer); valWaitTimer = null; }
      if (seq !== valSeq || !strip.isConnected) return;  /* superseded / closed */
      try {
        if (!r || !r.ok || !r.data || typeof r.data.ok !== "boolean") {
          /* transport error or a non-validate shape (offline, 403 readonly,
           * 400 unknown kind): one muted line, then stop until the next input */
          valOffline = true;
          renderOffline();
        } else if (r.data.ok) {
          renderValid();
        } else {
          renderInvalid(r.data.errors);
        }
      } catch (e) { /* a render fault must never break typing */ }
    };

    const scheduleValidate = () => {
      if (!vspec) return;
      try {
        valOffline = false;   /* fresh input lifts the no-retry latch */
        if (valTimer) clearTimeout(valTimer);
        valTimer = setTimeout(() => {
          if (strip.isConnected) runValidate();
        }, 600);
      } catch (e) { /* the keystroke path must never throw */ }
    };
    if (vspec) {
      ta.addEventListener("input", scheduleValidate);
      try { runValidate(); } catch (e) { /* prime once on open */ }
    }

    /* ---- WP1 impact strip (debounced /api/impact, 600 ms) ----
     * On dialogue (or any shot field) edit pause, show what the change would
     * stale/recompile/re-render and roughly cost. Pure read; advisory only. */
    const ispec = (opts.impact && opts.impact.shot) ? opts.impact : null;
    const impactStrip = el("div", "ed-impact");
    if (ispec) dlg.appendChild(impactStrip);
    let impactSeq = 0;
    let impactTimer = null;

    const parseDialogueText = (yamlText) => {
      /* Minimal extract of dialogue.text from the buffer — no full YAML parse
       * in the browser. Looks for `text:` under a dialogue: block, or a flat
       * `dialogue.text:` key. Best-effort; a miss just skips the field/value. */
      try {
        const lines = String(yamlText || "").split("\n");
        let inDialogue = false;
        for (let i = 0; i < lines.length; i++) {
          const line = lines[i];
          if (/^\s*dialogue\s*:/.test(line)) { inDialogue = true; continue; }
          if (inDialogue && /^\S/.test(line) && !/^\s*dialogue\s*:/.test(line)) {
            inDialogue = false;
          }
          const m = line.match(/^\s*text\s*:\s*(.*)$/);
          if (m && inDialogue) {
            let v = m[1].trim();
            if ((v.startsWith('"') && v.endsWith('"')) ||
                (v.startsWith("'") && v.endsWith("'"))) {
              v = v.slice(1, -1);
            }
            return v;
          }
        }
      } catch (e) { /* never break typing */ }
      return null;
    };

    const runImpact = async () => {
      if (!ispec || !impactStrip.isConnected) return;
      const seq = ++impactSeq;
      const text = parseDialogueText(ta.value);
      const body = { shot: ispec.shot };
      if (text !== null) {
        body.field = "dialogue.text";
        body.value = text;
      }
      let r = null;
      try {
        r = await apiRaw("POST", "/api/impact", body);
      } catch (err) {
        r = null;
      }
      if (seq !== impactSeq || !impactStrip.isConnected) return;
      try {
        clear(impactStrip);
        if (!r || !r.ok || !r.data) {
          impactStrip.appendChild(el("span", "ed-valid-na", "影响预览不可用"));
          return;
        }
        const summary = r.data.summary_zh || "此修改无明显连锁影响";
        const cls = (summary.indexOf("无明显") >= 0) ? "ed-impact-ok" : "";
        impactStrip.appendChild(el("span", cls, summary));
      } catch (e) { /* never break typing */ }
    };

    const scheduleImpact = () => {
      if (!ispec) return;
      try {
        if (impactTimer) clearTimeout(impactTimer);
        impactTimer = setTimeout(() => {
          if (impactStrip.isConnected) runImpact();
        }, 600);
      } catch (e) { /* keystroke path must never throw */ }
    };
    if (ispec) {
      ta.addEventListener("input", scheduleImpact);
      try { runImpact(); } catch (e) { /* prime once on open */ }
    }

    /* WP2 试听 ▶ — disposable TTS sample via /api/voice/preview (job-borne) */
    if (ispec && ispec.shot) {
      const previewRow = el("div", "btnrow");
      previewRow.style.marginTop = ".35rem";
      const prevBtn = el("button", "btn ghost", "试听 ▶ (Preview voice)");
      prevBtn.type = "button";
      const aud = document.createElement("audio");
      aud.controls = true;
      aud.style.display = "none";
      aud.style.maxWidth = "100%";
      aud.style.marginTop = ".3rem";
      prevBtn.addEventListener("click", async () => {
        prevBtn.disabled = true;
        try {
          const text = parseDialogueText(ta.value);
          const body = { shot: ispec.shot, assume_yes: true };
          if (text !== null) body.text = text;
          const r = await apiRaw("POST", "/api/voice/preview", body);
          if (!r || !r.ok) {
            toast((r && r.data && r.data.error) || "TTS 不可用", "err");
            return;
          }
          /* job-borne: wait a few polls for done */
          let job = r.data && r.data.job;
          const jid = job && job.id;
          if (jid) {
            for (let n = 0; n < 40; n++) {
              await new Promise((res) => setTimeout(res, 400));
              const st = await api("GET", "/api/jobs");
              const list = (st && st.jobs) || st || [];
              const found = (Array.isArray(list) ? list : []).find((j) => j.id === jid);
              if (found && found.state === "done") {
                job = found;
                break;
              }
              if (found && (found.state === "failed" || found.state === "error")) {
                const err = (found.result && found.result.error) || "TTS 不可用";
                toast(err, "err");
                return;
              }
            }
          }
          const res = (job && job.result) || {};
          if (res.ok === false || res.code === "tts_unavailable") {
            toast(res.error || "TTS 不可用", "err");
            return;
          }
          const path = res.preview;
          if (!path) {
            toast("TTS 不可用", "err");
            return;
          }
          aud.src = "/preview/" + encodeURI(path);
          aud.style.display = "block";
          try { await aud.play(); } catch (e) { /* autoplay may block */ }
          toast(res.cached ? "试听(缓存)" : "试听已合成", "ok");
        } catch (err) {
          toast("TTS 不可用", "err");
        } finally {
          prevBtn.disabled = false;
        }
      });
      previewRow.appendChild(prevBtn);
      dlg.appendChild(previewRow);
      dlg.appendChild(aud);
    }

    const errBox = el("div", "ed-errors");
    dlg.appendChild(errBox);
    const row = el("div", "btnrow ed-btnrow");
    const save = el("button", "btn", "保存 (Save)");
    save.type = "button";
    const cancel = el("button", "btn ghost", "取消 (Cancel)");
    cancel.type = "button";
    cancel.addEventListener("click", () => closeEditor());
    save.addEventListener("click", async () => {
      save.disabled = true;
      cancel.disabled = true;
      clear(errBox);
      try {
        const saveBody = { yaml: ta.value };
        if (opts.rev !== undefined && opts.rev !== null) saveBody.expected_rev = opts.rev;
        const r = await apiRaw("POST", opts.saveUrl, saveBody);
        if (r.ok) {
          const d = r.data || {};
          toast(d.created ? "已创建 " + opts.label + " (created)"
            : "已保存 " + opts.label + " (saved)", "ok");
          const warns = Array.isArray(d.warnings) ? d.warnings : [];
          if (warns.length) toast("警告 (warnings): " + warns.join(" / "), "warn");
          closeEditor();   /* its close handler forces the state refresh */
          return;
        }
        /* 400 (bad YAML) / 409 (check failed, already reverted server-side):
         * render errors INSIDE the dialog, keep it open, keep the text */
        const d = r.data || {};
        errBox.appendChild(el("p", "ed-err-title", d.error || ("HTTP " + r.status)));
        (Array.isArray(d.errors) ? d.errors : []).forEach((m) =>
          errBox.appendChild(el("p", "ed-err", "✗ " + m)));
        /* conflict banner (Figma visibility-over-locking): the 409 carries
         * `current` — the reverted-to disk text — so the dialog can show a
         * buffer-vs-truth diff and offer a one-click reload, no refetch */
        if (r.status === 409 && typeof d.current === "string") {
          try { errBox.appendChild(conflictSection(ta, errBox, d.current)); }
          catch (e) { /* the diff is advisory — the error list above stands */ }
        }
      } catch (err) {
        errBox.appendChild(el("p", "ed-err-title", "保存失败 (save failed): " + errMsg(err)));
      } finally {
        save.disabled = false;
        cancel.disabled = false;
      }
    });
    row.appendChild(save);
    row.appendChild(cancel);
    dlg.appendChild(row);

    editorOpen = true;   /* pause polling BEFORE the dialog becomes visible */
    pauseLive();         /* … and abort the in-flight watch: its late response
                            must never rerender under the user's cursor */
    if (typeof dlg.showModal === "function") {
      if (!dlg.open) dlg.showModal();   /* native: Esc -> cancel -> close event */
    } else {
      dlg.setAttribute("open", "");     /* overlay fallback; Esc handled globally */
    }
    ta.focus();
  }

  function closeEditor() {
    const dlg = $("editor");
    if (typeof dlg.close === "function" && dlg.open) dlg.close();  /* fires "close" */
    else { dlg.removeAttribute("open"); editorClosed(); }
  }

  function editorClosed() {
    if (!editorOpen) return;
    editorOpen = false;
    clear($("editor"));
    refresh();   /* resume the paused poll loop with fresh state */
  }

  /* -------------------------------------------------------- shots bar --- */
  function initShotsBar() {
    const root = $("shotsbar");
    clear(root);
    root.appendChild(el("h2", null, "分镜 (shots)"));
    const form = el("span", "ns-form hidden");
    const input = document.createElement("input");
    input.type = "text";
    input.className = "ns-input";
    input.placeholder = "S007";
    input.maxLength = 64;
    input.pattern = "[A-Za-z0-9_-]{1,64}";
    const go = el("button", "btn", "创建 (Create)");
    go.type = "button";
    const cancel = el("button", "btn ghost", "取消 (Cancel)");
    cancel.type = "button";
    const newBtn = el("button", "btn ghost", "新建镜头 (New shot)");
    newBtn.type = "button";
    newShotBtn = newBtn;   /* readonly-gated via updateGates() */
    const submit = async () => {
      const id = input.value.trim();
      if (!SHOT_ID_RE.test(id)) {
        toast("无效镜头 id (invalid shot id): 需匹配 [A-Za-z0-9_-]{1,64}", "err");
        return;
      }
      go.disabled = true;
      newBtn.disabled = true;
      try {
        const data = await api("GET", shotUrl(id));
        let yamlText;
        let isNew;
        if (data && data.exists) {
          yamlText = data.yaml;
          isNew = false;
          toast(id + " 已存在,进入编辑 (already exists — editing)", "warn");
        } else {
          yamlText = await newShotTemplate(id);
          isNew = true;
        }
        form.classList.add("hidden");
        input.value = "";
        showShotEditor(id, yamlText, (data && data.locked) || [], isNew,
          !data || data.in_index !== false);
      } catch (err) {
        toast("无法打开编辑器 (cannot open editor): " + errMsg(err), "err");
      } finally {
        go.disabled = false;
        newBtn.disabled = false;
      }
    };
    go.addEventListener("click", submit);
    cancel.addEventListener("click", () => form.classList.add("hidden"));
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
      else if (e.key === "Escape") form.classList.add("hidden");
    });
    newBtn.addEventListener("click", () => {
      form.classList.toggle("hidden");
      if (!form.classList.contains("hidden")) input.focus();
    });
    form.appendChild(input);
    form.appendChild(go);
    form.appendChild(cancel);
    root.appendChild(form);
    root.appendChild(newBtn);

    /* unread-first triage: 未阅 N count + 标记已阅 snapshot button. Local
     * preference only (localStorage) — deliberately NOT readonly-gated. */
    reviewChipEl = el("span", "chip unread hidden", "未阅 0 (unread)");
    reviewChipEl.id = "unreadchip";
    reviewChipEl.title = "上次标记已阅之后新增的 take (takes added since the last mark)";
    root.appendChild(reviewChipEl);
    const rvBtn = el("button", "btn ghost", "标记已阅 (Mark reviewed)");
    rvBtn.type = "button";
    rvBtn.id = "markreviewed";
    rvBtn.title = "记录当前 take 清单;此后新增的 take 会带 新 标记 "
      + "(snapshot current take names — later ones get a 新 chip)";
    rvBtn.addEventListener("click", () => {
      try { markReviewed(); } catch (err) { toast(errMsg(err), "err"); }
    });
    root.appendChild(rvBtn);

    /* batch selection helpers (goal item 3): always reachable, not just when
     * the floating bar is up. Mutating, so both are readonly-gated. */
    const staleBtn = el("button", "btn ghost", "全选过期 (Select all stale)");
    staleBtn.type = "button";
    staleBtn.title = "勾选所有 stale 分镜做批量重做 (select every stale shot)";
    roGate(staleBtn);
    staleBtn.addEventListener("click", () => selectAllStale());
    root.appendChild(staleBtn);
    const clrBtn = el("button", "btn ghost", "清除选择 (Clear)");
    clrBtn.type = "button";
    roGate(clrBtn);
    clrBtn.addEventListener("click", () => clearBatch());
    root.appendChild(clrBtn);
  }

  /* -------------------------------------------------- import dropzone --- */
  const UPLOAD_MAX = 4 * 1024 * 1024 * 1024;  /* 4 GiB — the server 413s above */
  const upQueue = [];
  let upActive = false;
  let upDone = 0;
  let upTotal = 0;
  let zoneLabel = null;

  const zoneIdleText = () => "拖入素材导入 media/imports (drop files to import)";

  function updateZone() {
    if (!zoneLabel) return;
    const zone = $("dropzone");
    if (upActive) {
      zone.classList.add("busy");
      zoneLabel.textContent =
        "上传中 (uploading) " + Math.min(upDone + 1, upTotal) + "/" + upTotal + "…";
    } else {
      zone.classList.remove("busy");
      zoneLabel.textContent = zoneIdleText();
    }
  }

  async function uploadOne(file) {
    /* RAW bytes as the body — not JSON, not multipart (server contract) */
    const upHeaders = { "X-Manju-Token": TOKEN, "Content-Type": "application/octet-stream" };
    if (PROJECT) upHeaders["X-Manju-Project"] = PROJECT;
    const res = await fetch("/api/upload?name=" + encodeURIComponent(file.name), {
      method: "POST",
      headers: upHeaders,
      body: file,
    });
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) throw new Error(data && data.error ? data.error : "HTTP " + res.status);
    return data;
  }

  async function drainUploads() {
    upActive = true;
    while (upQueue.length) {
      updateZone();
      const f = upQueue.shift();
      try {
        const data = await uploadOne(f);   /* strictly sequential */
        toast("已导入 (imported) " + ((data && data.imported) || f.name), "ok");
      } catch (err) {
        toast("导入失败 (import failed) " + f.name + ": " + errMsg(err), "err");
      }
      upDone += 1;
    }
    upActive = false;
    upDone = 0;
    upTotal = 0;
    updateZone();
    refresh();   /* imports land in events/state */
  }

  function enqueueUploads(fileList) {
    Array.from(fileList || []).forEach((f) => {
      if (f.size > UPLOAD_MAX) {
        toast("跳过 (skipped) " + f.name + ": 超过 4GiB 上限 (over the 4 GiB limit)", "err");
        return;
      }
      upQueue.push(f);
      upTotal += 1;
    });
    if (upQueue.length && !upActive) drainUploads();
    else updateZone();
  }

  function initDropzone() {
    const zone = $("dropzone");
    clear(zone);
    zoneLabel = el("span", null, zoneIdleText());
    zone.appendChild(zoneLabel);
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.className = "hidden";
    input.addEventListener("change", () => {
      enqueueUploads(input.files);
      input.value = "";
    });
    zone.appendChild(input);
    actAsButton(zone, "选择或拖入素材导入 (import files)");
    zone.addEventListener("click", () => input.click());
    zone.addEventListener("dragover", (e) => {
      e.preventDefault();
      zone.classList.add("drag");
    });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("drag");
      if (e.dataTransfer) enqueueUploads(e.dataTransfer.files);
    });
    /* a drop that misses the zone must not navigate away from the workbench */
    window.addEventListener("dragover", (e) => e.preventDefault());
    window.addEventListener("drop", (e) => e.preventDefault());
  }

  /* -------------------------------------------------------- QC panel --- */
  function renderQC(qc) {
    const root = $("qc");
    clear(root);
    if (!qc) { root.classList.add("hidden"); return; }
    root.classList.remove("hidden");
    root.appendChild(el("h2", null, "QC"));
    const chips = el("div", "chips");
    if (qc.ok === true) chips.appendChild(el("span", "badge st-fresh", "✓ 通过"));
    chips.appendChild(el("span", "badge qc-err", "错误 " + (qc.errors || 0)));
    chips.appendChild(el("span", "badge qc-warn", "警告 " + (qc.warnings || 0)));
    root.appendChild(chips);
    const items = Array.isArray(qc.items) ? qc.items : [];
    if (!items.length) return;
    const list = el("div", "list");
    items.forEach((it) => {
      const row = el("div", "qc-item");
      const lvl = String(it.level || "info").toLowerCase();
      row.appendChild(el("span", "badge lvl-" + lvl, it.level || "info"));
      const msg = el("span", "qmsg");
      if (it.shot) msg.appendChild(el("span", "qshot", it.shot + " · "));
      msg.appendChild(document.createTextNode(it.message || ""));
      row.appendChild(msg);
      if (it.suggestion) row.appendChild(el("div", "muted qsug", "建议: " + it.suggestion));
      list.appendChild(row);
    });
    root.appendChild(list);
  }

  /* ----------------------------------------------------- events feed --- */
  /* One row builder, shared by the live 15-row tail and the 更多 expansion. */
  function eventRow(ev) {
    const row = el("div", "event");
    row.appendChild(el("span", "etime", fmtClock(ev.ts)));
    const actor = String(ev.actor || "engine").toLowerCase();
    const cls = actor === "human" ? "ac-human" : (actor === "ai" ? "ac-ai" : "ac-engine");
    row.appendChild(el("span", "badge " + cls, ev.actor || "engine"));
    row.appendChild(el("span", "eaction", ev.action || ""));
    let detail = "";
    try { detail = JSON.stringify(ev.detail); } catch (e) { detail = String(ev.detail); }
    if (detail && detail !== "{}" && detail !== "null" && detail !== "undefined") {
      const d = el("span", "edetail",
        detail.length > 120 ? detail.slice(0, 120) + "…" : detail);
      d.title = detail;
      row.appendChild(d);
    }
    return row;
  }

  /* The state carries only the newest 15 (server _EVENTS_TAIL); 更多 pulls up
   * to 100 over the token-free GET /api/events?n=100 and replaces the list,
   * then hides itself. The live 15-row behavior holds on every poll until the
   * button is pressed; the expanded view then lives until the NEXT full
   * re-render of this section — section("events", …) only re-runs when
   * s.events changes (a new event lands), which resets back to 15 + button. */
  function renderEvents(events) {
    const root = $("events");
    clear(root);
    root.appendChild(el("h2", null, "事件 (events)"));
    const list = el("div", "list");
    const last = events.slice(-15).reverse();
    if (!last.length) list.appendChild(el("p", "muted", "暂无事件 (no events)"));
    last.forEach((ev) => list.appendChild(eventRow(ev)));
    root.appendChild(list);
    /* only offer 更多 when the tail is full — a shorter tail already IS the
     * whole log, so fetching 100 would return the very same rows */
    if (last.length >= 15) {
      const more = el("button", "btn ghost small ev-more", "更多 (more)");
      more.type = "button";
      more.title = "加载最近 100 条事件 (load the latest 100 events)";
      more.addEventListener("click", async () => {
        more.disabled = true;
        try {
          const data = await api("GET", "/api/events?n=100");
          const all = (data && Array.isArray(data.events)) ? data.events : [];
          const rows = all.slice(-100).reverse();   /* newest first, same format */
          clear(list);
          if (!rows.length) list.appendChild(el("p", "muted", "暂无事件 (no events)"));
          rows.forEach((ev) => list.appendChild(eventRow(ev)));
          more.classList.add("hidden");   /* expanded until the next re-render */
        } catch (err) {
          toast("加载更多失败 (load more failed): " + errMsg(err), "err");
          more.disabled = false;
        }
      });
      root.appendChild(more);
    }
  }

  /* -------------------------------------------------------- git panel --- */
  /* Lazily fetched: on first expand, after each commit, and after each done
   * job — never on the poll. A 500 (e.g. git endpoints not available)
   * renders one muted line inside this section only. */
  let gitOpen = !!loadUI().git;  /* per-project memory (#49a) */
  let gitLoaded = false;
  let gitStale = true;
  let gitBusy = false;
  let gitStatus = null;    /* /api/git/status payload.git */
  let gitLog = null;       /* /api/git/log payload.log */
  let gitErr = null;
  let gitLogErr = null;
  let gitBody = null;
  let gitChips = null;

  function initGitPanel() {
    const root = $("git");
    clear(root);
    const head = el("div", "git-head");
    const arrow = el("span", "git-arrow", gitOpen ? "▾" : "▸");
    head.appendChild(arrow);
    head.appendChild(el("h2", null, "版本 (git)"));
    gitChips = el("div", "chips");
    head.appendChild(gitChips);
    gitBody = el("div", gitOpen ? null : "hidden");
    actAsButton(head, "版本 (git) 面板");
    head.setAttribute("aria-expanded", gitOpen ? "true" : "false");
    head.addEventListener("click", () => {
      gitOpen = !gitOpen;
      arrow.textContent = gitOpen ? "▾" : "▸";
      head.setAttribute("aria-expanded", gitOpen ? "true" : "false");
      saveUI({ git: gitOpen });
      gitBody.classList.toggle("hidden", !gitOpen);
      if (gitOpen && (gitStale || !gitLoaded)) fetchGitPanel();
    });
    root.appendChild(head);
    root.appendChild(gitBody);
    if (gitOpen && !gitLoaded) fetchGitPanel();  /* restored-open lazy load */
  }

  async function fetchGitPanel() {
    if (gitBusy) return;
    gitBusy = true;
    gitErr = null;
    gitLogErr = null;
    try {
      const st = await api("GET", "/api/git/status");
      gitStatus = st ? st.git : null;
      gitLog = null;
      if (gitStatus) {
        try {
          const lg = await api("GET", "/api/git/log?n=10");
          gitLog = (lg && Array.isArray(lg.log)) ? lg.log : [];
        } catch (err) {
          gitLogErr = errMsg(err);
        }
      }
      gitLoaded = true;
      gitStale = false;
    } catch (err) {
      gitErr = errMsg(err);
    } finally {
      gitBusy = false;
      renderGit();
    }
  }

  function renderDiffInto(holder, text) {
    clear(holder);
    if (!text) {
      holder.appendChild(el("p", "muted", "无差异 (no diff)"));
      return;
    }
    /* server-derived text: SPLIT into lines, one div per line, textContent
     * only — never innerHTML (a diff carries arbitrary file content) */
    const box = el("div", "diff");
    String(text).split("\n").forEach((line) => {
      let cls = "dl-ctx";
      if (line.startsWith("+++") || line.startsWith("---")) cls = "dl-meta";
      else if (line.startsWith("+")) cls = "dl-add";
      else if (line.startsWith("-")) cls = "dl-del";
      else if (line.startsWith("@@")) cls = "dl-hunk";
      else if (line.startsWith("diff ") || line.startsWith("index ")) cls = "dl-meta";
      box.appendChild(el("div", cls, line === "" ? " " : line));
    });
    holder.appendChild(box);
  }

  /* truth-text files core/history.rollback_file will restore (media/renders/
   * exports are refused by the server; mirror that so the button only shows
   * where it can succeed) */
  const ROLLBACK_PREFIXES = ["story/", "shots/", "bible/", "timeline/", "captions/"];
  function isRollbackable(path) {
    if (!path) return false;
    if (path === "project.yaml") return true;
    return ROLLBACK_PREFIXES.some((p) => path.indexOf(p) === 0);
  }

  function gitFileRow(f) {
    const wrap = el("div");
    const row = el("div", "gs-row");
    row.appendChild(el("span", "gs-chip", f.status || "??"));
    row.appendChild(el("span", "gs-path", f.path || ""));
    if (!readonly && isRollbackable(f.path)) {
      /* rollback-file (core/history): discard working changes to ONE truth
       * file back to HEAD — guarded by a confirm since it drops local edits */
      const rb = el("button", "btn tiny ghost gs-rollback", "回滚 (rollback)");
      rb.type = "button";
      rb.title = "把该文件恢复到 HEAD (git checkout HEAD -- <file>) — 丢弃工作区改动";
      rb.addEventListener("click", async (ev) => {
        ev.stopPropagation();   /* the row's click toggles the diff */
        if (!window.confirm("回滚 " + f.path + " 到 HEAD?工作区改动将丢失 (discard local changes)")) return;
        rb.disabled = true;
        try {
          const r = await apiRaw("POST", "/api/git/rollback-file", { path: f.path });
          if (r.ok) {
            toast("已回滚 (rolled back) " + f.path, "ok");
            fetchGitPanel();
            refresh();
          } else {
            toast("回滚失败 (rollback failed): " + ((r.data && r.data.error) || ("HTTP " + r.status)), "err");
            rb.disabled = false;
          }
        } catch (err) {
          toast("回滚失败 (rollback failed): " + errMsg(err), "err");
          rb.disabled = false;
        }
      });
      row.appendChild(rb);
    }
    const holder = el("div", "hidden");
    let open = false;
    let busy = false;
    row.addEventListener("click", async () => {
      if (busy) return;
      if (open) {   /* second click collapses */
        open = false;
        holder.classList.add("hidden");
        clear(holder);
        return;
      }
      busy = true;
      try {
        const d = await api("GET", "/api/git/diff?path=" + encodeURIComponent(f.path || ""));
        renderDiffInto(holder, d ? d.diff : null);
        holder.classList.remove("hidden");
        open = true;
      } catch (err) {
        toast("差异不可用 (diff unavailable): " + errMsg(err), "err");
      } finally {
        busy = false;
      }
    });
    wrap.appendChild(row);
    wrap.appendChild(holder);
    return wrap;
  }

  function renderGit() {
    if (!gitBody || !gitChips) return;
    clear(gitBody);
    clear(gitChips);
    if (gitErr) {
      gitBody.appendChild(el("p", "muted", "git 状态不可用 (git unavailable): " + gitErr));
      return;
    }
    if (!gitLoaded) {
      gitBody.appendChild(el("p", "muted", "加载中 (loading)…"));
      return;
    }
    const g = gitStatus;
    if (!g) {
      gitBody.appendChild(el("p", "muted", "非 git 仓库 (not a repo)"));
      return;
    }
    /* header chips stay visible even when the body is collapsed again */
    gitChips.appendChild(el("span", "chip", "分支 (branch) " + (g.branch || "?")));
    gitChips.appendChild(el("span", "badge " + (g.dirty ? "st-stale" : "st-fresh"),
      g.dirty ? "有改动 (dirty)" : "干净 (clean)"));
    if (g.ahead > 0) gitChips.appendChild(el("span", "chip", "领先 (ahead) ↑" + g.ahead));
    if (g.behind > 0) gitChips.appendChild(el("span", "chip", "落后 (behind) ↓" + g.behind));

    const files = Array.isArray(g.files) ? g.files : [];
    if (files.length) {
      const list = el("div");
      files.forEach((f) => list.appendChild(gitFileRow(f)));
      if (g.truncated) list.appendChild(el("p", "muted", "(文件列表已截断 truncated)"));
      gitBody.appendChild(list);
      const btnrow = el("div", "btnrow");
      const full = el("button", "btn ghost", "全部差异 (full diff)");
      full.type = "button";
      const holder = el("div", "hidden");
      let fullOpen = false;
      full.addEventListener("click", async () => {
        if (fullOpen) {
          fullOpen = false;
          holder.classList.add("hidden");
          clear(holder);
          return;
        }
        full.disabled = true;
        try {
          const d = await api("GET", "/api/git/diff");
          renderDiffInto(holder, d ? d.diff : null);
          holder.classList.remove("hidden");
          fullOpen = true;
        } catch (err) {
          toast("差异不可用 (diff unavailable): " + errMsg(err), "err");
        } finally {
          full.disabled = false;
        }
      });
      btnrow.appendChild(full);
      gitBody.appendChild(btnrow);
      gitBody.appendChild(holder);
    } else {
      gitBody.appendChild(el("p", "muted", "工作区干净 (working tree clean)"));
    }

    /* commit box: disabled when the message is empty or the repo is clean;
     * absent entirely in readonly mode (the diff/log views above stay live) */
    if (!readonly) {
      const crow = el("div", "commit-row");
      const msg = document.createElement("input");
      msg.type = "text";
      msg.className = "commit-msg";
      msg.placeholder = "提交信息 (commit message)";
      const cbtn = el("button", "btn", "提交 (Commit)");
      cbtn.type = "button";
      const syncBtn = () => { cbtn.disabled = !msg.value.trim() || !g.dirty; };
      const doCommit = async () => {
        if (cbtn.disabled) return;
        cbtn.disabled = true;
        try {
          const r = await apiRaw("POST", "/api/git/commit", { message: msg.value.trim() });
          if (r.ok) {
            toast("已提交 (committed) " + String((r.data && r.data.hash) || "").slice(0, 7), "ok");
            fetchGitPanel();   /* re-renders the panel (status + log) */
            return;
          }
          toast("提交失败 (commit failed): " +
            ((r.data && r.data.error) || ("HTTP " + r.status)), "err");
        } catch (err) {
          toast("提交失败 (commit failed): " + errMsg(err), "err");
        }
        syncBtn();
      };
      syncBtn();
      msg.addEventListener("input", syncBtn);
      msg.addEventListener("keydown", (e) => { if (e.key === "Enter") doCommit(); });
      cbtn.addEventListener("click", doCommit);
      crow.appendChild(msg);
      crow.appendChild(cbtn);
      gitBody.appendChild(crow);

      /* snapshot (core/history.snapshot): a labeled checkpoint of the truth
       * text — the cheap point a rollback can return to. Always available
       * (unlike commit, it no-ops cleanly when the tree is already clean). */
      const srow = el("div", "snap-row");
      const slabel = document.createElement("input");
      slabel.type = "text";
      slabel.className = "snap-input";
      slabel.placeholder = "快照标签 (snapshot label, optional)";
      const sbtn = el("button", "btn ghost", "快照 (Snapshot)");
      sbtn.type = "button";
      sbtn.title = "为当前真相文本打一个带标签的检查点 (labeled git checkpoint)";
      const doSnap = async () => {
        sbtn.disabled = true;
        try {
          const r = await apiRaw("POST", "/api/git/snapshot", { label: slabel.value.trim() });
          if (r.ok) {
            const d = r.data || {};
            toast(d.clean ? "已是最新,无需快照 (already checkpointed)"
              : "已快照 (snapshot) " + String(d.sha || "").slice(0, 7), "ok");
            slabel.value = "";
            fetchGitPanel();
          } else {
            toast("快照失败 (snapshot failed): " + ((r.data && r.data.error) || ("HTTP " + r.status)), "err");
          }
        } catch (err) {
          toast("快照失败 (snapshot failed): " + errMsg(err), "err");
        } finally {
          sbtn.disabled = false;
        }
      };
      slabel.addEventListener("keydown", (e) => { if (e.key === "Enter") doSnap(); });
      sbtn.addEventListener("click", doSnap);
      srow.appendChild(slabel);
      srow.appendChild(sbtn);
      gitBody.appendChild(srow);
    }

    gitBody.appendChild(el("h3", null, "最近提交 (recent commits)"));
    if (gitLogErr) {
      gitBody.appendChild(el("p", "muted", "提交记录不可用 (log unavailable): " + gitLogErr));
    } else if (!gitLog || !gitLog.length) {
      gitBody.appendChild(el("p", "muted", "暂无提交 (no commits)"));
    } else {
      gitLog.forEach((c) => {
        const row = el("div", "git-log-row");
        row.appendChild(el("span", "git-hash", String(c.hash || "").slice(0, 7)));
        row.appendChild(el("span", null, c.subject || ""));
        row.appendChild(el("span", "muted", c.author || ""));
        if (c.ts) row.title = String(c.ts);
        gitBody.appendChild(row);
      });
    }
  }

  /* --------------------------------------------------- proposals panel --- */
  /* proposals/ is the AI→human channel (§5) — a collapsible section like the
   * git panel. Lazily fetched: on first expand, after each done job, and on
   * a state refresh AT MOST once per fingerprint change (never on the raw
   * poll cadence). The count badge in the section head stays live even when
   * the body is collapsed. */
  let propOpen = !!loadUI().prop;  /* per-project memory (#49a) */
  let propBusy = false;
  let propLoaded = false;
  let propStale = false;    /* set by a done job; cleared by the next fetch */
  let propFp = null;        /* fingerprint the current list was fetched at */
  let propItems = [];
  let propErr = null;
  let propExpanded = {};    /* name -> true: row expansion survives renders */
  let propBody = null;
  let propChips = null;

  function initProposalsPanel() {
    const root = $("proposals");
    clear(root);
    const head = el("div", "git-head");
    const arrow = el("span", "git-arrow", propOpen ? "▾" : "▸");
    head.appendChild(arrow);
    head.appendChild(el("h2", null, "提案 (proposals)"));
    propChips = el("div", "chips");
    head.appendChild(propChips);
    propBody = el("div", propOpen ? null : "hidden");
    actAsButton(head, "提案 (proposals) 面板");
    head.setAttribute("aria-expanded", propOpen ? "true" : "false");
    head.addEventListener("click", () => {
      propOpen = !propOpen;
      arrow.textContent = propOpen ? "▾" : "▸";
      head.setAttribute("aria-expanded", propOpen ? "true" : "false");
      saveUI({ prop: propOpen });
      propBody.classList.toggle("hidden", !propOpen);
      if (propOpen && (!propLoaded || propStale)) fetchProposals();
    });
    root.appendChild(head);
    root.appendChild(propBody);
    if (propOpen && !propLoaded) fetchProposals();  /* restored-open lazy load */
  }

  function maybeProposals(s) {
    if (typeof s.fp === "string" && s.fp && (s.fp !== propFp || propStale)) {
      fetchProposals(s.fp);
    }
  }

  async function fetchProposals(fp) {
    if (propBusy) return;
    propBusy = true;
    if (fp !== undefined) propFp = fp;  /* recorded up front: an error must
                                           not hammer /api/proposals */
    propStale = false;
    try {
      const d = await api("GET", "/api/proposals");
      propItems = (d && Array.isArray(d.proposals)) ? d.proposals : [];
      propErr = null;
      propLoaded = true;
    } catch (err) {
      propErr = errMsg(err);   /* section-local degrade, loop unharmed */
    } finally {
      propBusy = false;
      renderProposals();
    }
  }

  const fmtDate = (epochSec) => {   /* proposal mtime -> local Y-m-d HH:MM */
    if (typeof epochSec !== "number" || !isFinite(epochSec) || epochSec <= 0) return "?";
    const d = new Date(epochSec * 1000);
    if (isNaN(d.getTime())) return "?";
    const p = (x) => String(x).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
      " " + p(d.getHours()) + ":" + p(d.getMinutes());
  };

  function renderProposals() {
    if (!propBody || !propChips) return;
    clear(propBody);
    clear(propChips);
    if (propItems.length) {
      propChips.appendChild(el("span", "badge st-stale", String(propItems.length)));
    }
    if (propErr) {
      propBody.appendChild(el("p", "muted", "提案不可用 (proposals unavailable): " + propErr));
      return;
    }
    if (!propLoaded) {
      propBody.appendChild(el("p", "muted", "加载中 (loading)…"));
      return;
    }
    if (!propItems.length) {
      propBody.appendChild(el("p", "muted", "暂无提案 (no proposals)"));
      return;
    }
    propItems.forEach((p) => {   /* newest first, straight from the server */
      const wrap = el("div");
      const row = el("div", "prop-row");
      const arrow = el("span", "git-arrow", propExpanded[p.name] ? "▾" : "▸");
      row.appendChild(arrow);
      row.appendChild(el("span", "prop-name", p.name || "?"));
      row.appendChild(el("span", "prop-date", fmtDate(p.mtime)));
      if (p.truncated) row.appendChild(el("span", "chip", "已截断 (truncated)"));
      const pre = el("pre", "prop-pre" + (propExpanded[p.name] ? "" : " hidden"));
      pre.textContent = p.text || "";   /* arbitrary markdown: text only */
      row.addEventListener("click", () => {
        const open = !propExpanded[p.name];
        propExpanded[p.name] = open;
        arrow.textContent = open ? "▾" : "▸";
        pre.classList.toggle("hidden", !open);
      });
      wrap.appendChild(row);
      wrap.appendChild(pre);
      propBody.appendChild(wrap);
    });
  }

  /* --------------------------------------------- review keyboard mode --- */
  /* "?" toggles the hint bar; j/k walk the shot cards, e opens the editor,
   * 1-9 select take N, space toggles the selected take's <video>. All of it
   * is inert while an input/select/textarea or the editor dialog has focus. */
  let kbFocusId = null;
  let kbHintOn = false;

  function kbHint() {
    kbHintOn = !kbHintOn;
    $("kbdhint").classList.toggle("hidden", !kbHintOn);
  }

  function kbSetFocus(id) {
    kbFocusId = id;
    document.querySelectorAll("#shots .shot").forEach((c) =>
      c.classList.toggle("kb-focus", c.dataset.sid === kbFocusId));
    const card = kbFocusId ? cardById(kbFocusId) : null;
    if (card) flashCard(card);   /* scroll + flash, like timeline clicks */
  }

  function kbMove(delta) {
    /* bug-hunt #51: walk only the VISIBLE grid — with a state filter on,
     * focus used to land on cards that are not rendered at all. */
    const pool = stateFilter
      ? lastShots.filter((s) => s.state === stateFilter) : lastShots;
    const ids = pool.map((s) => s.id);
    if (!ids.length) return;
    const i = ids.indexOf(kbFocusId);
    let next;
    if (i < 0) next = delta > 0 ? 0 : ids.length - 1;   /* enter the grid */
    else next = Math.min(ids.length - 1, Math.max(0, i + delta));
    kbSetFocus(ids[next]);
  }

  function kbSelectTake(n) {
    if (readonly) return;
    const shot = lastShots.find((s) => s.id === kbFocusId);
    if (!shot) return;
    const take = (Array.isArray(shot.takes) ? shot.takes : [])[n - 1];
    if (!take || take.selected) return;   /* only existing, unselected takes */
    post(null, "/api/select", { shot: shot.id, take: take.name },
      "已选用 " + shot.id + " / " + take.name);
  }

  function kbPlayPause() {
    const card = kbFocusId ? cardById(kbFocusId) : null;
    if (!card) return false;   /* no focused card: space keeps scrolling */
    const v = card.querySelector(".take.selected video");
    if (v) {
      if (v.paused) {
        const p = v.play();
        if (p && typeof p.catch === "function") p.catch(() => {});
      } else {
        v.pause();
      }
    }
    return true;   /* a focused card claims space even without a video */
  }

  /* =====================================================================
   * S8a — GUI CORE INTERACTIONS
   * ===================================================================== */

  /* ---------------------------------------- pre-generation plan modal --- */
  /* Invariant §4.4 + goal item 5: before ANY priced or generative job we show
   * the plan (shot / why / provider / est cost, totals, cache savings) and take
   * an explicit confirm. Wired ON TOP of the existing dry-run + spend-confirm:
   * /api/plan runs the same estimators read-only, and confirm submits the real
   * action with assume_yes. Zero-cost plans still show — with a 免费/本地 badge
   * (the goal asks for explanation BEFORE generation, not only before spend).
   * The modal is its own <dialog>, separate from #shots, so the poll loop keeps
   * running underneath without disturbing it. */
  let planOpen = false;

  function closePlanModal() {
    const dlg = $("planmodal");
    try { if (typeof dlg.close === "function" && dlg.open) dlg.close(); }
    catch (e) { dlg.removeAttribute("open"); }
    if (dlg.hasAttribute("open")) dlg.removeAttribute("open");
    planOpen = false;
    clear(dlg);
  }

  async function showPlanModal(action, params, opts) {
    opts = opts || {};
    const dlg = $("planmodal");
    clear(dlg);
    planOpen = true;
    const head = el("div", "pm-head");
    head.appendChild(el("h3", null, opts.title || "生成前计划 (plan before generation)"));
    dlg.appendChild(head);
    const box = el("div", "pm-body");
    box.appendChild(el("p", "muted", "计算计划中… (computing plan)"));
    dlg.appendChild(box);
    if (typeof dlg.showModal === "function") { if (!dlg.open) dlg.showModal(); }
    else dlg.setAttribute("open", "");
    let data = null;
    try {
      data = await api("POST", "/api/plan", Object.assign({ action: action }, params));
    } catch (err) {
      data = { _error: errMsg(err) };
    }
    if (!planOpen) return;   /* cancelled while the plan was in flight */
    renderPlanModalBody(box, data, opts);
  }

  function renderPlanModalBody(box, data, opts) {
    clear(box);
    const failed = !!(data && data._error);
    const rows = (data && Array.isArray(data.rows)) ? data.rows : [];
    const skipped = (data && Array.isArray(data.skipped)) ? data.skipped : [];
    const nothing = !failed && rows.length === 0;
    if (failed) {
      box.appendChild(el("p", "pm-err", "无法计算计划 (plan unavailable): " + data._error));
    } else {
      const zero = !!data.zero_cost;
      const cur = data.currency || "";
      box.appendChild(el("div", "pm-badge " + (zero ? "free" : "spendy"),
        zero ? "免费 / 本地 (free / local)" : "预估花费 (estimated spend)"));
      if (nothing) {
        box.appendChild(el("p", "muted", "无事可做 (nothing to generate)。"));
      } else {
        const wrap = el("div", "tablewrap");
        const table = document.createElement("table");
        const thead = document.createElement("thead");
        const hr = document.createElement("tr");
        ["shot", "why", "provider"].forEach((h) => hr.appendChild(el("th", null, h)));
        hr.appendChild(el("th", "num", "≈cost"));
        thead.appendChild(hr);
        table.appendChild(thead);
        const tbody = document.createElement("tbody");
        rows.forEach((it) => {
          const tr = document.createElement("tr");
          if (it.kind === "voice") tr.className = "voice-row";
          const shotCell = el("td", null, it.shot || "");
          if (it.kind === "voice") shotCell.appendChild(el("span", "badge st-manual vtag", "配音"));
          tr.appendChild(shotCell);
          tr.appendChild(el("td", null, it.reason || ""));
          tr.appendChild(el("td", null, it.provider || ""));
          tr.appendChild(el("td", "num", "≈" + fmtMoney(it.estimated_cost || 0)));
          tbody.appendChild(tr);
        });
        table.appendChild(tbody);
        wrap.appendChild(table);
        box.appendChild(wrap);
        const tot = el("div", "pm-total");
        tot.appendChild(el("span", null, "合计 (total) " + rows.length + " 项 · ≈"
          + fmtMoney(data.estimated_cost) + (cur ? " " + cur : "")));
        if (Number(data.saved_cost) > 0) {
          tot.appendChild(el("span", "pm-saved",
            "缓存节省 (cache saved) ≈" + fmtMoney(data.saved_cost)));
        }
        if (data.routing) {
          const rc = el("span", "chip", "routing.yaml");
          rc.title = "provider 由 routing.yaml 解析 (resolved via routing)";
          tot.appendChild(rc);
        }
        box.appendChild(tot);
      }
    }
    if (skipped.length) {
      box.appendChild(el("h4", null, "将跳过 (will skip) · " + skipped.length));
      const sl = el("div", "pm-skip");
      skipped.forEach((s) => {
        const r = el("div", "pm-skiprow");
        r.appendChild(el("span", "badge st-manual", s.shot || "?"));
        r.appendChild(el("span", "muted", s.reason || ""));
        sl.appendChild(r);
      });
      box.appendChild(sl);
    }
    const row = el("div", "btnrow");
    if (!nothing) {
      const zero = !failed && !!data.zero_cost;
      const ok = el("button", "btn " + (zero ? "primary" : "confirm"),
        failed ? "仍要继续 (proceed anyway)" : "确认生成 (Confirm)");
      ok.type = "button";
      ok.addEventListener("click", () => {
        closePlanModal();
        try { opts.onConfirm(data); } catch (e) { toast(errMsg(e), "err"); }
      });
      row.appendChild(ok);
    }
    const no = el("button", "btn ghost", "取消 (Cancel)");
    no.type = "button";
    no.addEventListener("click", () => closePlanModal());
    row.appendChild(no);
    box.appendChild(row);
  }

  /* --------------------------------------------- first-run onboarding --- */
  /* Goal item 2: a dismissable checklist with LIVE done-detection from project
   * state (never a stored "done" flag). Auto-shows once for an empty-ish,
   * undismissed project; dismissal persists per-user (~/.manju/gui_state.json,
   * server-side). Re-openable from the header 帮助 chip at any time. */
  let obShownFor = null;   /* project name we already auto-evaluated this session */
  let obForce = false;     /* header 帮助: show even if dismissed/non-empty */

  function maybeOnboarding(s) {
    const name = (s.project && s.project.name) ? String(s.project.name) : "";
    if (obShownFor === name) return;   /* one auto-check per project per load */
    obShownFor = name;
    fetchOnboarding(false);
  }

  function openOnboarding() {
    obForce = true;
    fetchOnboarding(true);
  }

  async function fetchOnboarding(force) {
    let data = null;
    try {
      data = await api("GET", "/api/onboarding");
    } catch (err) {
      if (force) toast("引导不可用 (onboarding unavailable): " + errMsg(err), "err");
      return;
    }
    const show = force || obForce || (data && data.should_show);
    if (show) renderOnboarding(data);
    else $("onboarding").classList.add("hidden");
  }

  function renderOnboarding(data) {
    const root = $("onboarding");
    clear(root);
    root.classList.remove("hidden");
    const head = el("div", "ob-head");
    head.appendChild(el("h2", null, "新手引导 (getting started)"));
    head.appendChild(el("span", "ob-prog",
      (data.done_count || 0) + " / " + (data.total || 0) + " 完成"));
    const x = el("button", "btn ghost mini", "知道了 · 收起 (Dismiss)");
    x.type = "button";
    x.title = "收起清单;此项目不再自动弹出 (dismiss — won't auto-show for this project)";
    x.addEventListener("click", () => dismissOnboarding());
    head.appendChild(x);
    root.appendChild(head);
    root.appendChild(el("p", "ob-intro",
      "从创意到成片的六步。每步的完成状态实时来自项目本身 (live from project state)。"));
    (Array.isArray(data.steps) ? data.steps : []).forEach((st, i) => {
      const step = el("div", "ob-step " + (st.done ? "done" : "todo"));
      step.appendChild(el("span", "ob-mark", st.done ? "✓" : (i + 1) + "."));
      const main = el("div", "ob-main");
      main.appendChild(el("div", "ob-title", st.title));
      if (!st.done) {
        if (st.hint) main.appendChild(el("div", "ob-hint", "怎么做: " + st.hint));
        if (st.cli) main.appendChild(el("span", "ob-cli", st.cli));
        if (st.view) {
          const go = el("button", "btn ghost mini", "去这里 (go)");
          go.type = "button";
          go.addEventListener("click", () => scrollToView(st.view));
          main.appendChild(document.createTextNode(" "));
          main.appendChild(go);
        }
      }
      step.appendChild(main);
      root.appendChild(step);
    });
  }

  function scrollToView(id) {
    const node = $(id);
    if (!node) return;
    try { node.scrollIntoView({ behavior: "smooth", block: "start" }); }
    catch (e) { node.scrollIntoView(); }
  }

  async function dismissOnboarding() {
    obForce = false;
    $("onboarding").classList.add("hidden");
    try { await api("POST", "/api/onboarding/dismiss", { dismissed: true }); }
    catch (err) { /* persistence is best-effort; the panel is already hidden */ }
  }

  /* ------------------------------------------------- failure cards ------ */
  /* Goal item 10 (GUI): render reports/failures.jsonl (carried in /api/state,
   * so it polls with everything else) as expandable cards. Collapsed shows
   * step + subject + cause; expanded shows evidence (mono), hint, log path and
   * the correlated job. Errors red, degradations (info) neutral. A retry lives
   * on cards whose subject is a shot — it re-runs redo through the plan modal. */
  const failOpen = {};   /* failure id -> expanded (survives re-render) */

  function renderFailures(failures) {
    const root = $("failures");
    clear(root);
    const list = Array.isArray(failures) ? failures : [];
    if (!list.length) { root.classList.add("hidden"); return; }
    root.classList.remove("hidden");
    const errs = list.filter((f) => (f.level || "error") === "error").length;
    const head = el("div", "fail-head");
    head.appendChild(el("h2", null, "失败 (failures)"));
    if (errs) head.appendChild(el("span", "badge qc-err", "错误 " + errs));
    const infos = list.length - errs;
    if (infos) head.appendChild(el("span", "badge qc-warn", "降级 " + infos));
    root.appendChild(head);
    const shotIds = {};
    lastShots.forEach((s) => { shotIds[s.id] = true; });
    list.forEach((f) => root.appendChild(failCard(f, shotIds)));
  }

  function failCard(f, shotIds) {
    const level = (f.level === "info") ? "info" : "err";
    const card = el("div", "fail-card " + level);
    const id = f.id || "";
    const sum = el("div", "fail-sum");
    const open0 = !!failOpen[id];
    const arrow = el("span", "fail-arrow", open0 ? "▾" : "▸");
    sum.appendChild(arrow);
    sum.appendChild(el("span", "fail-step", f.step || "?"));
    sum.appendChild(el("span", "fail-subj", f.subject || ""));
    sum.appendChild(el("span", "fail-cause", f.cause || ""));
    const bodyBox = el("div", "fail-body" + (open0 ? "" : " hidden"));
    const fillBody = () => {
      clear(bodyBox);
      if (f.evidence) bodyBox.appendChild(el("div", "fail-ev", f.evidence));
      if (f.hint) bodyBox.appendChild(el("div", "fail-hint", "💡 " + f.hint));
      if (f.log_path) bodyBox.appendChild(el("div", "fail-meta", "日志 (log): " + f.log_path));
      const job = (f.detail && (f.detail.job_id || f.detail.node_id)) || null;
      if (job) bodyBox.appendChild(el("div", "fail-meta", "关联任务 (job): " + job));
      if (f.ts) bodyBox.appendChild(el("div", "fail-meta", f.ts + " · " + (f.actor || "engine")));
      /* 复制诊断上下文 (#50): a clean task block for Claude — error, log,
       * files, recommended step — instead of pasting the whole project. */
      const cp = el("button", "btn ghost mini", "复制诊断上下文");
      cp.type = "button";
      cp.title = "复制该失败的结构化上下文(步骤/原因/日志/文件),交给 Claude 或 agent";
      cp.addEventListener("click", async () => {
        const lines = ["失败步骤: " + (f.step || "?")];
        if (f.subject) lines.push("对象: " + f.subject);
        if (f.cause) lines.push("原因: " + f.cause);
        if (f.evidence) lines.push("证据: " + f.evidence);
        if (f.hint) lines.push("引擎提示: " + f.hint);
        if (f.log_path) lines.push("日志: " + f.log_path);
        if (job) lines.push("任务: " + job);
        if (f.subject && shotIds[f.subject]) lines.push("镜头文件: shots/" + f.subject + ".yaml");
        lines.push("目标: (写下要 Claude 做的事)");
        try {
          await navigator.clipboard.writeText(lines.join("\n"));
          toast("诊断上下文已复制 — 粘给 Claude 即可", "ok");
        } catch (e) { toast("复制失败,请手动选择", "err"); }
      });
      bodyBox.appendChild(cp);
      /* retry: only where the subject is a shot in THIS project, through the
       * normal plan modal (a redo is a priced action) */
      if (!readonly && f.subject && shotIds[f.subject]) {
        const retry = el("button", "btn ghost mini", "重试 (Retry) " + f.subject);
        retry.type = "button";
        retry.addEventListener("click", () => showPlanModal("redo", { shot: f.subject }, {
          title: "重试前计划 (plan before retry) · " + f.subject,
          onConfirm: () => post(retry, "/api/redo", { shot: f.subject, assume_yes: true },
            f.subject + " 重试已入队 (retry queued)"),
        }));
        const wrap = el("div", "btnrow");
        wrap.appendChild(retry);
        bodyBox.appendChild(wrap);
      }
    };
    if (open0) fillBody();
    sum.addEventListener("click", () => {
      const open = !failOpen[id];
      failOpen[id] = open;
      arrow.textContent = open ? "▾" : "▸";
      bodyBox.classList.toggle("hidden", !open);
      if (open) fillBody();
    });
    card.appendChild(sum);
    card.appendChild(bodyBox);
    return card;
  }

  /* ------------------------------------------------- batch select bar --- */
  /* Goal item 3 (GUI): checkbox multi-select on shot cards + a floating bulk
   * bar (N selected → 重做/配音) calling the wave-1 redo_batch/voice_batch
   * through a job. Selection survives poll re-renders (kept in a Set, pruned to
   * the live shots on each render). Helpers: 全选过期 / 清除. */
  const batchSel = new Set();

  function toggleBatch(id, on) {
    if (on) batchSel.add(id); else batchSel.delete(id);
    renderBatchBar();
  }

  function selectAllStale() {
    lastShots.forEach((s) => { if (s.state === "stale") batchSel.add(s.id); });
    try { renderShots(lastShots); } catch (e) { /* checkboxes are advisory */ }
    renderBatchBar();
  }

  function clearBatch() {
    batchSel.clear();
    try { renderShots(lastShots); } catch (e) { /* re-render to clear checks */ }
    renderBatchBar();
  }

  function renderBatchBar() {
    const bar = $("batchbar");
    clear(bar);
    const ids = {};
    lastShots.forEach((s) => { ids[s.id] = true; });
    Array.from(batchSel).forEach((id) => { if (!ids[id]) batchSel.delete(id); });
    const n = batchSel.size;
    if (n === 0 || readonly) { bar.classList.add("hidden"); return; }
    bar.classList.remove("hidden");
    bar.appendChild(el("span", "bb-count", n + " 选中 (selected)"));
    const redo = el("button", "btn confirm", "重做 (Redo)");
    redo.type = "button";
    redo.addEventListener("click", () => {
      const shots = Array.from(batchSel);
      showPlanModal("batch-redo", { shots: shots }, {
        title: "批量重做前计划 (plan before batch redo)",
        onConfirm: () => post(redo, "/api/redo-batch", { shots: shots, assume_yes: true },
          "批量重做已入队 (batch redo queued)"),
      });
    });
    bar.appendChild(redo);
    const voice = el("button", "btn", "配音 (Voice)");
    voice.type = "button";
    voice.addEventListener("click", () => {
      const shots = Array.from(batchSel);
      showPlanModal("batch-voice", { shots: shots }, {
        title: "批量配音前计划 (plan before batch voice)",
        onConfirm: () => post(voice, "/api/voice-batch", { shots: shots, assume_yes: true },
          "批量配音已入队 (batch voice queued)"),
      });
    });
    bar.appendChild(voice);
    const clr = el("button", "btn ghost", "清除 (Clear)");
    clr.type = "button";
    clr.addEventListener("click", () => clearBatch());
    bar.appendChild(clr);
  }

  /* --------------------------------------------------------- tasks view --- */
  /* WORKBENCH row "Spend / tasks" (S8a): render `manju tasks` JSON — the
   * JOB/QUEUE view over the disposable run ledger. Collapsible like the git
   * panel; lazily fetched on expand and after a done job. */
  let tasksOpen = !!loadUI().tasks;  /* per-project memory (#49a) */
  let tasksLoaded = false;
  let tasksBusy = false;
  let tasksBody = null;
  let tasksChips = null;

  function initTasksPanel() {
    const root = $("tasks");
    clear(root);
    const head = el("div", "git-head");
    const arrow = el("span", "git-arrow", tasksOpen ? "▾" : "▸");
    head.appendChild(arrow);
    head.appendChild(el("h2", null, "任务 · 账本 (tasks)"));
    tasksChips = el("div", "chips");
    head.appendChild(tasksChips);
    tasksBody = el("div", tasksOpen ? null : "hidden");
    actAsButton(head, "任务 · 账本 (tasks) 面板");
    head.setAttribute("aria-expanded", tasksOpen ? "true" : "false");
    head.addEventListener("click", () => {
      tasksOpen = !tasksOpen;
      arrow.textContent = tasksOpen ? "▾" : "▸";
      head.setAttribute("aria-expanded", tasksOpen ? "true" : "false");
      saveUI({ tasks: tasksOpen });
      tasksBody.classList.toggle("hidden", !tasksOpen);
      if (tasksOpen) fetchTasks();
    });
    root.appendChild(head);
    root.appendChild(tasksBody);
    if (tasksOpen) fetchTasks();  /* restored-open lazy load */
  }

  async function fetchTasks() {
    if (tasksBusy) return;
    tasksBusy = true;
    try {
      const data = await api("GET", "/api/tasks");
      tasksLoaded = true;
      renderTasks(data);
    } catch (err) {
      if (tasksBody) {
        clear(tasksBody);
        tasksBody.appendChild(el("p", "muted", "账本不可用 (ledger unavailable): " + errMsg(err)));
      }
    } finally {
      tasksBusy = false;
    }
  }

  function renderTasks(data) {
    if (!tasksBody || !tasksChips) return;
    clear(tasksBody);
    clear(tasksChips);
    const spend = (data && data.spend) || {};
    tasksChips.appendChild(el("span", "chip",
      "总花费 (spend) " + fmtMoney(spend.total || 0) + " " + (spend.currency || "")));
    const tasks = (data && Array.isArray(data.tasks)) ? data.tasks : [];
    const pending = (data && Array.isArray(data.pending)) ? data.pending : [];
    if (pending.length) tasksChips.appendChild(el("span", "chip", "在途 (pending) " + pending.length));
    if (data && data.available === false) {
      tasksBody.appendChild(el("p", "muted", data.note || "账本为空 (empty)"));
      return;
    }
    pending.forEach((p) => {
      const row = el("div", "tk-row");
      row.appendChild(el("span", "badge tk-polling", "polling"));
      row.appendChild(el("span", null, (p.shot || "—") + " · " + (p.provider || "—")));
      if (p.remote_job_id) row.appendChild(el("span", "tk-id", String(p.remote_job_id)));
      tasksBody.appendChild(row);
    });
    if (!tasks.length && !pending.length) {
      tasksBody.appendChild(el("p", "muted", "登记账本为空 (no runs yet;云生成/redo 后才有记录)"));
      return;
    }
    tasks.forEach((t) => {
      const row = el("div", "tk-row");
      if (t.id !== undefined && t.id !== null) row.appendChild(el("span", "tk-id", "#" + t.id));
      row.appendChild(el("span", "badge tk-" + (t.status || "").replace(/\s+/g, "-"), t.status || "?"));
      row.appendChild(el("span", null, (t.shot || "—") + " · " + (t.provider || "—")));
      if (t.cost) row.appendChild(el("span", "muted", fmtMoney(t.cost) + " " + (t.currency || "")));
      if (t.created) row.appendChild(el("span", "muted", fmtClock(t.created)));
      if (t.reason) row.appendChild(el("span", "tk-reason", "↳ " + String(t.reason)));
      tasksBody.appendChild(row);
    });
  }

  /* ------------------------------------------------------------ boot --- */
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      pauseLive();   /* clears the timer AND aborts the in-flight watch, so
                        a stale long-poll response can never clobber */
    } else {
      refresh();
    }
  });
  /* any click outside a workspace menu closes it (menu items run first) */
  document.addEventListener("click", () => closeWsMenus(null));
  document.addEventListener("keydown", (e) => {
    if (cmpOverlay) {   /* compare overlay up: Esc closes it, other keys inert */
      if (e.key === "Escape") { try { closeCompare(); } catch (err) { /* gone */ } }
      return;
    }
    if (planOpen) {   /* plan modal up: native <dialog> Esc closes; keys inert */
      if (e.key === "Escape" && typeof $("planmodal").showModal !== "function") closePlanModal();
      return;
    }
    if (editorOpen) {
      /* native <dialog> handles Esc itself (cancel -> close event); the
       * overlay fallback needs the hand-rolled Esc */
      if (e.key === "Escape" && typeof $("editor").showModal !== "function") closeEditor();
      return;
    }
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    const tag = t && t.tagName ? t.tagName.toLowerCase() : "";
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    if (t && t.isContentEditable) return;
    const k = e.key;
    if (k === "r") { refresh(); return; }
    if (k === "?") { kbHint(); return; }
    if (k === "j" || k === "k") { kbMove(k === "j" ? 1 : -1); return; }
    if (k === "e") {
      if (kbFocusId && !readonly) openEditorFor(kbFocusId, null);
      return;
    }
    /* keys with a native meaning stay native on interactive elements */
    if (tag === "button" || tag === "a" || tag === "video" ||
        tag === "audio" || tag === "summary") return;
    if (k === " " || k === "Spacebar") {
      if (kbPlayPause()) e.preventDefault();   /* the page must not scroll */
      return;
    }
    if (k >= "1" && k <= "9") kbSelectTake(Number(k));
  });

  /* static bilingual hint bar content (toggled by "?") */
  (() => {
    const hint = $("kbdhint");
    clear(hint);
    [["j/k", " 上/下一个镜头 (next/prev shot)"],
     ["e", " 编辑 (edit)"],
     ["1-9", " 选用 take (select take)"],
     ["Space", " 播放/暂停 (play/pause)"],
     ["r", " 刷新 (refresh)"],
     ["?", " 关闭提示 (hide hints)"]].forEach((pair, i) => {
      if (i) hint.appendChild(document.createTextNode("  ·  "));
      hint.appendChild(el("b", null, pair[0]));
      hint.appendChild(document.createTextNode(pair[1]));
    });
  })();

  initBuildPanel();
  initShotsBar();
  initDropzone();
  initGitPanel();
  initProposalsPanel();
  initTasksPanel();
  $("editor").addEventListener("close", editorClosed);
  $("planmodal").addEventListener("close", () => { planOpen = false; clear($("planmodal")); });

  /* Safe quit: explicit button only — never cancel jobs from beforeunload. */
  async function requestAppQuit(mode) {
    try {
      await api("POST", "/api/app/quit", { mode: mode || "after_current" });
      toast("正在退出…", "ok");
    } catch (err) {
      toast("退出失败：" + errMsg(err), "err");
    }
  }
  async function promptAppQuit() {
    try {
      const st = await api("GET", "/api/app/status");
      if (st.running_job || (st.queued_count || 0) > 0) {
        if (typeof showQuitDialog === "function") {
          showQuitDialog(st, (mode) => requestAppQuit(mode));
          return;
        }
      }
      await requestAppQuit("after_current");
    } catch (err) {
      toast("无法读取任务状态：" + errMsg(err), "err");
    }
  }
  try {
    api("GET", "/api/app/status").then((st) => {
      if (!st) return;
      const host = $("header") || document.body;
      if (!host || document.getElementById("mj-quit-btn")) return;
      const btn = el("button", "btn ghost mini", "退出");
      btn.type = "button";
      btn.id = "mj-quit-btn";
      btn.title = "安全退出 (finish or cancel jobs)";
      btn.addEventListener("click", () => promptAppQuit());
      host.appendChild(btn);
    }).catch(() => {});
  } catch (e) { /* boot continues */ }

  refresh();
})();
""".strip() + "\n"


def render_page(project_name: str, token: str) -> str:
    """Return the complete ``<!doctype html>`` document served at ``/``.

    Two server-side values are embedded — the project name (title) and the
    per-run CSRF token (``manju-token`` meta tag), both HTML-escaped — plus the
    mode-aware nav + body class resolved by :func:`manju.gui.pages.chrome` (round
    U: the 新手/专业 view switch, its fresh-user hint and the 显示专业术语 toggle).
    Everything else visible is a static skeleton (``#header`` … ``#proposals`` …
    ``#git``, plus the ``#editor`` dialog, the ``#kbdhint`` bar and the
    ``#toast`` rack) that ``/app.js`` fills from ``/api/state`` and the
    v2/v3 endpoints; there is no inline script and no inline ``style=``
    attribute, so the page works under a strict CSP.
    """
    from .pages import GLOSSARY_HEAD, chrome

    name = html.escape(project_name)
    tok = html.escape(token)
    nav, bcls = chrome("/")
    return (
        "<!doctype html>\n"
        '<html lang="zh">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{tok}">\n'
        f"<title>{name} · manju gui</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'  # round-S: shared page chrome (S8b)
        + GLOSSARY_HEAD                                 # round U: glossary + mode + webclient
        + '<script src="/app.js" defer></script>\n'
        "</head>\n"
        # round-S (S8b): nav to the server-rendered workbench pages so every
        # capability in docs/WORKBENCH.md is reachable with one obvious click.
        # round U: nav + body class are mode-aware (chrome()); 新手 omits the
        # pro-only page links (still reachable by URL) and shows a hint bar.
        f'<body class="{bcls}">\n'
        + nav + "\n"
        # round V (goal item 4): the project cockpit — one glance → one action →
        # activity → risk-by-exception. Filled from /api/cockpit (fingerprint-
        # gated, riding the existing poll); the state details bar (#header) that
        # /api/state fills sits directly beneath it.
        '<div id="cockpit" class="cockpit">\n'
        '  <p class="loading">加载中 (loading)…</p>\n'
        "</div>\n"
        '<div id="header" class="panel"></div>\n'
        "<main>\n"
        '  <div id="onboarding" class="panel hidden"></div>\n'
        '  <div id="failures" class="panel hidden"></div>\n'
        '  <div id="dropzone" class="dropzone"></div>\n'
        '  <div id="buildpanel" class="panel"></div>\n'
        '  <div id="jobs" class="panel hidden"></div>\n'
        '  <div id="timeline" class="panel hidden"></div>\n'
        '  <div id="shotsbar" class="shotsbar"></div>\n'
        '  <div id="shots"></div>\n'
        '  <div class="cols">\n'
        '    <div id="qc" class="panel hidden"></div>\n'
        '    <div id="events" class="panel"></div>\n'
        "  </div>\n"
        '  <div id="proposals" class="panel"></div>\n'
        '  <div id="tasks" class="panel"></div>\n'
        '  <div id="git" class="panel"></div>\n'
        "</main>\n"
        '<div id="toast"></div>\n'
        '<div id="batchbar" class="batchbar hidden"></div>\n'
        '<div id="kbdhint" class="hidden"></div>\n'
        '<dialog id="editor" class="editor"></dialog>\n'
        '<dialog id="planmodal" class="editor planmodal"></dialog>\n'
        "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n"
        "</html>\n"
    )


def render_css() -> str:
    """Return the full stylesheet, served as ``/app.css``."""
    return _CSS


def render_js() -> str:
    """Return the full application script, served as ``/app.js``."""
    return _JS
