"""`manju appearances` (goal item 11) — a read-only cross-reference of every
bible id against the shots that use it.

Three kinds of bible entry are *referenced by a shot field*, so those are the
ones this report walks:

- **scene**     ← ``shot.scene`` (one per shot)
- **character** ← ``shot.characters`` (a list)
- **prop**      ← ``shot.continuity.locks`` entries prefixed ``prop:`` (the
  §4.1 grammar: ``locks: [character:linxia, scene, prop:future_coin]``)

``style`` and ``voices`` are consumed globally (style) or via a character's
voice-shaping fields (voices), never through a shot field, so they are left out
of the appearance/orphan walk on purpose.

This never mutates anything and never duplicates ``manju check``: a *missing*
scene/character reference is already a check error, so it is only surfaced here
for context (with a pointer at check). A missing *prop* reference is genuinely
new information — ``continuity.locks`` is a free-form list the schema does not
validate — so it is worth reporting on its own.
"""

from __future__ import annotations

from typing import Any

from .container import BIBLE_FILES, Project
from .yamlio import read_yaml

# The bible kinds a shot can reference, paired with the file each lives in.
_REFERENCED_KINDS = (("characters", "characters"), ("scenes", "scenes"), ("props", "props"))


def _bible_by_file(project: Project) -> dict[str, dict[str, dict[str, Any]]]:
    """Per-file id -> entry maps (unmerged), so orphans keep their kind."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for fname in BIBLE_FILES:
        path = project.root / "bible" / f"{fname}.yaml"
        data = read_yaml(path) or {} if path.exists() else {}
        out[fname] = (
            {k: v for k, v in data.items() if isinstance(v, dict)}
            if isinstance(data, dict)
            else {}
        )
    return out


def _prop_refs(shot_raw: dict[str, Any]) -> list[str]:
    """Prop ids referenced from continuity.locks (``prop:<id>`` entries)."""
    continuity = shot_raw.get("continuity")
    locks = continuity.get("locks") if isinstance(continuity, dict) else None
    refs: list[str] = []
    for entry in locks or []:
        if isinstance(entry, str) and entry.startswith("prop:"):
            pid = entry.split(":", 1)[1].strip()
            if pid and pid not in refs:
                refs.append(pid)
    return refs


def appearances(project: Project) -> dict[str, Any]:
    """Walk every shot (in index order) and cross-reference bible ids.

    Returns a JSON-serializable dict with, per kind, the referenced entries and
    the ordered shot list each appears in, plus ``orphans`` (bible entries no
    shot references) and ``missing`` (shot references absent from the bible).
    """
    by_file = _bible_by_file(project)
    order = project.shot_ids()

    # kind -> id -> ordered list of shots that reference it (dedup within a shot)
    refs: dict[str, dict[str, list[str]]] = {"characters": {}, "scenes": {}, "props": {}}
    for sid in order:
        try:
            raw = project.load_shot_raw(sid)
        except Exception:
            continue  # a broken shot is check's problem; the report just skips it
        scene = raw.get("scene")
        if isinstance(scene, str) and scene:
            refs["scenes"].setdefault(scene, []).append(sid)
        for ch in raw.get("characters") or []:
            if isinstance(ch, str) and ch:
                bucket = refs["characters"].setdefault(ch, [])
                if sid not in bucket:
                    bucket.append(sid)
        for prop in _prop_refs(raw):
            bucket = refs["props"].setdefault(prop, [])
            if sid not in bucket:
                bucket.append(sid)

    result: dict[str, Any] = {"shots_total": len(order)}
    orphans: dict[str, list[str]] = {}
    missing: dict[str, list[dict[str, Any]]] = {}
    for kind, bible_key in _REFERENCED_KINDS:
        bible_ids = by_file.get(bible_key, {})
        used = refs[kind]
        present: dict[str, dict[str, Any]] = {}
        miss: list[dict[str, Any]] = []
        for rid, shots in used.items():
            if rid in bible_ids:
                present[rid] = {"name": bible_ids[rid].get("name"), "shots": shots}
            else:
                miss.append({"id": rid, "shots": shots})
        result[kind] = present
        orphans[kind] = sorted(rid for rid in bible_ids if rid not in used)
        missing[kind] = miss

    result["orphans"] = orphans
    result["missing"] = missing
    result["note"] = (
        "缺失的 scene/character 引用同样会被 `manju check` 报错(此处仅并列展示,"
        "以 check 为准);缺失的 prop 引用只有本报告校验(continuity.locks 不入 schema)。"
        "style/voices 不由镜头字段引用,不参与出场/孤儿统计。"
    )
    return result
