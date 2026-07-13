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

__all__ = [
    "SAFE_SEGMENT_PATTERN",
    "UnsafeIdentifierError",
    "is_safe_segment",
    "validate_safe_segment",
    "WINDOWS_RESERVED_NAMES",
    "windows_segment_problems",
    "windows_relpath_problems",
    "windows_collision_key",
]

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


# --------------------------------------------------------------------- W1
# Windows-lexical path rules (MANJU_WINDOWS_ONLY_LEAN_V3 §3.2). Free-form
# project-relative paths (media files, path_hints, uploads) legitimately carry
# spaces, CJK and dots — SAFE_SEGMENT is far too strict for them — but Windows
# still refuses a specific lexical family: reserved device names (CON.txt is
# unopenable with ANY extension, any case), NTFS alternate data streams
# (``:`` in a segment), and trailing dot/space (Win32 silently strips them,
# so ``take.`` and ``take`` become the SAME file). These predicates are the
# ONE owner every intake gate (`shotpackage`, GUI upload/new-project, pack
# member extraction) and `manju check` consults — never re-implement per site.

# CON/PRN/AUX/NUL/COM1..COM9/LPT1..LPT9 — reserved for the STEM before the
# first dot, case-insensitively ("CON", "con.txt", "nul.tar.gz" all refuse).
WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def windows_segment_problems(segment: str) -> list[str]:
    """Windows-lexical problems of ONE path segment (spelling preserved in the
    messages — display case is truth, only the COMPARISON folds, §3.2)."""
    problems: list[str] = []
    if not segment:
        problems.append("空路径段(empty segment)")
        return problems
    stem = segment.split(".", 1)[0]
    if stem.upper() in WINDOWS_RESERVED_NAMES:
        problems.append(
            f"{segment!r}: Windows 保留设备名(reserved device name {stem.upper()})"
            " — Windows 上无法创建/打开该文件"
        )
    if ":" in segment:
        problems.append(
            f"{segment!r}: 含冒号(NTFS ADS/驱动器语义)— Windows 上会被解析为数据流"
        )
    if segment != segment.rstrip(" ."):
        problems.append(
            f"{segment!r}: 以点/空格结尾 — Win32 会静默剥离,得到另一个文件名"
        )
    if "\x00" in segment:
        problems.append(f"{segment!r}: 含 NUL 字节")
    return problems


def windows_relpath_problems(rel: object) -> list[str]:
    """Windows-lexical problems of a PROJECT-RELATIVE path (POSIX style is the
    §3 storage contract): backslashes, UNC/absolute roots, ``..`` traversal,
    plus every per-segment rule above. Empty list == portable. This is a
    LEXICAL check only — containment (symlink/junction escapes) stays owned by
    ``Project.resolve``; the two are complementary, not alternatives."""
    if not isinstance(rel, str) or not rel:
        return ["路径必须是非空字符串(project-relative POSIX path)"]
    problems: list[str] = []
    if "\\" in rel:
        problems.append(f"{rel!r}: 含反斜杠 — 项目内只保存 POSIX 风格相对路径")
    normalized = rel.replace("\\", "/")
    if normalized.startswith("//"):
        problems.append(f"{rel!r}: UNC 路径 — 拒绝网络共享根")
    if normalized.startswith("/"):
        problems.append(f"{rel!r}: 绝对路径 — 项目内只保存相对路径")
    if re.match(r"^[A-Za-z]:", normalized):
        problems.append(f"{rel!r}: 盘符绝对路径 — 项目内只保存相对路径")
        # the drive-letter colon is already reported; don't double-report it
        normalized = normalized[2:]
    segments = [s for s in normalized.split("/") if s != ""]
    if not segments:
        problems.append(f"{rel!r}: 没有有效路径段")
    for segment in segments:
        if segment == ".":
            continue
        if segment == "..":
            problems.append(f"{rel!r}: 含 '..' 上溯段 — 拒绝越界")
            continue
        problems.extend(windows_segment_problems(segment))
    return problems


def windows_collision_key(rel: str) -> str:
    """The §3.2 comparison key: Windows filenames are case-insensitive, so two
    stored paths whose keys match WILL collide on NTFS even though Linux kept
    them apart. ``str.casefold`` approximates NTFS's upcase folding closely
    enough for collision DETECTION (never for display — spelling is truth)."""
    return rel.replace("\\", "/").casefold()
