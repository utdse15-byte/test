"""Read-only comparisons of validated story snapshots; never a merge or approval.

Identity comes from stable IDs, not display names or list positions. Kept separate
from Story/v1 so old portable archives remain unchanged. Browser parity is tested.
"""
from __future__ import annotations

from html import escape
import json
from pathlib import Path
from typing import Any

from .core import AuthoringError, canonical, digest
from .story import Story, brief_status

SCHEMA = 'manju.story-diff/v1'
FIELDS = {
    'story': {'title': '作品名称', 'form': '作品形态', 'intent': '作品意图',
              'ending': '作者结局字段', 'link': '镜头关联'},
    'scenes': {'episode': '分集', 'title': '场景名称', 'purpose': '这一场的作用',
               'action': '实际动作与因果', 'dialogue': '对白', 'before': '入场状态',
               'after': '离场变化', 'source_ids': '引用依据', 'references': '参考素材职责',
               'policy': '前情与知情范围'},
    'sources': {'kind': '依据类型', 'name': '名称', 'text': '人物 / 依据正文',
                'scope': '可见范围', 'from_scene_id': '起始场景', 'knower_id': '知情人物',
                'provenance': '来源说明'},
    'briefs': {'scene_id': '关联场景', 'content': '已记录的制作内容',
               'content_sha256': '制作内容校验值', 'observation': '实际成片复盘'},
}
NOTES = [
    '只比较这两份故事的字面内容与结构，不做剧情语义、画质或创作批准。',
    '结局字段未改，不等于其他改动不会影响结局；需连贯阅读人物动机和因果。',
    '编号识别对象，位置表示当时的实际场序；新增一场造成的位置后移不等于重写。',
    '包含作者计划与历史正文，分享前检查范围；报告不是素材备份或自动合并指令。',
]


def _document(value: Story | dict | None) -> tuple[Story | None, dict | None]:
    if value is None:
        return None, None
    # Validate a fresh dict even for model instances; do not trust unchecked mutations.
    model = Story.model_validate(value.model_dump(mode='json') if isinstance(value, Story) else value)
    return model, model.model_dump(mode='json')


def _changes(old: dict | None, new: dict | None, fields: dict[str, str]) -> list[dict]:
    rows = []
    for key, label in fields.items():
        a, b = old.get(key) if old is not None else None, new.get(key) if new is not None else None
        if (old is None) != (new is None) or canonical(a) != canonical(b):
            rows.append({'field': key, 'label': label, 'before': a, 'after': b,
                         'before_present': old is not None, 'after_present': new is not None})
    return rows


def _entities(before: dict | None, after: dict | None, kind: str) -> tuple[list, list, dict]:
    left, right = (before or {}).get(kind, []), (after or {}).get(kind, [])
    a, b = {x['id']: x for x in left}, {x['id']: x for x in right}
    apos, bpos = {x['id']: i+1 for i, x in enumerate(left)}, {x['id']: i+1 for i, x in enumerate(right)}
    rows, unchanged = [], []
    sequence = [x['id'] for x in right] + [x['id'] for x in left if x['id'] not in b]
    for identity in sequence:
        old, new = a.get(identity), b.get(identity)
        changes = _changes(old, new, FIELDS[kind])
        present = old is not None and new is not None
        status = 'added' if old is None else 'removed' if new is None else 'changed' if changes else 'unchanged'
        shift = present and apos[identity] != bpos[identity]
        if status == 'unchanged':
            unchanged.append(identity)
        if status == 'unchanged' and not (kind == 'scenes' and shift):
            continue
        title_key = 'title' if kind == 'scenes' else 'name' if kind == 'sources' else 'scene_id'
        rows.append({'id': identity, 'status': status, 'title_before': (old or {}).get(title_key),
                     'title_after': (new or {}).get(title_key), 'before_position': apos.get(identity),
                     'after_position': bpos.get(identity), 'position_changed': bool(shift),
                     'changes': changes, 'unchanged_fields': [key for key in FIELDS[kind]
                        if present and key not in {r['field'] for r in changes}]})
    shared_a = [x['id'] for x in left if x['id'] in b]
    shared_b = [x['id'] for x in right if x['id'] in a]
    order = {'before': [x['id'] for x in left], 'after': [x['id'] for x in right],
             'changed': [x['id'] for x in left] != [x['id'] for x in right],
             'shared_order_changed': shared_a != shared_b}
    return rows, unchanged, order


