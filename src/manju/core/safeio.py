"""The ONE owner for validated output publication (audit wave: ledger P0s).

Every command that materializes a file at a caller-chosen path (``--out`` /
``--output`` / id-derived report paths) historically opened the destination
directly (``open(out, "w")`` / ``zipfile.ZipFile(out, "w")``). The merged
external audit demonstrated the consequences on the fixed baseline:

- ``pack --out project.yaml`` truncates project truth before any byte of the
  archive is written (PACK-P0-001), and every sibling command with an output
  option shares the shape (OPENCLAP/DELIVERY/RELINK/BRIDGE/PULLSHEET/
  REFPACK/TEMPLATE/SUPPORT/TASKS P0 entries);
- a predictable temp name (``<out>.tmp`` / ``.relink_tmp``) pre-created as a
  symlink redirects the write outside the project;
- append-mode runtime ledgers (``events.jsonl`` …) follow a symlink or write
  through a hardlink into truth.

This module is the shared fix. Policy (single-user tool, so the enemy is a
foot-gun or an imported hostile tree, not a co-tenant):

1. an output path may live OUTSIDE the project (that is the normal use of
   ``pack --out``); an EXISTING regular file there is replaced atomically —
   explicit user intent, same as every archiver;
2. INSIDE the project an output may only land under the publish subtrees the
   caller names (``exports/``, ``reports/`` …) — truth, imports and config
   can never be named as a destination;
3. the destination leaf is never followed: an existing symlink/junction, a
   directory, or any non-regular file refuses loudly instead of being
   replaced or written through;
4. all bytes go to a sibling ``mkstemp`` temp (unpredictable name, O_EXCL)
   and reach the destination only via atomic replace —失败零写入.

Callers surface :class:`SafeOutError` through their existing ``--json``
error envelopes (the CLI already has the ``bad_out`` precedent).
"""

from __future__ import annotations

import os
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

from .yamlio import _fsync_dir, replace_with_retry

__all__ = [
    "SafeOutError",
    "checked_out_path",
    "publish_bytes",
    "publish_text",
    "publish_tmp",
    "open_append_nofollow",
]


class SafeOutError(RuntimeError):
    """A destination path was refused before anything touched disk.

    ``reason`` is a stable machine token for ``--json`` envelopes; ``str()``
    stays the human diagnostic."""

    def __init__(self, message: str, *, reason: str = "bad_out") -> None:
        super().__init__(message)
        self.reason = reason


