"""Skill library (round V, goal item 1) — packaged domain EXPERTISE, not code.

A skill is judgment written down: criteria, worked examples, checklists,
failure catalogs — the difference between "an AI that can run the CLI" and
"an AI that cuts like an editor". The engine stays LLM-free (§10); skills are
how capability reaches whatever agent drives Manju.

Layout — three tiers, most specific wins, same precedence family as
providers/routing (project > user > bundled)::

    <project>/skills/<id>/SKILL.md      project overlay (per-film house rules)
    ~/.manju/skills/<id>/SKILL.md       user library (MANJU_SKILLS_DIR overrides)
    <repo>/skills/<id>/SKILL.md         bundled with Manju

``SKILL.md`` follows the anthropics/skills convention: YAML frontmatter
(``name`` / ``description`` and, ours additively, ``when_to_use`` / ``tags`` /
``requires``) above a markdown body. Parsing is tolerant — a file with broken
or missing frontmatter is still a skill (id from its directory, description
from its first heading); a library must never crash the CLI.

Progressive disclosure (the load-bearing design decision): agents get the
INDEX (id + 何时用 one-liners) cheaply — ``manju auto`` injects the index plus
only the core ``manju`` protocol skill — and pull one skill's FULL body on
demand (``manju skills show <id>``, MCP ``skill_show``). The whole library is
never inlined into a prompt; that is what separates a skill library from a
protocol document that only grows.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "SkillInfo",
    "bundled_skills_dir",
    "user_skills_dir",
    "list_skills",
    "load_skill",
    "skill_index_text",
    "CORE_SKILL_ID",
]

CORE_SKILL_ID = "manju"  # the operating protocol; always listed first

_TIER_ORDER = ("project", "user", "bundled")  # display order for provenance


@dataclass
class SkillInfo:
    id: str
    name: str
    description: str
    when_to_use: str = ""
    tags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)
    source: str = "bundled"  # project | user | bundled
    path: Path | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "tags": list(self.tags),
            "requires": list(self.requires),
            "source": self.source,
            "path": str(self.path) if self.path else None,
        }


def bundled_skills_dir() -> Path:
    """``<repo>/skills`` — the library shipped next to the package (the same
    resolution ``manju auto`` has always used for skills/manju/SKILL.md)."""
    import manju

    return Path(manju.__file__).resolve().parents[2] / "skills"


def user_skills_dir() -> Path:
    override = os.environ.get("MANJU_SKILLS_DIR")
    if override:
        return Path(override)
    return Path.home() / ".manju" / "skills"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body) — tolerant. No frontmatter → ({}, whole text)."""
    if not text.startswith("---"):
        return {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.DOTALL)
    if not m:
        return {}, text
    try:
        import yaml

        data = yaml.safe_load(m.group(1)) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    return data, text[m.end():]


def _str_list(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if str(v)]
    return []


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def _read_skill(skill_dir: Path, source: str) -> SkillInfo | None:
    md = skill_dir / "SKILL.md"
    if not md.is_file():
        return None
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, body = _parse_frontmatter(text)
    return SkillInfo(
        id=skill_dir.name,
        name=str(fm.get("name") or skill_dir.name),
        description=str(fm.get("description") or _first_heading(body) or skill_dir.name),
        when_to_use=str(fm.get("when_to_use") or ""),
        tags=_str_list(fm.get("tags")),
        requires=_str_list(fm.get("requires")),
        source=source,
        path=md,
    )


def _scan(root: Path, source: str) -> dict[str, SkillInfo]:
    out: dict[str, SkillInfo] = {}
    if not root.is_dir():
        return out
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        info = _read_skill(child, source)
        if info is not None:
            out[info.id] = info
    return out


def list_skills(project=None) -> list[SkillInfo]:
    """Every visible skill, project > user > bundled per id; the core protocol
    skill first, then alphabetical. ``project`` may be None (no project tier)."""
    merged: dict[str, SkillInfo] = {}
    merged.update(_scan(bundled_skills_dir(), "bundled"))
    merged.update(_scan(user_skills_dir(), "user"))
    if project is not None:
        merged.update(_scan(Path(project.root) / "skills", "project"))

    def key(info: SkillInfo):
        return (0 if info.id == CORE_SKILL_ID else 1, info.id)

    return sorted(merged.values(), key=key)


def load_skill(project, skill_id: str) -> SkillInfo:
    """The resolved skill (project > user > bundled) with its FULL text on
    ``.path`` — raises KeyError with the available ids when unknown."""
    for info in list_skills(project):
        if info.id == skill_id:
            return info
    ids = ", ".join(s.id for s in list_skills(project)) or "(库为空)"
    raise KeyError(f"unknown skill {skill_id!r} — available: {ids}")


def skill_text(project, skill_id: str) -> str:
    info = load_skill(project, skill_id)
    return info.path.read_text(encoding="utf-8") if info.path else ""


def skill_index_text(project=None, *, exclude: tuple[str, ...] = ()) -> str:
    """The 中文 index block agents receive instead of the whole library —
    one line per skill: id + 何时用. Empty string when nothing to list."""
    rows = [s for s in list_skills(project) if s.id not in exclude]
    if not rows:
        return ""
    lines = ["可用技能库(用 `manju skills show <id>` 取全文,按需加载,不要全量复制):"]
    for s in rows:
        hint = s.when_to_use or s.description
        src = "" if s.source == "bundled" else f" [{s.source}]"
        lines.append(f"  - {s.id}: {hint}{src}")
    return "\n".join(lines)
