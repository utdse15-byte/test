# `manju gui` — 本地 Web 工作台 / The local web workbench

Code: `src/manju/gui/` (`server.py`, `jobs.py`, `state.py`, `page.py`) plus the
`gui` command in `src/manju/cli.py`. Historical design context:
`docs/archive/DESIGN_v2.2.md`
§1-⑦, §3, §5, §11.

## 位置 / Position

§1-⑦ deferred the GUI indefinitely: in a self-use, Claude-Code-driven setup a
GUI has the worst value-for-effort, and the static `manju board` covers ~80% of
the "director's workbench" at under a twentieth of the cost. This GUI revisits that
deferral as a user-directed decision — and it can be shipped cheaply **because**
the deferral's rationale still holds: the GUI is the **third client** of the
same engine core, next to files+CLI and CLI/GUI. It calls the same functions
(`project_status`, `evaluate_all`, `run_check`, `run_build`, `redo_shot`,
`run_qc`, `explain`), writes the same text files through the same
`Project.update_shot_raw`, and logs to the same `events.jsonl` (with
`via: "gui"`).

The GUI adds **no state of its own**. Truth stays in text files (§3): a select
or a lock made in the browser is the same one-line YAML diff the CLI would have
produced, reviewable and revertible in git. The only GUI-side objects — jobs —
are in-memory conveniences that die with the server; durable history remains
where it always was: `events.jsonl` and the run ledger. Kill the server and
nothing is lost. That is why shipping it does not contradict §1-⑦: what §1-⑦
guarded against was a GUI that becomes a *second source of truth or a second
engine*; a pure client was always cheap to add once the core froze.

## 架构 / Architecture

**`server.py`** — a stdlib `ThreadingHTTPServer` (`GuiServer`) with one request
handler. It owns the `Project`, a per-run CSRF token
(`secrets.token_urlsafe(24)`), the `JobRunner`, a Host allowlist, and a small
`quick_mutex` that serializes the quick read-modify-write text mutations
(select / lock) among themselves. GET routes are read-only; POST routes either
mutate one YAML file under the mutex or enqueue a job. `create_server()`
builds one (port `0` = pick a free port); `serve()` is the blocking CLI entry
that optionally opens a browser.

**`jobs.py`** — `JobRunner`: one daemon worker thread, strict FIFO. Mutating
engine operations (build / redo / voice / qc) run one at a time — an honest
model of the engine's single-writer design (§3, §5: value locks guard
*content*, not *processes*). A second click queues behind the first. Jobs are
in-memory only; the runner keeps at most 50 finished jobs (`_HISTORY_CAP`),
never dropping active ones. A job's `result` is whatever dict the engine call
returned — the same payload the CLI prints with `--json`.

**`state.py`** — `build_state()` assembles the consolidated `/api/state`
payload: project meta, status summary, per-shot cards (takes, voice takes,
locks), QC summary, events tail, job list. Everything is read-only and **cheap
by construction**: no ffprobe, no timeline compile, no content-key hashing on
the poll path — take durations come from sidecar-cached probe data, and the
expensive explainer stays behind its own on-demand endpoint. A polling UI must
never mutate the project or make it slower.

**`page.py`** (front-end; described here only by its contract) — three pure
functions `render_page` / `render_css` / `render_js` returning strings served
at `/`, `/app.css`, `/app.js`. Vanilla JS, no framework; all DOM is built via
`textContent`. It polls `/api/state` every 1.5 s while jobs are active, 5 s
when idle. A hidden tab with an ACTIVE job keeps a slow 20 s job check (so a
long build can notify on completion — system notification, `(N 完成)` title
badge, optional sound); a hidden idle tab pauses entirely.

