"""The creation funnel — 立意→梗概→节拍→剧本→分镜→生成计划→生成 as STAGED DATA.

Round V, goal item 2's engine half (§2 of REPORTS/ROUND-V-REFERENCES-1.md). The
market's idea→storyboard funnels (LTX / HeyGen / 即创…) all propose STRUCTURE
from an idea; Manju's stance (§0) is that the LLM lives in the driving agent, so
the engine ships the STAGE GRAPH, not the creativity. Each stage is one truth
artifact a human or agent WRITES (never the engine); this module knows the
ordered stages, DETECTS progress from the files on disk, and names the concrete
next action — plus the three approve-before-spend gates the field validates
(§2d: HeyGen "Video Plan" / InVideo "Always Ask" / LTX "review before Generate").

The seven canonical stages (my contract):

1. brief      立意       story/brief.md          一页纸立意/受众/时长/平台
2. synopsis   梗概       story/synopsis.md       立意展开成一段梗概
3. beats      节拍       story/beats.md          Hook→Value→Payoff→CTA,≥3 节拍
4. script     剧本       story/script.md         分场与对白(→ shots.dialogue.text)
5. storyboard 分镜       shots/*.yaml            镜头存在且 manju check 过校验
6. plan       生成计划   proposals + final       approve-before-spend 闸门
7. produce    生成       renders/final/final_v*  最新成片存在

`funnel_status(project)` walks these in order: the FIRST stage whose done-
predicate is False is ``current``; everything before it is ``done``, everything
after is ``todo`` (a funnel is linear). The result is JSON-serializable and
degrades per-stage on a read error (a broken file never bricks the status).

Convention note (§reuse): mentions.py already scans ``story/*.md`` for @handles,
so ``synopsis.md``/``beats.md`` are ordinary story files under the same
convention — ``manju new`` scaffolds brief/outline/script; this funnel adds
``synopsis.md``/``beats.md`` via ``manju create`` (part B) and treats the
existing ``script.md`` as the 剧本 artifact. The storyboard stage reuses
``core.check.run_check`` read-only (never a parallel validator).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from ..core.container import Project

__all__ = [
    "FunnelError",
    "Stage",
    "STAGES",
    "STAGE_IDS",
    "PRE_STORYBOARD",
    "SCAFFOLDS",
    "funnel_status",
    "funnel_current",
    "scaffold_stage",
]


class FunnelError(RuntimeError):
    """Clean one-line failures for the CLI/MCP (FIX-D envelope)."""


# ------------------------------------------------------------- text predicates

# A stage artifact is "non-trivial" when the human wrote real PROSE into it —
# not just the scaffold's headings and guidance comments. So content = the file
# with HTML comment blocks removed and markdown heading / blank lines dropped;
# a fresh template collapses to zero content and reads as todo, while a filled
# brief easily clears the threshold.
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_LIST_ITEM_RE = re.compile(r"^\s{0,3}([-*+]|\d+[.)])\s+\S")
_WS_RE = re.compile(r"\s+")

_MIN_CONTENT = 80  # chars of real content for a text stage to count as done
_MIN_BEATS = 3     # beat lines (- / 1.) the 节拍 stage needs


def _prose_lines(text: str) -> list[str]:
    """Non-comment, non-heading, non-blank lines — the human's actual writing."""
    no_comments = _COMMENT_RE.sub("", text)
    return [ln for ln in no_comments.splitlines()
            if ln.strip() and not _HEADING_RE.match(ln)]


def _content_len(text: str) -> int:
    """Length of real (whitespace-stripped) prose content, comments/headings out."""
    return len(_WS_RE.sub("", "".join(_prose_lines(text))))


def _count_beats(text: str) -> int:
    """Beat lines = markdown list items (``-`` / ``*`` / ``1.``) outside comments."""
    no_comments = _COMMENT_RE.sub("", text)
    return sum(1 for ln in no_comments.splitlines() if _LIST_ITEM_RE.match(ln))


def _short(exc: BaseException) -> str:
    return " ".join(str(exc).split()) or exc.__class__.__name__


def _read_artifact(project: Project, relpath: str) -> str | None:
    path = project.root / relpath
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


# ------------------------------------------------------------ done-predicates
# Each returns (done, evidence一句话). Pure reads — nothing here ever writes.


