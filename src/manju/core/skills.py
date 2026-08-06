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
    "core_skill_shadow_warning",
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


def _project_skills_dir(project) -> Path:
    return Path(project.root) / "skills"


def core_skill_shadow_warning(project=None) -> str | None:
    """A loud 中文 warning line when a PROJECT carries its own
    ``skills/manju/SKILL.md`` (goal item 46) — the id core/skills.py refuses
    to let the project tier override, precisely because a downloaded/
    untrusted project could otherwise shadow the core operating protocol an
    agent's every action is checked against. ``None`` when there is nothing
    to warn about (no project, or no such shadow attempt)."""
    if project is None:
        return None
    shadow_path = _project_skills_dir(project) / CORE_SKILL_ID / "SKILL.md"
    if not shadow_path.is_file():
        return None
    return (
        f"⚠ 检测到项目内 skills/{CORE_SKILL_ID}/SKILL.md,但核心协议技能"
        f"({CORE_SKILL_ID!r})不允许被项目层覆盖(可能是不可信项目伪装核心操作协议)"
        "——已忽略,继续使用 bundled/user 版本。如果这是你自己的项目,请改用其他 "
        "skill id,或把覆盖放进 ~/.manju/skills(user 层允许覆盖)。"
    )


def list_skills(project=None) -> list[SkillInfo]:
    """Every visible skill, project > user > bundled per id; the core protocol
    skill first, then alphabetical. ``project`` may be None (no project tier).

    goal item 46: the PROJECT tier may NOT override ``CORE_SKILL_ID`` — a
    project's own machine is not the same trust boundary as an untrusted
    downloaded project, and the core skill IS the operating protocol every
    other safety rail (locks, check-before-build, ask_before) is described
    in. The user tier (``~/.manju/skills``, the user's OWN machine) can still
    override it, same as always.
    """
    merged: dict[str, SkillInfo] = {}
    merged.update(_scan(bundled_skills_dir(), "bundled"))
    merged.update(_scan(user_skills_dir(), "user"))
    if project is not None:
        project_scan = _scan(_project_skills_dir(project), "project")
        project_scan.pop(CORE_SKILL_ID, None)  # never let a project shadow core
        merged.update(project_scan)

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
    one line per skill: id + 何时用. Empty string when nothing to list AND no
    shadow warning applies (goal item 46: the warning still surfaces even if
    every other skill is excluded, e.g. ``manju auto``'s
    ``exclude=(CORE_SKILL_ID,)`` call)."""
    rows = [s for s in list_skills(project) if s.id not in exclude]
    warning = core_skill_shadow_warning(project)
    if not rows and not warning:
        return ""
    lines: list[str] = []
    if warning:
        lines.append(warning)
    if rows:
        lines.append(
            "作者合同: SceneContract/ShotContract。生产资格术语: "
            "proxy-only=系统体检代理, candidate=已登记但尚未证明最终资格, "
            "final-eligible=当前媒体已满足人工批准与 assurance；build ok 不等于 Picture Lock。"
        )
        lines.append("可用技能库(用 `manju skills show <id>` 取全文,按需加载,不要全量复制):")
        for s in rows:
            hint = s.when_to_use or s.description
            src = "" if s.source == "bundled" else f" [{s.source}]"
            lines.append(f"  - {s.id}: {hint}{src}")
    return "\n".join(lines)
