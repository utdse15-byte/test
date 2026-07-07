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
      "onboarding_dismissed": { "<resolved project root>": "<iso ts>" }
    }

Dismissal is keyed by the resolved project root, so dismissing the first-run
checklist in one project never suppresses it in another.
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
]

_VERSION = 1


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