def _text_stage_done(project: Project, relpath: str,
                     stage: str | None = None) -> tuple[bool, str]:
    text = _read_artifact(project, relpath)
    if text is None:
        # UX audit F4: name the STAGE in the hint — the generic "manju create"
        # was circular (it told the owner to re-run the command that printed
        # this checklist; the scaffolder is `manju create <stage>`).
        cmd = f"manju create {stage}" if stage else "manju create"
        return False, f"{relpath} 还不存在({cmd} 可生成模板)"
    n = _content_len(text)
    if n > _MIN_CONTENT:
        return True, f"{relpath} 已填写(约 {n} 字正文)"
    return False, f"{relpath} 仍是模板/过短({n} 字,需 >{_MIN_CONTENT})"


def _brief_done(project: Project) -> tuple[bool, str]:
    return _text_stage_done(project, "story/brief.md", stage="brief")


def _synopsis_done(project: Project) -> tuple[bool, str]:
    return _text_stage_done(project, "story/synopsis.md", stage="synopsis")


def _beats_done(project: Project) -> tuple[bool, str]:
    text = _read_artifact(project, "story/beats.md")
    if text is None:
        return False, "story/beats.md 还不存在(manju create beats 可生成模板)"
    n, beats = _content_len(text), _count_beats(text)
    if n > _MIN_CONTENT and beats >= _MIN_BEATS:
        return True, f"story/beats.md 已写 {beats} 个节拍(约 {n} 字)"
    if beats < _MIN_BEATS:
        return False, f"story/beats.md 只有 {beats} 个节拍(需 ≥{_MIN_BEATS}:Hook→Value→Payoff→CTA)"
    return False, f"story/beats.md 正文过短({n} 字,需 >{_MIN_CONTENT})"


def _script_done(project: Project) -> tuple[bool, str]:
    return _text_stage_done(project, "story/script.md")


def _storyboard_done(project: Project) -> tuple[bool, str]:
    """Shots exist (index.order non-empty) AND every shot passes check-level
    validity. Reuses ``core.check.run_check`` read-only — never a parallel
    validator, so the funnel and ``manju check`` can never disagree."""
    order = list(project.load_index().order)
    if not order:
        return False, "还没有分镜:shots/index.yaml 的 order 为空(先拆分镜)"
    n = len(project.shot_ids())
    from ..core.check import run_check

    report = run_check(project)
    if report.ok:
        return True, f"{n} 个镜头,manju check 通过"
    return False, f"{n} 个镜头,但 check 有 {len(report.errors)} 处错误(先 manju check 修好)"


def _plan_done(project: Project) -> tuple[bool, str]:
    """The approve-before-spend gate: done when a director proposal reached a
    confirmed/executing/done state OR a final already exists (the plan landed).

    Round-W #66: a proposal sitting on disk with ``state="confirmed"`` is only
    HONESTLY confirmed while it is still CURRENT — ``director.confirm``/
    ``execute`` lazily flip a stale one to ``expired`` the next time someone
    tries to act on it, but nothing re-checks a merely-READ ``confirmed``
    proposal, so the funnel used to keep reporting "已过审" long after the
    project moved underneath it. Reuses the director's own fingerprint check
    (:func:`..build.director.state_fingerprint`) — never a parallel one.
    ``executing``/``done`` proposals are past tense: they already passed this
    exact check at the moment they transitioned, so they are NOT re-checked
    here (a completed plan does not need to remain "current" forever)."""
    final = project.newest_final_path()
    if final is not None:
        return True, f"已有成片 {project.relpath(final)}(生成计划已落地)"
    from .director import list_proposals, state_fingerprint

    approved = [p for p in list_proposals(project)
                if p.state in ("confirmed", "executing", "done")]
    if approved:
        p = approved[0]
        if p.state == "confirmed" and p.fingerprint != state_fingerprint(project):
            return False, (
                f"过审提案 {p.id} 已待更新(项目状态已变,指纹不匹配)— 需重新 "
                "manju director propose 再 confirm(approve-before-spend)"
            )
        return True, f"已有过审的生成计划提案 {p.id}({p.state})"
    return False, "还没有过审的生成计划:manju director propose 起草再 confirm(approve-before-spend)"


def _produce_done(project: Project) -> tuple[bool, str]:
    """Round-W #66: produce done requires the newest final to be UP_TO_DATE
    per the existing export-center freshness verdict (:mod:`..build.
    exportstatus`), not merely "a final file exists on disk" — a stale final
    (specs moved on since the render) is honestly reported as still current
    work, with the SAME evidence the export center itself shows, never a
    parallel staleness path."""
    final = project.newest_final_path()
    if final is None:
        # C2 honesty: locale-only finals live under renders/final/locales/.
        locales_root = project.final_dir / "locales"
        locale_hits: list[str] = []
        if locales_root.is_dir():
            for d in sorted(locales_root.iterdir()):
                if d.is_dir() and any(d.glob("final_v*.mp4")):
                    locale_hits.append(d.name)
        if locale_hits:
            return False, (
                "还没有 base 成片(renders/final/),但已有 locale 成片: "
                + ", ".join(locale_hits)
                + " — funnel 按 base final 计;locale 交付用 manju build --lang"
            )
        return False, "还没有成片:manju build 生成 final"
    from .exportstatus import Freshness, deliverables

    try:
        rows = deliverables(project)
        final_row = next((r for r in rows if r.kind == "final"), None)
    except Exception:
        final_row = None
    if final_row is None:  # the freshness engine itself errored — degrade honestly
        return True, f"已有成片:{project.relpath(final)}"
    if final_row.freshness is Freshness.UP_TO_DATE:
        return True, f"已有成片:{project.relpath(final)}({final_row.basis})"
    return False, f"成片已过期:{final_row.basis} — manju build 重新生成"


