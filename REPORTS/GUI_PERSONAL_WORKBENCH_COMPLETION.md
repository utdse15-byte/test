# GUI-WAVE-PERSONAL-01 Completion

## 1. Branch and HEAD

- Branch: `claude/gui-personal-workbench-reliability-scale`
- Base: `claude/fable-opus-task-division-wv97i6` @ `46fb0c5`
- This wave HEAD: (see git after commit)

## 2. What shipped

### Project session / window model
- Frozen session (prior) + `already_open` for same path
- Unified `next_action` schema
- Launch: `POST /api/workspace/launch` (argv, no shell)
- CLI: `manju gui <PROJECT> --app --port 0`

### API client
- `webclient.js`: ManjuApiError + requestJson
- Project action dialog (no false “已切换”)
- Static allowlist scan for new raw fetch sites

### Safe quit
- `/api/app/status`, `/api/app/quit`
- Cancel queued; after_current / cancel_running
- closing → 503 on mutating posts
- SPA 退出 button + dialog

### Personal restore
- `/api/ui-state`, `/api/ui-state/draft`
- `~/.manju/gui_state.json` workspaces by project_identity
- localStorage → server migration (best-effort)

### Scale / incremental render
- `ui_rev` on every shot card
- Keyed DOM patch (create/reuse/replace/remove)
- IntersectionObserver lazy media
- `/api/state?perf=1` timings + payload bytes
- `tests/fixtures/make_gui_scale_project.py`
- `scripts/gui_soak.py`

## 3. Test results (this machine)

```text
pytest tests/test_gui.py tests/test_gui_core.py tests/test_workspace.py
      tests/test_jobs_lifecycle.py tests/test_job_cancel.py
      tests/test_fp_gui_endpoints.py tests/test_gui_session_repair.py
      tests/test_gui_project_actions.py tests/test_gui_wave_personal.py
      tests/test_ux_polish.py
→ 282 passed in ~113s
```

## 4. Scale sample

| Size | build_state_ms | payload |
|------|---------------:|--------:|
| 10×5 | ~280 | ~19 KiB |
| 50×5 | ~1265 | ~92 KiB |

## 5. Remaining limits (honest)

- Full 200-shot browser interaction + Windows CI job not re-run in this agent session (local GUI suite green; CI is remote).
- Residual raw `fetch` on specialized pages (upload/stream) still on allowlist.
- App window X close ≠ guaranteed process stop (explicit 退出 button is the supported path).
- Full keyboard a11y audit / review single-item mode polish not fully rewritten (existing review features retained).
- No full `server.py` split this wave (stability over large refactor).

## 6. Merge / rollback

```bash
# merge
git switch claude/fable-opus-task-division-wv97i6
git merge --ff-only claude/gui-personal-workbench-reliability-scale

# rollback branch tip
git reset --hard 46fb0c5
```
