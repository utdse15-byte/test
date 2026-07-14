"""Round-T finishing pages: 字幕 subtitles, 混音 mixer, 打包 packaging v2.

The three surfaces that let a normal video's captions, sound and packaging be
finished WITHOUT opening JianYing, siblings of the round-S S8b pages
(:mod:`manju.gui.pages`) and built to the same stance:

  * server-rendered — a plain GET carries the real content baked into the DOM;
    ``/pages-t.js`` layers on the cue editor, the mixer round-trip and the
    packaging forms (cover strip / teaser range / live card preview);
  * CSP-safe — CSS/JS in external files, no inline handlers or ``style=``
    (dynamic geometry via the CSSOM), every mutating request carries the
    ``X-Manju-Token`` from the ``manju-token`` meta tag;
  * XSS-safe — all server text is ``html.escape``-d here and the script only
    ever writes ``textContent`` / element properties, never ``innerHTML``;
  * one core — subtitles write ``captions.srt`` (§3 manual-takeover), the mixer
    goes through build/mixer.read_mixer/apply_mixer verbatim, packaging validates
    through the real PackagingSpec; every change lands the same event the CLI would.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

from ..core.library import AUDIO_EXTS

__all__ = [
    "PAGE_PATHS_T",
    "render",
    "render_pages_t_css",
    "render_pages_t_js",
]

PAGE_PATHS_T = frozenset({"/subtitles", "/mixer", "/packaging"})


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _shell(title: str, token: str, active: str, body: str) -> str:
    from .pages import GLOSSARY_HEAD, chrome

    nav, bcls = chrome(active)
    return (
        "<!doctype html>\n"
        '<html lang="zh">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="manju-token" content="{_e(token)}">\n'
        f"<title>{_e(title)} · manju</title>\n"
        '<link rel="stylesheet" href="/app.css">\n'
        '<link rel="stylesheet" href="/pages.css">\n'
        '<link rel="stylesheet" href="/pages-t.css">\n'
        + GLOSSARY_HEAD
        + '<script src="/common.js" defer></script>\n<script src="/pages-t.js" defer></script>\n'
        "</head>\n"
        f'<body data-page="{_e(active)}" class="{bcls}">\n'
        + nav
        + "\n<main>\n"
        + body
        + "\n</main>\n"
        '<div id="toast"></div>\n'
        "<noscript><p>manju gui 需要 JavaScript (requires JavaScript)。</p></noscript>\n"
        "</body>\n</html>\n"
    )


def render(path: str, project: Any, token: str, query: dict[str, list[str]]) -> str:
    if path == "/subtitles":
        return render_subtitles(project, token)
    if path == "/mixer":
        return render_mixer(project, token)
    if path == "/packaging":
        return render_packaging(project, token)
    raise KeyError(path)


def _final_rel(project: Any) -> str:
    """Project-relative path of the newest final, or '' when none is rendered."""
    try:
        final = project.newest_final_path()
    except Exception:
        final = None
    return project.relpath(final) if final else ""


# ============================================================ 字幕 subtitles


def _fmt_ms(ms: int) -> str:
    ms = max(0, int(ms))
    s, milli = divmod(ms, 1000)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}.{milli:03d}"


def render_subtitles(project: Any, token: str) -> str:
    from .captions_edit import CaptionEditError, read_current_cues

    head = ('<div class="page-h"><h1>字幕 Subtitles</h1>'
            '<span class="muted">改字幕 → 存入 captions.srt(§3 首次编辑接管为 manual)</span></div>')
    try:
        info = read_current_cues(project)
    except CaptionEditError as exc:
        return _shell("字幕", token, "/subtitles", head + f'<p class="err panel">{_e(exc)}</p>')

    mode, cues = info["mode"], info["cues"]
    manual = mode == "manual"
    mode_badge = ('<span class="badge st-needs">manual 人工truth</span>' if manual
                  else '<span class="badge st-fresh">compiled 自动</span>')
    gen_note = (' · 编译对比: captions.generated.srt' if info["generated"] else "")
    revert = ('<button class="btn ghost" id="sub-revert">还原自动字幕</button>' if manual else "")

    explain = (
        '<div class="sub-explain panel">'
        f'<div>当前模式 {mode_badge}<span class="muted"> 来源 {_e(info["source"])}{_e(gen_note)}</span></div>'
        '<p class="muted">首次保存会把 <code>rules.captions.mode</code> 翻转为 '
        '<b>manual</b>:captions.srt 成为人工truth,编译版本转存 captions.generated.srt 作对比;'
        '重建时 ASS 从人工字幕逐字重烧 = <b>final 重渲染</b>。'
        '<span class="muted">（还原自动字幕会翻回 compiled 并重新生成。）</span></p>'
        f'{revert}</div>'
    )

    rows = "".join(_cue_row(c) for c in cues) or ""
    empty = ('<tr class="cue-empty"><td colspan="6" class="muted">'
             '暂无字幕 — 先 build 生成,或点“＋ 添加字幕”。</td></tr>' if not cues else "")
    table = (
        '<div class="sub-tablewrap panel"><table class="cue-table"><thead><tr>'
        '<th>#</th><th>起 start</th><th>止 end</th><th>说话人</th><th>文本 text</th><th>操作</th>'
        f'</tr></thead><tbody id="cue-body">{rows}{empty}</tbody></table>'
        '<div class="sub-actions">'
        '<button class="btn ghost" id="cue-add">＋ 添加字幕</button>'
        '<button class="btn" id="cue-save">保存字幕</button>'
        '<span class="muted">保存后运行 build 重烧 ASS(= final 重渲染)</span>'
        '</div>'
        '<div id="cue-warn" class="sub-warn muted"></div></div>'
    )

    final_rel = _final_rel(project)
    preview = (
        f'<div id="safe-area" class="sub-preview panel" data-final="{_e(final_rel)}">'
        '<h2>安全区预览 Safe-area preview</h2>'
        + ('<p class="muted">点某行字幕预览:取该 cue 中点的 final 帧,'
           '文本叠在安全区内(近似烧录样式)。</p>'
           if final_rel else
           '<p class="muted">还没有 final 渲染 — build 之后可预览烧录效果。</p>')
        + '<div class="sa-stage"><img id="sa-frame" alt="" hidden>'
        '<div id="sa-text" class="sa-caption"></div></div></div>'
    )

    body = head + explain + table + preview
    return _shell("字幕", token, "/subtitles", body)


def _cue_row(c: dict[str, Any]) -> str:
    idx = c.get("index", "")
    return (
        f'<tr class="cue-row" id="cue-{_e(idx)}" data-index="{_e(idx)}">'
        f'<td class="cue-i">{_e(idx)}</td>'
        f'<td><input class="cue-start" type="number" min="0" step="10" value="{_e(c["start_ms"])}">'
        f'<span class="cue-tc muted">{_e(_fmt_ms(c["start_ms"]))}</span></td>'
        f'<td><input class="cue-end" type="number" min="0" step="10" value="{_e(c["end_ms"])}">'
        f'<span class="cue-tc muted">{_e(_fmt_ms(c["end_ms"]))}</span></td>'
        f'<td><input class="cue-speaker" type="text" value="{_e(c.get("speaker", ""))}" '
        'placeholder="—" readonly></td>'
        f'<td><input class="cue-text" type="text" value="{_e(c["text"])}"></td>'
        '<td class="cue-ops">'
        '<button class="btn mini ghost" data-act="preview">看</button>'
        '<button class="btn mini ghost" data-act="split">拆分</button>'
        '<button class="btn mini ghost" data-act="merge">合并↓</button>'
        '<button class="btn mini ghost" data-act="del">删</button>'
        '</td></tr>'
    )


# ================================================================ 混音 mixer


def _audio_options(project: Any) -> list[dict[str, str]]:
    """Audio source candidates for the mixer pickers: project imports/refs audio
    plus every library audio asset (offered as a ``lib:<hash8>`` token, resolved
    into the project on apply)."""
    opts: list[dict[str, str]] = []
    for d, tag in ((project.imports_dir, "imports"), (project.refs_dir, "refs")):
        try:
            entries = sorted(d.rglob("*")) if d.exists() else []
        except OSError:
            entries = []
        for p in entries:
            if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
                rel = project.relpath(p)
                opts.append({"value": rel, "label": f"{tag}/{p.name}",
                             "url": "/media/" + quote(rel, safe="/")})
    try:
        from ..core.library import Library, _hex

        for a in Library().list_assets(kind="audio"):
            h = _hex(a["hash"])[:8]
            opts.append({"value": "lib:" + h, "label": "库/" + str(a.get("name") or h),
                         "url": "/lib-blob/" + h})
    except Exception:
        pass
    return opts


def _source_select(opts: list[dict[str, str]], current: str | None, *, cls: str) -> str:
    parts = [f'<select class="{cls}">']
    cur = current or ""
    matched = not cur
    parts.append(f'<option value="" data-audio=""{" selected" if not cur else ""}>'
                 '（无 none）</option>')
    for o in opts:
        sel = " selected" if o["value"] == cur else ""
        if sel:
            matched = True
        parts.append(f'<option value="{_e(o["value"])}" data-audio="{_e(o["url"])}"{sel}>'
                     f'{_e(o["label"])}</option>')
    if not matched and cur:  # a current source outside imports/refs — keep it selectable
        parts.append(f'<option value="{_e(cur)}" data-audio="/media/{quote(cur, safe="/")}" '
                     f'selected>{_e(cur)} (当前)</option>')
    parts.append("</select>")
    return "".join(parts)


def _num(cls: str, value: Any, *, step: str = "1", mn: str | None = None) -> str:
    mattr = f' min="{mn}"' if mn is not None else ""
    return f'<input class="{cls}" type="number" step="{step}"{mattr} value="{_e(value)}">'


def _bed_panel(title: str, bed: dict[str, Any], opts: list[dict[str, str]], *,
               prefix: str, hint: str) -> str:
    duck = bed.get("duck") or {}
    return (
        f'<div class="mx-bed panel" data-bed="{_e(prefix)}"><h2>{_e(title)}</h2>'
        f'<p class="muted">{_e(hint)}</p>'
        '<div class="mx-row"><label>音源 source</label>'
        + _source_select(opts, bed.get("source"), cls=f"{prefix}-source") +
        f'<audio class="mx-audition {prefix}-audition" controls preload="none"></audio></div>'
        '<div class="mx-row"><label>音量 gain (dB)</label>'
        + _num(f"{prefix}-gain", bed.get("gain_db", 0), step="0.5") +
        '</div>'
        # advanced rows (round U): in-point/fades/ducking hidden in 新手 mode
        '<div class="mx-row mj-pro-only"><label>入点 in-point start_offset (ms)</label>'
        + _num(f"{prefix}-start", bed.get("start_offset_ms", 0), step="50", mn="0") +
        '</div>'
        '<div class="mx-row mj-pro-only"><label>淡入 fade_in (ms)</label>'
        + _num(f"{prefix}-fadein", bed.get("fade_in_ms", 0), step="50", mn="0") +
        '</div>'
        '<div class="mx-row mj-pro-only"><label>淡出 fade_out (ms)</label>'
        + _num(f"{prefix}-fadeout", bed.get("fade_out_ms", 0), step="50", mn="0") +
        '</div>'
        '<div class="mx-row mj-pro-only"><label>闪避 ducking</label>'
        f'<input class="{prefix}-duck" type="checkbox"'
        + (" checked" if bed.get("ducking") else "") + "></div>"
        '<details class="mx-duck mj-pro-only"><summary class="muted">闪避参数 ducking shape</summary>'
        '<div class="mx-row"><label>threshold</label>'
        + _num(f"{prefix}-dth", duck.get("threshold", 0.05), step="0.01") + '</div>'
        '<div class="mx-row"><label>ratio</label>'
        + _num(f"{prefix}-dra", duck.get("ratio", 8.0), step="0.5") + '</div>'
        '<div class="mx-row"><label>attack_ms</label>'
        + _num(f"{prefix}-dat", duck.get("attack_ms", 5), step="1", mn="0") + '</div>'
        '<div class="mx-row"><label>release_ms</label>'
        + _num(f"{prefix}-dre", duck.get("release_ms", 250), step="10", mn="0") + '</div>'
        '</details></div>'
    )


def render_mixer(project: Any, token: str) -> str:
    from ..build.mixer import read_mixer

    head = ('<div class="page-h"><h1>混音 Mixer</h1>'
            '<span class="muted">1:1 绑定 read_mixer/apply_mixer · 人声/BGM/环境/音效</span></div>')
    try:
        mix = read_mixer(project)
    except Exception as exc:
        return _shell("混音", token, "/mixer", head + f'<p class="err panel">{_e(exc)}</p>')

    opts = _audio_options(project)

    voice = (
        '<div class="mx-voice panel"><h2>人声 Voice</h2>'
        '<div class="mx-row"><label>人声音量 voice_gain (dB)</label>'
        + _num("voice-gain", mix.get("voice_gain_db", 0), step="0.5") + '</div></div>'
    )
    music = _bed_panel("背景音乐 BGM (music)", mix.get("music") or {}, opts,
                       prefix="music", hint="从 imports/库 选曲;入点跳过前奏,淡入从静音起。")
    ambient = _bed_panel("环境床 Ambient", mix.get("ambient") or {}, opts,
                         prefix="ambient", hint="室内底噪/氛围,整片铺底,可闪避于人声。")

    sfx_rows = "".join(_sfx_row(s, opts) for s in (mix.get("sfx") or []))
    sfx = (
        '<div class="mx-sfx panel" data-opts-count="' + str(len(opts)) + '"><h2>音效 SFX</h2>'
        '<p class="muted">锚点语法 anchor: 空=从 0 绝对偏移 · '
        '<code>shot:&lt;id&gt;</code>=该镜起点 · <code>shot:&lt;id&gt;:end</code>=该镜结束。'
        '整表替换写入(full-list replace)。</p>'
        f'<table class="sfx-table"><thead><tr><th>音源</th><th>anchor at</th>'
        f'<th>offset_ms</th><th>gain_db</th><th></th></tr></thead>'
        f'<tbody id="sfx-body">{sfx_rows}</tbody></table>'
        '<button class="btn ghost" id="sfx-add">＋ 添加音效</button></div>'
    )

    trans = mix.get("transition") or {}
    transition = (
        '<div class="mx-trans panel mj-pro-only"><h2>转场音 Transition hit</h2>'
        '<div class="mx-row"><label>音源 source</label>'
        + _source_select(opts, trans.get("source"), cls="trans-source") +
        '<audio class="mx-audition trans-audition" controls preload="none"></audio></div>'
        '<div class="mx-row"><label>音量 gain (dB)</label>'
        + _num("trans-gain", trans.get("gain_db", -12), step="0.5") + '</div></div>'
    )

    footage = (
        '<div class="mx-footage panel"><h2>逐镜素材原声 Per-shot footage audio</h2>'
        '<p class="muted">每个镜头导入素材的自带声音在片段级处理 — 到 '
        '<a href="/edit">/edit 片段检查器</a> 调 gain/mute(避免重复)。</p></div>'
    )

    apply = (
        '<div class="mx-apply panel"><button class="btn" id="mx-apply">应用混音 Apply</button>'
        '<span class="muted">apply_mixer → 显示重建影响(timeline/final/segments)</span>'
        '<div id="mx-verdict" class="mx-verdict"></div></div>'
    )

    body = head + voice + music + ambient + sfx + transition + footage + apply
    return _shell("混音", token, "/mixer", body)


def _sfx_row(s: dict[str, Any], opts: list[dict[str, str]]) -> str:
    return (
        '<tr class="sfx-row">'
        '<td>' + _source_select(opts, s.get("source"), cls="sfx-source") +
        '<audio class="mx-audition sfx-audition" controls preload="none"></audio></td>'
        f'<td><input class="sfx-at" type="text" value="{_e(s.get("at", ""))}" '
        'placeholder="shot:S001:end"></td>'
        f'<td>{_num("sfx-offset", s.get("offset_ms", 0), step="50")}</td>'
        f'<td>{_num("sfx-gain", s.get("gain_db", -6), step="0.5")}</td>'
        '<td><button class="btn mini ghost" data-act="sfx-del">删</button></td></tr>'
    )


# ============================================================ 打包 packaging


# Round X (agent XG §C): 4 named preset combos on top of `template` (font
# scale / bg colour-or-gradient / text position — see media/card.py +
# media/html_card.py:CARD_STYLE_PRESETS for the actual knobs). "" is the
# classic/no-preset look, kept first so it is always the pre-selected default.
_CARD_PRESET_OPTS = (
    ("", "经典 classic"),
    ("mono_black", "简约黑"),
    ("white_big", "白底大字"),
    ("warm_gradient", "暖色渐变"),
    ("neon", "霓虹"),
)


def _card_form(kind: str, label: str, card: dict[str, Any]) -> str:
    tpl = card.get("template", "chapter")
    return (
        f'<div class="pk-card" data-kind="{_e(kind)}"><h3>{_e(label)}</h3>'
        '<label class="pk-check"><input type="checkbox" class="pk-enabled"'
        + (" checked" if card.get("enabled") else "") + '> 启用 enabled</label>'
        '<div class="mx-row"><label>模板 template</label>'
        f'<select class="pk-template">{_tpl_opts(tpl)}</select></div>'
        '<div class="mx-row"><label>预设样式 preset</label>'
        f'<select class="pk-preset">{_preset_opts(card.get("style_preset", ""))}</select></div>'
        f'<div class="mx-row"><label>标题 text</label>'
        f'<input class="pk-text" type="text" value="{_e(card.get("text", ""))}"></div>'
        f'<div class="mx-row"><label>副标题 subtext</label>'
        f'<input class="pk-subtext" type="text" value="{_e(card.get("subtext", ""))}"></div>'
        '<div class="mx-row"><label>时长 duration (ms)</label>'
        + _num("pk-duration", card.get("duration_ms", 2000), step="100", mn="0") + '</div>'
        '<button class="btn mini ghost" data-act="card-preview">预览卡片</button>'
        '<div class="pk-preview"><img class="pk-prev-img" alt="" hidden></div></div>'
    )


def _tpl_opts(current: str) -> str:
    out = []
    for v, lbl in (("chapter", "chapter 章节"), ("caption", "caption 字幕")):
        out.append(f'<option value="{v}"{" selected" if v == current else ""}>{lbl}</option>')
    return "".join(out)


def _preset_opts(current: str) -> str:
    out = []
    for v, lbl in _CARD_PRESET_OPTS:
        out.append(f'<option value="{_e(v)}"{" selected" if v == (current or "") else ""}>'
                   f'{_e(lbl)}</option>')
    return "".join(out)


def _image_options(project: Any) -> list[str]:
    from ..core.library import IMAGE_EXTS

    rels: list[str] = []
    for d in (project.imports_dir, project.refs_dir):
        try:
            entries = sorted(d.rglob("*")) if d.exists() else []
        except OSError:
            entries = []
        for p in entries:
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                rels.append(project.relpath(p))
    return rels


def _image_select(cls: str, current: str, images: list[str]) -> str:
    parts = [f'<select class="{cls}">']
    cur = current or ""
    parts.append(f'<option value=""{" selected" if not cur else ""}>（无 none）</option>')
    seen = False
    for rel in images:
        sel = " selected" if rel == cur else ""
        if sel:
            seen = True
        parts.append(f'<option value="{_e(rel)}"{sel}>{_e(rel)}</option>')
    if cur and not seen:
        parts.append(f'<option value="{_e(cur)}" selected>{_e(cur)} (当前)</option>')
    parts.append("</select>")
    return "".join(parts)


def render_packaging(project: Any, token: str) -> str:
    head = ('<div class="page-h"><h1>打包 Packaging v2</h1>'
            '<span class="muted">表单化 packaging.yaml · 存前校验 · 每次改动记事件</span></div>')
    try:
        spec = project.load_packaging()  # a validated PackagingSpec
    except Exception as exc:
        return _shell("打包", token, "/packaging",
                      head + f'<p class="err panel">{_e(exc)}</p>')
    pk = spec.model_dump()
    final_rel = _final_rel(project)
    images = _image_options(project)

    # intro / outro cards
    cards = (
        '<div class="pk-sect panel" data-section="cards"><h2>片头/片尾卡片 Intro / Outro</h2>'
        '<div class="pk-cards">'
        + _card_form("intro", "片头 Intro", pk["intro"])
        + _card_form("outro", "片尾 Outro", pk["outro"])
        + '</div><button class="btn" data-act="save" data-section="cards">保存卡片</button></div>'
    )

    # info cards list
    ic_rows = "".join(_infocard_row(c) for c in pk.get("info_cards", []))
    info_cards = (
        '<div class="pk-sect panel" data-section="info"><h2>信息卡 Info cards</h2>'
        '<p class="muted">章节/角色/信息卡,anchor 语法同 SFX。</p>'
        '<table class="ic-table"><thead><tr><th>kind</th><th>text</th><th>at</th>'
        '<th>offset_ms</th><th>duration_ms</th><th>template</th><th></th></tr></thead>'
        f'<tbody id="ic-body">{ic_rows}</tbody></table>'
        '<button class="btn ghost" id="ic-add">＋ 添加信息卡</button> '
        '<button class="btn" data-act="save" data-section="info">保存信息卡</button></div>'
    )

    # cover picker
    cover = pk["cover"]
    cover_sect = (
        f'<div class="pk-sect panel" data-section="cover" data-final="{_e(final_rel)}" '
        f'data-frame-ms="{_e(cover.get("frame_ms", 0))}">'
        '<h2>封面 Cover</h2>'
        '<div class="mx-row"><label>模式 mode</label>'
        f'<select class="cover-mode">{_cover_mode_opts(cover.get("mode", "frame"))}</select></div>'
        '<div class="cover-frame-ui">'
        + ('<p class="muted">拖动缩略图选帧,±步进微调,写入 cover.frame_ms。</p>'
           '<div id="cover-strip" class="pk-strip"></div>'
           '<div class="cover-fine"><button class="btn mini ghost" data-nudge="-500">−500</button>'
           '<button class="btn mini ghost" data-nudge="-100">−100</button>'
           f'<span id="cover-ms" class="cover-ms">{_e(cover.get("frame_ms", 0))}</span> ms'
           '<button class="btn mini ghost" data-nudge="100">+100</button>'
           '<button class="btn mini ghost" data-nudge="500">+500</button></div>'
           '<div class="cover-fine-frame"><img id="cover-fine-img" alt="" hidden></div>'
           if final_rel else '<p class="muted">还没有 final — build 之后可选帧。</p>')
        + '</div>'
        '<div class="cover-card-ui">'
        f'<div class="mx-row"><label>卡片标题 text</label>'
        f'<input class="cover-text" type="text" value="{_e(cover.get("text", ""))}"></div>'
        '<div class="mx-row"><label>模板 template</label>'
        f'<select class="cover-template">{_tpl_opts(cover.get("template", "chapter"))}</select></div>'
        '<button class="btn mini ghost" data-act="cover-card-preview">预览卡片封面</button>'
        '<div class="pk-preview"><img id="cover-card-img" alt="" hidden></div></div>'
        '<button class="btn" data-act="save" data-section="cover">保存封面</button></div>'
    )

    # teaser
    teaser = pk["teaser"]
    teaser_sect = (
        f'<div class="pk-sect panel" data-section="teaser" data-final="{_e(final_rel)}">'
        '<h2>预告片 Teaser</h2>'
        '<label class="pk-check"><input type="checkbox" class="teaser-enabled"'
        + (" checked" if teaser.get("enabled") else "") + '> 启用 enabled</label>'
        + ('<p class="muted">在条带上点两下选起点/终点(两个滑块),写 from_ms/duration_ms。</p>'
           '<div id="teaser-strip" class="pk-strip"></div>' if final_rel else "")
        + '<div class="mx-row"><label>from_ms</label>'
        + _num("teaser-from", teaser.get("from_ms", 0), step="100", mn="0") + '</div>'
        '<div class="mx-row"><label>duration_ms</label>'
        + _num("teaser-duration", teaser.get("duration_ms", 5000), step="100", mn="0") + '</div>'
        '<button class="btn" data-act="save" data-section="teaser">保存预告</button>'
        '<div class="teaser-warn muted"></div></div>'
    )

    branding = _branding_sect(pk, images)

    warn_bar = '<div id="pk-warn" class="pk-warnbar muted"></div>'
    body = head + warn_bar + cards + info_cards + cover_sect + teaser_sect + branding
    return _shell("打包", token, "/packaging", body)


def _cover_mode_opts(current: str) -> str:
    out = []
    for v, lbl in (("frame", "frame 取帧"), ("card", "card 卡片")):
        out.append(f'<option value="{v}"{" selected" if v == current else ""}>{lbl}</option>')
    return "".join(out)


def _infocard_row(c: dict[str, Any]) -> str:
    kinds = "".join(
        f'<option value="{k}"{" selected" if c.get("kind") == k else ""}>{k}</option>'
        for k in ("chapter", "role", "info"))
    return (
        '<tr class="ic-row">'
        f'<td><select class="ic-kind">{kinds}</select></td>'
        f'<td><input class="ic-text" type="text" value="{_e(c.get("text", ""))}"></td>'
        f'<td><input class="ic-at" type="text" value="{_e(c.get("at", ""))}" '
        'placeholder="shot:S001"></td>'
        f'<td>{_num("ic-offset", c.get("offset_ms", 0), step="50")}</td>'
        f'<td>{_num("ic-duration", c.get("duration_ms", 1500), step="100", mn="0")}</td>'
        f'<td><select class="ic-template">{_tpl_opts(c.get("template", "chapter"))}</select></td>'
        '<td><button class="btn mini ghost" data-act="ic-del">删</button></td></tr>'
    )


def _branding_sect(pk: dict[str, Any], images: list[str]) -> str:
    logo = pk["logo"]
    wm = pk["watermark"]
    badge = pk["badge"]
    cta = pk["cta"]

    def corner_opts(cls_current: str) -> str:
        return "".join(
            f'<option value="{v}"{" selected" if cls_current == v else ""}>{v}</option>'
            for v in ("tl", "tr", "bl", "br"))

    logo_html = (
        '<div class="pk-brand" data-brand="logo"><h3>Logo / 角标</h3>'
        '<label class="pk-check"><input type="checkbox" class="lg-enabled"'
        + (" checked" if logo.get("enabled") else "") + '> 启用</label>'
        '<div class="mx-row"><label>图片 image</label>'
        + _image_select("lg-image", logo.get("image", ""), images) + '</div>'
        '<div class="mx-row"><label>角 corner</label>'
        f'<select class="lg-corner">{corner_opts(logo.get("corner", "tr"))}</select></div>'
        '<div class="mx-row"><label>size_pct</label>'
        + _num("lg-size", logo.get("size_pct", 12.0), step="0.5") + '</div>'
        '<div class="mx-row"><label>margin_pct</label>'
        + _num("lg-margin", logo.get("margin_pct", 2.5), step="0.5") + '</div>'
        '<div class="mx-row"><label>opacity (0–1)</label>'
        + _num("lg-opacity", logo.get("opacity", 1.0), step="0.05") + '</div></div>'
    )
    wm_html = (
        '<div class="pk-brand" data-brand="watermark"><h3>水印 Watermark</h3>'
        '<label class="pk-check"><input type="checkbox" class="wm-enabled"'
        + (" checked" if wm.get("enabled") else "") + '> 启用</label>'
        f'<div class="mx-row"><label>文字 text</label>'
        f'<input class="wm-text" type="text" value="{_e(wm.get("text", ""))}"></div>'
        '<div class="mx-row"><label>图片 image</label>'
        + _image_select("wm-image", wm.get("image", ""), images) + '</div>'
        '<div class="mx-row"><label>opacity (0–1)</label>'
        + _num("wm-opacity", wm.get("opacity", 0.35), step="0.05") + '</div>'
        '<div class="mx-row"><label>size_pct</label>'
        + _num("wm-size", wm.get("size_pct", 30.0), step="1") + '</div>'
        '<div class="mx-row"><label>position</label>'
        '<select class="wm-position">'
        + "".join(f'<option value="{v}"{" selected" if wm.get("position") == v else ""}>{v}</option>'
                  for v in ("center", "diagonal_tile"))
        + '</select></div></div>'
    )
    badge_html = (
        '<div class="pk-brand" data-brand="badge"><h3>角标 Badge</h3>'
        '<label class="pk-check"><input type="checkbox" class="bd-enabled"'
        + (" checked" if badge.get("enabled") else "") + '> 启用</label>'
        f'<div class="mx-row"><label>文字 text</label>'
        f'<input class="bd-text" type="text" value="{_e(badge.get("text", ""))}"></div>'
        '<div class="mx-row"><label>角 corner</label>'
        f'<select class="bd-corner">{corner_opts(badge.get("corner", "tl"))}</select></div></div>'
    )
    cta_html = (
        '<div class="pk-brand" data-brand="cta"><h3>行动号召 CTA</h3>'
        '<label class="pk-check"><input type="checkbox" class="ct-enabled"'
        + (" checked" if cta.get("enabled") else "") + '> 启用</label>'
        f'<div class="mx-row"><label>文字 text</label>'
        f'<input class="ct-text" type="text" value="{_e(cta.get("text", ""))}"></div>'
        '<div class="mx-row"><label>at_end_ms(片尾窗口)</label>'
        + _num("ct-atend", cta.get("at_end_ms", 3000), step="100", mn="0") + '</div>'
        '<div class="mx-row"><label>position</label>'
        '<select class="ct-position">'
        + "".join(f'<option value="{v}"{" selected" if cta.get("position") == v else ""}>{v}</option>'
                  for v in ("bottom", "center"))
        + '</select></div></div>'
    )
    return (
        '<div class="pk-sect panel" data-section="branding"><h2>品牌叠加 Branding</h2>'
        '<div class="pk-brands">' + logo_html + wm_html + badge_html + cta_html + '</div>'
        '<button class="btn" data-act="save" data-section="branding">保存品牌</button></div>'
    )


# ============================================================ assets (css/js)


def render_pages_t_css() -> str:
    return _PAGES_T_CSS


def render_pages_t_js() -> str:
    return _PAGES_T_JS


_PAGES_T_CSS = """
.sub-explain code, .mx-sfx code, .info code { background:#1c2230; padding:1px 5px; border-radius:4px; }
.cue-table, .sfx-table, .ic-table { width:100%; border-collapse:collapse; }
.cue-row.cue-flash td { background: rgba(122, 162, 247, .25);
  transition: background 1.2s ease; }
.cue-table th, .sfx-table th, .ic-table th { text-align:left; font-weight:600;
  color:#8b93a3; font-size:12px; padding:6px 8px; border-bottom:1px solid #262c38; }
.cue-table td, .sfx-table td, .ic-table td { padding:5px 8px; vertical-align:top;
  border-bottom:1px solid #1a1f29; }
.cue-i { color:var(--accent); font-variant-numeric:tabular-nums; }
.cue-table input[type=number] { width:88px; }
.cue-table .cue-text, .sfx-table input[type=text] { width:100%; min-width:160px; }
.cue-tc { display:block; font-size:11px; }
.cue-ops, .sfx-row td:last-child, .ic-row td:last-child { white-space:nowrap; }
.btn.mini { padding:2px 8px; font-size:12px; }
.sub-actions, .cover-fine { display:flex; gap:10px; align-items:center;
  margin-top:12px; flex-wrap:wrap; }
.sub-warn, .teaser-warn, .pk-warnbar { margin-top:10px; }
.sub-warn:empty, .teaser-warn:empty, .pk-warnbar:empty { display:none; }
.sub-warn .w, .pk-warnbar .w { color:#e0af68; display:block; }
.sa-stage { position:relative; max-width:360px; margin-top:10px; background:#0b0e14;
  border-radius:8px; overflow:hidden; }
.sa-stage img { display:block; width:100%; }
.sa-caption { position:absolute; left:6%; right:6%; bottom:8%; text-align:center;
  color:#fff; font-weight:600; line-height:1.4; text-shadow:0 0 3px #000, 0 2px 6px #000;
  font-size:15px; pointer-events:none; }
.mx-row { display:flex; align-items:center; gap:10px; margin:7px 0; flex-wrap:wrap; }
.mx-row > label { min-width:220px; color:#c3c9d5; }
.mx-audition { height:30px; max-width:220px; }
.mx-duck { margin-top:8px; }
.mx-verdict { margin-top:10px; }
.mx-verdict .v { display:block; color:#9aa4b5; }
.pk-cards, .pk-brands { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:14px; }
.pk-card, .pk-brand { background:#12161f; border:1px solid #232a36; border-radius:8px;
  padding:12px; }
.pk-check { display:block; margin:6px 0; }
.pk-preview img, .cover-fine-frame img, .pk-prev-img { max-width:240px; margin-top:8px;
  border-radius:6px; display:block; }
.pk-strip { display:flex; gap:2px; overflow-x:auto; margin:10px 0; padding-bottom:4px; }
.pk-strip img { height:64px; cursor:pointer; border:2px solid transparent; border-radius:4px; }
.pk-strip img.sel { border-color:var(--accent); }
.pk-strip img.sel-a { border-color:#7ee787; }
.pk-strip img.sel-b { border-color:#e0af68; }
.cover-ms { color:var(--accent); font-variant-numeric:tabular-nums; }
.pk-sect { margin-bottom:16px; }
.cover-card-ui, .cover-frame-ui { margin:8px 0; }
"""


_PAGES_T_JS = r"""
"use strict";
(function () {

  /* convenience wave 4: the timeline's caption clips deep-link here with
   * #cue-<n> — land ON that row (scroll + brief flash) instead of at the
   * top of a long table. Same hash-restore discipline as the board tabs. */
  (function landOnCue() {
    if (!location.hash || location.hash.indexOf("#cue-") !== 0) return;
    var row = document.getElementById(location.hash.slice(1));
    if (!row) return;
    row.scrollIntoView({ block: "center" });
    row.classList.add("cue-flash");
    setTimeout(function () { row.classList.remove("cue-flash"); }, 2400);
  })();

  function reloadSoon() { setTimeout(function () { location.reload(); }, 500); }
  function warnList(el, warns) {
    if (!el) return;
    el.textContent = "";
    (warns || []).forEach(function (w) {
      var d = document.createElement("div"); d.className = "w"; d.textContent = "⚠ " + w;
      el.appendChild(d);
    });
  }
  function elv(root, sel) { var e = root.querySelector(sel); return e ? e.value : ""; }
  function num(root, sel, dflt) {
    var e = root.querySelector(sel); if (!e) return dflt;
    var v = parseFloat(e.value); return isNaN(v) ? dflt : v;
  }
  function intv(root, sel, dflt) {
    var e = root.querySelector(sel); if (!e) return dflt;
    var v = parseInt(e.value, 10); return isNaN(v) ? dflt : v;
  }
  function chk(root, sel) { var e = root.querySelector(sel); return !!(e && e.checked); }

  var page = document.body.getAttribute("data-page");
  if (page === "/subtitles") initSubtitles();
  else if (page === "/mixer") initMixer();
  else if (page === "/packaging") initPackaging();

  // ---------------------------------------------------------- subtitles
  function initSubtitles() {
    var body = document.getElementById("cue-body");
    var stage = document.getElementById("safe-area");

    function mkRow(c) {
      var tr = document.createElement("tr");
      tr.className = "cue-row";
      function cell(cls) { var td = document.createElement("td"); td.className = cls || ""; tr.appendChild(td); return td; }
      cell("cue-i");
      var s = document.createElement("input"); s.type = "number"; s.min = "0"; s.step = "10";
      s.className = "cue-start"; s.value = c.start_ms; cell().appendChild(s);
      var e = document.createElement("input"); e.type = "number"; e.min = "0"; e.step = "10";
      e.className = "cue-end"; e.value = c.end_ms; cell().appendChild(e);
      var sp = document.createElement("input"); sp.type = "text"; sp.className = "cue-speaker";
      sp.value = c.speaker || ""; sp.readOnly = true; cell().appendChild(sp);
      var tx = document.createElement("input"); tx.type = "text"; tx.className = "cue-text";
      tx.value = c.text || ""; cell().appendChild(tx);
      var ops = cell("cue-ops");
      [["preview", "看"], ["split", "拆分"], ["merge", "合并↓"], ["del", "删"]].forEach(function (o) {
        var b = document.createElement("button"); b.className = "btn mini ghost";
        b.setAttribute("data-act", o[0]); b.textContent = o[1]; ops.appendChild(b);
      });
      return tr;
    }
    function collect() {
      var out = [];
      body.querySelectorAll(".cue-row").forEach(function (r) {
        out.push({
          start_ms: intv(r, ".cue-start", 0), end_ms: intv(r, ".cue-end", 0),
          text: elv(r, ".cue-text"), speaker: elv(r, ".cue-speaker")
        });
      });
      return out;
    }
    function rebuild(cues) {
      body.querySelectorAll(".cue-row, .cue-empty").forEach(function (r) { r.remove(); });
      cues.forEach(function (c, i) { var row = mkRow(c); row.setAttribute("data-index", i + 1);
        row.querySelector(".cue-i").textContent = i + 1; body.appendChild(row); });
    }
    function splitAt(cues, i) {
      var c = cues[i];
      var at = Math.round((c.start_ms + c.end_ms) / 2);
      if (!(c.start_ms < at && at < c.end_ms)) return cues;
      var span = c.end_ms - c.start_ms, frac = (at - c.start_ms) / span;
      var cut = Math.round((c.text || "").length * frac);
      var a = { start_ms: c.start_ms, end_ms: at, text: (c.text || "").slice(0, cut).trim(), speaker: c.speaker };
      var b = { start_ms: at, end_ms: c.end_ms, text: (c.text || "").slice(cut).trim(), speaker: c.speaker };
      return cues.slice(0, i).concat([a, b], cues.slice(i + 1));
    }
    function mergeAt(cues, i) {
      if (i >= cues.length - 1) return cues;
      var a = cues[i], b = cues[i + 1];
      var m = { start_ms: Math.min(a.start_ms, b.start_ms), end_ms: Math.max(a.end_ms, b.end_ms),
        text: (a.text || "").trim() + (b.text || "").trim(), speaker: a.speaker || b.speaker };
      return cues.slice(0, i).concat([m], cues.slice(i + 2));
    }
    function preview(r) {
      if (!stage) return;
      var start = intv(r, ".cue-start", 0), end = intv(r, ".cue-end", 0);
      var img = document.getElementById("sa-frame"), box = document.getElementById("sa-text");
      box.textContent = elv(r, ".cue-text");
      var final = stage.getAttribute("data-final");
      if (final) {
        img.hidden = false;
        img.src = "/frame?src=" + encodeURIComponent(final) + "&ms=" + Math.round((start + end) / 2) + "&w=480";
      }
    }

    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-act]");
      if (btn && body.contains(btn)) {
        var r = btn.closest(".cue-row");
        var rows = Array.prototype.slice.call(body.querySelectorAll(".cue-row"));
        var i = rows.indexOf(r);
        var act = btn.getAttribute("data-act");
        if (act === "del") { var c = collect(); c.splice(i, 1); rebuild(c); }
        else if (act === "split") { rebuild(splitAt(collect(), i)); }
        else if (act === "merge") { rebuild(mergeAt(collect(), i)); }
        else if (act === "preview") { preview(r); }
        return;
      }
      if (ev.target.id === "cue-add") {
        var c = collect();
        var last = c.length ? c[c.length - 1].end_ms : 0;
        c.push({ start_ms: last, end_ms: last + 2000, text: "", speaker: "" });
        rebuild(c);
      } else if (ev.target.id === "cue-save") {
        doSave(false);
      } else if (ev.target.id === "sub-revert") {
        if (!confirm("还原为自动字幕?rules.captions.mode 翻回 compiled 并重新生成 captions.srt。")) return;
        post("/api/subtitles/revert", {}).then(function (res) {
          if (res.status === 200) { toast(res.data.message || "已还原", true); reloadSoon(); }
          else toast((res.data && res.data.error) || "失败", false);
        });
      }
    });

    function doSave(confirmed) {
      var cues = collect();
      post("/api/subtitles/save", { cues: cues, confirm_manual: confirmed }).then(function (res) {
        var warnEl = document.getElementById("cue-warn");
        if (res.status === 200 && res.data.needs_confirm) {
          if (confirm(res.data.message + "\n\n确定接管为 manual 吗?")) doSave(true);
          return;
        }
        if (res.status === 200 && res.data.ok) {
          warnList(warnEl, res.data.warnings);
          toast(res.data.flipped ? "已保存并接管为 manual" : "字幕已保存", true);
          reloadSoon();
        } else {
          warnList(warnEl, (res.data.errors || []).concat(res.data.warnings || []));
          toast((res.data && res.data.error) || "校验失败", false);
        }
      });
    }
  }

  // -------------------------------------------------------------- mixer
  function bedChange(prefix) {
    return {
      source: elv(document, "." + prefix + "-source"),
      gain_db: num(document, "." + prefix + "-gain", 0),
      start_offset_ms: intv(document, "." + prefix + "-start", 0),
      fade_in_ms: intv(document, "." + prefix + "-fadein", 0),
      fade_out_ms: intv(document, "." + prefix + "-fadeout", 0),
      ducking: chk(document, "." + prefix + "-duck"),
      duck: {
        threshold: num(document, "." + prefix + "-dth", 0.05),
        ratio: num(document, "." + prefix + "-dra", 8),
        attack_ms: intv(document, "." + prefix + "-dat", 5),
        release_ms: intv(document, "." + prefix + "-dre", 250)
      }
    };
  }
  function initMixer() {
    // audition: wire every source select to its sibling <audio>
    document.addEventListener("change", function (ev) {
      var sel = ev.target;
      if (sel.tagName !== "SELECT") return;
      var row = sel.closest(".mx-row, .sfx-row, td") || sel.parentNode;
      var audio = row.querySelector("audio.mx-audition") ||
        (sel.closest(".mx-bed, .mx-trans, .sfx-row") || document).querySelector("audio.mx-audition");
      var opt = sel.options[sel.selectedIndex];
      var url = opt ? opt.getAttribute("data-audio") : "";
      if (audio && url) { audio.src = url; }
    });

    document.addEventListener("click", function (ev) {
      if (ev.target.id === "sfx-add") {
        var body = document.getElementById("sfx-body");
        var proto = body.querySelector(".sfx-row");
        var row;
        if (proto) { row = proto.cloneNode(true); row.querySelectorAll("input").forEach(function (i) {
          if (i.classList.contains("sfx-offset")) i.value = "0";
          else if (i.classList.contains("sfx-gain")) i.value = "-6";
          else i.value = "";
        }); var s = row.querySelector("select"); if (s) s.selectedIndex = 0; }
        else { toast("先在 rules 里加一个音效,或直接保存后再加", false); return; }
        body.appendChild(row);
      } else if (ev.target.getAttribute && ev.target.getAttribute("data-act") === "sfx-del") {
        var r = ev.target.closest(".sfx-row"); if (r) r.remove();
      } else if (ev.target.id === "mx-apply") {
        applyMixer();
      }
    });

    function applyMixer() {
      var sfx = [];
      document.querySelectorAll("#sfx-body .sfx-row").forEach(function (r) {
        var src = elv(r, ".sfx-source");
        if (!src) return;
        sfx.push({ source: src, at: elv(r, ".sfx-at"),
          offset_ms: intv(r, ".sfx-offset", 0), gain_db: num(r, ".sfx-gain", -6) });
      });
      var changes = {
        voice_gain_db: num(document, ".voice-gain", 0),
        music: bedChange("music"),
        ambient: bedChange("ambient"),
        sfx: sfx,
        transition: { source: elv(document, ".trans-source"), gain_db: num(document, ".trans-gain", -12) }
      };
      post("/api/mixer/apply", { changes: changes }).then(function (res) {
        var v = document.getElementById("mx-verdict");
        if (res.status === 200 && res.data.ok) {
          var rb = res.data.rebuild || {};
          v.textContent = "";
          [["时间线 timeline", rb.timeline], ["最终 final", rb.final],
           ["重算片段 segments", (rb.segments_restale || []).join(", ") || "无"]].forEach(function (p) {
            var d = document.createElement("div"); d.className = "v";
            d.textContent = p[0] + ": " + (p[1] || "unchanged"); v.appendChild(d);
          });
          toast("混音已应用: " + (res.data.changed || []).join(", "), true);
        } else { toast((res.data && res.data.error) || "失败", false); }
      });
    }
  }

  // ---------------------------------------------------------- packaging
  function initPackaging() {
    var warnBar = document.getElementById("pk-warn");

    // live card preview (intro/outro/cover-card)
    document.addEventListener("click", function (ev) {
      var btn = ev.target.closest("[data-act]");
      if (btn) {
        var act = btn.getAttribute("data-act");
        if (act === "card-preview") { cardPreview(btn); return; }
        if (act === "cover-card-preview") { coverCardPreview(); return; }
        if (act === "save") { saveSection(btn.getAttribute("data-section")); return; }
        if (act === "ic-del") { var r = ev.target.closest(".ic-row"); if (r) r.remove(); return; }
      }
      if (ev.target.id === "ic-add") { addInfoCard(); }
    });
    document.addEventListener("click", function (ev) {
      var nudge = ev.target.getAttribute && ev.target.getAttribute("data-nudge");
      if (nudge) { nudgeCover(parseInt(nudge, 10)); }
    });

    function cardPreview(btn) {
      var card = btn.closest(".pk-card");
      var img = card.querySelector(".pk-prev-img");
      var q = "text=" + encodeURIComponent(elv(card, ".pk-text")) +
        "&subtext=" + encodeURIComponent(elv(card, ".pk-subtext")) +
        "&template=" + encodeURIComponent(elv(card, ".pk-template")) +
        "&preset=" + encodeURIComponent(elv(card, ".pk-preset"));
      img.hidden = false; img.src = "/api/card-preview?" + q;
    }
    function coverCardPreview() {
      var sect = document.querySelector('[data-section="cover"]');
      var img = document.getElementById("cover-card-img");
      var q = "text=" + encodeURIComponent(elv(sect, ".cover-text")) +
        "&template=" + encodeURIComponent(elv(sect, ".cover-template"));
      img.hidden = false; img.src = "/api/card-preview?" + q;
    }
    function addInfoCard() {
      var body = document.getElementById("ic-body");
      var proto = body.querySelector(".ic-row");
      if (!proto) { toast("已存在一条即可复制;先保存空表也可", false); }
      var row;
      if (proto) {
        row = proto.cloneNode(true);
        row.querySelectorAll("input").forEach(function (i) {
          if (i.classList.contains("ic-duration")) i.value = "1500";
          else if (i.classList.contains("ic-offset")) i.value = "0";
          else i.value = "";
        });
      } else {
        return; // no prototype to clone; a fresh project seeds none
      }
      body.appendChild(row);
    }

    function patchFor(section) {
      if (section === "cards") {
        return {
          intro: cardPatch('[data-kind="intro"]'),
          outro: cardPatch('[data-kind="outro"]')
        };
      }
      if (section === "info") {
        var list = [];
        document.querySelectorAll("#ic-body .ic-row").forEach(function (r) {
          list.push({ kind: elv(r, ".ic-kind"), text: elv(r, ".ic-text"), at: elv(r, ".ic-at"),
            offset_ms: intv(r, ".ic-offset", 0), duration_ms: intv(r, ".ic-duration", 1500),
            template: elv(r, ".ic-template") });
        });
        return { info_cards: list };
      }
      if (section === "cover") {
        var sect = document.querySelector('[data-section="cover"]');
        return { cover: { mode: elv(sect, ".cover-mode"),
          frame_ms: parseInt(sect.getAttribute("data-frame-ms") || "0", 10),
          text: elv(sect, ".cover-text"), template: elv(sect, ".cover-template") } };
      }
      if (section === "teaser") {
        var t = document.querySelector('[data-section="teaser"]');
        return { teaser: { enabled: chk(t, ".teaser-enabled"),
          from_ms: intv(t, ".teaser-from", 0), duration_ms: intv(t, ".teaser-duration", 5000) } };
      }
      if (section === "branding") {
        return {
          logo: { enabled: chk(document, ".lg-enabled"), image: elv(document, ".lg-image"),
            corner: elv(document, ".lg-corner"), size_pct: num(document, ".lg-size", 12),
            margin_pct: num(document, ".lg-margin", 2.5), opacity: num(document, ".lg-opacity", 1) },
          watermark: { enabled: chk(document, ".wm-enabled"), text: elv(document, ".wm-text"),
            image: elv(document, ".wm-image"), opacity: num(document, ".wm-opacity", 0.35),
            size_pct: num(document, ".wm-size", 30), position: elv(document, ".wm-position") },
          badge: { enabled: chk(document, ".bd-enabled"), text: elv(document, ".bd-text"),
            corner: elv(document, ".bd-corner") },
          cta: { enabled: chk(document, ".ct-enabled"), text: elv(document, ".ct-text"),
            at_end_ms: intv(document, ".ct-atend", 3000), position: elv(document, ".ct-position") }
        };
      }
      return {};
    }
    function cardPatch(sel) {
      var card = document.querySelector(sel);
      return { enabled: chk(card, ".pk-enabled"), template: elv(card, ".pk-template"),
        style_preset: elv(card, ".pk-preset"),
        text: elv(card, ".pk-text"), subtext: elv(card, ".pk-subtext"),
        duration_ms: intv(card, ".pk-duration", 2000) };
    }
    function saveSection(section) {
      post("/api/packaging/apply", { patch: patchFor(section) }).then(function (res) {
        if (res.status === 200 && res.data.ok) {
          warnList(warnBar, res.data.warnings);
          toast("已保存 " + section + (res.data.warnings && res.data.warnings.length ? " (见警告)" : ""), true);
        } else { toast((res.data && res.data.error) || "校验失败", false); }
      });
    }

    // cover fine-tune nudge
    function nudgeCover(delta) {
      var sect = document.querySelector('[data-section="cover"]');
      var ms = Math.max(0, parseInt(sect.getAttribute("data-frame-ms") || "0", 10) + delta);
      setCoverMs(ms);
    }
    function setCoverMs(ms) {
      var sect = document.querySelector('[data-section="cover"]');
      sect.setAttribute("data-frame-ms", ms);
      var lbl = document.getElementById("cover-ms"); if (lbl) lbl.textContent = ms;
      var final = sect.getAttribute("data-final");
      var img = document.getElementById("cover-fine-img");
      if (final && img) { img.hidden = false; img.src = "/frame?src=" + encodeURIComponent(final) + "&ms=" + ms + "&w=240"; }
    }

    // strips for cover + teaser
    var coverSect = document.querySelector('[data-section="cover"]');
    var final = coverSect ? coverSect.getAttribute("data-final") : "";
    if (final) {
      fetch("/api/strip?src=" + encodeURIComponent(final) + "&count=12")
        .then(function (r) { return r.json(); })
        .then(function (d) {
          buildStrip("cover-strip", d.frames, function (ms) { setCoverMs(ms); markStrip("cover-strip", ms); });
          buildTeaserStrip(d.frames);
        }).catch(function () {});
    }
    function buildStrip(id, frames, onclick) {
      var el = document.getElementById(id);
      if (!el || !frames) return;
      frames.forEach(function (f) {
        var img = document.createElement("img"); img.src = f.url; img.alt = "";
        img.setAttribute("data-ms", f.ms);
        img.addEventListener("click", function () { onclick(f.ms); });
        el.appendChild(img);
      });
    }
    function markStrip(id, ms) {
      var el = document.getElementById(id); if (!el) return;
      el.querySelectorAll("img").forEach(function (im) {
        im.classList.toggle("sel", parseInt(im.getAttribute("data-ms"), 10) === ms);
      });
    }
    function buildTeaserStrip(frames) {
      var t = document.querySelector('[data-section="teaser"]');
      if (!t) return;
      var state = { a: null, b: null, next: "a" };
      buildStrip("teaser-strip", frames, function (ms) {
        state[state.next] = ms;
        state.next = state.next === "a" ? "b" : "a";
        var lo = state.a, hi = state.b;
        if (lo !== null && hi !== null) {
          if (lo > hi) { var tmp = lo; lo = hi; hi = tmp; }
          var from = t.querySelector(".teaser-from"), dur = t.querySelector(".teaser-duration");
          if (from) from.value = lo; if (dur) dur.value = Math.max(100, hi - lo);
        }
        var el = document.getElementById("teaser-strip");
        el.querySelectorAll("img").forEach(function (im) {
          var v = parseInt(im.getAttribute("data-ms"), 10);
          im.classList.toggle("sel-a", v === state.a);
          im.classList.toggle("sel-b", v === state.b);
        });
      });
    }
  }
})();
"""