# ------------------------------------------------------------------- stages


@dataclass(frozen=True)
class Stage:
    """One funnel stage as data: id, 中文名, primary artifact path, a
    done-predicate (project → (done, evidence)), the skill it points at, and the
    concrete 中文 next action (a command or an edit)."""

    id: str
    cn: str
    artifact: str
    skill: str
    next_action: str
    predicate: Callable[[Project], tuple[bool, str]]


STAGES: list[Stage] = [
    Stage(
        id="brief", cn="立意", artifact="story/brief.md", skill="creation-funnel",
        next_action="编辑 story/brief.md 写清一句话立意/给谁看/时长/平台"
                    "(manju create brief 生成模板),参考 manju skills show creation-funnel",
        predicate=_brief_done,
    ),
    Stage(
        id="synopsis", cn="梗概", artifact="story/synopsis.md", skill="creation-funnel",
        next_action="编辑 story/synopsis.md 把立意展开成一段梗概"
                    "(manju create synopsis 生成模板),参考 manju skills show creation-funnel",
        predicate=_synopsis_done,
    ),
    Stage(
        id="beats", cn="节拍", artifact="story/beats.md", skill="narrative-pacing",
        next_action="编辑 story/beats.md 按 Hook→Value→Payoff→CTA 写 ≥3 个节拍"
                    "(manju create beats 生成模板),参考 manju skills show narrative-pacing",
        predicate=_beats_done,
    ),
    Stage(
        id="script", cn="剧本", artifact="story/script.md", skill="creation-funnel",
        next_action="编辑 story/script.md 写分场与对白(对白会成为 shots 的 dialogue.text),"
                    "参考 manju skills show creation-funnel",
        predicate=_script_done,
    ),
    Stage(
        id="storyboard", cn="分镜", artifact="shots/", skill="creation-funnel",
        next_action="拆分镜:补齐 shots/*.yaml 并 manju check 过校验(manju board 可辅助),"
                    "参考 manju skills show creation-funnel",
        predicate=_storyboard_done,
    ),
    Stage(
        id="plan", cn="生成计划", artifact="reports/proposals/", skill="creation-funnel",
        next_action="生成前先过审:manju director propose 起草生成计划再 confirm"
                    "(approve-before-spend 闸门)",
        predicate=_plan_done,
    ),
    Stage(
        id="produce", cn="生成", artifact="renders/final/", skill="creation-funnel",
        next_action="manju build 生成成片 final",
        predicate=_produce_done,
    ),
]

STAGE_IDS: tuple[str, ...] = tuple(s.id for s in STAGES)

# The pre-storyboard writing stages — before any pixel is generated. The director
# loop nudges these FIRST (part C): finish the story before spending on shots.
PRE_STORYBOARD: tuple[str, ...] = ("brief", "synopsis", "beats", "script")


# --------------------------------------------------------------- status walk


def funnel_status(project: Project) -> dict[str, Any]:
    """Per-stage funnel state as a JSON-serializable dict (part A).

    Walk the stages in order; the FIRST not-done stage is ``current``, every
    stage before it is ``done``, every stage after it is ``todo`` (a funnel is
    linear). Each stage's done-predicate is evaluated defensively — a read error
    degrades that stage to not-done with the error as its evidence, never a
    traceback."""
    raw: list[tuple[Stage, bool, str]] = []
    for stage in STAGES:
        try:
            done, evidence = stage.predicate(project)
        except Exception as exc:  # a broken file never bricks the whole status
            done, evidence = False, f"读取失败:{_short(exc)}"
        raw.append((stage, bool(done), evidence))

    current_idx = next((i for i, (_, done, _) in enumerate(raw) if not done), None)

    stages: list[dict[str, Any]] = []
    for i, (stage, _done, evidence) in enumerate(raw):
        if current_idx is None or i < current_idx:
            state = "done"
        elif i == current_idx:
            state = "current"
        else:
            state = "todo"
        stages.append({
            "id": stage.id,
            "cn": stage.cn,          # 中文名
            "state": state,
            "artifact": stage.artifact,
            "evidence": evidence,
            "skill": stage.skill,
            "next_action": stage.next_action,
        })

    done_count = sum(1 for s in stages if s["state"] == "done")
    return {
        "stages": stages,
        "current": (STAGES[current_idx].id if current_idx is not None else None),
        "done": done_count,
        "total": len(STAGES),
        "complete": current_idx is None,
    }


