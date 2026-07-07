"""Read-mostly git surface scoped to a project root (§1-②).

Git IS the patch engine: truth is text, every decision — a dialogue tweak, a
lock, a take promotion — is a reviewable one-line diff, and ``manju new`` runs
``git init`` (see :mod:`manju.core.container`). Nothing so far exposed that
history to a non-git user; this module is the window the GUI (and any other
non-terminal surface) reads it through.

Read-mostly by design: :func:`commit_all` is the ONLY write. There is
deliberately NO checkout / reset / revert here — destructive history surgery
stays in a real terminal with a human at the keyboard, the same doctrine that
keeps ``unlock`` and ``gc --hard`` off the agent/MCP surface (§5). "Undo" at
this layer means *see the diff, commit or don't* — never rewriting history.

Contract shared by every helper:

- ``root: Path`` is always the first argument (the project root).
- git is invoked as ``["git", "-C", str(root), ...]`` via :func:`subprocess.run`
  with ``capture_output=True, text=True, encoding="utf-8"`` — never
  ``shell=True``.
- A missing git binary (:func:`shutil.which` gate), a ``root`` that is not a
  repository, or any nonzero git exit returns the documented "unavailable"
  value (``False`` / ``None`` / ``[]`` / ``ok=False``) — these helpers never
  raise.
- Nothing here ever pushes, pulls, or touches repository config
  (:func:`commit_all` sets its identity with per-invocation ``-c`` overrides).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .yamlio import atomic_write_text

__all__ = [
    "is_repo",
    "identity_args",
    "repo_status",
    "diff_text",
    "file_log",
    "commit_all",
    "install_check_hook",
]

# Cap on the number of entries repo_status returns in "files". Module-level
# (rather than a hidden literal) so tests can monkeypatch it instead of
# staging 500+ files.
_FILE_CAP = 500

# git log field/record separators: ASCII unit/record separators cannot appear
# in hashes, ISO dates, or (sanely) author names/subjects, unlike "|" or tabs.
_LOG_PRETTY = "format:%h%x1f%aI%x1f%an%x1f%s%x1e"


def _run(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run ``git -C root *args``; None when git itself cannot be executed."""
    if shutil.which("git") is None:
        return None
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:  # git vanished between which() and exec, or similar
        return None


def _one_line(proc: subprocess.CompletedProcess[str]) -> str:
    """Collapse a command's stderr+stdout into a single display line."""
    return " ".join((proc.stderr + " " + proc.stdout).split())


def is_repo(root: Path) -> bool:
    """True only when ``root`` IS the top of its git work tree.

    Being merely *inside* a work tree is not enough: a project directory
    without its own ``.git`` that happens to live under some OUTER repository
    (a monorepo, the user's home repo, an unpacked archive) must read as "no
    repo" — otherwise every function here would silently operate on the
    enclosing tree: status listing foreign files, diff serving content from
    OUTSIDE the project over the GUI's /api/git/diff, and commit_all staging
    the whole outer repository. False also covers: git binary missing,
    ``root`` nonexistent, bare repos.
    """
    proc = _run(root, "rev-parse", "--show-toplevel")
    if proc is None or proc.returncode != 0:
        return False
    top = proc.stdout.strip()
    if not top:
        return False
    try:
        return Path(top).resolve() == Path(root).resolve()
    except OSError:
        return False


def identity_args(root: Path, actor: str = "human") -> list[str]:
    """The per-invocation ``-c user.*`` overrides for a manju-made commit.

    ONE identity policy, shared by every manju commit surface (this module's
    :func:`commit_all` and :mod:`manju.core.history`'s ``snapshot``): the
    human's own configured git identity always WINS — when ``git config
    user.email`` yields anything we return ``[]`` and let git use it, so a
    snapshot in a developer's repo carries their real name. Only when NO
    identity is configured (the common no-config CI/fresh-repo case) do we
    inject a ``manju-<actor>`` / ``<actor>@manju.local`` default so a commit
    never fails for lack of identity and stays attributable to the actor that
    made it. These are ``-c`` overrides for a single ``git`` invocation;
    repository and global config are NEVER modified.
    """
    email = _run(root, "config", "user.email")
    if email is not None and email.returncode == 0 and email.stdout.strip():
        return []  # the human's configured identity wins
    return ["-c", f"user.name=manju-{actor}", "-c", f"user.email={actor}@manju.local"]


