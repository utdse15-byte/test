# FP Loop A — F0 Contract Governance (completion report)

> Program: Manju Function-Perfection, Loop A (F0).
> Date: 2026-07-12. Branch: `claude/cost-optimization-strategy-cjfmn5`.
> Scope: roadmap §4 (Contract Governance). ADDITIVE ONLY — zero engine
> behaviour changes. `python -m pytest` only; no commit/push; DECISIONS.md
> untouched.

This loop puts the frozen-feature governance layer around Manju's public
contracts: a declared registry, a pure loader, registry⇄code consistency tests,
a CLI compatibility snapshot, an old-project fixture corpus, and documentation
validation. Governance here is **declared + test-enforced, never a runtime
input** — no build/qc/provider/CLI path reads the registry (wiring a
documentation file into the engine would be exactly the second-source mistake §4
warns against).

---

## 1. Contract Registry (`CONTRACTS.yaml`)

50 entries total, at the repo root next to DECISIONS.md.

| kind | count | statuses |
|---|---:|---|
| schema (`manju.*/vN`) | 40 | 38 stable, 2 experimental |
| document (non-schema public contract) | 10 | 8 stable, 2 internal |

- **All 40** `manju.*/vN` schema-id literals found in `src/manju` are registered;
  the registered schema set equals the src literal set **exactly** (no ghosts, no
  unregistered ids). No schema id beyond the audited 40 was found.
- **experimental** (ruling): only `manju.qc.colorstats.preview/v1` and
  `manju.qc.colorstats.compare/v1`. Every other shipped schema defaults to
  **stable**.
- **internal** documents: the `events.jsonl` envelope (one row for the
  envelope — the `action` strings are values, not schemas) and the `reports/`
  dir tree (derived + deletable projections).
- Each entry carries `id, kind, owner, status, latest_version, read_older,
  write_older, notes`. `latest_version` matches the major in the id;
  `write_older` is false everywhere (no downgrade path ships this loop);
  every stable entry declares `read_older: true`.
- Non-schema document rows: `project.yaml`, `bible`, `shot.yaml`,
  `timeline.json`, `take-sidecar`, `events-envelope`, `reports-dir`,
  `cli-json-surface`, `mcp-tool-surface`, `provider-manifest`.

**Ownership map (§4.7)** — encoded in `CONTRACTS.yaml` under `ownership:` and
asserted complete for all 8 concepts: shot_intent→Shot YAML,
character_identity→Bible, selected_take→Shot YAML, provider_request→
GenerationRequest, quality_evidence→QC evidence, release_approval→verification
event, delivery_list→DeliveryManifest, runtime_state→append-only evidence +
SQLite projection.

**Planned migrations** — one declared, NOT implemented:
`project.fps int → rational edit_rate`. Recorded so the compatibility boundary is
on the books; the migration itself waits for `manju.media-technical-profile/v1`
(F1). `models.ProjectConfig.fps` is pinned to `int` today, and a test asserts
that fact stays true.

## 2. Loader (`src/manju/core/contracts.py`)

A small pure reader: parses `CONTRACTS.yaml`, validates shape (unknown status →
`ContractsError`; also missing fields, bad types, duplicate ids, wrong kind), and
exposes `registry()`, `entry(id)`, `schema_ids()`, plus `ownership()` and
`planned_migrations()`. Its only I/O is reading the one YAML file. Nothing in the
engine imports it.

## 3. Registry ⇄ code consistency (`tests/test_fp_contracts.py`, 17 tests)

- (a) every `manju.*/vN` literal in `src/manju/**` is registered;
- (b) every registered schema id still appears in src (no ghost rows);
- (c) all statuses are one of the four §4.2 levels; every stable contract
  declares `read_older: true`;
- (d) the §4.7 ownership map is complete for exactly the 8 concepts;
- plus: schema count meets the audited **floor** (`>= 40`, growth-compatible),
  `latest_version` matches the id major, the two colorstats projections **stay
  experimental** (membership + direction — never silently promoted; new
  experimental schemas are allowed to arrive), `write_older` false everywhere,
  **every registered owner module/symbol imports** (the
  registry can never name code that does not exist), the declared future major
  is recorded-not-implemented, and loader shape-guards (unknown status / missing
  fields / duplicate ids / unknown id) — each driven off a **temporary** registry
  file via `contracts._load(tmp_path)`, never by mutating the real CONTRACTS.yaml
  (the loader is `lru_cache`d on the path, so a real-file mutation would read
  stale cache).

## 4. CLI compatibility snapshot (`tests/fixtures/cli_surface.json`)