```text
browser (vanilla JS, textContent, polling)
   │  HTTP: GET /api/state · POST /api/{select,build,redo,voice,qc,lock}
   ▼
GuiServer (server.py)          quick_mutex ── select/lock (one YAML edit)
   │                           JobRunner  ── build/redo/voice/qc (FIFO)
   ▼
engine core (same calls as CLI & CLI/GUI: status/stale/check/build/qc/explain)
   │
   ▼
text files (truth): shots/*.yaml · events.jsonl   +   media/renders (append-only)
```

## API reference

Conventions (from `server.py`): every request — GET and POST — first passes the
Host allowlist or gets `403`. All API responses are JSON with
`Cache-Control: no-store` and `X-Content-Type-Options: nosniff`; errors are
`{"error": "<one line>"}`; unhandled engine exceptions become a one-line `500`.
POST additionally requires the `X-Manju-Token` header (else `403`), a JSON
*object* body (else `400`) of at most 1 MiB (else `413`). Unknown paths: `404`.

### GET

| Route | Response | Notes |
| --- | --- | --- |
| `/` | HTML page | title/name from `project.yaml` (fallback: dir name); carries the CSRF token; strict CSP (see threat model) |
| `/app.css` | `text/css` | `render_css()` |
| `/app.js` | `application/javascript` | `render_js()` |
| `/api/state` | consolidated state | see field list below |
| `/api/jobs` | `{"jobs": [job…]}` | newest first |
| `/api/check` | `{"ok", "errors": [str], "warnings": [str]}` | `run_check` — schema + references + locks |
| `/api/explain?graph=` | `{"shots", "timeline", "renders"}` (+ `"graph"` when `graph=1`) | `manju explain` — recompiles + hashes; on demand only. `graph=1`\|`true`\|`yes` appends the same `manju.graph-diagnostics/v1` document `manju explain --graph` prints (`diagnose_project`, read-only); opt-in because deriving it recompiles again |
| `/api/events?n=&actor=&action=` | `{"events": [{"ts","actor","action","detail"}…]}` | tail of `events.jsonl`; `n` default 50, clamped 1–1000 (bad `n` → 50); optional exact-match `actor`/`action` filters, applied over the last 1000 events before the `n`-tail |
| `/api/shot/<id>` | `{"id","exists","yaml","locked","in_index"}` | raw shot-file text for the editor (see POST notes) |
| `/api/bible/<name>` | `{"name","exists","yaml"}` | raw text of `bible/{characters,scenes,props,style}.yaml` |
| `/api/rules` | `{"exists","yaml"}` | raw text of `timeline/rules.yaml` |
| `/api/watch?fp=&timeout=` | `{"fp","changed"}` | long-poll (≤30 s): returns when the project fingerprint differs from `fp` — the co-presence channel the page's idle loop uses (an AI edit reaches the browser within ~0.5 s); at most 8 held watchers, extras answer immediately |
| `/api/projects` | `{"projects":[{"slug","name","root","shots","active"}],"workspace":bool}` | the workspace project list (see workspace mode below); outside workspace mode: `[]` + `workspace: false` |
| `/api/proposals` | `{"proposals":[{"name","path","mtime","text","truncated"}]}` | the AI→human request channel (§5), newest first, text capped at 10 KB |
| `/api/spend` | `{"total","currency","budget_limit","by_provider":[{"provider","runs","cost"}…],"by_shot":[{"shot","runs","cost"}…],"recent":[{"ts","shot","provider","take","cost","currency","status"}…],"source"}` | `spend_report` (`build/spend.py`, §8.3 事后) — the same payload as `manju spend --json`: run-ledger authoritative, take-sidecar fallback, `source` says which (`ledger`\|`sidecars`\|`empty`); `by_*` sorted by cost desc; `recent` newest first, capped at 20 |
| `/api/schema` | `{"schemas": {name: JSON Schema}}` | the truth-file model schemas (§12) via `export_json_schemas()` — `project`, `shot`, `shot_index`, `take_sidecar`, `timeline_rules`, `timeline`; same set as `manju schema` |
| `/api/timeline` | `{"timeline": null \| Timeline dump}` | the compiled `timeline/timeline.json`, verbatim |
| `/api/doctor` | `{"checks":[{"name","ok","detail","line"}],"ok"}` | same probes as `manju doctor` (`build/doctor.py`) |
| `/api/git/status` | `{"git": null \| {"branch","dirty","files","ahead","behind","truncated"}}` | `null` when not a repo / git missing |
| `/api/git/diff?path=` | `{"diff": str\|null}` | working-tree unified diff, truncated at 200 KB |
| `/api/git/log?path=&n=` | `{"log":[{"hash","ts","author","subject"}]}` | `n` clamped 1–200 |
| `/media/<rel>` | file bytes | allowlisted subtrees only; ETag/`304`; `Range` → `206`/`416`; `Cache-Control: no-cache` |
| `/preview/<rel>` | browser-safe bytes | same allowlist/gates as `/media`; non-browser-safe sources (`.mkv`/`.flac`/ProRes `.mov`…) are lazily transcoded once into the disposable `.manju/webpreview` cache (`media/webpreview.py`) and served from there; on any transcode failure the ORIGINAL bytes are served (degradation, never an error) |
| `/thumb/<rel>` | jpg bytes | lazy cached frame-grab of a take (same allowlist); `404` when no thumbnail can be made — the page shows the plain card |

