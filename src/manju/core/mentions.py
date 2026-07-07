"""@mention system (round U, goal item 6).

Parse ``@<id-or-alias>`` handles out of shot free-text and story prose, resolve
them against the asset matrix (``core/assets.py``), and — on explicit request —
REGISTER the resolved character/scene mentions into a shot's real fields.

DESIGN DECISION — mentions are a REGISTRATION AID, not a hidden runtime binding.
There is deliberately NO code path where an @mention silently injects a reference
or a character into a build. ``manju mentions --check`` only reports;
``manju mentions --apply`` writes resolved character/scene mentions into the
shot's registered fields (``shot.characters`` / ``shot.scene``) through the
normal spec write path (``Project.update_shot_raw`` — truth is text, §3), and
NEVER through a locked field: a locked ``characters``/``scene`` is left untouched
and an advisory is emitted instead (write a proposal, §5). The build itself never
reads mentions — once applied, the shot's ordinary fields carry the binding, so
staleness/locks/QC all work exactly as they always have.

Parsing rules (:func:`find_mentions`):

- a mention is ``@`` immediately followed by one or more id chars
  (``[\\w\\-]`` — Unicode word chars, so CJK ids/aliases work, plus ``-``);
- it is terminated by whitespace, punctuation, or another ``@``;
- ``@`` glued to the RIGHT of a word char is NOT a mention (so
  ``name@example.com`` never yields ``@example``) — UNLESS it chains directly
  off a preceding mention's token, which is the "another ``@`` terminates and
  restarts" rule (``@a@b`` yields both ``a`` and ``b``).

:func:`find_mentions` and :func:`resolve_mentions` are deterministic pure
functions; the ``apply``/report helpers below take a :class:`Project`.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from .assets import build_lookup
from .container import Project

# One or more id chars after @. ``\w`` is Unicode (CJK ids/aliases); ``-`` is
# allowed inside an id (e.g. ``future-coin``); ``@`` and everything else (space,
# CJK/ASCII punctuation) terminates the token.
_MENTION_RE = re.compile(r"@([\w\-]+)", re.UNICODE)
_WORDISH_RE = re.compile(r"[\w\-]", re.UNICODE)


@dataclass(frozen=True)
class Mention:
    """One parsed ``@handle``. ``span`` is ``(start, end)`` of the whole match
    INCLUDING the ``@``; ``raw`` is the handle text WITHOUT the ``@``."""

    span: tuple[int, int]
    raw: str

    @property
    def at(self) -> int:
        return self.span[0]


def find_mentions(text: str) -> list[Mention]:
    """Every ``@<id-or-alias>`` in ``text``, left to right, non-overlapping.

    Pure and deterministic. Filters the email/handle-glued-to-a-word false
    positive while honoring the ``@a@b`` chain rule (see the module docstring)."""
    if not text:
        return []
    out: list[Mention] = []
    prev_end = -1
    for m in _MENTION_RE.finditer(text):
        start = m.start()
        preceded_by_word = start > 0 and bool(_WORDISH_RE.match(text[start - 1]))
        chains = start == prev_end  # glued to the previous accepted mention's end
        if start == 0 or not preceded_by_word or chains:
            out.append(Mention(span=(start, m.end()), raw=m.group(1)))
            prev_end = m.end()
    return out


def resolve_mentions(
    text: str, matrix: dict[str, Any]
) -> tuple[list[tuple[Mention, str, str]], list[Mention]]:
    """Resolve every mention in ``text`` against the asset ``matrix``.

    Returns ``(resolved, unresolved)`` where ``resolved`` is a list of
    ``(mention, kind, asset_id)`` (collision resolved by kind priority
    character > scene > prop > voice > style) and ``unresolved`` is the list of
    mentions that matched no id or alias. Pure and deterministic given a matrix.
    """
    lookup = build_lookup(matrix)
    resolved: list[tuple[Mention, str, str]] = []
    unresolved: list[Mention] = []
    for mention in find_mentions(text):
        hit = lookup.resolve(mention.raw)
        if hit is None:
            unresolved.append(mention)
        else:
            kind, asset_id = hit
            resolved.append((mention, kind, asset_id))
    return resolved, unresolved


def nearest_ids(token: str, matrix: dict[str, Any], n: int = 3) -> list[str]:
    """Up to ``n`` ids/aliases in the matrix closest to ``token`` — the QC /
    CLI hint for an unresolved @mention. Empty when the matrix has no names."""
    lookup = build_lookup(matrix)
    if not lookup.all_names:
        return []
    # difflib close matches, then a substring pass for CJK where ratio is coarse.
    close = difflib.get_close_matches(token, lookup.all_names, n=n, cutoff=0.5)
    if len(close) < n:
        for name in lookup.all_names:
            if name in close:
                continue
            if token and (token in name or name in token):
                close.append(name)
            if len(close) >= n:
                break
    return close[:n]


# --------------------------------------------- shot free-text field extraction


def shot_mention_fields(shot: Any) -> list[tuple[str, str]]:
    """``(field_path, text)`` for every free-text shot field @mentions are read
    from: action (main/emotion), the dialogue line, and each quality
    must_show/avoid entry. ``continuity.locks`` is deliberately excluded — it
    follows the ``prop:``/``character:`` lock grammar, not @mentions."""
    out: list[tuple[str, str]] = []
    if shot.action.main:
        out.append(("action.main", shot.action.main))
    if shot.action.emotion:
        out.append(("action.emotion", shot.action.emotion))
    if shot.dialogue.text:
        out.append(("dialogue.text", shot.dialogue.text))
    for i, t in enumerate(shot.quality.must_show):
        if isinstance(t, str) and t:
            out.append((f"quality.must_show[{i}]", t))
    for i, t in enumerate(shot.quality.avoid):
        if isinstance(t, str) and t:
            out.append((f"quality.avoid[{i}]", t))
    return out


def shot_mention_text(shot: Any) -> str:
    """All of a shot's mention-bearing free text joined for a single scan."""
    return "\n".join(t for _, t in shot_mention_fields(shot))


