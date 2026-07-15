# GUI Personal Workbench Baseline

- Date: 2026-07-15
- Branch start: `claude/fable-opus-task-division-wv97i6` @ `46fb0c5`
- Work branch: `claude/gui-personal-workbench-reliability-scale`
- Task: GUI-WAVE-PERSONAL-01

## Scope already landed before this wave (46fb0c5)

Prior session implemented session freeze, `Job.project_id`, runner lifecycle,
`webclient`/`project_action`, launch, quit APIs. This wave extends with
ui_rev/keyed DOM, personal UI state, scale fixtures, soak, and metrics.

## GUI module line counts (approx, start of wave)

| File | Lines |
|------|------:|
| server.py | ~5764 |
| page.py | ~5175 |
| pages.py | ~2200 |
| edit.py | ~2584 |
| jobs.py | ~535 |
| state.py | ~335 |
| workspace.py | ~382 |
| glossary.py | ~373 |

## Project open / create / close (current contract)

| Scenario | Result |
|----------|--------|
| Unbound open path | `project_bound` + `reload_current` |
| Bound open same path | `already_open` (no change) |
| Bound open other | 409 `project_session_immutable` + `open_in_new_window` |
| Bound new project | 201 `project_created` + `open_in_new_window` |
| Unbound new | 201 `project_created_and_bound` + `reload_current` |
| Switch | always 409 immutable |
| Launch | `POST /api/workspace/launch` argv, shell=False |
| Quit | `POST /api/app/quit` after_current \| cancel_running |

## Project / job APIs

- Project: `/api/workspace/open|new|launch|recents`, `/api/new-project`, `/api/switch`, `/api/projects`, `/api/project-id`
- Jobs: `/api/jobs`, `/api/jobs/cancel`, `/api/jobs/retry`, build/redo/voice* posts
- App: `/api/app/status`, `/api/app/quit`
- Personal: `/api/ui-state`, `/api/ui-state/draft`

## Frontend fetch inventory

Owner: `webclient.py` (`requestJson` / `ManjuApiError`).

Allowlisted residual raw `fetch` (upload/stream/poll/fallback):
page, common_js, glossary, workspace, ingest/lab/create/exports/series/pages/pages_t/edit/storyboard/director.

## localStorage keys (SPA)

- `manju-ui-<projectIdentity>` — filter / panel prefs
- `manju-last-<projectIdentity>` — last page trail
- `manju-reviewed-<projectIdentity>` — unread triage
- Legacy: `manju-reviewed-<projectName>` (migrated)

Server durable store: `~/.manju/gui_state.json` workspaces map (via `MANJU_GUI_STATE` in tests).

## Media preload (after wave)

- Selected take video: `preload=metadata`, src set
- Off-screen non-selected: `preload=none`, lazy src via IntersectionObserver
- Images: `loading=lazy`; non-selected use data-URI placeholder until observed

## Shot DOM strategy (after wave)

Keyed by `shot.id` + `ui_rev`. Reuse node when rev matches; replace only changed cards; preserve scroll/focus/playback where possible.

## Scale measurements (this machine, lightweight PNG takes)

| Shots × takes | build_state_ms | payload_bytes |
|--------------:|---------------:|--------------:|
| 10 × 5 | ~280 | ~19 KiB |
| 50 × 5 | ~1265 | ~92 KiB |

200-shot runs are supported by the fixture; measure on demand with:

```text
python tests/fixtures/make_gui_scale_project.py <dir> --shots 200 --takes-per-shot 5
GET /api/state?perf=1
```

## Tests

GUI-related suites targeted this wave: session repair, project actions, wave personal, gui, workspace, jobs lifecycle, ux polish.

## Architecture pins

- Text is truth; UI state is disposable user prefs
- One process = one frozen ProjectSession = one JobRunner
- No shell=True launch; no paid providers in tests