`/media` and `/preview` serve only project-relative paths starting with one of
`MEDIA_PREFIXES = ("media/", "renders/", "reports/", "exports/", "captions/")`
— anything else is `403 path not served`, a missing file is `404`. Content
types come from a fixed suffix map (mp4/mov/mkv/webm, png/jpg/gif/webp,
wav/mp3/m4a/flac, srt/ass/json/md); unknown suffixes fall back to
`application/octet-stream` with `nosniff`. `state.py` picks the URL per take:
browser-safe suffixes get `/media/…`, everything else gets `/preview/…` — the
page just plays whatever URL it was handed.

`/api/state` additionally carries `"fp"` (the fingerprint its content was
computed at — seed `/api/watch` with it) and `"readonly"` (true under
`manju gui --readonly`, where every POST answers 403 — review-only sharing;
GETs may still WRITE the disposable caches: `/preview`/`/thumb` fill
`.manju/webpreview` and opening the ledger touches `state.sqlite` — runtime
only, never truth).
The state payload is cached server-side by fingerprint, so polling a large
project costs one cheap stat-walk per request until something actually
changes.

### Workspace mode (`manju gui --workspace <dir>`)

`discover_workspace` scans the directory (itself + direct children) for
`project.yaml` roots; `GET /api/projects` lists them (slug/name/root/shots/
active) and `POST /api/switch {"slug"}` swaps the server's ONE active
project — every route then serves the new project, and `/api/state` carries
`"workspace": {"active", "count"}`. Honest limits of the single-active model:
two tabs on the same server share the active project (a switch in one
retargets the other on its next poll — by design, it's one director), and a
STALE tab's media URLs resolve against the new active project (same relative
path in both projects → the wrong file plays until refresh; the page reloads
state after switching, so only backgrounded tabs can see this). In-flight
jobs keep the project they closed over — a switch never retargets running
work. For genuinely simultaneous multi-project work, run one server per
project.


### POST