def _safe_rel(root: Path, path: str | None) -> str | None:
    """Validate a user-supplied pathspec: must stay inside ``root``.

    Returns the path to pass to git, or raises ValueError on escape —
    callers translate that into their documented "unavailable" value.
    """
    if path is None:
        return None
    candidate = Path(path)
    resolved = (Path(root) / candidate).resolve() if not candidate.is_absolute() \
        else candidate.resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise ValueError(f"pathspec escapes the project: {path}")
    return path


def repo_status(root: Path) -> dict | None:
    """Snapshot of the working tree, or None when git/repo is unavailable.

    Returns::

        {"branch": str | None,   # None when HEAD is detached
         "dirty": bool,          # any staged/unstaged/untracked entry
         "files": [{"path": str, "status": str}],  # status = porcelain XY
         "ahead": int, "behind": int,              # vs upstream, 0 if none
         "truncated": bool}      # True when files was capped at _FILE_CAP

    Parsed from ``git status --porcelain=v1 -z --branch``: ``-z`` keeps CJK
    and space-containing paths verbatim (no C-quoting), branch/ahead/behind
    come from the leading ``## `` record, and rename/copy entries — which in
    ``-z`` mode carry the origin path as a SECOND NUL-separated field — are
    consumed as a pair (``path`` is the new name).
    """
    if not is_repo(root):
        return None
    proc = _run(root, "status", "--porcelain=v1", "-z", "--branch")
    if proc is None or proc.returncode != 0:
        return None

    records = proc.stdout.split("\0")

    branch: str | None = None
    ahead = behind = 0
    start = 0
    if records and records[0].startswith("## "):
        start = 1
        header = records[0][3:]
        if header == "HEAD (no branch)":
            branch = None  # detached HEAD
        elif header.startswith("No commits yet on "):
            branch = header[len("No commits yet on "):]
        elif header.startswith("Initial commit on "):  # pre-2.15 wording
            branch = header[len("Initial commit on "):]
        else:
            branch = header.split("...", 1)[0]
        if m := re.search(r"\[ahead (\d+)", header):
            ahead = int(m.group(1))
        if m := re.search(r"behind (\d+)\]", header):
            behind = int(m.group(1))

    files: list[dict] = []
    i = start
    while i < len(records):
        rec = records[i]
        i += 1
        if not rec:
            continue  # trailing empty field after the final NUL
        if len(rec) < 4 or rec[2] != " ":
            continue  # defensive: not an "XY path" entry
        xy, path = rec[:2], rec[3:]
        if ("R" in xy or "C" in xy) and i < len(records):
            i += 1  # rename/copy: swallow the origin-path field
        files.append({"path": path, "status": xy})

    dirty = bool(files)
    truncated = len(files) > _FILE_CAP
    if truncated:
        files = files[:_FILE_CAP]

    return {
        "branch": branch,
        "dirty": dirty,
        "files": files,
        "ahead": ahead,
        "behind": behind,
        "truncated": truncated,
    }


def diff_text(
    root: Path,
    path: str | None = None,
    *,
    staged: bool = False,
    max_bytes: int = 200_000,
) -> str | None:
    """Unified diff of the working tree (or one ``path``) as text.

    ``staged=True`` diffs the index (``--cached``) instead of the worktree.
    Output larger than ``max_bytes`` (measured in UTF-8 bytes) is cut at a
    character boundary and given a ``"\\n… (truncated)"`` tail. A clean tree
    yields ``""``; None means git/repo unavailable.
    """
    if not is_repo(root):
        return None
    try:
        path = _safe_rel(root, path)
    except ValueError:
        return None
    args = ["diff", "--no-color"]
    if staged:
        args.append("--cached")
    if path is not None:
        args += ["--", path]
    proc = _run(root, *args)
    if proc is None or proc.returncode != 0:
        return None
    text = proc.stdout
    raw = text.encode("utf-8")
    if len(raw) > max_bytes:
        text = raw[:max_bytes].decode("utf-8", errors="ignore") + "\n… (truncated)"
    return text


def file_log(root: Path, path: str | None = None, n: int = 20) -> list[dict]:
    """Last ``n`` commits (optionally only those touching ``path``).

    Returns ``[{"hash": short, "ts": iso-8601 author date, "author": str,
    "subject": str}]``, newest first. Fields/records are separated with the
    control characters ``%x1f``/``%x1e`` so subjects containing ``|``, tabs
    or CJK punctuation parse safely. ``[]`` when git/repo is unavailable or
    the repository has no commits yet.
    """
    if not is_repo(root):
        return []
    try:
        path = _safe_rel(root, path)
    except ValueError:
        return []
    args = ["log", f"--pretty={_LOG_PRETTY}", "-n", str(n)]
    if path is not None:
        args += ["--", path]
    proc = _run(root, *args)
    if proc is None or proc.returncode != 0:
        return []
    entries: list[dict] = []
    for record in proc.stdout.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        fields = record.split("\x1f")
        if len(fields) != 4:
            continue  # defensive: never let one odd record break the list
        commit_hash, ts, author, subject = fields
        entries.append(
            {"hash": commit_hash, "ts": ts, "author": author, "subject": subject}
        )
    return entries


