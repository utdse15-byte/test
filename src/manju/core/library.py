"""The PRIVATE asset library (goal item 8, round-S).

A personal, machine-local shelf of reusable footage / images / audio — the DAM
pattern (tags + previews + content-hash dedup), scoped to the user, never
uploaded and never entangled with any one project. It lives OUTSIDE every
project at ``~/.manju/library/`` (override with ``MANJU_LIBRARY``), so the same
b-roll, logo sting or room-tone bed can be pulled into any film.

Layout::

    ~/.manju/library/
      index.json                one catalogue: path, name, tags, added, size, kind, note
      <sha256hex><ext>          content-addressed blobs (dedup by content hash)
      .thumbs/<sha256hex>.jpg    best-effort previews for the GUI page

Disciplines, mirroring the project container (§3):

- content-addressed + dedup: a blob's filename is its full sha256 (extension
  preserved); adding an identical file again just merges tags — bytes are stored
  once;
- the source is sacred: ``add`` COPIES in, never moves or deletes the original;
- ``use`` copies a blob INTO a project (a normal human asset from then on); the
  library stays independent — projects never write back to it;
- ``rm`` is library-side only and gated behind an explicit confirmation; it
  never touches any project.

The index is the single catalogue and is written atomically like every other
truth file (temp + rename). It is also the schema the GUI asset page reads.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .hashing import hash_file
from .yamlio import atomic_write_text, read_json

try:
    import fcntl
except ImportError:  # Windows: no fcntl — the lock degrades to a no-op below
    fcntl = None  # type: ignore[assignment]

# Kind classification by suffix — the same buckets media/preview uses, so a
# library asset and an imported one agree on what "video/image/audio" mean.
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}

INDEX_VERSION = 1


class LibraryError(RuntimeError):
    pass


def library_root() -> Path:
    """The library directory: ``MANJU_LIBRARY`` if set, else ``~/.manju/library``.

    Read at call time (never cached) so a test or a per-invocation override of
    the env var takes effect immediately."""
    override = os.environ.get("MANJU_LIBRARY")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".manju" / "library"


def kind_of(suffix: str) -> str:
    s = suffix.lower()
    if s in VIDEO_EXTS:
        return "video"
    if s in IMAGE_EXTS:
        return "image"
    if s in AUDIO_EXTS:
        return "audio"
    return "other"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _hex(content_hash: str) -> str:
    """The bare hex of a ``sha256:...`` hash (blob filenames + hash8 handles)."""
    return content_hash.removeprefix("sha256:")


_INDEX_LOCK_TIMEOUT_S = 30.0


@contextlib.contextmanager
def _index_lock(root: Path, *, timeout_s: float = _INDEX_LOCK_TIMEOUT_S) -> Iterator[None]:
    """Cross-process mutex around the index read-modify-write (round W, #42).

    ``add``/``remove`` (and any future ``tag``) are read-whole-index → mutate
    → write-whole-index; two processes (two `manju lib add` invocations, or a
    CLI run racing a GUI asset-page action) doing that concurrently can lose
    one side's update — the later writer's snapshot never saw the earlier
    writer's row. ``fcntl.flock`` on a sibling ``index.lock`` serializes the
    whole read-modify-write, so no add/remove/tag is ever computed against a
    stale snapshot.

    Advisory + POSIX-only: on a platform without ``fcntl`` (Windows) this
    degrades to a no-op rather than blocking forever — the same "a missing OS
    primitive degrades, never hangs" stance as the rest of this project
    (§14). ``timeout_s`` bounds the wait so a crashed holder cannot wedge
    every future library command; a timeout is a clear `LibraryError`, not a
    hang.
    """
    root.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        yield
        return
    lock_path = root / "index.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LibraryError(
                        f"library index busy (another process holds {lock_path}) — "
                        f"timed out after {timeout_s:.0f}s; retry once it finishes"
                    )
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


class Library:
    """A private asset library rooted at :func:`library_root` (or an explicit
    path, used by tests). All state is the on-disk blobs + ``index.json``; the
    object holds no cache, so two ``Library`` instances over the same root stay
    coherent."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root).expanduser() if root is not None else library_root()

    # --------------------------------------------------------------- paths

    @property
    def index_path(self) -> Path:
        return self.root / "index.json"

    @property
    def thumbs_dir(self) -> Path:
        return self.root / ".thumbs"

    def blob_path(self, entry: dict[str, Any]) -> Path:
        return self.root / entry["blob"]

    # --------------------------------------------------------------- index io

    def load_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"version": INDEX_VERSION, "assets": []}
        try:
            data = read_json(self.index_path)
        except (OSError, ValueError):
            raise LibraryError(f"library index is unreadable: {self.index_path}")
        if not isinstance(data, dict) or not isinstance(data.get("assets"), list):
            raise LibraryError(f"library index is malformed: {self.index_path}")
        return data

    def _save_index(self, index: dict[str, Any]) -> None:
        import json

        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            self.index_path,
            json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        )

    def assets(self) -> list[dict[str, Any]]:
        return self.load_index()["assets"]

    # --------------------------------------------------------------- lookup

    def find(self, hash8: str) -> dict[str, Any] | None:
        """Resolve a short hash handle (any hex prefix) to its entry. An
        ambiguous prefix — two blobs share it — is an error, not a guess."""
        needle = hash8.strip().removeprefix("sha256:").lower()
        if not needle:
            raise LibraryError("empty hash handle")
        matches = [e for e in self.assets() if _hex(e["hash"]).startswith(needle)]
        if len(matches) > 1:
            ids = ", ".join(_hex(e["hash"])[:12] for e in matches)
            raise LibraryError(f"ambiguous hash {needle!r} — matches: {ids}")
        return matches[0] if matches else None

    def get(self, hash8: str) -> dict[str, Any]:
        entry = self.find(hash8)
        if entry is None:
            raise LibraryError(f"no asset with hash {hash8!r} in the library")
        return entry

    def list_assets(self, *, tag: str | None = None,
                    kind: str | None = None) -> list[dict[str, Any]]:
        out = self.assets()
        if tag is not None:
            out = [e for e in out if tag in (e.get("tags") or [])]
        if kind is not None:
            out = [e for e in out if e.get("kind") == kind]
        return out

    # --------------------------------------------------------------- thumbs

    def _make_thumb(self, blob: Path) -> str | None:
        """Best-effort preview into ``.thumbs/`` (reusing media/preview). The
        blob stem is its content hash, so the thumbnail name is unique. Returns
        the library-relative path or None (ffmpeg absent / unprobeable bytes)."""
        try:
            from ..media.preview import make_preview

            preview = make_preview(blob, self.thumbs_dir)
        except Exception:
            return None
        if preview is None:
            return None
        return (Path(".thumbs") / preview.name).as_posix()

    # --------------------------------------------------------------- add

    def add(self, src: Path | str, *, tags: list[str] | None = None,
            note: str | None = None) -> dict[str, Any]:
        """Copy one file into the library, deduped by content hash.

        Returns ``{"entry": <entry>, "deduped": bool}``. A file already present
        (same content) is NOT re-copied — its tags are merged and ``deduped`` is
        True. The source is never moved or deleted."""
        src = Path(src)
        if not src.is_file():
            raise LibraryError(f"not a file: {src}")
        tags = _clean_tags(tags)
        content_hash = hash_file(src)  # pure read of the SOURCE — no index lock needed yet

        # #42: load → dedup-check → mutate → save is ONE critical section —
        # the whole thing (not just the final write) must run under the
        # index lock, or a concurrent add of the SAME content between our
        # read and our write would race two "new entry" branches into two
        # rows for one asset instead of a dedup merge.
        with _index_lock(self.root):
            index = self.load_index()

            existing = next(
                (e for e in index["assets"] if e["hash"] == content_hash), None
            )
            if existing is not None:
                merged = list(existing.get("tags") or [])
                for t in tags:
                    if t not in merged:
                        merged.append(t)
                existing["tags"] = merged
                if note and not existing.get("note"):
                    existing["note"] = note
                self._save_index(index)
                return {"entry": existing, "deduped": True}

            self.root.mkdir(parents=True, exist_ok=True)
            ext = src.suffix.lower()
            blob_name = _hex(content_hash) + ext
            blob = self.root / blob_name
            if not blob.exists():  # content-addressed: identical bytes, identical name
                shutil.copy2(src, blob)
            entry: dict[str, Any] = {
                "hash": content_hash,
                "blob": blob_name,
                "name": src.name,
                "kind": kind_of(ext),
                "size": blob.stat().st_size,
                "tags": tags,
                "note": note or "",
                "added": _now(),
                "thumb": self._make_thumb(blob),
            }
            index["assets"].append(entry)
            self._save_index(index)
            return {"entry": entry, "deduped": False}

    # --------------------------------------------------------------- remove

    def remove(self, hash8: str) -> dict[str, Any]:
        """Delete one asset from the library (blob + thumbnail + index row).
        Library-side only — no project is ever touched."""
        with _index_lock(self.root):  # #42: same read-modify-write discipline as add()
            index = self.load_index()
            entry = self.find(hash8)
            if entry is None:
                raise LibraryError(f"no asset with hash {hash8!r} in the library")
            blob = self.root / entry["blob"]
            blob.unlink(missing_ok=True)
            if entry.get("thumb"):
                (self.root / entry["thumb"]).unlink(missing_ok=True)
            index["assets"] = [e for e in index["assets"] if e["hash"] != entry["hash"]]
            self._save_index(index)
            return entry


def _clean_tags(tags: list[str] | None) -> list[str]:
    """De-duplicate and trim tags, preserving first-seen order."""
    out: list[str] = []
    for t in tags or []:
        t = t.strip()
        if t and t not in out:
            out.append(t)
    return out
