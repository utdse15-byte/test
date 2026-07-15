# Continuous residual scan (C22–C54, 2026-07-15)

## Themes closed this session slice

### Cancel honesty
- ComfyUI /history poll → `ProviderCanceled` (C22)
- ASR async poll cancel (C23) → later `ProviderCanceled` (C30)
- Single voice / redo cancelable + wired (C24/C25)
- Cancelable kind **name** honesty: `ingest`, `series_sync_bible`, `edit_preview_batch` (C26)
- TTS cancel as `ProviderCanceled` + locale maps to `BuildCanceled` (C27)
- voice_batch mid-poll (C28); Edge TTS mid-stream (C29)
- Non-cancelable running jobs show「运行中不可中途取消」(C30)
- voice_preview cancel (C31); local_cmd subprocess cancel (C32)
- export packaging `cancel_scope` (C33); repair `cancel_scope` (C34)
- run_qc batch cancel (C46–C48); build QC phase cancel (C47)
- handle_rebuild cancel (C51/C54); roundtrip row cancel (C52)

### Locale / multi-lang honesty
- GUI QC `lang` no base fallback (C37); QC button prefers locale final (C38)
- GUI build `lang` (C39); build panel language select (C40–C41)
- Job strip ·lang chip (C42); build retry preserves lang (C43)
- MCP build validates lang (C44)
- Declared `locales` in state (C45)
- next_step `build_locale` (C49); cockpit hero honors it (C50)

### Windows / proxy honesty
- Loopback `default_transport` bypasses HTTP_PROXY (C35)
- reachability_probe loopback no-proxy (C36)

## Still open (next cycles)
- series_new_episode still non-cancelable mid-create (usually short)
- Multi-lang QC when >1 locale_finals still picks first sorted only
- job_cancel ffmpeg subprocess WinError flakes on some hosts (env)
- Paid cloud TTS cancel still may bill remote job (§8.1 documented)

## Later cycles (C55–C69 themes)
- Locale hero/build plan lang (C55); roundtrip/run_qc functional pins
- MCP assume_yes + WaitingUser ToolError (C59–C61)
- GUI WaitingUser honesty for redo/voice/batch/lab/retry/handle_rebuild (C62–C66)
- Spend banner multi-kind + project-switch dismiss (C67–C69)
- 109 commits since continuous base 81d6206; loop continues

## Evidence
- Cycle pin tests C22–C54 suite: 64+ passed in batch
- Broader suite snapshots: 147–165 passed (cycle* + bugfix + GUI subset)
- Continuous ledger: `REPORTS/CONTINUOUS_CYCLE_2026-07-15.md`


## Cycles 90-91 (MCP cancel honesty)
- MCP build/redo map BuildCanceled/ProviderCanceled → ToolError canceled
