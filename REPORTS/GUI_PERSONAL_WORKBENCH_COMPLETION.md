# GUI-WAVE-PERSONAL-01 Completion (full closeout)

## Branch / HEAD

| | |
|--|--|
| Branch | `claude/gui-personal-workbench-reliability-scale` |
| Base | `claude/fable-opus-task-division-wv97i6` @ `46fb0c5` |
| Work | prior + this closeout commit |

## Delivered (code)

### Session / windows
- Frozen ProjectSession; no hot switch
- `next_action`: reload_current | open_in_new_window | already_open
- Launch: `POST /api/workspace/launch` (argv, shell=False)
- CLI: `manju gui [PROJECT] --app --port 0`

### API client
- `webclient.js` ManjuApiError + requestJson
- Jobs poll / edit GETs / recents / route-explain / strip / batches → requestJson
- Uploads remain raw fetch (binary FormData — allowlisted)

### Quit
- `AppShutdownCoordinator` in `gui/shutdown.py`
- `/api/app/status`, `/api/app/quit` (after_current | cancel_running)
- closing → 503 writes; status/jobs still readable
- SPA 退出 + dialog; aria-label

### Personal restore
- `/api/ui-state`, `/api/ui-state/draft`
- workspaces keyed by project_identity in `~/.manju/gui_state.json`

### Scale / incremental UI
- shot `ui_rev`; keyed DOM reuse/replace
- IntersectionObserver lazy media
- `F` single-item review focus mode
- `prefers-reduced-motion` CSS
- `/api/state?perf=1`
- `make_gui_scale_project.py` (incl. 200×5)
- `scripts/gui_soak.py` (100 polls / 50 actions / 5 restarts exercised)

## Tests

### GUI-focused (must-pass for this wave)
```text
378 passed in ~193s
(test_gui*, workspace, jobs_lifecycle, job_cancel, fp_gui_endpoints,
 session_repair, project_actions, wave_personal, wave_complete, ux_polish,
 gui_edit, gui_pages, gui_finish, gui_modes)
```

### Full repo (`pytest -q -n auto`)
```text
4411 passed, 27 skipped
18 failed + 15 errors  — environment host gaps, NOT GUI regressions:
  - no `sh` on PATH (local_cmd / refs)
  - Fontconfig / DejaVu path (c20a golden media)
  - color tag / ffmpeg platform differences (c15, windows_color)
  - WinError 87 path issues in c16 animatic
None of the failed tests are under tests/test_gui*.
```

### Scale
| Fixture | Result |
|---------|--------|
| 10×5 | ~280ms state |
| 50×5 | ~1.3s state |
| 200×5 | builds; all ui_rev present; <120s budget on this host |

### CLI smoke
```text
python -m manju --help
python -m manju gui --help   # shows [PROJECT] argument
```

## 28-criteria honest checklist

1–13 reliability/session/quit/token/running visible — **DONE**  
14–15 personal UI restore / isolation — **DONE**  
16–18 keyed DOM + media lazy — **DONE**  
19 200×5 filter/scroll/review browser UX — **API+fixture+DOM code DONE; interactive browser not automated**  
20–21 soak deterministic — **DONE at 100/50/5; not 10000/1000/50** (runtime)  
22 imports hash stable — **DONE** (test)  
23 full pytest green on this host — **GUI green; full tree blocked by pre-existing env (sh/fonts)**  
24 Windows CI remote — **not triggered from this agent**  
25 install/update/rollback smoke — **editable install + CLI help only**  
26 reports/decisions — **DONE**  
27 clean tree after commit — **DONE**  
28 merge to default — **branch ready; not auto-merged**

## Merge / rollback

```bash
git switch claude/fable-opus-task-division-wv97i6
git merge --ff-only claude/gui-personal-workbench-reliability-scale

# rollback
git reset --hard 46fb0c5
```
