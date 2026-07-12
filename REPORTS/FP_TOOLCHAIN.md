# FP Loop H — `manju.toolchain-manifest/v1` (record-only reproducibility evidence)

Roadmap §7.7. Branch `claude/cost-optimization-strategy-cjfmn5`, on top of 05f85b2.

## Scope ruling honoured

**RECORD-ONLY.** The manifest captures the toolchain facts that can change
output bytes, as a derived, deletable evidence document. Nothing is wired into
content keys, build caching, resume, or authorization — a grep-pinned test
(`test_grep_pin_never_read_by_build_core_providers_runtime`) proves no
`build/core/providers/runtime` module references `reports/toolchain/` or
imports the module. The future step — 影响输出字节的工具进入内容键 — is
declared in `src/manju/core/toolchain.py`'s docstring as a separately-audited
migration (it invalidates every existing cache key), exactly mirroring the fps
migration pattern in `core/timebase.py`.

## The document

`toolchain_manifest(project=None)` → facts / volatile split; `volatile` is
**empty this loop** (no timestamps, no counters), so the document is
reproducible byte-for-byte on an unchanged machine.

| fact block | content | absent behaviour |
|---|---|---|
| `manju` | version: importlib.metadata → `git describe` → `"unknown"` | `"unknown"` |
| `python` | version / implementation / arch | n/a (always present) |
| `os` | system / release / machine — **no hostname** | n/a |
| `tools` | `ffmpeg`, `ffprobe`: `-version` FIRST line verbatim (the `configuration:` line with `--prefix` paths is never read) | `"missing"`, never a crash |
| `optional_tools` | `tesseract` (qc/content gate), `chromium` (via the renderer's own `find_chromium` locator) — **presence-only bools**, resolved path never stored | `false` |
| `deps` | pydantic / typer / PyYAML / Pillow / OpenTimelineIO / hypothesis versions | `"absent"`, import wrapped |
| `fonts` | `drawtext_cjk`: the file `media/card.find_font()` resolves — the ONE locator both burn surfaces use (§8.4 drawtext card + `media/render.py` caption/text overlays); recorded as **basename + sha256 content hash** | `"unknown"` — never a path |
| `locale` | `sys.getfilesystemencoding()` + `LANG` **presence-only** (value never stored) | n/a |

Font audit note: the HTML card path (`media/html_card.py`) names CSS families
that Chromium resolves internally via fontconfig — it exposes no file for us
to hash, so the drawtext locator result is the honest machine fact recorded.

- `manifest_digest(doc)` — `hash_value` over the `facts` block ONLY; envelope
  or volatile edits never move it, any fact change always does; refuses a
  factless document.
- `write_toolchain_manifest(project, doc)` → `reports/toolchain/<digest-hex>.json`,
  atomic, sorted-keys byte-stable, content-addressed by the RECOMPUTED facts
  digest; an embedded digest that disagrees with the facts is refused
  (tampered manifest), never silently re-filed. Deletable projection.
- `toolchain_drift(old, new)` → pure compare, no I/O: sorted structured rows
  `{"fact": <dotted path>, "old", "new"}` for every changed fact; a side
  missing the fact carries `None` (never a real fact value — gaps are the
  strings `missing`/`absent`/`unknown`). Accepts full documents or bare facts
  mappings. This is the drift surface the dashboard/archive loops consume.

## CLI

`manju toolchain [--write] [--json] [--diff OLD.json]` — anchored directly
after the `appearances` command body in `cli.py`. Needs a project only for
`--write` (machine facts need none). `--diff` prints/emits the drift rows;
drift is evidence, never an error — exit 0 (record-only ruling: no gating
semantics this loop). An unreadable/non-manifest `--diff` file fails with the
stable code `toolchain_diff_unreadable`.

## Hygiene proof (pinned by tests)

The serialized document is scanned for `/home/`, `/root/`, `/Users/`, `C:\`,
`\Users\`, `/usr/`, `/opt/`, `/tmp/`, `/var/`, the hostname (`platform.node()`,
guarded), and the username (`getpass.getuser()`/`USER`/`LOGNAME`,
os.getlogin-family) — none may appear. Fonts carry basename only; `LANG` is a
bool; chromium presence is a bool.

## Tests (red-first)

`tests/test_fp_toolchain.py` — 22 tests, written first and failing on
collection (module absent), then went green:
capture (real ffmpeg first lines verbatim; empty-PATH ⇒ `"missing"`/`false`
without a crash; deps present-or-absent; font basename+hash form), determinism
(two calls ⇒ identical doc, canonical bytes, digest; digest facts-only),
hygiene (no-path-leak scan), inertness (content-addressed byte-stable write,
delete inert, tampered-digest refusal, grep-pin), drift (exact changed rows
incl. added/removed sides, sorted; identical ⇒ `[]`; digest coupling; live
manifest self-drift empty), CLI (json without a project, `--write` lands
project-relative under `reports/toolchain/`, `--diff` rows with exit 0,
unreadable diff fails structured).

## Registry row (NOT applied here — CONTRACTS.yaml untouched, orchestrator applies)

```yaml
  - id: manju.toolchain-manifest/v1
    kind: schema
    owner: manju.core.toolchain
    status: experimental
    latest_version: 1
    read_older: true
    write_older: false
    notes: >-
      Record-only toolchain reproducibility evidence (manju/python/OS/ffmpeg/
      deps/burn-font/locale facts + facts-only digest). Derived + deletable
      under reports/toolchain/; never a content-key/cache/authorization input.
```

Until that row lands, `test_fp_contracts.py` fails 2 tests naming exactly
`manju.toolchain-manifest/v1` — the designed handoff, verified locally.

## CLI surface snapshot

Regenerated LAST via `python -m tests.test_fp_cli_snapshot` → 117 commands
(floor 112 holds). Net new entry vs HEAD: `toolchain` only — parallel loops
E (`qc conformance`) and G (`relink`) were already folded in at HEAD, and the
regen kept them (expected parallel-fold behaviour). 6/6 snapshot tests green.