def compare_stories(before: Story | dict | None, after: Story | dict | None) -> dict:
    """Explain two complete snapshots without modifying either or inferring intent."""
    am, a = _document(before)
    bm, b = _document(after)
    relationship = ('empty' if a is None and b is None else 'created' if a is None else
                    'removed' if b is None else 'same_project' if a['project_id'] == b['project_id']
                    else 'different_project')
    result: dict[str, Any] = {
        'schema_id': SCHEMA, 'before_sha256': digest(a), 'after_sha256': digest(b),
        'before_project_id': a['project_id'] if a else None,
        'after_project_id': b['project_id'] if b else None,
        'before_title': a['title'] if a else None, 'after_title': b['title'] if b else None,
        'relationship': relationship, 'changed': canonical(a) != canonical(b),
        'read_only': True, 'automatic_execution': False, 'semantic_validation': False,
        'merges_later_browser_edits': False, 'notes': NOTES[:],
        'story_fields': [], 'scenes': [], 'sources': [], 'briefs': [],
        'unchanged_scene_ids': [], 'orders': {}, 'brief_status_changes': [],
        'ending_field_unchanged': None, 'last_scene_content_unchanged': None,
        'last_scene_id': None,
    }
    # IDs from unrelated projects are never matched even if their spelling collides.
    if relationship == 'different_project':
        return result
    result['story_fields'] = _changes(a, b, FIELDS['story'])
    for kind in ('scenes', 'sources', 'briefs'):
        rows, unchanged, order = _entities(a, b, kind)
        result[kind] = rows
        result['orders'][kind] = order
        if kind == 'scenes':
            result['unchanged_scene_ids'] = unchanged
    if a is not None and b is not None:
        result['ending_field_unchanged'] = a['ending'] == b['ending']
        if a['scenes'] and b['scenes'] and a['scenes'][-1]['id'] == b['scenes'][-1]['id']:
            result['last_scene_id'] = a['scenes'][-1]['id']
            result['last_scene_content_unchanged'] = canonical(a['scenes'][-1]) == canonical(b['scenes'][-1])
        old_briefs = {x.id for x in am.briefs}
        for brief in bm.briefs:
            left = brief_status(am, brief.id) if brief.id in old_briefs else None
            right = brief_status(bm, brief.id)
            if left is None or canonical(left) != canonical(right):
                result['brief_status_changes'].append({'id': brief.id, 'scene_id': brief.scene_id,
                                                       'before': left, 'after': right})
    return result


