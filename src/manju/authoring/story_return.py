"""Explicit three-way story proposals, using unchanged Story/v1 save documents.

A packet contains author-private base/candidate snapshots, not media, approvals
or executable commands. The structural group is deliberately conservative. We
return a validated new document; no caller-owned input is changed in place.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any
import tempfile
import json

from .core import AuthoringError, canonical, digest, file_digest
from .story import Story, assert_settable_story, brief_status, read_story

SCHEMA = 'manju.story-return/v1'
PREVIEW = 'manju.story-return-preview/v1'
MAX_PACKET_BYTES = 2 * 1024 * 1024 + 4096
PROJECT_FIELDS = ('title', 'form', 'intent', 'ending')
TEXT_FIELDS = {'sources': ('name', 'text', 'provenance'),
               'scenes': ('episode', 'title', 'purpose', 'action', 'dialogue', 'before', 'after')}
STRUCT_FIELDS = {'sources': ('kind', 'scope', 'from_scene_id', 'knower_id'),
                 'scenes': ('source_ids', 'references', 'policy')}
PREFIXES = {'sources': 'source', 'scenes': 'scene'}


def _story(value: Story | dict) -> dict:
    raw = value.model_dump(mode='json') if isinstance(value, Story) else value
    doc = Story.model_validate(raw)  # revalidate even an instance mutated after creation
    assert_settable_story(doc)
    return doc.model_dump(mode='json')


def _same(a: Any, b: Any) -> bool:
    return canonical(a) == canonical(b)


def _compatible(a: dict, b: dict) -> None:
    if a['project_id'] != b['project_id']:
        raise AuthoringError('different story identity; use explicit full import instead')
    if not _same(a['link'], b['link']):
        raise AuthoringError('story linkage needs explicit workbench confirmation')
    old = {x['id']: x for x in a['briefs']}
    new = {x['id']: x for x in b['briefs']}
    if any(k not in new or not _same(v, new[k]) for k, v in old.items()):
        raise AuthoringError('previous briefs and observations are immutable')
    if [x['id'] for x in b['briefs'] if x['id'] in old] != list(old):
        raise AuthoringError('previous brief history order must be preserved')


def create_return(base: Story | dict, candidate: Story | dict) -> dict:
    a, b = _story(base), _story(candidate)
    _compatible(a, b)
    packet = {'schema_id': SCHEMA, 'base': a, 'candidate': b,
              'base_sha256': digest(a), 'candidate_sha256': digest(b),
              'automatic_execution': False}
    if len(canonical(packet)) > MAX_PACKET_BYTES:
        raise AuthoringError('story return exceeds portable file limit')
    return packet


def parse_return(value: Any) -> dict:
    keys = {'schema_id', 'base', 'candidate', 'base_sha256', 'candidate_sha256', 'automatic_execution'}
    if (not isinstance(value, dict) or set(value) != keys or value['schema_id'] != SCHEMA
            or value['automatic_execution'] is not False):
        raise AuthoringError('invalid story return envelope')
    packet = create_return(value['base'], value['candidate'])
    if not _same(packet, value):
        raise AuthoringError('story return content/hash mismatch')
    return packet


def read_return(path: Path) -> dict:
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise AuthoringError('story return must be an ordinary file')
    with p.open('rb') as stream:
        raw = stream.read(MAX_PACKET_BYTES + 1)
    if len(raw) > MAX_PACKET_BYTES:
        raise AuthoringError('story return file too large')
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise AuthoringError('duplicate JSON key: ' + key)
            out[key] = value
        return out
    def invalid_number(value):
        raise AuthoringError('non-finite JSON number: ' + value)
    try:
        # The proposal may exceed the legacy 2 MiB workspace metadata envelope;
        # each contained story is still independently capped at 1 MiB.
        return parse_return(json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique,
                                       parse_constant=invalid_number))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AuthoringError('invalid story return JSON: ' + str(exc)) from exc


def _special(a: dict, b: dict) -> dict[str, set[str]]:
    return {kind: {x['id'] for x in a[kind]} ^ {x['id'] for x in b[kind]}
            for kind in TEXT_FIELDS}


def _structure(doc: dict, special: dict[str, set[str]]) -> dict:
    # Deletion's precondition includes the old object's complete text. Do not
    # erase a newly edited local paragraph merely because its ID is unchanged.
    value = {'briefs': doc['briefs']}
    for kind, fields in STRUCT_FIELDS.items():
        value[kind] = [deepcopy(row) if row['id'] in special[kind]
                       else {k: row[k] for k in ('id', *fields)} for row in doc[kind]]
    return value


def _status(old: Any, now: Any, new: Any, present: bool) -> str:
    if not present:
        return 'missing'
    if _same(now, new):
        return 'already_applied'
    return 'ready' if _same(now, old) else 'conflict'


def preview_return(current: Story | dict, packet: dict) -> dict:
    p, local = parse_return(packet), _story(current)
    base, candidate = p['base'], p['candidate']
    if local['project_id'] != base['project_id']:
        raise AuthoringError('current story belongs to a different project')
    rows = []

    def add(key: str, kind: str, id_: str | None, field: str,
            old: Any, new: Any, now: Any, present: bool = True,
            bp: int | None = None, cp: int | None = None, ap: int | None = None,
            title: str = '') -> None:
        rows.append({'key': key, 'kind': kind, 'id': id_, 'field': field,
                     'before': old, 'current': now, 'after': new,
                     'current_present': present, 'status': _status(old, now, new, present),
                     'before_position': bp, 'current_position': cp,
                     'after_position': ap, 'title': title})

    for f in PROJECT_FIELDS:
        if not _same(base[f], candidate[f]):
            add('project/' + f, 'project', None, f, base[f], candidate[f], local[f], title=local['title'])
    for kind, fields in TEXT_FIELDS.items():
        old = {x['id']: (i+1, x) for i, x in enumerate(base[kind])}
        now = {x['id']: (i+1, x) for i, x in enumerate(local[kind])}
        for i, row in enumerate(candidate[kind]):
            rid = row['id']
            if rid not in old:
                continue  # new objects travel with the structural group
            bp, original = old[rid]
            cp, current_row = now.get(rid, (None, None))
            for f in fields:
                if not _same(original[f], row[f]):
                    add(f'{PREFIXES[kind]}/{rid}/{f}', kind, rid, f, original[f], row[f],
                        current_row[f] if current_row else None, current_row is not None,
                        bp, cp, i+1, row.get('title', row.get('name', rid)))
    special = _special(base, candidate)
    old, new = _structure(base, special), _structure(candidate, special)
    if not _same(old, new):
        add('structure', 'structure', None, 'structure', old, new, _structure(local, special),
            title='人物、引用、场序与历史新增：整组接回')
    report = {'schema_id': PREVIEW, 'project_id': local['project_id'],
              'base_sha256': p['base_sha256'], 'candidate_sha256': p['candidate_sha256'],
              'current_sha256': digest(local), 'packet_sha256': digest(p), 'rows': rows,
              'ready_keys': [r['key'] for r in rows if r['status'] == 'ready'],
              'automatic_execution': False, 'media_unchanged': True,
              'checks_only_this_local_snapshot': True}
    report['preview_sha256'] = digest(report)
    return report


def apply_return(current: Story | dict, packet: dict, selected: list[str], *,
                 expected_preview: str) -> dict:
    report = preview_return(current, packet)
    if report['preview_sha256'] != expected_preview:
        raise AuthoringError('stale preview; compare the current content again')
    if (not isinstance(selected, list) or not selected or any(not isinstance(x, str) for x in selected)
            or len(set(selected)) != len(selected)):
        raise AuthoringError('choose distinct, nonempty fields')
    rows = {x['key']: x for x in report['rows']}
    if any(k not in rows or rows[k]['status'] != 'ready' for k in selected):
        raise AuthoringError('selected changes conflict, are missing or are already applied')
    result, candidate = _story(current), packet['candidate']
    if 'structure' in selected:
        for kind, fields in STRUCT_FIELDS.items():
            local = {x['id']: x for x in result[kind]}
            assembled = []
            for proposed in candidate[kind]:
                if proposed['id'] not in local:
                    assembled.append(deepcopy(proposed))
                else:
                    merged = deepcopy(local[proposed['id']])
                    merged.update({k: deepcopy(proposed[k]) for k in fields})
                    assembled.append(merged)
            result[kind] = assembled
        result['briefs'] = deepcopy(candidate['briefs'])
    for key in selected:
        row = rows[key]
        if row['kind'] == 'structure':
            continue
        if row['kind'] == 'project':
            result[row['field']] = deepcopy(row['after'])
        else:
            target = next(x for x in result[row['kind']] if x['id'] == row['id'])
            target[row['field']] = deepcopy(row['after'])
    try:
        return _story(result)
    except ValueError as exc:
        raise AuthoringError('combined story invalid or exceeds capacity; nothing applied: ' + str(exc)) from exc


def preview_files(current: Path, returned: Path) -> dict:
    return preview_return(read_story(current), read_return(returned))


def apply_files(current: Path, returned: Path, output: Path, selected: list[str], *,
                expected_preview: str) -> dict:
    from .desk import _publish_new
    current, returned, output = Path(current), Path(returned), Path(output)
    if output.exists() or output.is_symlink():
        raise AuthoringError('use a new output file; never overwrite a story or return packet')
    for path, limit in ((current, 2 * 1024 * 1024), (returned, MAX_PACKET_BYTES)):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise AuthoringError('input must be an ordinary bounded file')
    before = {p: file_digest(p) for p in (current, returned)}
    result = apply_return(read_story(current), read_return(returned), selected, expected_preview=expected_preview)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.manju-selected-', dir=output.parent) as td:
        staged = Path(td) / 'STORY.json'; staged.write_bytes(canonical(result))
        if any(file_digest(p) != h for p, h in before.items()):
            raise AuthoringError('input changed during application; nothing published')
        _publish_new(staged, output)
    return {'ok': True, 'output': str(output), 'story_sha256': digest(result), 'applied_keys': selected,
            'source_modified': False, 'automatic_execution': False,
            'brief_status': [brief_status(result, b['id']) for b in result['briefs']]}
