"""First-run onboarding checklist (goal item 2, WORKBENCH row "First-run guidance").

A dismissable checklist that walks a new user through the pipeline —
建项目 → 写剧本 → 建镜头 → 生成 → 审片 → 导出 — with LIVE done-detection read
straight off project state (never a stored flag: the checklist can't lie about
where the project actually is). Each step also carries a "怎么做" hint and the
one-line CLI that does the same thing, plus a ``view`` anchor the page scrolls
to. The pattern is the Linear/Notion first-run checklist: obvious, dismissable,
and honest about progress.

Read-only and cheap: it globs a couple of directories and reads the story
scaffolds. Dismissal is a per-USER preference (see :mod:`manju.gui.userstate`),
not project truth.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["build_onboarding"]

# Comment/heading/blank stripping to tell a written scaffold from the seed the
# `new` command drops (a bare heading + an HTML hint comment is "not written").
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_STORY_FILES = ("script.md", "outline.md", "brief.md")


def _story_written(project: Any) -> bool:
    """True once any story scaffold carries real prose beyond its seed — a
    non-blank line that is neither a markdown heading nor an HTML comment."""
    for name in _STORY_FILES:
        path = project.root / "story" / name
        try:
            # errors="replace": a GBK/ANSI-saved story file still answers the
            # only question asked here ("is there prose?") — it must degrade,
            # not 500 the /api/onboarding endpoint.
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = _COMMENT_RE.sub("", text)
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                return True
    return False


def _has_takes(project: Any, shot_ids: list[str]) -> bool:
    for sid in shot_ids:
        try:
            if project.takes(sid):
                return True
        except Exception:
            continue
    return False


def _has_final(project: Any) -> bool:
    try:
        return any(project.final_dir.glob("final_v*.mp4"))
    except OSError:
        return False


def _has_export(project: Any) -> bool:
    try:
        return any(p.is_file() for p in project.exports_dir.rglob("*"))
    except OSError:
        return False


def build_onboarding(project: Any) -> dict[str, Any]:
    """The checklist payload: per-step done-detection plus the aggregate the
    page uses to decide whether to auto-show (empty-ish + not dismissed)."""
    try:
        shot_ids = project.shot_ids()
    except Exception:
        shot_ids = []
    has_shots = bool(shot_ids)
    has_takes = _has_takes(project, shot_ids)
    has_final = _has_final(project)
    has_export = _has_export(project)
    story = _story_written(project)

    steps = [
        {
            "key": "project", "title": "建项目 (create project)", "done": True,
            "hint": "项目已创建 (project created)。工作区里可用“新建项目”再开一个。",
            "cli": "manju new <名字> --preset <kit>", "view": None,
        },
        {
            "key": "story", "title": "写剧本 (story/script.md)", "done": story,
            "hint": "在 story/script.md 写分场与对白;对白会成为 shots 的 dialogue.text。",
            "cli": "$EDITOR story/script.md", "view": None,
        },
        {
            "key": "shots", "title": "建镜头 (create shots)", "done": has_shots,
            "hint": "在分镜区点“新建镜头”,或编辑 shots/*.yaml(引擎从不代写内容)。",
            "cli": "manju new <名字> --shots N", "view": "shotsbar",
        },
        {
            "key": "generate", "title": "生成 (generate takes)", "done": has_takes,
            "hint": "在构建面板点“构建”生成镜头;点击前会先看计划并确认花费。",
            "cli": "manju build", "view": "buildpanel",
        },
        {
            "key": "review", "title": "审片 (review & final)", "done": has_final,
            "hint": "用 好/弃 评价 take、对比,并合成成片(renders/final)。",
            "cli": "manju build --target final", "view": "shots",
        },
        {
            "key": "export", "title": "导出 (export)", "done": has_export,
            "hint": "构建目标选 exports,或用 CLI 导出到剪辑软件工程。",
            "cli": "manju export", "view": "buildpanel",
        },
    ]
    done_count = sum(1 for s in steps if s["done"])
    # "empty-ish" = nothing generated yet (a brand-new / just-scaffolded project).
    empty_ish = not has_takes
    return {
        "steps": steps,
        "done_count": done_count,
        "total": len(steps),
        "empty_ish": empty_ish,
        "has_shots": has_shots,
        "has_takes": has_takes,
        "has_final": has_final,
        "has_export": has_export,
    }