def _value(value: Any, present: bool = True) -> str:
    if not present:
        return '（本侧不存在）'
    if value == '':
        return '（空白）'
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def render_preview_html(preview: dict) -> str:
    """A script-free, offline companion to ide-preview; no action controls."""
    diff = preview['story_diff']
    parts = []
    esc = lambda x: escape(str(x), quote=True)
    def pair(row):
        return ('<div class="pair"><section><h4>导出基线</h4><pre>' + esc(_value(row['before'], row.get('before_present', True))) +
                '</pre></section><section><h4>候选新版</h4><pre>' + esc(_value(row['after'], row.get('after_present', True))) + '</pre></section></div>')
    parts.append('<p class="eyebrow">MANJU · 修改对照 · 只读</p><h1>这次改了什么</h1>')
    parts.append('<p>比较的是 IDE 导出基线与当前磁盘候选。不是后来网页的新稿，不自动合并或接受修改。</p>')
    parts.append('<div class="facts">' + ('作者结局字段未改' if diff['ending_field_unchanged'] else '请查看结局字段') +
                 ' · ' + ('场序未改' if not diff.get('orders', {}).get('scenes', {}).get('changed', True) else '场序有变化或尚无可比场序') + '</div>')
    for row in diff['story_fields']:
        parts.append('<article><h2>' + esc(row['label']) + '</h2>' + pair(row) + '</article>')
    for kind, label in [('scenes','场景'),('sources','人物与依据'),('briefs','历史版本与复盘')]:
        if not diff[kind]:
            continue
        parts.append('<h2>' + label + '</h2>')
        for entity in diff[kind]:
            state = {'added':'新增','removed':'移除','changed':'内容变化','unchanged':'内容未改，位置变化'}[entity['status']]
            pos = f"第 {entity['before_position'] or '∅'} 位 → 第 {entity['after_position'] or '∅'} 位" if kind=='scenes' else ''
            title = entity['title_after'] or entity['title_before'] or '未命名'
            parts.append('<article><h3>' + esc(title) + '</h3><p class="meta">' + esc(f'{entity["id"]} · {state} · {pos}') + '</p>')
            if entity['unchanged_fields']:
                parts.append('<p class="meta">未改：' + esc('、'.join(FIELDS[kind][k] for k in entity['unchanged_fields'])) + '</p>')
            for row in entity['changes']:
                parts.append('<details open><summary>' + esc(row['label']) + '</summary>' + pair(row) + '</details>')
            parts.append('</article>')
    if diff['brief_status_changes']:
        parts.append('<details><summary>关联制作简报的复核状态</summary><pre>' + esc(json.dumps(diff['brief_status_changes'],ensure_ascii=False,indent=2))+'</pre></details>')
    if preview.get('fields'):
        parts.append('<details open><summary>镜头 / 导演 / 返工文字</summary><pre>'+esc(json.dumps(preview['fields'],ensure_ascii=False,indent=2))+'</pre></details>')
    if not diff['changed']:
        parts.append('<p>故事与导出基线逐字一致。</p>')
    parts.append('<details><summary>编号、排列与校验值</summary><pre>' + esc(json.dumps({'orders': diff['orders'], 'unchanged_scene_ids': diff['unchanged_scene_ids'], 'preview_sha256': preview['preview_sha256']},ensure_ascii=False,indent=2)) + '</pre></details>')
    parts.append('<aside>' + ''.join('<p>'+esc(n)+'</p>' for n in NOTES) + '</aside>')
    style = '''html{font:17px/1.7 system-ui,sans-serif;background:#f6f5ef;color:#233630}body{max-width:1080px;margin:48px auto;padding:0 24px 60px}h1{font-size:36px;line-height:1.2}h2{margin-top:28px}h3{margin:0}h4{margin:0 0 10px}.eyebrow,.meta{color:#52655d;font-size:14px}article,aside{padding:24px;background:#fff;border:1px solid #d6ddd5;border-radius:14px;margin:20px 0}.facts{padding:16px 20px;background:#eaf1e9;border-radius:10px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:14px 0}.pair section{min-width:0;padding:18px;border:1px solid #ccd5d0;border-radius:10px}.pair section:last-child{background:#eff7ed}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere;max-height:65vh;overflow:auto;margin:0}summary{cursor:pointer;font-weight:650;padding:10px 0}aside{font-size:14px}@media(max-width:700px){body{margin:24px auto;padding:0 16px 40px}.pair{grid-template-columns:1fr}article{padding:16px}h1{font-size:29px}}'''
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'"><title>Manju · 修改对照（只读）</title><style>'+style+'</style><body>'+''.join(parts)+'</body></html>'


def write_preview_html(preview: dict, path: Path) -> None:
    """Create an explicit report file, never overwrite inputs or prior reports."""
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise AuthoringError('use a new HTML report filename; never overwrite')
    data = render_preview_html(preview).encode('utf-8')
    owned = False
    try:
        with path.open('xb') as stream:
            owned = True
            stream.write(data)
    except BaseException:
        if owned:
            path.unlink(missing_ok=True)
        raise
