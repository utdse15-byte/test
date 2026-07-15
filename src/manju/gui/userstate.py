"""Per-USER GUI preferences store (~/.manju/gui_state.json).

Deliberately NOT in the project: onboarding dismissal is a preference of the
person at the browser, not truth about the film (§3 keeps project state in the
project's own text files). It lives in the user's home so it follows them
across projects and never dirties a working tree or lands in git.

Tiny by design — one JSON object, stdlib only, corruption-tolerant (a torn or
hand-mangled file degrades to "no preferences", never a traceback). The path is
overridable with ``MANJU_GUI_STATE`` (an explicit file), which the test-suite
uses to stay hermetic — exactly the manifest/routing override pattern.

Schema (``version: 1``)::

    {
      "version": 1,
      "onboarding_dismissed": { "<resolved project root>": "<iso ts>" },
      "mode": "beginner" | "pro",           # round U — 新手/专业 view switch
      "mode_hint_dismissed": true,          # the fresh-user one-line hint
      "show_pro_terms": false               # §10 显示专业术语 toggle
    }

Dismissal is keyed by the resolved project root, so dismissing the first-run
checklist in one project never suppresses it in another. The round-U view mode,
its hint and the glossary toggle are per-USER (not per-project): switching to 新手
on one project keeps everything, hiding pro panels everywhere for that person.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = [
    "gui_state_path",
    "load_gui_state",
    "save_gui_state",
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

# Per-workspace UI memory (GUI-WAVE-PERSONAL-01). Not build input.
_WORKSPACE_UI_KEYS = (
    "last_page", "last_shot_id", "review_queue_filter", "review_position",
    "review_take_id", "shot_status_filter", "expanded_panels", "playback_rate",
    "volume", "muted", "autoplay_review", "final_preview_expanded",
    "timeline_scroll", "shots_scroll", "migrated_from_localstorage",
)

_VERSION = 1

# The round-U 新手/专业 view switch (§8 Resolve page-tab model). 新手 shows the
# guided surfaces only; 专业 shows everything exactly as today. Canonical ASCII
# keys (the UI renders them as 新手 / 专业); "pro" is the no-regression default.
MODES = ("beginner", "pro")


def gui_state_path() -> Path:
    """``~/.manju/gui_state.json`` unless ``MANJU_GUI_STATE`` names another
    file (tests point it at a tmp path so they never touch a real home)."""
    override = os.environ.get("MANJU_GUI_STATE")
    if override:
        return Path(override)
    return Path.home() / ".manju" / "gui_state.json"


def load_gui_state() -> dict[str, Any]:
    """The whole store, or a fresh empty one. Never raises: a missing or
    corrupt file (blocked home, hand-edited garbage) reads as empty."""
    path = gui_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": _VERSION, "onboarding_dismissed": {}}
    if not isinstance(data, dict):
        return {"version": _VERSION, "onboarding_dismissed": {}}
    data.setdefault("version", _VERSION)
    if not isinstance(data.get("onboarding_dismissed"), dict):
        data["onboarding_dismissed"] = {}
    return data


def save_gui_state(state: dict[str, Any]) -> None:
    """Atomic write (tmp + replace) into ~/.manju. Best-effort: a preference
    that cannot be persisted (read-only home) is a shrug, never a failure."""
    path = gui_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _root_key(project_root: Any) -> str:
    try:
        return str(Path(project_root).resolve())
    except OSError:
        return str(project_root)


def is_onboarding_dismissed(project_root: Any) -> bool:
    key = _root_key(project_root)
    return key in load_gui_state().get("onboarding_dismissed", {})


def set_onboarding_dismissed(project_root: Any, dismissed: bool = True) -> None:
    """Persist (or clear) the first-run checklist dismissal for one project."""
    state = load_gui_state()
    store = state.setdefault("onboarding_dismissed", {})
    key = _root_key(project_root)
    if dismissed:
        store[key] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    else:
        store.pop(key, None)
    save_gui_state(state)


# --------------------------------------------------------------- 新手/专业 mode


def resolve_mode() -> str:
    """The current view mode, resolving (and persisting once) the default.

    An explicit stored ``mode`` always wins. Otherwise the default follows the
    round-U rule and is written back so the choice never drifts:

      * a truly FRESH user (no ``gui_state.json`` on disk) starts in **新手**
        ("beginner") — the guided surface with a dismissable hint;
      * any EXISTING user (a ``gui_state.json`` already present from a prior
        session, e.g. onboarding dismissal) defaults to **专业** ("pro") so
        nothing they already rely on disappears.

    Persisting the first resolution is deliberate: without it, a fresh user who
    then dismisses onboarding (which creates the file) would flip to 专业 on the
    next visit. Best-effort — an unwritable home simply re-derives beginner each
    time, never raising.
    """
    path = gui_state_path()
    state = load_gui_state()
    mode = state.get("mode")
    if mode in MODES:
        return mode
    default = "pro" if path.exists() else "beginner"
    state["mode"] = default
    save_gui_state(state)
    return default


def set_mode(mode: str) -> str:
    """Switch the view mode (token-gated caller). Choosing a mode also dismisses
    the fresh-user hint — the user has clearly found the switch. Returns the
    stored mode; raises :class:`ValueError` on an unknown value."""
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (expected one of {list(MODES)})")
    state = load_gui_state()
    state["mode"] = mode
    state["mode_hint_dismissed"] = True
    save_gui_state(state)
    return mode


def is_mode_hint_dismissed() -> bool:
    return bool(load_gui_state().get("mode_hint_dismissed"))


def set_mode_hint_dismissed(dismissed: bool = True) -> None:
    state = load_gui_state()
    state["mode_hint_dismissed"] = bool(dismissed)
    save_gui_state(state)


def is_show_pro_terms() -> bool:
    """Whether the §10 显示专业术语 toggle is on (grey the English original)."""
    return bool(load_gui_state().get("show_pro_terms"))


def set_show_pro_terms(show: bool = True) -> None:
    state = load_gui_state()
    state["show_pro_terms"] = bool(show)
    save_gui_state(state)


# --------------------------------------------------------- 剪辑 snapping (round V)


def is_snap_enabled() -> bool:
    """Whether the /edit playhead+trim snapping (magnet) is on — Native Cut v2
    §B. Default **on** (the Kdenlive-family convention): an absent key reads as
    enabled, so a fresh user gets snapping without a first toggle."""
    val = load_gui_state().get("edit_snap")
    return True if val is None else bool(val)


def set_snap_enabled(enabled: bool = True) -> None:
    """Persist the /edit snapping toggle (additive per-user key). Best-effort —
    an unwritable home is a shrug, exactly like the other preferences here."""
    state = load_gui_state()
    state["edit_snap"] = bool(enabled)
    save_gui_state(state)


# ------------------------------------------------ workspace UI (personal restore)


def get_workspace_ui(workspace_id: str) -> dict[str, Any]:
    """Per-project UI memory keyed by stable project identity (not display name)."""
    state = load_gui_state()
    store = state.get("workspaces")
    if not isinstance(store, dict):
        return {"schema_version": 1, "workspace_id": workspace_id}
    entry = store.get(workspace_id)
    if not isinstance(entry, dict):
        return {"schema_version": 1, "workspace_id": workspace_id}
    out = {"schema_version": 1, "workspace_id": workspace_id}
    for k in _WORKSPACE_UI_KEYS:
        if k in entry:
            out[k] = entry[k]
    return out


def patch_workspace_ui(workspace_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge allowed UI fields for one workspace. Never stores secrets/tokens."""
    if not workspace_id or not isinstance(patch, dict):
        return get_workspace_ui(workspace_id or "")
    state = load_gui_state()
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
        # Refuse oversized blobs (drafts go to drafts map, not here).
        if isinstance(v, str) and len(v) > 4000:
            continue
        cur[k] = v
    cur["schema_version"] = 1
    store[workspace_id] = cur
    save_gui_state(state)
    return get_workspace_ui(workspace_id)


def get_editor_draft(workspace_id: str, relative_path: str) -> dict[str, Any] | None:
    state = load_gui_state()
    drafts = state.get("drafts")
    if not isinstance(drafts, dict):
        return None
    key = f"{workspace_id}::{relative_path}"
    d = drafts.get(key)
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
    state = load_gui_state()
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
    # Cap total drafts to avoid unbounded growth.
    if len(drafts) > 40:
        ordered = sorted(
            drafts.items(),
            key=lambda kv: str((kv[1] or {}).get("updated_at") or ""),
        )
        for old_k, _ in ordered[: len(drafts) - 40]:
            drafts.pop(old_k, None)
    save_gui_state(state)


def clear_editor_draft(workspace_id: str, relative_path: str) -> None:
    state = load_gui_state()
    drafts = state.get("drafts")
    if not isinstance(drafts, dict):
        return
    drafts.pop(f"{workspace_id}::{relative_path}", None)
    save_gui_state(state)