**112 commands frozen** (matches the audit's 112 `@*.command()`), generated from
the typer app object directly (never shelling out per command). 62 commands
carry required params; 76 required params captured in total. The snapshot is
`{command_path: {params: [required only], exists: true}}`, deterministic
(sorted). Regenerate on an intentional surface change with
`python -m tests.test_fp_cli_snapshot`.

Tests (`tests/test_fp_cli_snapshot.py`, 6 tests): a removed/renamed command
FAILS; a dropped required param FAILS; a **new** unsnapshotted command FAILS with
a regenerate instruction (reviewed additions); a newly-required param FAILS.
The count pin is a **floor** (`>= 112`, not `== 112`): the surface may GROW via
reviewed regeneration, but a regenerated snapshot that drops below the audited
floor is the only tell of a truncated regeneration on top of a mass removal that
(a)/(b) — comparing live against the snapshot — cannot catch.

## 5. Old-project fixture corpus (`tests/fixtures/compat/`)

8 small hand-written old-shape fixtures (no copied media):

| fixture | current loader | pins |
|---|---|---|
| `project.yaml` | `Project.load_config` | old int-fps shape loads; no rewrite |
| `shot.yaml` | `Project.load_shot` | id defaulted from filename; no rewrite |
| `timeline.json` | `Project.load_timeline` | one video clip + caption; no rewrite |
| `take_sidecar.yaml` | `Project.takes` | pre-`spec_version` take (read as v1); no rewrite |
| `verdict_v1.jsonl` | `agent_review._read_records` | read as legacy (schema-less); no rewrite |
| `delivery_manifest.json` | (no read-back loader) | parses; schema is current major; provably inert |
| `torn.json` | `json.loads` | half-written → clean parse error, not a crash |
| `verdict_future_v99.json` | `agent_review.record_verdicts` | future major REFUSED at intake |

Tests (`tests/test_fp_compat.py`, 11 tests) pin: each fixture loads via its real
loader without exception **and without rewriting the file bytes** (sha256
before == after — the §4.5 "不静默改写" guarantee, and a belt-and-braces test
hashes the whole corpus before/after loading everything); the torn file is
rejected structurally (and a torn LINE appended to the append-only log is
skipped + counted, reader never raises); the unknown future major
`manju.qc.verdict/v99` is refused at intake (`record_verdicts` raises
`VerdictError`) AND never surfaced as evidence on read (neither the v2 reader nor
the legacy reader accepts it).

## 6. Documentation validation (`tests/test_fp_docs.py`, 5 tests)

- Parses every `manju <cmd>` backtick span in the README command-table rows
  (79 spans), normalizes (strips `[...]`/`<...>`/`…` placeholders, flags, and
  `/`-alternates, with a shared-group-prefix rescue for `board scene / keyframes`
  style rows), and asserts each resolves to a real command or sub-app in the live
  typer registry. **0 violations** — every README command resolves; the prose
  allowlist is empty. (Negative controls confirm the resolver rejects fake
  top-level commands, fake subcommands, and fake alternates — it is not lenient.)
- The four §8 / 14_21-closeout corrected claims stay true: the **更正块** heading
  is present in `REPORTS/AI_IDE_19_COMPLETION.md`; the README toolmap row calls
  `manju tool` a **resolver** (not a dispatcher); and the code the corrections
  name still exists — the bridge service seam `providers.base.dispatch_bridge`,
  the real `manju bridge plan|run|adopt` commands, the resolver functions
  `toolmap.resolve_tool`/`dry_run_tool`, and the adoption schema
  `manju.qc.assurance/v1` (registered stable).

## 7. Decisions

- **No `manju migrate` machinery this loop.** There is no real pending migration
  (every schema is v1/v2 with no prior-major data in the wild). Registry +
  fixtures + the declared plan is the honest minimum (§16 #20: build for a real
  benefit, not because "another project has it"). The `project.fps` major is
  recorded, not built.
- **No feature-flag runtime plumbing.** Status labels in the registry are the
  governance this loop (§4.8 deferred to a real experimental-gating need).
- **Governance is never an execution input.** `contracts.py` is consumed only by
  tests; the engine does not import it.
- **`manju.qc.assurance/v1` owner = `qc.assurance`** (defines the SCHEMA
  constant); `build.bridge` references it as a consumer. **`manju.shot-draft-
  package/v1` owner = `build.shotpackage`** (defines `PACKAGE_SCHEMA`);
  `build.pullsheet` emits it as a producer.
- **`bible` / `reports-dir` owners point at `Project`** (the module-level class
  that carries `load_bible` / `reports_dir`), since those are methods, not
  module-level symbols — so the owner-imports test stays honest.

## 8. Files added (all additive; no existing file edited)

- `CONTRACTS.yaml`
- `src/manju/core/contracts.py`
- `tests/test_fp_contracts.py`, `tests/test_fp_cli_snapshot.py`,
  `tests/test_fp_docs.py`, `tests/test_fp_compat.py`
- `tests/fixtures/cli_surface.json`
- `tests/fixtures/compat/` (8 fixtures)

Not touched: `cli.py`, all `src/manju` engine modules, `DECISIONS.md`,
`core/timebase.py` + `tests/test_fp_timebase*` (a parallel agent owns those).
README was **not** edited — every listed command already resolves.

## 9. Test results

- Targeted (the four new F0 modules): **39 passed** (contracts 17, cli-snapshot
  6, docs 5, compat 11).
- Full suite: see FP_A_RESULTS.md for the exact run count — 0 failed maintained.
