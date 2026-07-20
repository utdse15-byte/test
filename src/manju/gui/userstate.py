"""Per-USER GUI preferences store (~/.manju/gui_state.json).

Deliberately NOT project truth. Multi-window safe: every mutation goes through
:func:`update_gui_state` with a process-local RLock plus a cross-process
exclusive lock file covering the full read-modify-write cycle.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

__all__ = [
    "gui_state_path",
    "load_gui_state",
    "save_gui_state",
    "update_gui_state",
    "is_onboarding_dismissed",
    "set_onboarding_dismissed",
    "MODES",
    "resolve_mode",
    "set_mode",
    "is_mode_hint_dismissed",
    "set_mode_hint_dismissed",
    "is_show_pro_terms",
    "set_show_pro_terms",
    "is_snap_enabled",
    "set_snap_enabled",
    "get_workspace_ui",
    "patch_workspace_ui",
    "get_editor_draft",
    "set_editor_draft",
    "clear_editor_draft",
]

_log = logging.getLogger("manju.gui.userstate")

# Per-workspace UI memory (GUI-WAVE-PERSONAL-01). Not build input.
_WORKSPACE_UI_KEYS = (
    "last_page", "last_shot_id", "review_queue_filter", "review_position",
    "review_take_id", "shot_status_filter", "expanded_panels", "playback_rate",
    "volume", "muted", "autoplay_review", "final_preview_expanded",
    "timeline_scroll", "shots_scroll", "migrated_from_localstorage",
)

_VERSION = 1
MODES = ("beginner", "pro")

_THREAD_LOCK = threading.RLock()
_LOCK_STALE_S = 30.0
_LOCK_WAIT_S = 8.0


def gui_state_path() -> Path:
    """``~/.manju/gui_state.json`` unless ``MANJU_GUI_STATE`` names another file."""
    override = os.environ.get("MANJU_GUI_STATE")
    if override:
        return Path(override)
    return Path.home() / ".manju" / "gui_state.json"


def _lock_path() -> Path:
    p = gui_state_path()
    return p.with_name(p.name + ".lock")


def _steal_stale_lock(lock_path: Path) -> None:
    """Remove a stale lock under a short-lived STEAL MUTEX (`.steal` O_EXCL).

    A blind `unlink(); continue` let two windows both observe the same stale
    lock and race: A unlinks + re-creates, then B's unlink deletes A's FRESH
    lock — dual ownership of a store that promises multi-window safety. Only
    the steal-mutex holder may unlink, and it re-checks staleness inside the
    mutex, so a freshly minted lock can never be deleted."""
    steal = lock_path.with_name(lock_path.name + ".steal")
    try:
        fd = os.open(str(steal), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - steal.stat().st_mtime > _LOCK_STALE_S:
                steal.unlink(missing_ok=True)  # a dead stealer's leftovers
        except OSError:
            pass
        return  # someone else is mid-steal — let the outer loop re-poll
    except OSError:
        return
    try:
        try:
            if time.time() - lock_path.stat().st_mtime > _LOCK_STALE_S:
                lock_path.unlink(missing_ok=True)
        except OSError:
            pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            steal.unlink(missing_ok=True)
        except OSError:
            pass


def _acquire_cross_process_lock(timeout: float = _LOCK_WAIT_S) -> int:
    """Exclusive lock via O_EXCL create. Returns open fd (caller must close)."""
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, f"{os.getpid()}\n".encode("ascii", errors="replace"))
            except OSError:
                pass
            return fd
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > _LOCK_STALE_S:
                    _steal_stale_lock(lock_path)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"gui_state lock busy: {lock_path} (another Manju window?)")
            time.sleep(0.03)


def _release_cross_process_lock(fd: int) -> None:
    lock_path = _lock_path()
    try:
        os.close(fd)
    except OSError:
        pass
    try:
        # Owner check: if THIS process was suspended past _LOCK_STALE_S, a
        # sibling window may have legitimately stolen and re-minted the lock —
        # unlinking blindly would release *their* lock and re-open the race.
        content = lock_path.read_text(encoding="ascii", errors="replace").strip()
        if content and content != str(os.getpid()):
            return
    except OSError:
        pass
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def _quarantine_corrupt(path: Path, exc: Exception) -> dict[str, Any]:
    """Back a REALLY corrupt store aside and start fresh. A transient OSError
    must never land here — treating "briefly unreadable" (antivirus/backup
    holding the file on Windows) as corruption renamed the whole store away,
    and the next save then overwrote every draft/preference with defaults."""
    try:
        bak = path.with_name(
            f"{path.name}.corrupt.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}")
        path.replace(bak)
        _log.warning("gui_state corrupt; backed up to %s (%s)", bak, exc)
    except OSError:
        _log.warning("gui_state unparseable: %s", exc)
    return {"version": _VERSION, "onboarding_dismissed": {}}


def _load_unlocked() -> dict[str, Any]:
    path = gui_state_path()
    try:
        raw = _read_with_retry(path)
    except FileNotFoundError:
        return {"version": _VERSION, "onboarding_dismissed": {}}  # first run
    except UnicodeDecodeError as exc:  # non-UTF-8 bytes ARE corruption
        return _quarantine_corrupt(path, exc)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return _quarantine_corrupt(path, exc)
    if not isinstance(data, dict):
        return {"version": _VERSION, "onboarding_dismissed": {}}
    data.setdefault("version", _VERSION)
    if not isinstance(data.get("onboarding_dismissed"), dict):
        data["onboarding_dismissed"] = {}
    return data


def _read_with_retry(path: Path, tries: int = 3, delay_s: float = 0.05) -> str:
    """Strict-UTF-8 read with a short retry for TRANSIENT OSErrors (Windows
    sharing violations). FileNotFoundError propagates immediately (first run);
    a persistent OSError propagates after the retries — the caller's
    read-modify-write then fails WITHOUT clobbering the store with defaults."""
    last: OSError | None = None
    for i in range(tries):
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise
        except OSError as exc:
            last = exc
            if i + 1 < tries:
                time.sleep(delay_s)
    assert last is not None
    raise last


def _save_unlocked(state: dict[str, Any]) -> None:
    path = gui_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique tmp name avoids multi-window collisions on .tmp
    tmp = path.with_name(
        f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    payload = json.dumps(state, ensure_ascii=False, indent=2) + "\n"
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        _log.warning("gui_state save failed: %s", exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def load_gui_state() -> dict[str, Any]:
    """Read the whole store (no mutation). Never raises."""
    try:
        with _THREAD_LOCK:
            try:
                fd = _acquire_cross_process_lock(timeout=2.0)
            except TimeoutError:
                # Best-effort unlocked read if another window holds the lock briefly.
                return _load_unlocked()
            try:
                return _load_unlocked()
            finally:
                _release_cross_process_lock(fd)
    except Exception as exc:
        _log.warning("load_gui_state failed: %s", exc)
        return {"version": _VERSION, "onboarding_dismissed": {}}


def save_gui_state(state: dict[str, Any]) -> None:
    """Full replace write under lock. Prefer :func:`update_gui_state` for RMW."""
    try:
        with _THREAD_LOCK:
            fd = _acquire_cross_process_lock()
            try:
                _save_unlocked(state)
            finally:
                _release_cross_process_lock(fd)
    except Exception as exc:
        _log.warning("save_gui_state failed: %s", exc)


def update_gui_state(mutator: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Atomic read-modify-write under thread + cross-process lock."""
    with _THREAD_LOCK:
        fd = _acquire_cross_process_lock()
        try:
            state = _load_unlocked()
            mutator(state)
            _save_unlocked(state)
            return state
        finally:
            _release_cross_process_lock(fd)