def commit_all(root: Path, message: str, *, actor: str = "human") -> dict:
    """``git add -A`` + ``git commit -m message`` — the module's only write.

    Identity comes from the shared :func:`identity_args` policy (the human's
    configured git identity wins; otherwise a per-invocation
    ``manju-<actor> <actor@manju.local>`` default), supplied via
    ``-c user.name`` / ``-c user.email`` for this one invocation; repository
    and global config are never modified. NEVER pushes.

    Returns ``{"ok": bool, "hash": str | None, "error": str | None}``:
    - blank ``message`` -> ``ok False, error "empty message"`` (no git run),
    - git missing / not a repo / nothing to commit -> ``ok False`` with git's
      stderr+stdout collapsed to one line as ``error``,
    - success -> ``ok True`` with the new short hash.
    """
    if not message.strip():
        return {"ok": False, "hash": None, "error": "empty message"}
    if not is_repo(root):
        # containment, not convenience: `git add -A` from a project nested
        # inside an OUTER repo would stage the entire enclosing tree
        return {"ok": False, "hash": None,
                "error": "project root is not its own git repository"}

    add = _run(root, "add", "-A")
    if add is None:
        return {"ok": False, "hash": None, "error": "git unavailable"}
    if add.returncode != 0:
        return {"ok": False, "hash": None, "error": _one_line(add) or "git add failed"}

    commit = _run(
        root,
        *identity_args(root, actor),
        "commit", "-m", message,
    )
    if commit is None:
        return {"ok": False, "hash": None, "error": "git unavailable"}
    if commit.returncode != 0:
        error = _one_line(commit) or f"git commit failed (exit {commit.returncode})"
        return {"ok": False, "hash": None, "error": error}

    head = _run(root, "rev-parse", "--short", "HEAD")
    commit_hash = (
        head.stdout.strip() if head is not None and head.returncode == 0 else None
    )
    return {"ok": True, "hash": commit_hash, "error": None}


# ---------------------------------------------------------------- pre-commit hook

# Marker line stamping a pre-commit hook as ours. install_check_hook keys its
# "may I write here?" decision on this exact line: present -> we own the file
# and a rewrite is idempotent; absent -> a foreign hook we must never clobber.
_HOOK_MARKER = "# installed by manju (§1-②): truth cannot be committed broken"
_CHECK_HOOK_SCRIPT = f"#!/bin/sh\n{_HOOK_MARKER}\nexec manju check\n"


def install_check_hook(root: Path) -> Path | None:
    """Install a ``.git/hooks/pre-commit`` gate that runs ``manju check`` (§1-②).

    Git IS the patch engine (§1-②): truth is text and history is the record of
    every decision — so broken truth must never *enter* history in the first
    place. This is the husky / pre-commit ecosystem pattern applied to a manju
    project: a portable ``sh`` hook (mode ``0o755``) that fails the commit when
    ``manju check`` fails::

        #!/bin/sh
        # installed by manju (§1-②): truth cannot be committed broken
        exec manju check

    Opt-in ONLY — never installed silently. It is wired to
    ``manju new --check-hook`` and is the kind of thing a future
    ``manju doctor`` would *suggest*, never impose.

    Refuses (returns ``None``) when:

    - ``root`` is not its OWN git repository (:func:`is_repo` is False) — the
      same containment doctrine as the rest of this module: there is nothing to
      hook into, and we never reach up into an enclosing tree's ``.git``;
    - a pre-commit hook already exists and is NOT ours (its text lacks
      :data:`_HOOK_MARKER`): the human's own hook always wins and is left
      byte-for-byte untouched.

    Re-installing over OUR marker is idempotent (the script is rewritten and its
    path returned). Returns the hook path on success, else ``None``; never
    raises.
    """
    if not is_repo(root):
        return None
    hook_path = Path(root) / ".git" / "hooks" / "pre-commit"
    if hook_path.exists():
        try:
            existing = hook_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        if _HOOK_MARKER not in existing:
            return None  # foreign hook — never clobber
    try:
        atomic_write_text(hook_path, _CHECK_HOOK_SCRIPT)
        hook_path.chmod(0o755)
    except OSError:
        return None
    return hook_path