def _is_registered(shot: Any, kind: str, asset_id: str) -> bool | None:
    """Is a resolved mention already reflected in the shot's registered fields?
    ``True``/``False`` for character/scene; ``None`` for kinds --apply does not
    touch (prop/voice/style — there is no shot field to register them into)."""
    if kind == "character":
        return asset_id in shot.characters
    if kind == "scene":
        return shot.scene == asset_id
    return None


# ----------------------------------------------------------------- reporting


def mention_report(
    project: Project, matrix: dict[str, Any], shot_id: str | None = None
) -> dict[str, Any]:
    """A read-only, JSON-serializable report of every mention in the project's
    shots (and, when ``shot_id`` is None, its story prose too). Never writes."""
    shots = [shot_id] if shot_id else project.shot_ids()
    per_shot: list[dict[str, Any]] = []
    for sid in shots:
        try:
            shot = project.load_shot(sid)
        except Exception:
            continue
        resolved, unresolved = resolve_mentions(shot_mention_text(shot), matrix)
        per_shot.append({
            "shot": sid,
            "resolved": [
                {"raw": m.raw, "kind": k, "asset": a,
                 "registered": _is_registered(shot, k, a)}
                for m, k, a in resolved
            ],
            "unresolved": [
                {"raw": m.raw, "nearest": nearest_ids(m.raw, matrix)}
                for m in unresolved
            ],
        })
    story = [] if shot_id else _story_report(project, matrix)
    return {"shots": per_shot, "story": story}