def _is_reparse_point(path: Path) -> bool:
    """Windows junction/reparse-point detection (POSIX: always False).
    ``Path.is_symlink`` misses NTFS junctions; ``st_reparse_tag`` (3.8+ on
    Windows) catches them without following."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return bool(getattr(st, "st_reparse_tag", 0))


def _refuse_linked_ancestors(target: Path, boundary: Path) -> None:
    """Refuse when any directory between ``boundary`` and ``target`` is a
    symlink/junction — a linked parent redirects the whole write."""
    for parent in target.parents:
        if parent == boundary or boundary not in parent.parents:
            break
        if parent.is_symlink() or _is_reparse_point(parent):
            raise SafeOutError(
                f"输出路径的上级目录是链接(symlink/junction),拒绝写入: {parent}",
                reason="bad_out",
            )


def checked_out_path(
    out: Path | str,
    *,
    project_root: Path | None = None,
    inside_roots: tuple[str, ...] = ("exports", "reports"),
    kind: str = "输出",
) -> Path:
    """Validate a caller-chosen destination and return it absolute.

    ``project_root``: when given, a destination that resolves INSIDE the
    project must fall under one of ``inside_roots`` (top-level publish
    subtrees); ``.git``/``.manju`` are always refused. A destination outside
    the project is allowed as-is (policy #1 above).

    Refuses (raising :class:`SafeOutError`, nothing written):
    - an existing directory, symlink, junction, or non-regular file at the
      destination;
    - a linked ancestor directory inside the project;
    - inside-project destinations outside the allowed publish subtrees.
    """
    target = Path(out).expanduser()
    try:
        target = Path(os.path.abspath(target))
    except OSError as exc:  # pragma: no cover — pathological name
        raise SafeOutError(f"{kind}路径无效: {out} ({exc})") from exc

    if target.exists() and target.is_dir():
        raise SafeOutError(f"{kind}路径是一个目录,需要文件路径: {target}")
    if target.is_symlink() or _is_reparse_point(target):
        raise SafeOutError(
            f"{kind}路径是链接(symlink/junction),拒绝替换或穿透写入: {target}"
        )
    if target.exists() and not target.is_file():
        raise SafeOutError(f"{kind}路径不是普通文件,拒绝写入: {target}")

    if project_root is not None:
        root = Path(os.path.abspath(Path(project_root)))
        try:
            rel = target.relative_to(root)
        except ValueError:
            rel = None
        if rel is not None:
            _refuse_linked_ancestors(target, root)
            first = rel.parts[0] if rel.parts else ""
            if first in (".git", ".manju") or first not in inside_roots:
                allowed = "、".join(f"{r}/" for r in inside_roots)
                raise SafeOutError(
                    f"{kind}路径落在项目内的 {first or '.'}/ 下 — 项目内只允许写入 "
                    f"{allowed} (拒绝覆盖 truth/导入/配置): {target}",
                    reason="bad_out",
                )
    return target


@contextmanager
def publish_tmp(target: Path) -> Iterator[Path]:
    """Yield a sibling exclusive temp path; atomically replace ``target`` on
    clean exit, delete the temp on any failure (失败零写入).

    The temp name is random (``mkstemp``) and created O_EXCL, so a
    pre-planted link at a predictable name can never capture the write.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".part")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        yield tmp_path
        replace_with_retry(str(tmp_path), target)
        _fsync_dir(target.parent)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def publish_bytes(target: Path, data: bytes) -> None:
    """Atomically publish ``data`` at ``target`` (validated by the caller via
    :func:`checked_out_path`)."""
    with publish_tmp(target) as tmp:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())


def publish_text(target: Path, text: str) -> None:
    """Atomically publish UTF-8 ``text`` at ``target`` (``\\n`` newlines —
    same convention as ``yamlio.atomic_write_text``)."""
    publish_bytes(target, text.replace("\r\n", "\n").encode("utf-8"))


def open_append_nofollow(path: Path) -> IO[bytes]:
    """Open ``path`` for binary append without ever following a link.

    The shared front door for the runtime append ledgers (events / failures /
    recents / library index — the msvcrt lock quartet keeps its OWN locking;
    this only owns the safe open). Refuses:

    - a symlink or junction at the leaf (POSIX enforces via O_NOFOLLOW; the
      lstat pre-check covers platforms without it);
    - a non-regular file (FIFO, device, directory);
    - an existing file with more than one hard link — an append through a
      second name would write into whatever identity the other name pins.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or _is_reparse_point(path):
        raise SafeOutError(f"追加目标是链接(symlink/junction),拒绝写入: {path}")
    try:
        st = os.lstat(path)
    except OSError:
        st = None
    if st is not None and not stat.S_ISREG(st.st_mode):
        # pre-open check: an O_WRONLY open of a FIFO would BLOCK waiting for a
        # reader, so the special-file refusal must happen before os.open.
        raise SafeOutError(f"追加目标不是普通文件,拒绝写入: {path}")
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)  # belt & braces vs a racing FIFO
    flags |= getattr(os, "O_BINARY", 0)  # Windows: keep byte-exact JSONL
    fd = os.open(path, flags, 0o666)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise SafeOutError(f"追加目标不是普通文件,拒绝写入: {path}")
        if st.st_nlink > 1:
            raise SafeOutError(
                f"追加目标有 {st.st_nlink} 个硬链接名 — 可能写穿到另一个文件身份,拒绝: {path}"
            )
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "ab")
