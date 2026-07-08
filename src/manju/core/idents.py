"""Canonical safe-segment validator for ids used as path segments.

Untrusted PROJECTS are a real threat model here (§ threat model): a downloaded
``.manju`` project — or a hand-crafted CLI/MCP/board/GUI argument — must never
be able to smuggle ``../``, an absolute path, a backslash, a leading dot, or a
NUL byte into a path built from a ``shot_id`` / ``provider_id`` / take name /
episode id. Round-W goal items 11/72: ONE validator, wired at every place an
id gets turned into a path segment, instead of each call site inventing its
own (partial, drifting) regex.

The pattern intentionally mirrors the GUI's pre-existing ``_SHOT_ID_RE``
(``^[A-Za-z0-9_-]{1,64}$``) — every legitimate id already shaped like
``S001`` / ``take_03`` / ``my-provider`` keeps validating, byte-identically.
"""

from __future__ import annotations

import re

__all__ = ["SAFE_SEGMENT_PATTERN", "UnsafeIdentifierError", "is_safe_segment", "validate_safe_segment"]

# No ``.`` in the allowed charset at all, so this is inherently immune to
# ``.``/``..`` traversal segments, absolute-path leading ``/``, ``\`` on
# Windows-style inputs, and NUL bytes — all excluded simply by not being in
# the allowed set. Length bounded to a sane filename size.
SAFE_SEGMENT_PATTERN = r"^[A-Za-z0-9_\-]{1,64}$"
_SAFE_SEGMENT_RE = re.compile(SAFE_SEGMENT_PATTERN)


class UnsafeIdentifierError(ValueError):
    """An id destined to become a path segment failed the safe-segment check."""


def is_safe_segment(value: object) -> bool:
    """``True`` iff *value* is a str safe to use as ONE path segment."""
    return isinstance(value, str) and bool(_SAFE_SEGMENT_RE.fullmatch(value))


def validate_safe_segment(value: object, *, label: str = "id") -> str:
    """Return *value* unchanged, or raise :class:`UnsafeIdentifierError` with a
    bilingual explanation naming the offending value and the fix.

    Called at every boundary that turns a user/agent/project-supplied id into
    a filesystem path segment: ``ShotSpec.id``, ``ShotIndex.order`` entries,
    ``Project.shot_path``/``Project.takes_dir``, and the ``providers``
    add/enable/disable/show CLI commands.
    """
    if is_safe_segment(value):
        return value  # type: ignore[return-value]
    raise UnsafeIdentifierError(
        f"{label} 不合法: {value!r} — 只能包含字母、数字、下划线、连字符,长度 1-64;"
        f"不能包含 / \\ .. 空格或以 . 开头(它会被直接拼进文件路径,不受控字符可能"
        f"逃出项目目录)。请改用安全的 {label},例如 S001、take_03、my-provider。"
    )