def _story_report(project: Project, matrix: dict[str, Any]) -> list[dict[str, Any]]:
    """Mentions found in ``story/*.md`` and ``story/imports/*.{md,txt}`` — the
    only text-file convention this project has (no ``script/`` dir; see
    container.py). Report-only: prose has no registration target, so --apply
    never touches these files."""
    out: list[dict[str, Any]] = []
    story_dir = project.root / "story"
    if not story_dir.exists():
        return out
    files = sorted(story_dir.glob("*.md"))
    imports = story_dir / "imports"
    if imports.exists():
        files += sorted(p for p in imports.iterdir()
                        if p.is_file() and p.suffix.lower() in (".md", ".txt"))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        resolved, unresolved = resolve_mentions(text, matrix)
        if not resolved and not unresolved:
            continue
        out.append({
            "file": project.relpath(f),
            "resolved": [{"raw": m.raw, "kind": k, "asset": a}
                         for m, k, a in resolved],
            "unresolved": [{"raw": m.raw} for m in unresolved],
        })
    return out


# ------------------------------------------------------------------- apply


def apply_to_shot(
    project: Project, shot_id: str, matrix: dict[str, Any]
) -> dict[str, Any]:
    """Register a single shot's resolved character/scene mentions into
    ``shot.characters`` / ``shot.scene`` through the normal spec write path.

    Respects locks: a locked ``characters``/``scene`` is never written — it is
    reported under ``skipped`` with a proposal hint. Scene is set only when
    empty (a shot already anchored to a different scene is left as truth and the
    mention is skipped, first-mention-wins among several). Deterministic; writes
    at most once. Returns what was added/set/skipped/unresolved.
    """
    shot = project.load_shot(shot_id)
    resolved, unresolved = resolve_mentions(shot_mention_text(shot), matrix)
    locked_paths = set(shot.locked or {})

    to_add_chars: list[str] = []
    set_scene: str | None = None
    skipped: list[dict[str, str]] = []

    for m, kind, aid in resolved:
        if kind == "character":
            if aid in shot.characters or aid in to_add_chars:
                continue
            if "characters" in locked_paths:
                skipped.append({"mention": m.raw, "kind": kind, "asset": aid,
                                "reason": "characters 字段被锁定,未写入(改用 proposals/)"})
                continue
            to_add_chars.append(aid)
        elif kind == "scene":
            if shot.scene == aid or set_scene == aid:
                continue
            if "scene" in locked_paths:
                skipped.append({"mention": m.raw, "kind": kind, "asset": aid,
                                "reason": "scene 字段被锁定,未写入(改用 proposals/)"})
                continue
            if shot.scene:
                skipped.append({"mention": m.raw, "kind": kind, "asset": aid,
                                "reason": f"scene 已登记为 {shot.scene},不覆盖"})
                continue
            if set_scene is not None:
                skipped.append({"mention": m.raw, "kind": kind, "asset": aid,
                                "reason": f"本次已选择 scene={set_scene},忽略其余场景提及"})
                continue
            set_scene = aid
        # prop/voice/style resolve fine but have no shot field to register into;
        # they surface in the report, never auto-written (design decision).

    changed = bool(to_add_chars) or set_scene is not None
    if changed:
        def mutate(d: dict[str, Any]) -> None:
            if to_add_chars:
                chars = d.get("characters")
                if not isinstance(chars, list):
                    chars = []
                for a in to_add_chars:
                    if a not in chars:
                        chars.append(a)
                d["characters"] = chars
            if set_scene is not None:
                d["scene"] = set_scene

        project.update_shot_raw(shot_id, mutate)

    return {
        "shot": shot_id,
        "changed": changed,
        "added_characters": to_add_chars,
        "set_scene": set_scene,
        "skipped": skipped,
        "unresolved": [m.raw for m in unresolved],
    }


def apply_mentions(
    project: Project, matrix: dict[str, Any], shot_id: str | None = None
) -> list[dict[str, Any]]:
    """Apply resolved character/scene mentions across every shot (or one shot).
    Story prose is never written (no registration target). Per-shot isolation:
    a broken shot yields an ``error`` entry, the rest still process."""
    shots = [shot_id] if shot_id else project.shot_ids()
    results: list[dict[str, Any]] = []
    for sid in shots:
        try:
            results.append(apply_to_shot(project, sid, matrix))
        except Exception as exc:  # one broken shot never sinks the batch
            results.append({"shot": sid, "error": str(exc), "changed": False})
    return results