def _root_key(project_root: Any) -> str:
    try:
        return str(Path(project_root).resolve())
    except OSError:
        return str(project_root)


def is_onboarding_dismissed(project_root: Any) -> bool:
    key = _root_key(project_root)
    return key in load_gui_state().get("onboarding_dismissed", {})


def set_onboarding_dismissed(project_root: Any, dismissed: bool = True) -> None:
    key = _root_key(project_root)

    def mut(state: dict[str, Any]) -> None:
        store = state.setdefault("onboarding_dismissed", {})
        if not isinstance(store, dict):
            store = {}
            state["onboarding_dismissed"] = store
        if dismissed:
            store[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        else:
            store.pop(key, None)

    try:
        update_gui_state(mut)
    except Exception as exc:
        _log.warning("set_onboarding_dismissed failed: %s", exc)


def resolve_mode() -> str:
    path = gui_state_path()
    state = load_gui_state()
    mode = state.get("mode")
    if mode in MODES:
        return mode
    default = "pro" if path.exists() else "beginner"

    def mut(s: dict[str, Any]) -> None:
        if s.get("mode") not in MODES:
            s["mode"] = default

    try:
        update_gui_state(mut)
    except Exception as exc:
        _log.warning("resolve_mode persist failed: %s", exc)
        return default
    return default


def set_mode(mode: str) -> str:
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (expected one of {list(MODES)})")

    def mut(s: dict[str, Any]) -> None:
        s["mode"] = mode
        s["mode_hint_dismissed"] = True

    update_gui_state(mut)
    return mode


def is_mode_hint_dismissed() -> bool:
    return bool(load_gui_state().get("mode_hint_dismissed"))


def set_mode_hint_dismissed(dismissed: bool = True) -> None:
    def mut(s: dict[str, Any]) -> None:
        s["mode_hint_dismissed"] = bool(dismissed)

    try:
        update_gui_state(mut)
    except Exception as exc:
        _log.warning("set_mode_hint_dismissed failed: %s", exc)


def is_show_pro_terms() -> bool:
    return bool(load_gui_state().get("show_pro_terms"))


def set_show_pro_terms(show: bool = True) -> None:
    def mut(s: dict[str, Any]) -> None:
        s["show_pro_terms"] = bool(show)

    try:
        update_gui_state(mut)
    except Exception as exc:
        _log.warning("set_show_pro_terms failed: %s", exc)


def is_snap_enabled() -> bool:
    val = load_gui_state().get("edit_snap")
    return True if val is None else bool(val)


def set_snap_enabled(enabled: bool = True) -> None:
    def mut(s: dict[str, Any]) -> None:
        s["edit_snap"] = bool(enabled)

    try:
        update_gui_state(mut)
    except Exception as exc:
        _log.warning("set_snap_enabled failed: %s", exc)


def get_workspace_ui(workspace_id: str) -> dict[str, Any]:
    state = load_gui_state()
    store = state.get("workspaces")
    if not isinstance(store, dict):
        return {"schema_version": 1, "workspace_id": workspace_id}
    entry = store.get(workspace_id)
    if not isinstance(entry, dict):
        return {"schema_version": 1, "workspace_id": workspace_id}
    out: dict[str, Any] = {"schema_version": 1, "workspace_id": workspace_id}
    for k in _WORKSPACE_UI_KEYS:
        if k in entry:
            out[k] = entry[k]
    return out


def patch_workspace_ui(workspace_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    if not workspace_id or not isinstance(patch, dict):
        return get_workspace_ui(workspace_id or "")

    def mut(state: dict[str, Any]) -> None:
        store = state.setdefault("workspaces", {})
        if not isinstance(store, dict):
            store = {}
            state["workspaces"] = store
        cur = store.get(workspace_id)
        if not isinstance(cur, dict):
            cur = {}
        for k, v in patch.items():
            if k not in _WORKSPACE_UI_KEYS:
                continue
            if isinstance(v, str) and len(v) > 4000:
                continue
            cur[k] = v
        cur["schema_version"] = 1
        store[workspace_id] = cur

    try:
        update_gui_state(mut)
    except Exception as exc:
        _log.warning("patch_workspace_ui failed: %s", exc)
    return get_workspace_ui(workspace_id)


def get_editor_draft(workspace_id: str, relative_path: str) -> dict[str, Any] | None:
    state = load_gui_state()
    drafts = state.get("drafts")
    if not isinstance(drafts, dict):
        return None
    d = drafts.get(f"{workspace_id}::{relative_path}")
    return d if isinstance(d, dict) else None


def set_editor_draft(
    workspace_id: str,
    relative_path: str,
    *,
    expected_rev: str | None,
    draft_text: str,
) -> None:
    if not workspace_id or not relative_path:
        return
    if len(draft_text) > 200_000:
        draft_text = draft_text[:200_000]

    def mut(state: dict[str, Any]) -> None:
        drafts = state.setdefault("drafts", {})
        if not isinstance(drafts, dict):
            drafts = {}
            state["drafts"] = drafts
        key = f"{workspace_id}::{relative_path}"
        drafts[key] = {
            "workspace_id": workspace_id,
            "relative_file_path": relative_path,
            "expected_rev": expected_rev,
            "draft_text": draft_text,
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if len(drafts) > 40:
            ordered = sorted(
                drafts.items(),
                key=lambda kv: str((kv[1] or {}).get("updated_at") or ""),
            )
            for old_k, _ in ordered[: len(drafts) - 40]:
                drafts.pop(old_k, None)

    try:
        update_gui_state(mut)
    except Exception as exc:
        _log.warning("set_editor_draft failed: %s", exc)


def clear_editor_draft(workspace_id: str, relative_path: str) -> None:
    def mut(state: dict[str, Any]) -> None:
        drafts = state.get("drafts")
        if isinstance(drafts, dict):
            drafts.pop(f"{workspace_id}::{relative_path}", None)

    try:
        update_gui_state(mut)
    except Exception as exc:
        _log.warning("clear_editor_draft failed: %s", exc)