| Route | Body params | Success | Errors |
| --- | --- | --- | --- |
| `/api/select` | `shot`, `take` (both required) | `200 {"ok", "shot", "take"}` | `400` missing params; `404` no such take |
| `/api/lock` | `shot`, `field` (both required) | `200 {"ok", "shot", "field", "hash"}` | `400` missing params / unresolvable shot or field |
| `/api/build` | `target` (`proxy\|final\|exports\|qc`, default `final`), `gen` (`missing\|auto\|off`, default `missing`), `regen_stale`, `force`, `dry_run`, `assume_yes` (bools — see the ask_before gate note) | `202 {"job": …}` — or `200 {"dry_run": true, "result": …}` | `400` unknown target/gen |
| `/api/redo` | `shot` (required), `provider?`, `candidates?`, `seed?` | `202 {"job": …}` | `400` missing shot; `404` unknown shot |
| `/api/voice` | `shot` (required), `provider?` | `202 {"job": …}` | `400` missing shot / no `dialogue.text`; `404` unloadable shot |
| `/api/qc` | `deep?` (bool) | `202 {"job": …}` | — |
| `/api/shot/<id>` | `yaml` (raw file text) | `200 {"ok", "shot", "created", "warnings"}` | `400` bad id / bad YAML / not a mapping; `409` check failed — **auto-reverted** |
| `/api/upload?name=<file>` | RAW file bytes as body (not JSON) | `200 {"ok", "imported", "preview"}` | `400` bad name / truncated; `411` no length; `413` > 4 GiB |
| `/api/git/commit` | `message` (required) | `200 {"ok", "hash"}` | `400` empty message; `409` git error / nothing to commit / not a repo |
| `/api/bible/<name>` | `yaml` (raw text) | `200 {"ok","created","warnings"}` | `400` bad name/YAML; `409` check failed — auto-reverted |
| `/api/rules` | `yaml` (raw text) | `200 {"ok","created","warnings"}` | `400` bad YAML; `409` check failed — auto-reverted |
| `/api/index` | `order` (list of ALL current shot ids, reordered) | `200 {"ok","order"}` | `400` not a permutation of the current shots |
| `/api/take-note` | `shot`, `take`, `text` (empty text deletes) | `200 {"ok","shot","take","text"}` | `400` missing params / >2000 chars; `404` no such take |
| `/api/validate` | `kind` (`shot`\|`rules`\|`bible/<name>`), `yaml`, `id?` (shot only, default `S000`) | `200 {"ok","errors"}` — findings are data, not HTTP errors | `400` unknown kind |
| `/api/switch` | `slug` (workspace mode only) | `200 {"ok","slug","root"}` | `400` not in workspace mode; `404` unknown project |

Details:

- **select** validates the take exists (`project.get_take`) and then writes
  `status.selected_take` — one YAML line. It cannot register arbitrary files
  (no `--file` equivalent on this surface).
- **lock** seals the field's *current value* hash into the shot's `locked` map
  (§5 value-hash locks; a legacy list migrates to a dict). There is no unlock
  route — see the threat model.
- **build, `dry_run` rule**: `dry_run: true` runs `run_build(..., dry_run=True)`
  *synchronously* and answers `200` with the full `BuildResult` dict (`ok`,
  `plan`, `stale`, `estimated_cost`, …). Everything else is enqueued and
  answers `202` with the job. Dry-run deliberately runs *without* the process
  build lock (see `build/graph.py`), so an estimate is always available even
  mid-build.
- **build, ask_before gate (§8.3)**: a non-dry-run whose plan has
  `estimated_cost > 0` while `expensive_generation` is in the project's
  `ask_before` list finishes as `ok: false, waiting_user: true` unless the
  body carried `assume_yes: true`. The page should show the estimate and a
  confirm, then resend with `assume_yes` — the same yes a CLI user gives with
  `manju build --yes` and an agent must obtain from the human.
- **shot editor** (`GET`/`POST /api/shot/<id>`): GET returns the RAW file text
  (`{"id","exists","yaml","locked","in_index"}`) — human truth travels
  verbatim, never a model round-trip (§3). POST writes the text exactly as
  typed, runs the full `manju check`, and **reverts the write** if it
  introduced any new error — lock violations included, so the GUI cannot
  bypass §5 any more than CLI/GUI can. A new id creates the shot file.
- **editor 409 contract** (shot / bible / rules saves, `_gated_save`): the
  `409` body is `{"error", "errors": [only the NEW check findings vs the
  pre-save baseline], "current": <the reverted-to on-disk text — "" when the
  file didn't exist>}` — enough for the conflict banner to render
  buffer-vs-truth and offer a re-fill without a second fetch (Figma pattern).
