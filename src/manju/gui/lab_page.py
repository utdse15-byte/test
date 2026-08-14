"""镜头实验室 Shot lab — the single-shot experiment page (round U, goal 13).

A Pro-mode workbench for ONE shot: the three things a director iterates on before
committing a paid generate call, side by side —

  A. 参考 (references, left)   — the shot's resolved RefSet with per-ref tier
     lineage, the budget allocation (selected vs 被省略, straight from
     :class:`manju.providers.refbudget.BudgetReport`) when the routed provider
     caps ref inputs, and the per-ref cleanliness findings from
     :func:`manju.qc.ref_checks.check_refs` (including the honest 需要视觉模型
     rows). Actions write ``generation.params.refs`` through the normal
     lock-respecting spec path and upload a new ref via the existing machinery.
  B. 提示词 (prompts, centre) — the promptlab bundle rendered verbatim
     (:func:`manju.build.promptlab.shot_prompt_bundle`): image/video/director/
     negative prompts, the provider resolution trace, the pre-flight cost, and
     the single-action checks with their split-shot suggestions. Editing
     ``generation.prompt_override`` is lock-respecting; 按关键帧拆分 surfaces
     :func:`manju.media.boards.breakdown_action` as a COPYABLE suggestion that
     only writes (into ``keyframes``) behind an explicit apply button.
  C. 候选 (candidates, right)  — existing takes as cards (frame thumb, 生成来源,
     花费, created). 生成候选 runs the EXISTING single-shot redo through the jobs
     runner with a 生成来源 picker and a 草稿/成片 quality toggle (mapping to the
     UE build modes) showing the 试跑 cost DELTA between the two — reusing the
     SAME estimators ``build --dry-run`` / ``gui.plan`` price from — behind the
     existing confirm-before-spend flow. 选用 is the normal select-take; 存为参考
     extracts a frame and saves it into the shot's refs or the user library.

Stance (mirrors :mod:`manju.gui.exports_page`): server-rendered (a plain GET
carries the real bundle baked into the DOM), CSP-safe (CSS/JS in external files,
no inline handlers, every mutating POST carries the ``X-Manju-Token``), XSS-safe
(all server text is ``html.escape``-d, the script only writes ``textContent``),
and ONE core — every panel reads the exact code paths the CLI (``manju prompt`` /
``manju refs``) and the build use, so the two surfaces cannot disagree. No new
generation machinery: every priced action goes through the existing redo/jobs
path with its plan/cost confirm flow.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

from .a11y import HTML_LANG, NOSCRIPT_HTML, SKIP_LINK_HTML, main_open

__all__ = [
    "PAGE_PATH",
    "render",
    "render_lab_css",
    "render_lab_js",
    "lab_data",
    "generate_plan",
    "quality_cost",
    "resolve_quality_provider",
    "shot_manifest",
    "field_locked",
    "QUALITY_MODES",
]

PAGE_PATH = "/lab"

# The 草稿/成片 toggle maps to the UE build modes (build/modes.py): draft → the
# speed route (else-bias cheapest), final → the quality route (else-bias quality).
# Every surveyed tool ships a binary draft/final; we do not invent a third.
QUALITY_MODES = {"draft": "speed", "final": "quality"}

# ref-check level → the app.css badge class carrying the right colour.
_REF_LEVEL_CLASS = {
    "error": "st-broken",
    "warn": "st-stale",
    "info": "st-manual",
    "needs_vision": "st-needs",
}
_REF_LEVEL_ZH = {
    "error": "错误", "warn": "警告", "info": "提示", "needs_vision": "需要视觉模型",
}

# glossary tooltips (REPORTS §10) surfaced as `title=` at a term's first
# appearance — CSP-safe, no JS. Keyed by the engineering term.
_GLOSS = {
    "provider": "这条画面/配音是用哪个 AI 模型或服务做出来的。",
    "take": "同一个镜头反复生成的不同版本，可并排对比、挑一条留用。",
    "dry_run": "只算不真正生成，先看计划和预估花费再决定要不要开工。",
    "stale": "上游改过之后这一条还是旧的，得重新生成才跟得上。",
    "budget": "花费到上限就自动暂停，避免不知不觉超支。",
    "storyboard": "把整片拆成一个个镜头的计划表，先定好再生成。",
}


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _gl(term: str, label: str) -> str:
    """A glossary-tooltipped label span (中文用户词 + hover explanation)."""
    return f'<span class="gl" title="{_e(_GLOSS.get(term, ""))}">{_e(label)}</span>'


def _provider_label_html(value: Any) -> str:
    raw = str(value or "—")
    if raw == "auto(fallback chain)":
        return '自动（兜底链）<span class="mj-en" aria-hidden="true"> auto(fallback chain)</span>'
    return _e(raw)


# ================================================================= data layer


def shot_manifest(project: Any, shot: Any):
    """The manifest that WOULD deliver this shot's refs — the explicit 生成来源,
    else the first fallback-chain provider that carries one; ``None`` when the
    shot routes only to built-in local providers (no ref budget). Mirrors the
    ``manju refs`` CLI resolver so the two surfaces agree on the budget."""
    try:
        from ..providers.registry import fallback_chain, get_manifest

        order = ([shot.generation.provider] if shot.generation.provider else [])
        order += [n for n in fallback_chain(shot) if n not in order]
        for name in order:
            manifest = get_manifest(name)
            if manifest is not None:
                return manifest
    except Exception:
        return None
    return None


def field_locked(raw: dict[str, Any], target: str) -> str | None:
    """The locked dotted-path that BLOCKS a write to ``target`` (§5), or ``None``.

    A write to ``target`` is blocked when a lock covers it exactly, covers an
    ancestor of it, or covers a descendant of it (replacing ``target`` wholesale
    would overturn a sealed child). Mirrors the ``scaffold_keyframes`` refusal —
    the GUI never writes through a lock, unlock stays on the terminal."""
    locked = raw.get("locked") or {}
    if isinstance(locked, list):
        locked = {str(p): "" for p in locked}
    if not isinstance(locked, dict):
        return None
    for k in locked:
        k = str(k)
        if k == target or k.startswith(target + ".") or target.startswith(k + "."):
            return k
    return None


def _param_refs(shot: Any) -> list[str]:
    """The shot's OWN explicit ref picks — ``generation.params.refs`` normalised
    to a flat string list (the editable slot panel A writes back)."""
    val = None
    try:
        val = shot.generation.params.get("refs")
    except Exception:
        val = None
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        return [str(v) for v in val if v not in (None, "")]
    return [str(val)] if val != "" else []


def _candidates(project: Any, shot: Any) -> list[dict[str, Any]]:
    """Existing takes as candidate cards — 版本 name, 生成来源, 花费, created, a
    lazy frame thumb (video only), seed for recipe-reuse. Read-only."""
    selected = shot.status.selected_take
    out: list[dict[str, Any]] = []
    # round-W #35: a media-less "ghost" sidecar has nothing to show as a
    # candidate card (no media, no thumb) — the gallery is a listing, so it
    # skips them defensively (gc --hard now also removes the sidecar itself,
    # but an older project or an interrupted write can still leave one).
    for t in project.takes(shot.id, skip_ghosts=True):
        sc = t.sidecar
        rel = project.relpath(t.media_path) if t.media_path is not None else None
        thumb = None
        if rel is not None and t.media_path.suffix.lower() not in (
                ".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg"):
            thumb = "/thumb/" + quote(rel, safe="/")
        out.append({
            "name": t.name,
            "provider": sc.provider,
            "cost": (sc.remote.cost if sc.remote is not None else None),
            "currency": (sc.remote.currency if sc.remote is not None else None),
            "created": sc.created_at,
            "duration_ms": (sc.probe.duration_ms if sc.probe is not None else None),
            "media": rel,
            "thumb": thumb,
            "selected": t.name == selected,
            "seed": sc.params.get("seed") if isinstance(sc.params, dict) else None,
        })
    return out


def lab_data(project: Any, shot_id: str) -> dict[str, Any]:
    """Everything the shot lab needs for one shot, JSON-serialisable and READ-ONLY.

    Reuses the SAME code paths the build and the CLI use: the promptlab bundle
    (:func:`shot_prompt_bundle`), the single ref resolver, the budget allocator
    and the cleanliness checks — so nothing here can drift from what a paid call
    would actually send. Raises ``ProjectError`` when the shot is missing."""
    from ..build.promptlab import shot_prompt_bundle
    from ..providers.refbudget import allocate, classify_role
    from ..providers.refs import resolve_refs
    from ..qc.ref_checks import check_refs

    bundle = shot_prompt_bundle(project, shot_id)  # ProjectError if missing
    shot = project.load_shot(shot_id)
    bible = project.load_bible()

    refset = resolve_refs(project, shot, bible)
    manifest = shot_manifest(project, shot)
    limits = manifest.limits if manifest is not None else None
    budget = allocate(refset, limits, shot, bible=bible)
    cleanliness = [f.to_dict() for f in check_refs(project, shot, refset, bible=bible)]

    # per-ref rows with role (panel A lineage) — same classifier the budget uses.
    ref_rows = [
        {"ref": it.ref, "tier": it.tier, "kind": it.kind,
         "role": classify_role(it, shot, bible),
         "exists": it.exists, "is_url": it.is_url}
        for it in refset.items
    ]

    # 素材库建议 library reuse suggestions (round X agent XF, pain #7/#8) — a
    # broken/absent library must never break the lab page.
    try:
        from ..core.library import suggest_from_library

        library_suggestions = suggest_from_library(project, shot)
    except Exception:
        library_suggestions = []

    return {
        "shot": shot_id,
        "bundle": bundle,
        "ref_rows": ref_rows,
        "param_refs": _param_refs(shot),
        "budget": {
            "active": budget.active,
            "provider": (manifest.id if manifest is not None else None),
            "human_lines": budget.human_lines(),
            **budget.to_lineage(),
        },
        "cleanliness": cleanliness,
        "library_suggestions": library_suggestions,
        "candidates": _candidates(project, shot),
        "shot_ids": project.shot_ids(),
    }


# ------------------------------------------------------ quality / cost delta


def resolve_quality_provider(project: Any, shot: Any, quality: str,
                             explicit: str | None = None) -> str | None:
    """The 生成来源 a given quality would run first — an explicit override wins,
    else the shot's钦定 provider, else routing resolved under the mode's else-bias
    (draft→cheapest, final→quality), else the §8.4 fallback-chain head. This is
    exactly the resolution the generate action then passes to ``redo_shot``, so
    the priced provider and the run provider never differ."""
    if explicit:
        return str(explicit)
    if getattr(shot.generation, "provider", None):
        return shot.generation.provider
    from ..build.modes import mode_else_bias
    from ..providers.routing import RoutingError, resolve

    bias = mode_else_bias(QUALITY_MODES.get(quality))
    try:
        chosen = resolve(project, shot, else_bias=bias).chosen
        if chosen:
            return chosen
    except RoutingError:
        pass
    except Exception:
        pass
    try:
        from ..providers.registry import fallback_chain

        chain = fallback_chain(shot)
        return chain[0] if chain else None
    except Exception:
        return None


def quality_cost(project: Any, shot_id: str, quality: str,
                 provider: str | None = None) -> dict[str, Any]:
    """The 试跑 estimate for one quality — priced with the SAME estimators
    ``build --dry-run`` / ``gui.plan`` use (``graph._estimate_shot_cost`` at the
    shot's ``graph._target_duration_ms``), against the provider that quality
    resolves to. The resolved provider is applied to an IN-MEMORY shot copy only
    (never written back), so pricing is honest without touching truth."""
    from ..build.graph import _estimate_shot_cost, _target_duration_ms

    if quality not in QUALITY_MODES:
        raise ValueError(f"unknown quality: {quality!r} (draft|final)")
    shot = project.load_shot(shot_id)
    rules = project.load_rules()
    dur = _target_duration_ms(project, shot, rules)
    prov = resolve_quality_provider(project, shot, quality, provider)
    priced = shot
    if prov:
        priced = shot.model_copy(deep=True)
        priced.generation.provider = prov
    cost, currency = _estimate_shot_cost(priced, dur)
    return {
        "quality": quality,
        "mode": QUALITY_MODES[quality],
        "provider": prov,
        "estimated_cost": float(cost),
        "currency": currency,
        "duration_ms": int(dur),
    }


def generate_plan(project: Any, shot_id: str,
                  provider: str | None = None) -> dict[str, Any]:
    """The 草稿-vs-成片 cost comparison shown at the quality toggle before a
    confirm — two ``quality_cost`` rows and their delta. Read-only; the confirm
    then calls the existing redo path with the chosen quality's provider."""
    draft = quality_cost(project, shot_id, "draft", provider)
    final = quality_cost(project, shot_id, "final", provider)
    return {
        "shot": shot_id,
        "draft": draft,
        "final": final,
        "delta": round(final["estimated_cost"] - draft["estimated_cost"], 6),
        "currency": draft["currency"] or final["currency"],
    }


# ================================================================= rendering


def _shell(title: str, token: str, body: str, project: Any) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    nav, bcls = chrome(PAGE_PATH, project)

    return (
        "<!doctype html>\n"
        f'<html lang="{HTML_LANG}">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/lab.css">\n'
        + GLOSSARY_HEAD
        + '<script src="/common.js" defer></script>\n'
        + '<script src="/lab.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{PAGE_PATH}" class="{bcls}">\n'
        + SKIP_LINK_HTML + nav
        + "\n" + main_open() + "\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        + NOSCRIPT_HTML + "\n"
        + "</body>\n</html>\n"
    )


def _shot_picker(shot_ids: list[str], active: str) -> str:
    if not shot_ids:
        return ""
    opts = "".join(
        f'<a class="lab-pick{" active" if sid == active else ""}" '
        f'href="/lab?shot={quote(sid)}">{_e(sid)}</a>'
        for sid in shot_ids
    )
    return f'<div class="lab-picker">{_gl("shot", "镜头")}: {opts}</div>'


# ------------------------------------------------------------ panel A: refs


def _ref_row_html(row: dict[str, Any]) -> str:
    mark = "✓" if row["exists"] else ("URL" if row["is_url"] else "缺失")
    mark_cls = "ok" if row["exists"] else ("muted" if row["is_url"] else "err")
    return (
        '<li class="lab-refrow">'
        f'<span class="chip">{_e(row["tier"])}</span>'
        f'<span class="lab-refkind muted">{_e(row["kind"])}·{_e(row["role"])}</span>'
        f'<span class="lab-refpath">{_e(row["ref"])}</span>'
        f'<span class="lab-refmark {mark_cls}">{_e(mark)}</span>'
        '</li>'
    )


def _budget_html(budget: dict[str, Any]) -> str:
    if not budget.get("active"):
        prov = budget.get("provider")
        note = (f"生成来源 {prov} 未配置参考数量上限" if prov
                else "当前 生成来源 无参考数量上限")
        return (f'<div class="lab-budget muted">{_e(note)} — 投递与今日一致</div>')
    caps = []
    if budget.get("max_images") is not None:
        caps.append(f"图片 ≤ {budget['max_images']}")
    if budget.get("max_videos") is not None:
        caps.append(f"视频 ≤ {budget['max_videos']}")
    head = (f'<div class="lab-budget-head">{_gl("budget", "参考预算")} '
            f'({_e(budget.get("provider") or "?")}) · {_e(" · ".join(caps))}</div>')
    sel = "".join(
        f'<li class="lab-sel"><span class="lab-refmark ok">✓</span> '
        f'<span class="chip">{_e(s["tier"])}</span> {_e(s["ref"])} '
        f'<span class="muted">{_e(s["role"])}</span></li>'
        for s in budget.get("selected", []))
    om = "".join(
        f'<li class="lab-om"><span class="lab-refmark err">✗</span> '
        f'<span class="chip">{_e(o["tier"])}</span> {_e(o["ref"])} '
        f'<span class="lab-impact">— {_e(o["reason"])}</span></li>'
        for o in budget.get("omitted", []))
    selblock = f'<ul class="lab-list">{sel}</ul>' if sel else ""
    omblock = (f'<div class="lab-omhead">未投递<span class="mj-en" aria-hidden="true"> (omitted)</span>:</div>'
               f'<ul class="lab-list">{om}</ul>') if om else ""
    return f'<div class="lab-budget">{head}{selblock}{omblock}</div>'


def _cleanliness_html(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return '<div class="muted">参考质量检查：未发现问题<span class="mj-en" aria-hidden="true"> (clean)</span></div>'
    rows = []
    for f in findings:
        cls = _REF_LEVEL_CLASS.get(f["level"], "st-missing")
        zh = _REF_LEVEL_ZH.get(f["level"], f["level"])
        hint = (f'<div class="lab-hint muted">→ {_e(f["hint"])}</div>'
                if f.get("hint") else "")
        rows.append(
            f'<li class="lab-finding">'
            f'<span class="badge {cls}">{_e(zh)}</span> '
            f'<span class="lab-fcode muted">{_e(f["code"])}</span>'
            f'<div class="lab-fmsg">{_e(f["message"])}</div>{hint}'
            f'</li>')
    return f'<ul class="lab-list">{"".join(rows)}</ul>'


def _refs_editor_html(param_refs: list[str]) -> str:
    items = "".join(
        f'<li class="lab-editrow" data-ref="{_e(r)}">'
        f'<span class="lab-editpath">{_e(r)}</span>'
        f'<span class="lab-editbtns">'
        f'<button class="btn ghost mini" data-lab="ref-up">↑</button>'
        f'<button class="btn ghost mini" data-lab="ref-down">↓</button>'
        f'<button class="btn ghost mini" data-lab="ref-del">删</button>'
        f'</span></li>'
        for r in param_refs)
    empty = '' if param_refs else '<li class="muted lab-empty">（未挑选专属参考）</li>'
    return (
        '<div class="lab-refeditor">'
        f'<div class="lab-subh">本镜头专属参考<span class="mj-en" aria-hidden="true"> (generation.params.refs)</span></div>'
        f'<ul class="lab-list lab-editlist" id="lab-refedit">{items}{empty}</ul>'
        '<div class="lab-refadd">'
        '<input class="lab-in" id="lab-refadd-in" type="text" '
        'placeholder="新增参考路径或 URL，回车添加">'
        '<button class="btn mini" data-lab="ref-add">加入</button>'
        '<button class="btn ghost mini" data-lab="ref-save">保存参考</button>'
        '</div>'
        '<div class="lab-refupload">'
        '<label class="btn ghost mini" for="lab-reffile">上传参考图</label>'
        '<input id="lab-reffile" type="file" accept="image/*,video/*" hidden>'
        '<span class="muted lab-uphint">上传后自动加入本镜头参考</span>'
        '</div>'
        '</div>'
    )


def _library_suggestion_card_html(row: dict[str, Any]) -> str:
    thumb = row.get("thumb")
    thumb_html = (f'<img class="lab-thumb" src="/lib-thumb/{_e(row["hash8"])}" alt="">'
                 if thumb else '<div class="lab-thumb lab-nothumb muted">无预览</div>')
    matched = "、".join(row.get("matched") or [])
    return (
        f'<div class="lab-suggest-card" data-hash="{_e(row["hash8"])}">'
        f'{thumb_html}'
        f'<div class="lab-suggest-name" title="{_e(row.get("name"))}">{_e(row.get("name"))}</div>'
        f'<div class="lab-suggest-meta muted">{_e(row.get("kind"))} · 命中:{_e(matched)}</div>'
        '<button type="button" class="btn mini" data-lab="suggest-use" '
        f'data-hash="{_e(row["hash8"])}" title="复制入项目参考并加入本镜头">采用</button>'
        '</div>'
    )


def _library_suggestions_html(rows: list[dict[str, Any]]) -> str:
    """素材库建议 strip (round X agent XF, pain #7/#8) — deterministic tag/kind
    matches from :func:`manju.core.library.suggest_from_library`, offered
    right where a director is already deciding this shot's refs. Empty when
    nothing matched — no placeholder noise on the common case."""
    if not rows:
        return ""
    cards = "".join(_library_suggestion_card_html(r) for r in rows)
    return (
        '<div class="lab-suggest">'
        '<div class="lab-subh">素材库建议<span class="mj-en" aria-hidden="true"> (Library suggestions)</span> '
        '<span class="muted">(标签匹配本镜头角色/场景)</span></div>'
        f'<div class="lab-suggest-row">{cards}</div>'
        '</div>'
    )


def _panel_refs(data: dict[str, Any]) -> str:
    rows = data["ref_rows"]
    ref_rows = "".join(_ref_row_html(r) for r in rows) \
        or '<li class="muted">（没有解析到参考）</li>'
    missing = sum(1 for row in rows if not row.get("exists") and not row.get("is_url"))
    findings = data.get("cleanliness") or []
    omitted = (data.get("budget") or {}).get("omitted") or []
    diagnostics_open = " open" if missing or findings or omitted else ""
    summary_bits = [f'{len(rows)} 项参考']
    if missing:
        summary_bits.append(f'{missing} 项缺失')
    if findings:
        summary_bits.append(f'{len(findings)} 项需检查')
    if omitted:
        summary_bits.append(f'{len(omitted)} 项未投递')
    summary = " · ".join(summary_bits)
    return (
        '<section class="lab-panel" id="lab-a" data-shot="' + _e(data["shot"]) + '">'
        '<h2>参考<span class="mj-en" aria-hidden="true"> (References)</span></h2>'
        f'<div class="lab-panel-summary"><strong>{_e(summary)}</strong>'
        '<span class="muted">自动解析的设定参考不会被覆盖；专属参考只影响当前镜头。</span></div>'
        + _refs_editor_html(data["param_refs"])
        + _library_suggestions_html(data.get("library_suggestions") or [])
        + f'<details class="lab-detail-group"{diagnostics_open}>'
        '<summary>参考来源与质量检查<span class="mj-en" aria-hidden="true"> (lineage &amp; diagnostics)</span></summary>'
        '<div class="lab-detail-body">'
        '<div class="lab-subh">解析到的参考<span class="mj-en" aria-hidden="true"> (tier lineage)</span></div>'
        f'<ul class="lab-list">{ref_rows}</ul>'
        + _budget_html(data["budget"])
        + '<div class="lab-subh">参考质量检查<span class="mj-en" aria-hidden="true"> (Cleanliness)</span></div>'
        + _cleanliness_html(findings)
        + '</div></details>'
        + '</section>'
    )


# ---------------------------------------------------------- panel B: prompts


def _prompt_block(
    label: str,
    term: str | None,
    text: str,
    *,
    technical: str | None = None,
) -> str:
    lab = _gl(term, label) if term else _e(label)
    if technical:
        lab += f'<span class="mj-en" aria-hidden="true"> ({_e(technical)})</span>'
    body = _e(text) if text else '<span class="muted">（空）</span>'
    return (f'<div class="lab-prompt">'
            f'<div class="lab-plabel">{lab}</div>'
            f'<pre class="lab-ptext">{body}</pre></div>')


def _provider_trace_html(prov: dict[str, Any]) -> str:
    why_zh = {"explicit": "钦定", "rule": "规则命中", "fallback": "兜底链"}
    why = prov.get("why", "")
    why_label = why_zh.get(why, why)
    order = " → ".join(_e(p) for p in prov.get("order") or []) or "—"
    err = (f'<div class="lab-hint err">路由错误: {_e(prov.get("routing_error"))}</div>'
           if prov.get("routing_error") else "")
    return (
        '<div class="lab-trace panel2">'
        f'<div>{_gl("provider", "生成来源")}: <b>{_provider_label_html(prov.get("label") or prov.get("chosen") or "—")}</b> '
        f'<span class="chip">{_e(why_label)}</span></div>'
        f'<div class="muted lab-order">备用顺序: {order}</div>'
        f'{err}</div>'
    )


def _checks_html(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return '<div class="muted">单动作检查：无发现</div>'
    rows = []
    for c in checks:
        cls = "st-stale" if c.get("level") == "warning" else "st-manual"
        zh = "警告" if c.get("level") == "warning" else "提示"
        split = ""
        if c.get("split"):
            subs = c["split"].get("sub_shots", [])
            lines = "".join(
                f'<div class="lab-subshot">#{s["index"]} '
                f'<span class="muted">{s["duration_ms"]}ms</span> {_e(s["text"])}</div>'
                for s in subs)
            copy_text = "\n".join(
                f'{s["index"]}. {s["text"]} ({s["duration_ms"]}ms)' for s in subs)
            split = (
                '<div class="lab-split">'
                '<div class="lab-subh2">拆分建议 (可复制):</div>'
                f'{lines}'
                f'<button class="btn ghost mini" data-lab="copy" '
                f'data-copy="{_e(copy_text)}">复制拆分</button>'
                '</div>')
        rows.append(
            f'<li class="lab-finding">'
            f'<span class="badge {cls}">{_e(zh)}</span> '
            f'<span class="lab-fcode muted">{_e(c["code"])}</span>'
            f'<div class="lab-fmsg">{_e(c["message"])}</div>'
            f'<div class="lab-hint muted">→ {_e(c.get("suggestion") or "")}</div>'
            f'{split}</li>')
    return f'<ul class="lab-list">{"".join(rows)}</ul>'


def _scaffold_html(bundle: dict[str, Any]) -> str:
    """The 按关键帧拆分 helper — a COPYABLE breakdown preview + an explicit apply
    button. The preview is computed read-only from the action text; only the
    apply POST writes into ``keyframes`` (via the scaffold path)."""
    from ..media.boards import breakdown_action

    # derive the action text EXACTLY as the apply handler does (action.main, then
    # generation.prompt_override) so the preview and what gets written agree.
    spec = bundle.get("shot_spec") or {}
    spec = spec if isinstance(spec, dict) else {}
    action = str((spec.get("action") or {}).get("main") or "").strip()
    if not action:
        action = str((spec.get("generation") or {}).get("prompt_override") or "")
    n = 4
    beats = breakdown_action(action, n)
    preview = "".join(
        f'<div class="lab-beat">#{i + 1} {_e(b)}</div>' for i, b in enumerate(beats))
    copy_text = "\n".join(f"{i + 1}. {b}" for i, b in enumerate(beats))
    return (
        '<div class="lab-scaffold panel2">'
        f'<div class="lab-subh">按关键帧拆分<span class="mj-en" aria-hidden="true"> (storyboard scaffold)</span></div>'
        '<div class="lab-scaffold-ctl">'
        f'<label class="muted">拆成</label>'
        '<input class="lab-in lab-n" id="lab-scaffold-n" type="number" '
        f'min="2" max="9" value="{n}"> <label class="muted">个关键帧</label>'
        '</div>'
        f'<div class="lab-beats" id="lab-beats">{preview}</div>'
        '<div class="lab-scaffold-btns">'
        f'<button class="btn ghost mini" data-lab="copy" data-copy="{_e(copy_text)}">复制建议</button>'
        '<button class="btn mini" data-lab="scaffold-apply">应用为关键帧</button>'
        '</div>'
        '<div class="muted lab-uphint">应用会写入 shots/*.yaml 的 keyframes（尊重锁定）</div>'
        '</div>'
    )


def _panel_prompts(data: dict[str, Any]) -> str:
    b = data["bundle"]
    cost = b.get("cost") or {}
    duration_ms = int(cost.get("duration_ms") or 0)
    duration_label = f"{duration_ms / 1000:g} 秒" if duration_ms else "时长待定"
    cost_line = (
        f'{_gl("dry_run", "试算")}：'
        f'<b>{_e(cost.get("estimated_cost"))} {_e(cost.get("currency") or "")}</b> '
        f'<span class="muted">· {duration_label}</span>')
    override = ""
    try:
        override = str((b.get("shot_spec") or {}).get("generation", {})
                       .get("prompt_override") or "")
    except Exception:
        override = ""
    editor_open = " open" if override.strip() else ""
    editor = (
        f'<details class="lab-detail-group"{editor_open}>'
        '<summary>镜头结构与高级改写</summary>'
        '<div class="lab-detail-body">'
        + _scaffold_html(b)
        + '<div class="lab-override">'
        '<div class="lab-subh">改写视频提示词<span class="mj-en" aria-hidden="true"> (generation.prompt_override)</span></div>'
        f'<textarea class="lab-ta" id="lab-override">{_e(override)}</textarea>'
        '<button class="btn mini" data-lab="override-save">保存改写</button>'
        '</div></div></details>')
    checks = b.get("checks") or []
    checks_html = (
        '<div class="lab-attention">'
        '<div class="lab-subh">动作可生成性</div>'
        + _checks_html(checks)
        + '</div>'
        if checks else '<div class="lab-inline-ok">动作检查未发现明显问题。</div>'
    )
    return (
        '<section class="lab-panel" id="lab-b">'
        '<h2>提示词<span class="mj-en" aria-hidden="true"> (Prompts)</span></h2>'
        + _prompt_block("当前视频提示词", None, b.get("video_prompt") or "", technical="video")
        + '<div class="lab-prompt-summary panel2">'
        + f'<span>{_gl("provider", "生成来源")}: <b>{_provider_label_html((b.get("provider") or {}).get("label") or (b.get("provider") or {}).get("chosen") or "—")}</b></span>'
        + f'<span>{cost_line}</span>'
        + '</div>'
        + checks_html
        + '<details class="lab-detail-group">'
        '<summary>其它提示词与路由详情</summary>'
        '<div class="lab-detail-body">'
        + _prompt_block("图像提示词", None, b.get("image_prompt") or "", technical="image")
        + _prompt_block("导演备注", None, b.get("director_prompt") or "", technical="director")
        + _prompt_block("反向提示词", None, b.get("negative_prompt") or "", technical="negative")
        + _provider_trace_html(b.get("provider") or {})
        + '</div></details>'
        + editor
        + '</section>'
    )


# ------------------------------------------------------- panel C: candidates


def _candidate_card(c: dict[str, Any]) -> str:
    thumb = (f'<img class="lab-thumb" src="{_e(c["thumb"])}" alt="" loading="lazy">'
             if c.get("thumb") else '<div class="lab-thumb lab-nothumb muted">无预览</div>')
    sel = ' selected' if c.get("selected") else ''
    selbadge = '<span class="badge st-fresh">已选用</span>' if c.get("selected") else ''
    cost = (f'{c["cost"]} {c.get("currency") or ""}' if c.get("cost") is not None else "本地/免费")
    meta = (f'<div class="lab-cmeta muted">'
            f'{_gl("provider", "来源")}: {_e(c["provider"])} · 花费: {_e(cost)}'
            + (f' · {_e(str(c["created"])[:19])}' if c.get("created") else '') + '</div>')
    dur = c.get("duration_ms")
    mid = int(dur) // 2 if dur else 0
    pick_button = (
        '<button class="btn mini" type="button" disabled>当前选择</button>'
        if c.get("selected") else '<button class="btn mini" data-lab="pick">选用</button>'
    )
    return (
        f'<div class="lab-cand{sel}" data-take="{_e(c["name"])}" '
        f'data-media="{_e(c.get("media") or "")}">'
        f'{thumb}'
        f'<div class="lab-chead"><b>{_gl("take", c["name"])}</b> {selbadge}</div>'
        f'{meta}'
        '<div class="lab-cbtns">'
        + pick_button
        + '<span class="lab-saveref">'
        f'<input class="lab-in lab-atms" type="number" min="0" value="{mid}" '
        'title="截帧毫秒 at_ms">'
        '<select class="lab-in lab-dest">'
        '<option value="refs">存入本镜头参考</option>'
        '<option value="library">存入素材库</option>'
        '</select>'
        '<button class="btn ghost mini" data-lab="saveref">存为参考</button>'
        '</span>'
        '</div>'
        '</div>'
    )


def _generate_html(data: dict[str, Any]) -> str:
    return (
        '<div class="lab-gen panel2">'
        f'<div class="lab-subh">生成候选<span class="mj-en" aria-hidden="true"> (Generate {_gl("take", "version")})</span></div>'
        '<div class="lab-genrow">'
        f'<label class="muted">{_gl("provider", "生成来源")}</label>'
        '<input class="lab-in" id="lab-gen-provider" type="text" '
        'placeholder="留空=智能派单默认">'
        '</div>'
        '<div class="lab-genrow lab-quality" id="lab-quality">'
        '<button class="lab-qbtn active" data-q="draft">草稿<span class="mj-en" aria-hidden="true"> Draft</span></button>'
        '<button class="lab-qbtn" data-q="final">成片<span class="mj-en" aria-hidden="true"> Final</span></button>'
        '</div>'
        f'<div class="lab-gendelta muted" id="lab-gendelta">选择质量档查看 {_gl("dry_run", "试跑")} 花费</div>'
        '<button class="btn" data-lab="generate">先试算，再确认生成…</button>'
        '</div>'
    )


def _panel_candidates(data: dict[str, Any]) -> str:
    shot_id = str(data.get("shot") or "")
    cards = "".join(_candidate_card(c) for c in data["candidates"]) \
        or '<div class="muted lab-empty-candidates">还没有候选。可以在本页生成，或导入已有视频。</div>'
    return (
        '<section class="lab-panel" id="lab-c">'
        '<h2>候选<span class="mj-en" aria-hidden="true"> (Candidates)</span></h2>'
        + _generate_html(data)
        + f'<div class="lab-cands">{cards}</div>'
        + '<div class="lab-candidate-next">'
        + f'<a class="btn ghost mini" href="/ingest?shot={quote(shot_id)}&amp;role=take">导入本地候选</a>'
        + f'<a class="btn mini" href="/review?shot={quote(shot_id)}">去审片</a>'
        + '</div>'
        + '</section>'
    )


def render(project: Any, token: str, query: dict[str, list[str]]) -> str:
    """Render the 镜头实验室 page for ``?shot=<id>`` (defaults to the first shot)."""
    shot_ids = project.shot_ids()
    want = (query.get("shot") or [None])[0]
    shot_id = want if (want and want in shot_ids) else (shot_ids[0] if shot_ids else None)

    from .shot_journey import shot_journey_html

    head = ('<div class="page-h"><h1>镜头实验室<span class="mj-en" aria-hidden="true"> (Shot lab)</span></h1>'
            '<span class="muted">一次只处理一个镜头：整理参考，检查提示词，再获得可审片的候选。</span></div>')
    journey = shot_journey_html(PAGE_PATH, project, shot_id=shot_id)
    if shot_id is None:
        return _shell(
            "镜头实验室",
            token,
            head + journey + ('<div class="panel"><p><b>项目里还没有镜头。</b></p>'
                              '<p class="muted">先建立镜头计划，再回来准备参考、提示词和候选。</p>'
                              '<a class="btn" href="/create">去创作</a></div>'),
            project,
        )

    try:
        data = lab_data(project, shot_id)
    except Exception as exc:  # a broken shot must not break the whole page
        return _shell(
            "镜头实验室",
            token,
            head + journey + _shot_picker(shot_ids, shot_id)
            + f'<p class="err panel">{_e(" ".join(str(exc).split()))}</p>',
            project,
        )

    body = (
        head
        + journey
        + _shot_picker(shot_ids, shot_id)
        + f'<div class="lab-grid" data-shot="{_e(shot_id)}">'
        + _panel_refs(data)
        + _panel_prompts(data)
        + _panel_candidates(data)
        + '</div>'
    )
    return _shell("镜头实验室 " + shot_id, token, body, project)


# ============================================================ assets (css/js)


def render_lab_css() -> str:
    return _LAB_CSS


def render_lab_js() -> str:
    return _LAB_JS


_LAB_CSS = """
/* 镜头实验室 shot lab (round-U). Loaded AFTER /app.css + /pages.css; reuses their
   palette (--panel/--panel2/--line/--muted/--accent/--ok/--warn/--err/--mono)
   and badge chips (.badge.st-*). */
.lab-picker { margin: .4rem 0 .9rem; display: flex; flex-wrap: wrap; gap: .35rem; align-items: center; }
.lab-pick {
  padding: .12rem .5rem; border: 1px solid var(--line); border-radius: 999px;
  background: var(--panel2); color: var(--fg); font-size: .8rem; text-decoration: none;
}
.lab-pick.active { background: var(--accent); color: #0b1220; border-color: var(--accent); }
.lab-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1rem; align-items: start; }
@media (max-width: 1100px) {
  .lab-grid { grid-template-columns: 1fr; }
  .lab-prompt-summary { align-items: flex-start; }
}
.lab-panel {
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: .8rem .9rem; min-width: 0;
}
.lab-panel h2 { font-size: 1rem; margin: 0 0 .6rem; border-bottom: 1px solid var(--line); padding-bottom: .35rem; }
.lab-panel-summary {
  display: flex; flex-direction: column; gap: .18rem; margin: -.05rem 0 .65rem;
  font-size: .8rem; line-height: 1.45;
}
.lab-detail-group {
  margin-top: .65rem; border-top: 1px solid var(--line); padding-top: .55rem;
}
.lab-detail-group > summary {
  cursor: pointer; color: var(--fg); font-size: .82rem; font-weight: 650;
  list-style-position: outside;
}
.lab-detail-group[open] > summary { margin-bottom: .45rem; }
.lab-detail-body { display: flow-root; }
.lab-prompt-summary {
  display: flex; justify-content: space-between; align-items: baseline;
  gap: .45rem .8rem; flex-wrap: wrap; font-size: .8rem;
}
.lab-inline-ok {
  margin: .55rem 0; padding: .45rem .55rem; border: 1px solid rgba(111,220,160,.22);
  border-radius: 7px; color: var(--muted); background: rgba(111,220,160,.04); font-size: .8rem;
}
.lab-attention { margin-top: .55rem; }
.panel2 { background: var(--panel2); border: 1px solid var(--line); border-radius: 8px; padding: .5rem .6rem; margin: .5rem 0; }
.lab-subh { font-size: .82rem; color: var(--muted); margin: .7rem 0 .3rem; font-weight: 600; }
.lab-subh2 { font-size: .78rem; color: var(--muted); margin: .3rem 0 .2rem; }
.lab-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: .3rem; }
.lab-refrow, .lab-sel, .lab-om, .lab-finding, .lab-editrow {
  display: flex; flex-wrap: wrap; gap: .35rem; align-items: baseline;
  font-size: .82rem; padding: .25rem 0; border-bottom: 1px solid var(--line);
}
.lab-refkind { font-size: .74rem; }
.lab-refpath, .lab-editpath { font-family: var(--mono); word-break: break-all; flex: 1 1 auto; min-width: 0; }
.lab-refmark { font-size: .78rem; font-weight: 700; }
.lab-refmark.ok, .ok { color: var(--ok); }
.lab-refmark.err { color: var(--err); }
.chip { display: inline-block; padding: .02rem .4rem; border: 1px solid var(--line); border-radius: 999px; background: var(--panel2); font-size: .72rem; }
.lab-budget { margin: .5rem 0; font-size: .82rem; }
.lab-budget-head, .lab-omhead { font-weight: 600; margin: .3rem 0 .2rem; }
.lab-impact { color: var(--warn); }
.lab-finding { flex-direction: column; align-items: stretch; gap: .15rem; }
.lab-fmsg { font-size: .82rem; }
.lab-fcode { font-size: .72rem; font-family: var(--mono); }
.lab-hint { font-size: .76rem; }
.lab-hint.err { color: var(--err); }
.lab-prompt { margin: .4rem 0; }
.lab-plabel { font-size: .78rem; color: var(--muted); margin-bottom: .15rem; }
.lab-ptext {
  margin: 0; padding: .45rem .55rem; background: var(--panel2); border: 1px solid var(--line);
  border-radius: 6px; font-family: var(--mono); font-size: .8rem; white-space: pre-wrap;
  word-break: break-word; max-height: 180px; overflow: auto;
}
.lab-trace { font-size: .82rem; }
.lab-order { font-size: .76rem; margin-top: .2rem; }
.lab-cost { font-size: .86rem; }
.lab-subshot, .lab-beat { font-size: .8rem; padding: .1rem 0; }
.lab-split { margin-top: .3rem; }
.lab-scaffold-ctl, .lab-genrow { display: flex; gap: .4rem; align-items: center; margin: .3rem 0; flex-wrap: wrap; }
.lab-scaffold-btns, .lab-refadd, .lab-refupload { display: flex; gap: .4rem; align-items: center; margin: .35rem 0; flex-wrap: wrap; }
.lab-in {
  background: var(--panel2); color: var(--fg); border: 1px solid var(--line);
  border-radius: 6px; padding: .2rem .45rem; font: inherit; font-size: .8rem;
}
.lab-in.lab-n, .lab-in.lab-atms { width: 4.5rem; }
.lab-in#lab-refadd-in, .lab-in#lab-gen-provider { flex: 1 1 8rem; min-width: 6rem; }
.lab-ta {
  width: 100%; min-height: 70px; box-sizing: border-box; background: var(--panel2);
  color: var(--fg); border: 1px solid var(--line); border-radius: 6px; padding: .4rem .5rem;
  font-family: var(--mono); font-size: .8rem; resize: vertical;
}
.lab-quality { gap: 0; }
.lab-qbtn {
  padding: .25rem .7rem; border: 1px solid var(--line); background: var(--panel2);
  color: var(--fg); cursor: pointer; font: inherit; font-size: .82rem;
}
.lab-qbtn:first-child { border-radius: 6px 0 0 6px; }
.lab-qbtn:last-child { border-radius: 0 6px 6px 0; border-left: 0; }
.lab-qbtn.active { background: var(--accent); color: #0b1220; border-color: var(--accent); }
.lab-gendelta { font-size: .82rem; margin: .3rem 0; }
.lab-cands { display: flex; flex-direction: column; gap: .6rem; margin-top: .6rem; }
.lab-cand { border: 1px solid var(--line); border-radius: 8px; padding: .5rem; background: var(--panel2); }
.lab-cand.selected { border-color: var(--accent); }
.lab-thumb { width: 100%; max-height: 130px; object-fit: cover; border-radius: 6px; display: block; }
.lab-nothumb { display: flex; align-items: center; justify-content: center; height: 60px; font-size: .78rem; }
.lab-chead { margin: .3rem 0 .15rem; display: flex; gap: .4rem; align-items: baseline; }
.lab-cmeta { font-size: .76rem; }
.lab-cbtns { display: flex; gap: .35rem; align-items: center; flex-wrap: wrap; margin-top: .35rem; }
.lab-cbtns button[disabled] { opacity: .72; cursor: default; }
.lab-saveref { display: inline-flex; gap: .3rem; align-items: center; flex-wrap: wrap; }
.lab-candidate-next {
  display: flex; gap: .4rem; align-items: center; justify-content: flex-end;
  flex-wrap: wrap; margin-top: .7rem; padding-top: .6rem; border-top: 1px solid var(--line);
}
.lab-empty-candidates { padding: .45rem 0; }
.gl { border-bottom: 1px dotted var(--muted); cursor: help; }
.lab-empty, .lab-uphint { font-size: .78rem; }

/* -------------------------------------------- 素材库建议 library suggestions */
.lab-suggest { margin-top: .7rem; }
.lab-suggest-row { display: flex; flex-wrap: wrap; gap: .5rem; margin-top: .3rem; }
.lab-suggest-card {
  width: 8.5rem; border: 1px solid var(--line); border-radius: 8px; padding: .4rem;
  background: var(--panel2); display: flex; flex-direction: column; gap: .2rem;
}
.lab-suggest-card .lab-thumb { max-height: 80px; }
.lab-suggest-name { font-size: .78rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.lab-suggest-meta { font-size: .7rem; }
"""


_LAB_JS = r"""
"use strict";
(function () {
  var GRID = document.querySelector(".lab-grid");
  var SHOT = GRID ? GRID.getAttribute("data-shot") : "";
  var quality = "draft";

  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }

  // Poll /api/jobs until the given job finishes (generate runs on the job
  // runner). Adaptive cadence: 100ms while a quick local job usually lands
  // (~2s), then 500ms — the runner is SERIALIZED, so a generate queued behind
  // a long build legitimately takes minutes; the old ~90s cap misreported it
  // as a failure. ~10min cap; a transient fetch error retries, never rejects.
  function pollJob(jobId, tries) {
    tries = tries || 0;
    var opts = (typeof manjuApiOptions === "function") ? manjuApiOptions() : {};
    var p = (typeof requestJson === "function")
      ? requestJson("GET", "/api/jobs", undefined, opts)
      : fetch("/api/jobs").then(function (r) { return r.json(); });
    return p.then(function (d) {
      var job = ((d && d.jobs) || []).filter(function (j) { return j.id === jobId; })[0];
      if (job && (job.state === "done" || job.state === "failed" || job.state === "canceled" || job.state === "interrupted")) return job;
      return job || null;
    }).catch(function () { return null; }).then(function (job) {
      if (job && (job.state === "done" || job.state === "failed" || job.state === "canceled" || job.state === "interrupted")) return job;
      if (tries > 1215) return null;  /* alive at the cap = still running, never "failed" */  // 20×100ms + ~1195×500ms ≈ 10min
      return new Promise(function (res) {
        setTimeout(res, tries < 20 ? 100 : 500);
      }).then(function () { return pollJob(jobId, tries + 1); });
    });
  }

  // -------- panel A: refs editor (writes generation.params.refs) --------
  function currentRefs() {
    var out = [];
    var list = document.getElementById("lab-refedit");
    if (!list) return out;
    list.querySelectorAll(".lab-editrow").forEach(function (li) {
      out.push(li.getAttribute("data-ref"));
    });
    return out;
  }
  function renderRefs(refs) {
    var list = document.getElementById("lab-refedit");
    if (!list) return;
    list.textContent = "";
    if (!refs.length) {
      var e = document.createElement("li");
      e.className = "muted lab-empty"; e.textContent = "（未挑选专属参考）";
      list.appendChild(e); return;
    }
    refs.forEach(function (r) {
      var li = document.createElement("li");
      li.className = "lab-editrow"; li.setAttribute("data-ref", r);
      var path = document.createElement("span");
      path.className = "lab-editpath"; path.textContent = r;
      var btns = document.createElement("span");
      btns.className = "lab-editbtns";
      [["ref-up", "↑"], ["ref-down", "↓"], ["ref-del", "删"]].forEach(function (b) {
        var bt = document.createElement("button");
        bt.className = "btn ghost mini"; bt.setAttribute("data-lab", b[0]);
        bt.textContent = b[1]; btns.appendChild(bt);
      });
      li.appendChild(path); li.appendChild(btns); list.appendChild(li);
    });
  }
  function saveRefs() {
    post("/api/lab/refs", { shot: SHOT, refs: currentRefs() }).then(function (res) {
      if (res.status === 200 && res.data.ok) { toast("参考已保存", true); reloadSoon(); }
      else { toast((res.data && res.data.error) || "保存失败", false); }
    }).catch(function () { toast("网络错误", false); });
  }

  // -------- upload a new ref via the existing /api/upload machinery --------
  function uploadRef(file) {
    var url = "/api/upload?name=" + encodeURIComponent(file.name);
    var upHeaders = { "X-Manju-Token": TOKEN };
    if (typeof PROJECT === "string" && PROJECT) upHeaders["X-Manju-Project"] = PROJECT;
    fetch(url, { method: "POST", headers: upHeaders, body: file })
      .then(function (r) { return r.json().then(function (d) { return { status: r.status, data: d }; }); })
      .then(function (res) {
        if (res.status === 200 && res.data.imported) {
          var refs = currentRefs(); refs.push(res.data.imported);
          renderRefs(refs);
          toast("已上传，点“保存参考”写入镜头", true);
        } else { toast((res.data && res.data.error) || "上传失败", false); }
      }).catch(function () { toast("上传失败", false); });
  }

  // -------- panel B: prompt override + scaffold --------
  function saveOverride() {
    var ta = document.getElementById("lab-override");
    post("/api/lab/prompt-override", { shot: SHOT, text: ta ? ta.value : "" })
      .then(function (res) {
        if (res.status === 200 && res.data.ok) { toast("提示词改写已保存", true); reloadSoon(); }
        else { toast((res.data && res.data.error) || "保存失败", false); }
      }).catch(function () { toast("网络错误", false); });
  }
  function applyScaffold() {
    var n = document.getElementById("lab-scaffold-n");
    post("/api/lab/scaffold", { shot: SHOT, n: n ? parseInt(n.value, 10) : 4 })
      .then(function (res) {
        if (res.status === 200 && res.data && res.data.ok) {
          toast("已写入关键帧", true);
          reloadSoon();  /* C3: model must refresh so panels show new keyframes */
        }
        else { toast((res.data && res.data.error) || "应用失败", false); }
      }).catch(function () { toast("网络错误", false); });
  }

  // -------- panel C: quality toggle + generate + pick + save-as-ref --------
  function refreshDelta() {
    var prov = document.getElementById("lab-gen-provider");
    var q = "shot=" + encodeURIComponent(SHOT);
    if (prov && prov.value.trim()) q += "&provider=" + encodeURIComponent(prov.value.trim());
    (typeof requestJson==="function"?requestJson("GET","/api/lab/generate-plan?"+q,undefined,typeof manjuApiOptions==="function"?manjuApiOptions():{}):fetch("/api/lab/generate-plan?"+q).then(function(r){return r.json();})).then(function (d) {
      var el = document.getElementById("lab-gendelta");
      if (!el || !d || !d.draft) return;
      var cur = quality === "draft" ? d.draft : d.final;
      var cy = d.currency || "";
      el.textContent = "草稿 " + d.draft.estimated_cost + cy + " · 成片 " + d.final.estimated_cost + cy
        + " · 差值 " + d.delta + cy + "（当前: " + (cur.provider || "自动") + "）";
    }).catch(function () {});
  }
  function doGenerate(btn) {
    var prov = document.getElementById("lab-gen-provider");
    btn.disabled = true;
    /* NO assume_yes on the first click: 生成候选 is a priced redo and must
     * pass the §8.3 ask_before gate. A waiting_user result is confirmed on
     * the workbench's 「确认花费」 banner (the ONE approval surface), which
     * re-posts this job's exact params with assume_yes. */
    post("/api/lab/generate", {
      shot: SHOT, quality: quality,
      provider: prov && prov.value.trim() ? prov.value.trim() : null
    }).then(function (res) {
      /* C3: accept real 202 or any envelope carrying a job (defense in depth). */
      var job = res.data && res.data.job;
      if ((res.status === 202 || res.status === 200) && job) {
        return pollJob(job.id).then(function (job2) {
          btn.disabled = false;
          var r2 = (job2 && job2.result) || {};
          if (job2 && job2.state === "done" && r2.waiting_user === true) {
            toast("生成是付费动作,需先确认花费 — 回工作台在「确认花费」横幅点确认", false);
          }
          else if (job2 && job2.state === "done") { toast("已生成候选", true); reloadSoon(); }
          else if (!job2) { toast("生成任务仍在排队/运行(轮询超时)— 完成后刷新本页可见", false); }
          else { toast(job2.error || "生成失败", false); }
        });
      }
      btn.disabled = false;
      toast((res.data && res.data.error) || "生成失败", false);
    }).catch(function () { btn.disabled = false; toast("网络错误", false); });
  }
  function pickTake(card) {
    post("/api/select", { shot: SHOT, take: card.getAttribute("data-take") }).then(function (res) {
      if (res.status === 200 && res.data.ok) { toast("已选用 " + card.getAttribute("data-take"), true); reloadSoon(); }
      else { toast((res.data && res.data.error) || "选用失败", false); }
    }).catch(function () { toast("网络错误", false); });
  }
  function saveAsRef(card) {
    var atms = card.querySelector(".lab-atms");
    var dest = card.querySelector(".lab-dest");
    post("/api/lab/save-ref", {
      shot: SHOT, take: card.getAttribute("data-take"),
      at_ms: atms ? parseInt(atms.value, 10) : 0,
      dest: dest ? dest.value : "refs"
    }).then(function (res) {
      if (res.status === 200 && res.data.ok) {
        toast(res.data.dest === "library" ? "已存入素材库" : "已存为本镜头参考", true);
        if (res.data.dest === "refs") reloadSoon();
      } else { toast((res.data && res.data.error) || "存储失败", false); }
    }).catch(function () { toast("网络错误", false); });
  }

  // -------- 素材库建议 library suggestion adopt (round X agent XF) --------
  // Reuses the EXISTING lib-use flow (copy blob -> media/refs) then the
  // existing refs-save flow (append into generation.params.refs) — two
  // sequential calls to already-shipped endpoints, no new server surface.
  function adoptSuggestion(card) {
    var hash = card.getAttribute("data-hash");
    post("/api/lib/use", { hash: hash, as: "refs" }).then(function (res) {
      if (res.status !== 200 || !res.data.ok) {
        toast((res.data && res.data.error) || "采用失败", false);
        return null;
      }
      var refs = currentRefs();
      if (refs.indexOf(res.data.dest) === -1) refs.push(res.data.dest);
      renderRefs(refs);
      return post("/api/lab/refs", { shot: SHOT, refs: refs });
    }).then(function (res2) {
      if (!res2) return;
      if (res2.status === 200 && res2.data.ok) { toast("已采用素材库建议", true); reloadSoon(); }
      else { toast((res2.data && res2.data.error) || "保存失败", false); }
    }).catch(function () { toast("网络错误", false); });
  }

  // -------- delegated click handling --------
  document.addEventListener("click", function (ev) {
    var t = ev.target;
    var copyBtn = t.closest && t.closest('[data-lab="copy"]');
    if (copyBtn) {
      var txt = copyBtn.getAttribute("data-copy") || "";
      if (navigator.clipboard) navigator.clipboard.writeText(txt).then(function () { toast("已复制", true); });
      else toast("请手动复制", false);
      return;
    }
    var btn = t.closest && t.closest("[data-lab]");
    if (btn) {
      var act = btn.getAttribute("data-lab");
      if (act === "ref-save") return saveRefs();
      if (act === "ref-add") {
        var inp = document.getElementById("lab-refadd-in");
        if (inp && inp.value.trim()) { var refs = currentRefs(); refs.push(inp.value.trim()); renderRefs(refs); inp.value = ""; }
        return;
      }
      if (act === "ref-del") { var row = btn.closest(".lab-editrow"); if (row) row.remove(); return; }
      if (act === "ref-up" || act === "ref-down") {
        var li = btn.closest(".lab-editrow");
        if (li) { var sib = act === "ref-up" ? li.previousElementSibling : li.nextElementSibling;
          if (sib && sib.classList.contains("lab-editrow")) {
            if (act === "ref-up") li.parentNode.insertBefore(li, sib);
            else li.parentNode.insertBefore(sib, li);
          } }
        return;
      }
      if (act === "override-save") return saveOverride();
      if (act === "scaffold-apply") return applyScaffold();
      if (act === "generate") return doGenerate(btn);
      if (act === "pick") { var c = btn.closest(".lab-cand"); if (c) pickTake(c); return; }
      if (act === "saveref") { var c2 = btn.closest(".lab-cand"); if (c2) saveAsRef(c2); return; }
      if (act === "suggest-use") {
        var c3 = btn.closest(".lab-suggest-card"); if (c3) adoptSuggestion(c3); return;
      }
    }
    var qbtn = t.closest && t.closest(".lab-qbtn");
    if (qbtn) {
      quality = qbtn.getAttribute("data-q");
      document.querySelectorAll(".lab-qbtn").forEach(function (b) { b.classList.toggle("active", b === qbtn); });
      refreshDelta();
    }
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Enter" && ev.target && ev.target.id === "lab-refadd-in") {
      ev.preventDefault();
      var inp = ev.target;
      if (inp.value.trim()) { var refs = currentRefs(); refs.push(inp.value.trim()); renderRefs(refs); inp.value = ""; }
    }
  });
  document.addEventListener("change", function (ev) {
    if (ev.target && ev.target.id === "lab-reffile" && ev.target.files && ev.target.files[0]) {
      uploadRef(ev.target.files[0]);
    }
  });
  if (GRID) refreshDelta();
})();
"""