def funnel_current(project: Project) -> dict[str, Any] | None:
    """The single ``current`` stage entry (or None when the funnel is complete)."""
    status = funnel_status(project)
    cid = status.get("current")
    if cid is None:
        return None
    return next((s for s in status["stages"] if s["id"] == cid), None)


# ------------------------------------------------------------------ scaffolds
# `manju create <stage>` templates (part B). 中文-first, guidance in HTML
# comments that quote the funnel skill's key questions; the scaffold collapses to
# zero real content, so writing the template does NOT flip the stage to done —
# the human still has to fill it in. The engine scaffolds, it never authors (§2).

_BRIEF_TEMPLATE = """\
# 立意 / Brief

<!-- 一页纸立意(参考 manju skills show creation-funnel)。填写下面四问,
     删掉本注释、写下正文后本阶段即完成(正文 >80 字)。引擎从不代写内容(§2)。 -->

## 一句话立意
<!-- 谁、在哪、发生什么、为什么抓人?一句话说清。 -->

## 给谁看(受众)
<!-- 目标观众是谁?他们的痛点 / 好奇点在哪? -->

## 为什么现在
<!-- 这个选题此刻为什么值得做?蹭到什么热点 / 情绪? -->

## 平台与时长
<!-- 抖音 / 小红书 / YouTube Shorts…?竖屏还是横屏?目标时长(如 30s)? -->
"""

_SYNOPSIS_TEMPLATE = """\
# 梗概 / Synopsis

<!-- 把立意展开成一段梗概(参考 manju skills show creation-funnel):
     故事讲了什么?起承转合的主线是什么?情绪弧线怎么走?
     一段话(>80 字)即可,删掉本注释后写正文。 -->
"""

_BEATS_TEMPLATE = """\
# 节拍 / Beat Sheet

<!-- 短视频四段式节奏(参考 manju skills show narrative-pacing):
     Hook(0-3s 黄金三秒)→ Value(价值展示)→ Payoff(反转 / 高潮)→ CTA(引导)。
     每个节拍写一行(以 - 或 1. 开头),≥3 行且有正文本阶段才算完成。
     删掉下面的示例注释,写你自己的节拍: -->

<!-- 示例(请替换成真实节拍,注释内的行不计数):
- Hook: 用最反常识的一句话 / 一个画面在 3 秒内抓住人
- Value: 交付这条视频承诺的价值 / 信息
- Payoff: 留到最后的反转或高潮
- CTA: 引导点赞 / 关注 / 下一集
-->
"""

# stage id -> (artifact relpath, template text)
SCAFFOLDS: dict[str, tuple[str, str]] = {
    "brief": ("story/brief.md", _BRIEF_TEMPLATE),
    "synopsis": ("story/synopsis.md", _SYNOPSIS_TEMPLATE),
    "beats": ("story/beats.md", _BEATS_TEMPLATE),
}


def scaffold_stage(project: Project, stage_id: str, *, force: bool = False,
                   actor: str = "human") -> dict[str, Any]:
    """Write a stage's template file (part B). Refuses to overwrite an existing
    file unless ``force`` — a human's writing is never clobbered silently (§5).
    Records a ``funnel_scaffold`` event on every write. Returns
    ``{stage, path, created, template}``."""
    if stage_id not in SCAFFOLDS:
        raise FunnelError(
            f"manju create 只支持 {'/'.join(SCAFFOLDS)}(得到 {stage_id!r});"
            "剧本用 manju new 已脚手架的 story/script.md,分镜用 shots/*.yaml")
    relpath, template = SCAFFOLDS[stage_id]
    path = project.root / relpath
    if path.exists() and not force:
        raise FunnelError(
            f"{relpath} 已存在 — 不覆盖人写的内容;确要重置用 --force")
    from ..core.events import append_event
    from ..core.yamlio import atomic_write_text

    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, template)
    rel = project.relpath(path)
    append_event(project.root, actor, "funnel_scaffold",
                 {"stage": stage_id, "path": rel, "force": bool(force)})
    return {"stage": stage_id, "path": rel, "created": True, "template": template}
