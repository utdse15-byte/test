"""History + rollback over the project's own records (P2 §10).

Design stance: git IS the patch engine (v2.2 §3) — this module never invents
a second version store. It gives the two records that already exist a
user-facing surface:

- **history**: events.jsonl (who did what) merged with the project's git log
  (what changed on disk), one timeline, newest last.
- **snapshot**: a labeled git commit of the truth text — the cheap checkpoint
  a rollback can return to.
- **rollback shot**: re-select the previously selected take. Takes are
  append-only, so this is pure bookkeeping — nothing is deleted, the newer
  take stays available for compare.
- **rollback file**: a guarded `git checkout <ref> -- <path>` scoped to ONE
  truth-text file. Media, renders and exports are never touched (they are
  derived or human assets, and they are not in git anyway — §3 .gitignore).

Every rollback is itself an event: history only ever grows.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .container import Project
from .events import append_event, tail_events


class HistoryError(RuntimeError):
    """Clean one-line failures for the CLI (FIX-D envelope)."""


# Truth-text files rollback may restore: project-relative, text-only. Media
# (imports are human assets, gen is append-only), renders and exports are
# derived — restoring those via git would either lose human work or lie about
# what was rendered, so they are refused outright.
_ROLLBACK_PREFIXES = ("story/", "shots/", "bible/", "timeline/", "captions/")
_ROLLBACK_FILES = ("project.yaml",)
_ROLLBACK_SUFFIXES = {".yaml", ".yml", ".md", ".srt", ".json"}
_REFUSED_PREFIXES = ("media/", "renders/", "exports/", ".manju/")


def _git(project: Project, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=project.root, capture_output=True, text=True
    )


def _require_repo(project: Project) -> None:
    if not (project.root / ".git").exists():
        raise HistoryError(
            "this project is not a git repository — git is the patch engine "
            "(§3); run `git init` in the project root to enable snapshots/rollback"
        )
    if _git(project, "rev-parse", "--git-dir").returncode != 0:
        raise HistoryError("git is unavailable or the repository is unreadable")


def _identity_args(project: Project) -> list[str]:
    """`git commit` needs an identity; supply a project-local default only
    when the user has none configured (theirs always wins)."""
    email = _git(project, "config", "user.email")
    if email.returncode == 0 and email.stdout.strip():
        return []
    return ["-c", "user.name=manju", "-c", "user.email=manju@local"]


# ------------------------------------------------------------------ snapshot


def snapshot(project: Project, label: str = "") -> dict[str, Any]:
    """Stage everything git tracks per .gitignore and commit it, labeled.
    Returns {sha, label, clean} — clean=True means there was nothing to
    commit (that is a no-op, not an error: the checkpoint already exists)."""
    _require_repo(project)
    _git(project, "add", "-A")
    staged = _git(project, "diff", "--cached", "--quiet")
    if staged.returncode == 0:  # nothing staged → tree already checkpointed
        head = _git(project, "rev-parse", "--short", "HEAD")
        sha = head.stdout.strip() if head.returncode == 0 else ""
        return {"sha": sha, "label": label, "clean": True}
    # Record the event BEFORE committing so the commit contains it and the
    # tree is clean afterwards (an after-commit event would re-dirty it and
    # make back-to-back snapshots never converge). The sha lives in git's own
    # half of the history feed.
    append_event(project.root, _actor(), "snapshot", {"label": label})
    _git(project, "add", "-A")
    message = f"manju snapshot: {label}" if label else "manju snapshot"
    commit = _git(project, *_identity_args(project), "commit", "-q", "-m", message)
    if commit.returncode != 0:
        raise HistoryError(f"git commit failed — {commit.stderr.strip().splitlines()[-1]}")
    sha = _git(project, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"sha": sha, "label": label, "clean": False}


# ------------------------------------------------------------------- history


@dataclass
class HistoryRow:
    ts: str  # ISO-8601 (events) or epoch-derived ISO (git)
    source: str  # "event" | "git"
    actor: str  # human | ai | engine | git
    text: str
    detail: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"ts": self.ts, "source": self.source, "actor": self.actor,
                "text": self.text, "detail": self.detail}


def history(project: Project, n: int = 30) -> list[dict[str, Any]]:
    """The merged change feed, oldest→newest, last ``n`` rows: every
    events.jsonl record (actor-attributed actions) interleaved with the git
    log (committed disk state). Read-only."""
    rows: list[HistoryRow] = []
    for ev in tail_events(project.root, n=10_000):
        detail = ev.get("detail") or {}
        summary = ", ".join(f"{k}={v}" for k, v in list(detail.items())[:3])
        rows.append(HistoryRow(
            ts=str(ev.get("ts", "")), source="event",
            actor=str(ev.get("actor", "?")),
            text=f"{ev.get('action', '?')}" + (f" ({summary})" if summary else ""),
            detail=detail,
        ))
    if (project.root / ".git").exists():
        log = _git(project, "log", "--pretty=%h\x1f%cI\x1f%s", "-n", "200")
        if log.returncode == 0:
            for line in log.stdout.splitlines():
                parts = line.split("\x1f")
                if len(parts) == 3:
                    sha, ts, subject = parts
                    rows.append(HistoryRow(ts=ts, source="git", actor="git",
                                           text=subject, detail={"sha": sha}))
    rows.sort(key=lambda r: r.ts)
    return [r.as_dict() for r in rows[-n:]]


# ------------------------------------------------------------ rollback: shot


def rollback_shot(project: Project, shot_id: str) -> dict[str, Any]:
    """Re-select the take that was selected before the current one, using the
    select/rollback_shot events as the record. Append-only: the newer take
    stays on disk for compare; the change is one line of text plus an event."""
    if shot_id not in project.shot_ids():
        raise HistoryError(f"unknown shot '{shot_id}'")
    shot = project.load_shot(shot_id)
    current = (shot.status.selected_take or "") if shot.status else ""

    picks = [
        str(ev["detail"].get("take"))
        for ev in tail_events(project.root, n=10_000)
        if ev.get("action") in ("select", "rollback_shot")
        and (ev.get("detail") or {}).get("shot") == shot_id
        and (ev.get("detail") or {}).get("take")
    ]
    previous = next((t for t in reversed(picks) if t != current), None)
    if previous is None:
        takes = [t.name for t in project.takes(shot_id)]
        raise HistoryError(
            f"{shot_id} has no earlier selection on record to roll back to — "
            f"pick one explicitly: manju select {shot_id} <take>"
            + (f" (takes: {', '.join(takes)})" if takes else "")
        )
    if project.get_take(shot_id, previous) is None:
        raise HistoryError(
            f"the previously selected take '{previous}' no longer exists on disk"
        )
    project.update_shot_raw(
        shot_id, lambda d: d.setdefault("status", {}).__setitem__("selected_take", previous)
    )
    append_event(project.root, _actor(), "rollback_shot",
                 {"shot": shot_id, "take": previous, "was": current})
    return {"shot": shot_id, "take": previous, "was": current}


# ------------------------------------------------------------ rollback: file


def rollback_file(project: Project, relpath: str, ref: str = "HEAD") -> dict[str, Any]:
    """Restore ONE truth-text file from git — `git checkout <ref> -- <path>`
    with guardrails. Never touches media/renders/exports. Runs `manju check`
    semantics afterwards via the caller (the CLI reports findings)."""
    _require_repo(project)
    rel = relpath.replace("\\", "/").lstrip("./")
    if ".." in rel.split("/"):
        raise HistoryError("path escapes the project — use a project-relative path")
    if any(rel.startswith(p) for p in _REFUSED_PREFIXES):
        raise HistoryError(
            f"'{rel}' is media/derived output — rollback only restores truth "
            "text (story/, shots/, bible/, timeline/, captions/, project.yaml)"
        )
    if not (rel in _ROLLBACK_FILES or any(rel.startswith(p) for p in _ROLLBACK_PREFIXES)):
        raise HistoryError(
            f"'{rel}' is outside the rollback surface (story/, shots/, bible/, "
            "timeline/, captions/, project.yaml)"
        )
    if Path(rel).suffix.lower() not in _ROLLBACK_SUFFIXES:
        raise HistoryError(f"'{rel}' is not a text truth file")

    shown = _git(project, "cat-file", "-e", f"{ref}:{rel}")
    if shown.returncode != 0:
        raise HistoryError(f"'{rel}' does not exist at {ref} — nothing to restore")
    restored = _git(project, "checkout", ref, "--", rel)
    if restored.returncode != 0:
        raise HistoryError(
            f"git checkout failed — {restored.stderr.strip().splitlines()[-1]}"
        )
    append_event(project.root, _actor(), "rollback_file", {"path": rel, "ref": ref})
    return {"path": rel, "ref": ref}


def _actor() -> str:
    import os

    return os.environ.get("MANJU_ACTOR", "human")