- **validate** is the keystroke-time twin of the gated saves (VS Code
  settings.json pattern): YAML parse + model validation ONLY — no write, no
  full `manju check`, no lock verification (those stay in the save), so a
  debounced client can call it per pause. `shot` validates `ShotSpec` (the
  optional `id` fills the model's required id), `rules` validates
  `TimelineRules`, `bible/<name>` checks shape only (every top-level value a
  mapping). Parse/model findings return as `200 {"ok": false, "errors":
  [one-line strings]}`; only an unknown `kind` is a `400`. Purely advisory —
  the gated save remains the authority.
- **upload** is the GUI twin of `manju import`: append-only into
  `media/imports/` with collision-suffixed names, streamed through a temp file
  in `.manju/upload-tmp`. CLI/GUI excludes `import` because an agent could pull
  arbitrary *filesystem paths* into the project; a browser upload has no such
  power — the human pushes bytes they already hold.
- **git** (`/api/git/status|diff|log` + `commit`): read-mostly window over
  `core/gitops.py` — commit is the only write; there is deliberately no
  checkout/reset/revert on any manju surface (terminal-only, like unlock).
  The `git_commit` event is appended *before* committing so it rides inside
  the commit instead of re-dirtying the tree.
- **redo** result: `{"shot", "takes": [names]}` — append-only new takes.
  **voice** result: `{"shot", "take", "media"}` — a new voice take, newest
  wins. **qc** result: `{"ok", "errors", "warnings", "reports"}` and writes
  `reports/qc.json` / `qc.md`.

### Job lifecycle

`202` responses carry a job dict: `{"id", "kind", "params", "state",
"progress", "error", "result", "created", "started", "finished"}` (UTC ISO
timestamps). `state` walks `queued → running → done | failed`; `progress` is
a coarse advisory phase string (build sets it per phase, e.g.
`"render:final"`); on failure `error` is a one-line message. Job-borne mutating operations (build / redo / voice / qc) are
**strictly FIFO-serialized** through the single worker thread — no two jobs
ever run concurrently; the quick text mutations (select / lock) are serialized
among themselves by `quick_mutex`. Poll `/api/state` (or `/api/jobs`) to watch
a job finish; there is no per-job endpoint.

### `/api/state` payload (from `state.py`, plus three route-stamped fields)

| Field | Contents |
| --- | --- |
| `project` | `name`, `resolution` (`"1080x1920@24"`), `mode`, `width`, `height`, `fps` |
| `budget` | `total_cost` (run-ledger first, sidecar sum as fallback), `currency`, `limit` |
| `next_step` | suggested next action string, in build order (`build/status.py`) |
| `shots_by_state` | map state → shot ids; states: `missing\|fresh\|stale\|manual\|needs_selection\|broken` |
| `voice_by_state` | same for voice: `missing\|fresh\|stale\|manual` (`not_needed` omitted) |
| `timeline` | `exists`, `duration_ms\|null`, `mode\|null` — no compile is triggered |
| `latest_final` | `{path, url}` or `null` |
| `finals` | version stack (Frame.io pattern): newest-first `renders/final/final_v*.mp4`, capped at 10 — `{name, url, size, mtime, has_key}`; `has_key: false` = no `.key.json` content-key sidecar (crashed-render honesty) |
| `latest_final_note` | `str \| null` — "final may be incomplete (no content-key sidecar; crashed render?)" honesty flag |
| `build_lock` | `null \| {"pid","actor","started","hostname"}` (or `{"note"}` for an unreadable lock file) — the active process-lock holder |
| `qc` | `null` until `reports/qc.json` exists; else `ok`, `errors`, `warnings`, `items[{level, area, message, shot, suggestion}]` (unreadable file → one synthetic error item) |
| `shots` | one card per shot, index order: `id`, `state`, `note`, `action`, `speaker`, `dialogue`, `selected_take`, `spec_hash` (12-char short), `locked` (sorted field paths), `voice` (`{state, why}` or `null`), `takes`, `voice_takes`. A broken shot file still gets a card — `state`/`note` say why |
| `shots[].takes[]` | `name`, `provider`, `spec_hash` (short), `duration_ms` (sidecar-cached probe), `url` (`/media/…` or `/preview/…`, CJK-safe percent-encoded) or `null`, `thumb` (`/thumb/…` for non-audio takes, else `null`), `poster` (the selected take's `reports/frames/<shot>.jpg` when present, else the thumb), `selected`, `ext`, `note` (director take-note from `status.take_notes`, `null` when none), `seed` (sidecar `params.seed` — the recipe travels with the output for one-click same-recipe redo) |
| `shots[].voice_takes[]` | `name`, `url`, `manual` (`true` = hand-dropped, no sidecar) |
| `events` | last 15 lines of `events.jsonl` |
| `jobs` | full job list, newest first |
| `fp` | the fingerprint this payload was computed at — seed `/api/watch` with it (stamped by the route in `server.py`, as are the next two) |
| `readonly` | `true` under `manju gui --readonly` — every POST answers `403` |
| `workspace` | `null` outside workspace mode; else `{"active": slug, "count": n}` |

## 安全模型 / Threat model

A localhost web server is still a web server. Concretely (all in `server.py`):

- **DNS rebinding** — every request's `Host` header must be
  `localhost` / `127.0.0.1` / `[::1]` (plus the explicitly bound host, when it
  isn't `0.0.0.0`/`::`); anything else is `403` before any routing. A rebound
  hostname resolving to 127.0.0.1 therefore cannot reach the API.
- **CSRF** — every state-changing POST must carry `X-Manju-Token`, a per-run
  random token (`secrets.token_urlsafe(24)`) embedded in the served page.
  Cross-origin JS cannot supply it: a custom header forces a CORS preflight,
  which this server never answers (no OPTIONS handler, no CORS headers), so
  the POST never leaves the browser. An HTML form cannot set custom headers at
  all, so a blind cross-site form post fails the token check with `403`.
- **Path traversal** — `/media/<rel>` is gated three times: (1) the raw
  relative path must start with an allowlisted prefix; (2) `Project.resolve`
  resolves it (symlinks included) and rejects anything escaping the project
  root; (3) the allowlist is re-checked against the **resolved** path, so
  `media/../shots/S001.yaml` (inside the root, outside the allowlist) and a
  symlink pointing from an allowed tree elsewhere into the project are both
  `403`. A symlink leaving the root entirely dies at step 2. `.manju/`,
  `.git/`, `shots/`, `bible/` are never served.
- **XSS** — the API is JSON-only (`nosniff`, `no-store`); the page builds all
  DOM via `textContent`; and the one HTML document ships this exact CSP:
  `default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self';
  media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri
  'none'; form-action 'none'` — plus `X-Frame-Options: DENY` and
  `Referrer-Policy: no-referrer`. Even injected markup could execute nothing.
- **Absent by design** — `unlock`, `gc --hard`, `pack`/`unpack`, and
  arbitrary-path import/registration do not exist on this surface, mirroring
  the CLI safety boundary (§5: unlock is interactive-terminal-only; dangerous
  commands are not on the CLI/GUI face). Locking via the GUI is safe (it only
  *adds* a constraint); unlocking stays a deliberate, terminal-confirmed act.

Honest limits:

- **No auth beyond the token.** The trust model is "whoever can browse
  `http://127.0.0.1:<port>/` is the operator" — any local process/user that
  can fetch `/` gets the token. `manju gui --host <non-local>` prints an
  explicit warning (`cli.py`): no auth, trusted networks only. No TLS, no rate
  limiting — localhost assumptions throughout.
- **Cross-process races.** In-process, `quick_mutex` + the FIFO runner keep
  the GUI consistent with itself, and `run_build`/`redo_shot` already hold the
  per-project process lock (`src/manju/runtime/buildlock.py`) — a GUI build
  racing a CLI build loses cleanly with a one-line `BuildLocked` finding; the
  GUI's voice and qc jobs hold the same lock. Deliberately OUT of that lock:
  the quick text mutations (select / lock / the shot, bible and rules editor
  saves). An editor save racing another process's build can therefore see a
  spurious 409 (the check baseline picked up build-introduced errors) or
  revert a build's fresh auto-select — the same exposure hand-editing YAML
  mid-build always had (§3); the gated save only ever touches ONE truth file
  atomically, so nothing can be torn.

## 快速上手 / Quickstart (中文)

在项目目录里运行:

```bash
manju gui                    # 默认 http://127.0.0.1:8321/,自动打开浏览器
manju gui --port 0           # 端口被占时让系统挑一个空闲端口
manju gui --no-open          # 只起服务,不开浏览器
manju gui --host 0.0.0.0     # 会打印警告:除 CSRF token 外无任何鉴权,仅限可信网络
```

界面分区(均由 `/api/state` 一个请求驱动):**状态头**(项目名、分辨率、模式、
累计花费/预算、建议的下一步)、**构建面板**(target/gen/regen-stale/force 与
dry-run 估算)、**镜头卡片**(每镜头的状态、台词、候选 take 预览与选用、配音
take、锁定字段)、**QC 面板**(最近一次 `reports/qc.json` 的错误与警告)、
**事件流**(`events.jsonl` 尾部,谁在何时做了什么)。

推荐顺序:先在构建面板点 **dry-run 估算**——同步返回构建计划与预估成本
(§8.3 试算定价),不排队、不烧钱;确认后再点 **构建**,任务进入 FIFO 队列,
卡片与事件流会随轮询自动刷新。在编辑器里改过镜头文件后可随时 GET
`/api/check` 校验;构建本身也以同一校验为硬闸,校验失败即拒绝构建。

为什么"选用/锁定"敢放进网页?因为它们各自只是**一行文本改动**(§3):选用写
`status.selected_take: take_03`,锁定写入该字段当前值的哈希。事后 `git diff`
一目了然,`git revert` 随时可回滚——GUI 没有任何绕过文本真相的私有状态。解锁
不在网页上:它仍然只属于交互式终端(§5)。

## Design notes

- **stdlib `http.server`, not Flask/FastAPI.** Zero new dependencies keeps the
  §1-④ single-stack discipline honest: the engine's install surface stays
  pydantic/typer/PyYAML, and a threaded stdlib server is entirely adequate for
  a single-operator localhost tool. A web framework would buy routing we can
  do with one dict, at the price of a new dependency tree to pin and audit.
- **Polling, not websockets (v1).** `/api/state` is cheap by construction, the
  stdlib has no websocket server, and the page's 1.5 s (active) / 5 s (idle) /
  paused-when-hidden cadence makes freshness a non-issue. Polling also
  degrades gracefully: a dropped connection is just a missed tick.
- **`dry_run` is synchronous.** It is read-only planning + pricing — queueing
  it behind a running render would make the *estimate* wait on the *spend*,
  inverting its purpose. It also skips the process build lock for the same
  reason (`build/graph.py`).
- **Shipped since v1:** process-lock coverage over build/redo/voice/qc/gc;
  the check-gated editors (shots/bible/rules); uploads; the timeline strip;
  `/api/watch` long-poll (chosen over SSE: one code path serves both the
  poll fallback and the push case); workspace mode; per-take thumbnails;
  the conflict banner (editor 409s carry the reverted-to truth in
  `current`; the page renders a buffer-vs-truth diff with a confirm-gated
  re-fill — Figma pattern); unread-first triage (新/未阅 chips from a
  per-project local review snapshot); keystroke-time validation
  (`POST /api/validate` + the editors' debounced live strip); the A/B
  compare overlay (sync-play, fit-5s equal-length review, one-click select
  left/right); and static-board review-notes parity (verdict chips + take
  notes on `board.html`, warning when the selected take is 弃-marked).
  Still open: cancel for running jobs (ffmpeg is atomic per artifact, so v1
  builds run to completion); build-lock coverage over the quick text
  mutations (see the honest limits above).

## #50 方向计划之后 / After the direction program (2026-07-14)

The GUI converged on a **personal production console** (DECISIONS #49–#50a,
REPORTS/GUI_DIRECTION_2026-07-14.md). What a future session should know:

- **hidden 即隐藏**:app.css 以 `!important` 钉死 `.hidden`/`[hidden]` —
  永远不要再写 `.foo.hidden{display:none}` 补丁,也不要让作者 `display:`
  规则去对抗 `hidden` 属性(docs/PINS.md 有对应 pin)。
- **审片是最强页面**:有未评价的当前候选时默认进入队列模式(显式开关才持久化);
  队列按 待挑选→待更新→当前媒体阻塞/QC 错误→当前候选待评价→已评价待审批→无 take→已通过
  排序,载入时快照。进度只统计已有当前选择的镜头；历史候选的旧备注不会把新选择误算成已评价。
  页面明确分开当前选择、当前评价、镜头审批与 Picture Lock；`g` 只写“推荐”评价并在队列中
  前进，`x` 只写“不推荐”评价且不取消选择，`u` 撤回最近一次评价；播放倍速/音量/静音按项目记忆。
- **首页先"继续"再看数据**:common.js 在每个 server 页写
  `manju-last-<identity>`;cockpit 渲染 继续上次工作 chip 与可点击的状态
  计数(点击=筛选分镜网格);新 take 未阅数来自同一本地快照。
- **所有 per-project 浏览器状态**一律以 #45 的稳定身份令牌为键
  (`manju-ui-/-reviewed-/-rv-pos-/-rv-queue-/-rv-av-/-last-<identity>`),
  绝不用显示名。引擎不保存任何 UI 状态。
- **应用外壳按六个制作阶段稳定呈现**(工作台·创作·镜头·审片·成片·工具):
  第一行长期显示项目、执行模式和视图设置，第二行始终显示六个阶段；只有当前阶段
  展开页级子导航，避免 hover-only 下拉和 17 个同权入口。`_NAV` / `_NAV_GROUPS`
  仍是标签与分组 owner；成片页级顺序继续由其页面内流程条拥有，不重复显示。
- **镜头制作是分支式旅程,不是假四步流程**:`/storyboard` 负责规划；`/lab` 和
  `/ingest` 是两条替代的候选来源,共同汇入 `/review`。共享流程条只从当前 shot / take /
  review 事实派生,不保存第二份进度；`?shot=` 深链优先于浏览器上次位置。镜头实验室把
  参考诊断、其它提示词、路由和高级改写渐进展开；入库确认匹配永不自动选用候选。
- **成片阶段是一条连续旅程**:`/edit`、`/subtitles`、`/mixer`、
  `/packaging`、`/exports` 共用只读流程条;首屏后只读取一次
  `/api/finishing/status`,状态继续由 exportstatus/readiness 拥有。
  “可进入锁片评审”不是 Picture Lock,GUI 不保存第二份进度。
- **`/create` 不把 build 当锁片**:创作轨使用故事与结尾、SceneContract /
  ShotContract、Animatic / Proof 和 candidate 术语；同页只读显示
  `proxy-only` / `candidate` / `final-eligible` 与 Picture Lock eligibility，
  数据来自 `build/readiness.py` 的派生视图，不保存第二份状态。
- **AI 在 GUI 外**(§0):/review 卡片“复制给 IDE 助手”、失败卡复制诊断上下文
  只递结构化文本;不要在 GUI 里内建聊天/模型管理。
- **`manju gui --app`**:Edge/Chrome `--app=` 无边框窗口,找不到浏览器时
  具名回退默认浏览器;关窗不停服务(分离进程无法诚实通知)。
