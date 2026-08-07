"""The creation funnel — story→contracts→proof→candidate build as STAGED DATA.

Round V, goal item 2's engine half (§2 of REPORTS/ROUND-V-REFERENCES-1.md). The
market's idea→storyboard funnels (LTX / HeyGen / 即创…) all propose STRUCTURE
from an idea; Manju's stance (§0) is that the LLM lives in the driving agent, so
the engine ships the STAGE GRAPH, not the creativity. Each stage is one truth
artifact a human or agent WRITES (never the engine); this module knows the
ordered stages, DETECTS progress from the files on disk, and names the concrete
next action — plus the three approve-before-spend gates the field validates
(§2d: HeyGen "Video Plan" / InVideo "Always Ask" / LTX "review before Generate").

The seven canonical stages (my contract):

1. brief      故事与结尾   story/brief.md          故事意图、结尾和观众
2. synopsis   场次梗概     story/synopsis.md       场次进入/变化/离开
3. beats      变化节拍     story/beats.md          叙事变化与停顿,≥3 节拍
4. script     剧本与声音   story/script.md         分场、对白与 TEMP_AUDIO_FOR_ANIMATIC
5. storyboard 场次/镜头合同 shots/*.yaml          SceneContract/ShotContract + check
6. plan       Animatic/Proof计划 proposals + final  Animatic、Proof Shot、Proof Scene 闸门
7. produce    候选构建     renders/final/final_v*  候选产物;需另行取得 final-eligible

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
    """Clean one-line failures for the CLI and GUI (FIX-D envelope)."""


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
        return False, f"story/beats.md 只有 {beats} 个节拍(需 ≥{_MIN_BEATS} 个叙事变化)"
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
        return True, (
            f"已有候选构建产物 {project.relpath(final)}"
            "(生成计划已落地;媒体资格另见 manju production status)"
        )
    # C8: locale-only projects still have deliverable finals under locales/.
    locales_root = project.final_dir / "locales"
    if locales_root.is_dir():
        hits = [
            # sorted(Path) folds case on Windows (see build/ingest.py's note
            # citing the gate run it moved) — sort by the POSIX string.
            d.name for d in sorted(locales_root.iterdir(),
                                   key=lambda p: p.as_posix())
            if d.is_dir() and any(d.glob("final_v*.mp4"))
        ]
        if hits:
            return True, (
                "尚无 base 构建产物,但已有 locale 构建产物: "
                + ", ".join(hits)
                + "(locale 交付已落地;base 仍可用 manju build)"
            )
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
            # POSIX-string order — platform-independent (see above).
            for d in sorted(locales_root.iterdir(), key=lambda p: p.as_posix()):
                if d.is_dir() and any(d.glob("final_v*.mp4")):
                    locale_hits.append(d.name)
        if locale_hits:
            return False, (
                "还没有 base 候选构建产物(renders/final/),但已有 locale 构建产物: "
                + ", ".join(locale_hits)
                + " — funnel 按 base final 计;locale 交付用 manju build --lang"
            )
        return False, "还没有候选构建产物:manju build 生成 final(candidate)"
    from .exportstatus import Freshness, deliverables

    try:
        rows = deliverables(project)
        final_row = next((r for r in rows if r.kind == "final"), None)
    except Exception:
        final_row = None
    if final_row is None:  # the freshness engine itself errored — degrade honestly
        return True, f"已有候选构建产物:{project.relpath(final)}(资格未推断)"
    if final_row.freshness is Freshness.UP_TO_DATE:
        return True, (
            f"已有当前 candidate:{project.relpath(final)}({final_row.basis});"
            "build 成功不等于 final-eligible"
        )
    return False, f"成片构建产物已过期:{final_row.basis} — manju build 重新生成"


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


_TEMPLATE_HINT_RE = re.compile(r"[（(]manju create \w+ 生成模板[)）]")


def _next_action_for(project: Project, stage: "Stage") -> str:
    """`stage.next_action` with the "generate a template" clause dropped once
    the file is already there.

    `manju new` scaffolds story/brief.md and story/script.md, so on a brand-new
    project the brief stage advised "(manju create brief 生成模板)" — and
    running it answered "story/brief.md 已存在 — 不覆盖人写的内容". The refusal
    is right (never overwrite the owner's text); the ADVICE was wrong, and it
    was the very first instruction a new project gives. The script stage never
    carried the clause, so this also makes the two siblings agree.

    Found by walking the funnel as a newcomer rather than by reading it."""
    action = stage.next_action
    if not _TEMPLATE_HINT_RE.search(action):
        return action
    try:
        exists = (project.root / stage.artifact).exists()
    except Exception:
        return action
    if not exists:
        return action
    return re.sub(r"\s*" + _TEMPLATE_HINT_RE.pattern + r"\s*", "", action, count=1)


STAGES: list[Stage] = [
    Stage(
        id="brief", cn="故事与结尾", artifact="story/brief.md", skill="creation-funnel",
        next_action="编辑 story/brief.md 写清故事与结尾(立意)、人物处境和观众"
                    "(manju create brief 生成模板),参考 manju skills show creation-funnel",
        predicate=_brief_done,
    ),
    Stage(
        id="synopsis", cn="场次梗概", artifact="story/synopsis.md", skill="creation-funnel",
        next_action="编辑 story/synopsis.md 写清每场的进入状态、不可逆变化和离开状态"
                    "(manju create synopsis 生成模板),参考 manju skills show creation-funnel",
        predicate=_synopsis_done,
    ),
    Stage(
        id="beats", cn="变化节拍", artifact="story/beats.md", skill="narrative-pacing",
        next_action="编辑 story/beats.md 按信息、动作、反应、停顿和声音写 ≥3 个叙事节拍"
                    "(manju create beats 生成模板),参考 manju skills show narrative-pacing",
        predicate=_beats_done,
    ),
    Stage(
        id="script", cn="剧本与声音", artifact="story/script.md", skill="creation-funnel",
        next_action="编辑 story/script.md 写分场与对白(对白会成为 shots 的 dialogue.text),"
                    "并先用 TEMP_AUDIO_FOR_ANIMATIC 验证节奏,"
                    "参考 manju skills show creation-funnel",
        predicate=_script_done,
    ),
    Stage(
        id="storyboard", cn="场次/镜头合同", artifact="shots/", skill="creation-funnel",
        # The funnel's cliff, found by walking it: every stage before this one
        # was "edit ONE file, here is a template". This one said "补齐
        # shots/*.yaml" — plural, no template, and NO CLI command anywhere
        # creates a shot. It then offered `manju board` as the helper, but
        # board COMPOSES a storyboard out of shots that already exist: run it
        # here and it answers "has no shots — nothing to board" and exits 1.
        # Same defect as the brief stage — a recommendation that refuses — but
        # at the hardest step, where a newcomer has the least to fall back on.
        # So name the routes that actually work, and demote board to what it
        # really is: useful AFTER there are shots.
        # TRISURFACE F-08: this said 分镜页 — but the nav has a page literally
        # NAMED 分镜工作台 (/storyboard) with no create affordance; the
        # 新建镜头 button lives on the workbench HOME's shots panel. Point at
        # the door that exists.
        next_action="建立 SceneContract 与 ShotContract(本步没有模板命令,三条路任选):① manju gui → 工作台"
                    "首页镜头面板「新建镜头」点着建;② 手写 shots/S001.yaml 并加进 "
                    "shots/index.yaml 的 order(字段见 manju schema);"
                    "③ 让 AI 导演按剧本代写。建完跑 manju check 过校验;"
                    "有镜头之后 manju board 可以拼故事板。"
                    "参考 manju skills show shot-design",
        predicate=_storyboard_done,
    ),
    Stage(
        id="plan", cn="Animatic/Proof 计划", artifact="reports/proposals/", skill="creation-funnel",
        # Third instance of the same defect, found the same way: `manju
        # director propose` on its own answers "pass exactly one of
        # --from-file / --actions-json" and exits 1. And the action shape is
        # not guessable — `{"op": "build"}` is refused with "unknown action
        # type None". So give the line that actually runs.
        next_action='先生成并人工批准 Animatic,再过 Proof Shot/Proof Scene 闸门(approve-before-spend):'
                    'manju director propose --actions-json \'[{"type":"build",'
                    '"target":"final"}]\' --why "说明为什么" '
                    '→ manju director confirm <id> → manju director run <id>;'
                    '不想走提案流程也可以直接 manju build',
        predicate=_plan_done,
    ),
    Stage(
        id="produce", cn="候选构建", artifact="renders/final/", skill="creation-funnel",
        next_action="manju build 生成可审片 candidate;成功 build 不等于 final-eligible 或 Picture Lock",
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
            # `state` is POSITIONAL — everything past the first unfinished stage
            # is "todo" so the funnel keeps its order. But a later stage's own
            # predicate can already be satisfied (a project that was built before
            # its brief was written has plan/produce evidence saying exactly
            # that), and rendering it as ○ next to evidence reading 已落地 is a
            # flat contradiction. Carry the predicate itself so the renderer can
            # say "met, just not its turn" instead. Additive: `state` and the
            # done count are unchanged for every existing consumer.
            "satisfied": bool(_done),
            "artifact": stage.artifact,
            "evidence": evidence,
            "skill": stage.skill,
            "next_action": _next_action_for(project, stage),
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

<!-- 故事与结尾(参考 manju skills show creation-funnel)。填写下面三问,
     删掉本注释、写下正文后本阶段即完成(正文 >80 字)。引擎从不代写内容(§2)。 -->

## 故事与结尾
<!-- 谁处于什么处境?故事如何结束?写清不可逆变化,不要先写镜头。 -->

## 观众与观看条件(可选)
<!-- 谁会看?他们需要理解什么?若有格式/时长要求,在这里写明来源。 -->

## 不能丢的事实
<!-- 角色、地点、道具、声音或结尾必须保留的事实。 -->
"""

_SYNOPSIS_TEMPLATE = """\
# 场次梗概 / Synopsis

<!-- 把故事展开成场次变化(参考 manju skills show creation-funnel):
     每场写进入状态、不可逆变化、离开状态和下一场要继承的事实。
     一段话(>80 字)即可,删掉本注释后写正文。 -->
"""

_BEATS_TEMPLATE = """\
# 变化节拍 / Beat Sheet

<!-- 按信息、动作、反应、停顿和声音组织叙事变化(参考
     manju skills show narrative-pacing)。每个节拍写一行(以 - 或 1. 开头),
     ≥3 行且有正文本阶段才算完成。删掉下面的示例注释,写你自己的节拍: -->

<!-- 示例(请替换成真实节拍,注释内的行不计数):
- 进入: 角色带着尚未解决的问题进入场次
- 变化: 一个动作让关系或知识状态不可逆地改变
- 离开: 角色带着新的状态离开,下一场从这里承接
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
    # FUNNEL-P0-001: a symlinked ``story/`` (or any linked segment under it)
    # would scaffold the template THROUGH the link outside the project while the
    # returned relpath still reads as ``story/<stage>.md`` (``--force`` widens
    # the blast radius). Refuse the linked directory chain BEFORE the exists /
    # overwrite check and the write; publish via the no-follow atomic publisher.
    from ..core.safeio import SafeOutError, publish_text, refuse_linked_within

    try:
        refuse_linked_within(path.parent, project.root, kind="story 目录")
    except SafeOutError as exc:
        # surface through the existing FunnelError envelope (create catches it)
        raise FunnelError(str(exc)) from exc
    if path.exists() and not force:
        raise FunnelError(
            f"{relpath} 已存在 — 不覆盖人写的内容;确要重置用 --force")
    from ..core.events import append_event

    path.parent.mkdir(parents=True, exist_ok=True)
    publish_text(path, template)
    rel = project.relpath(path)
    append_event(project.root, actor, "funnel_scaffold",
                 {"stage": stage_id, "path": rel, "force": bool(force)})
    return {"stage": stage_id, "path": rel, "created": True, "template": template}
