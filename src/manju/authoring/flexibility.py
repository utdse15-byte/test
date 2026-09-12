"""Composable offline drafts, never model execution or transferable approval.

Composition is deliberately section replacement, not an ambiguous deep merge.
Shot and review travel together; catalog replacement is an independent opt-in.
Outputs keep Studio/v1, so R13 readers can still verify and restore them.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import shutil
import tempfile
from typing import Literal
import zipfile

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .core import AuthoringError, canonical, digest, file_digest
from .studio import RawDirector, RawRepair, Studio, studio_media, verify_studio
from .workspace import MAX_JSON, Workspace, json_value, required_media

SECTIONS = ('shot', 'repair', 'director', 'catalog')
TEMPLATE_FIELDS = {
    'shot': ('task', 'prompt', 'duration', 'resolution', 'ratio', 'preserve', 'change'),
    'repair': ('repair-preserve', 'repair-change', 'repair-audio'),
    'director': ('director-preserve', 'director-change', 'director-audio'),
}


def selected_sections(values: list[str], allowed=SECTIONS) -> list[str]:
    if (not isinstance(values, list) or not values or
            any(not isinstance(v, str) or v not in allowed for v in values) or
            len(set(values)) != len(values)):
        raise AuthoringError('choose nonempty, unique, supported sections')
    return [key for key in allowed if key in values]


def _value(doc: Studio | dict) -> dict:
    # Validate even caller-created model instances; mutable nested fields exist.
    raw = doc.model_dump(mode='json') if isinstance(doc, Studio) else doc
    return Studio.model_validate(deepcopy(raw)).model_dump(mode='json')


def _rebind(raw: dict, bindings: dict[str, dict]) -> Studio:
    # Compute closure from the already valid source objects, then validate the
    # combined graph. Sizes, geometry and shared resource limits cannot drift.
    skeleton = Studio.model_construct(**{
        **raw,
        'workspace': Workspace.model_validate(raw['workspace']),
        'repair': RawRepair.model_validate(raw['repair']),
        'director': RawDirector.model_validate(raw['director']),
    })
    needed = studio_media(skeleton)
    workspace_bindings = {m['sha256']: m for m in raw['workspace']['media']}
    raw['workspace']['media'] = [deepcopy(workspace_bindings[h]) for h in required_media(skeleton.workspace)]
    effective = deepcopy(bindings)
    effective.update(workspace_bindings)
    # Match the existing R13 restore/export order and display names. A preview's
    # after-document digest then also identifies the next exported STUDIO.json.
    for source in (skeleton.repair.source, skeleton.director.source):
        if source is not None:
            if source.sha256 not in bindings:
                raise AuthoringError('missing source binding')
            effective[source.sha256] = {**bindings[source.sha256], 'filename': source.filename}
    for anchor in skeleton.director.anchors:
        for raster in (anchor.frame, anchor.guide):
            if raster is not None:
                if raster.sha256 not in bindings:
                    raise AuthoringError('missing raster binding')
                effective[raster.sha256] = {**bindings[raster.sha256],
                                           'filename': raster.filename, 'mime_type': 'image/png'}
    raw['media'] = []
    for h, size in needed.items():
        record = effective.get(h)
        if record is None or record['bytes'] != size:
            raise AuthoringError('composition has missing or conflicting media: ' + h)
        raw['media'].append(deepcopy(record))
    return Studio.model_validate(raw)


def compose_studios(target: Studio | dict, donor: Studio | dict,
                    take: list[str]) -> Studio:
    """Return a new validated document; neither input is mutated."""
    take = selected_sections(take)
    a, b = _value(target), _value(donor)
    result = deepcopy(a)
    if 'shot' in take:
        catalog = result['workspace']['catalog']
        result['workspace'] = deepcopy(b['workspace'])
        result['workspace']['catalog'] = catalog
        result['auxiliary_form'] = deepcopy(b['auxiliary_form'])
    for area in ('repair', 'director'):
        if area in take:
            result[area] = deepcopy(b[area])
    if 'catalog' in take:
        result['workspace']['catalog'] = deepcopy(b['workspace']['catalog'])
    # Retained files keep their display names; records in each selected area
    # carry their own original names. A digest identifies bytes, not a filename.
    bindings = {m['sha256']: m for m in b['media']}
    bindings.update({m['sha256']: m for m in a['media']})
    return _rebind(result, bindings)


class CreativeTemplate(BaseModel):
    """Whitelisted text only: no assets, IDs, accounts, model choice or approval."""
    model_config = ConfigDict(extra='forbid', strict=True)
    schema_id: Literal['manju.creative-template/v1']
    name: str = Field(min_length=1, max_length=120)
    notes: str = Field(max_length=4000)
    fields: dict[str, dict[str, str]]
    quality_only_on_apply: Literal[True]
    contains_media: Literal[False]
    contains_approvals: Literal[False]
    automatic_execution: Literal[False]

    @model_validator(mode='after')
    def whitelist(self):
        if not self.name.strip() or any(ord(c) < 32 for c in self.name):
            raise ValueError('template name is blank or contains control characters')
        if not self.fields or set(self.fields) - TEMPLATE_FIELDS.keys():
            raise ValueError('template areas are empty or unknown')
        for area, fields in self.fields.items():
            if not fields or set(fields) - set(TEMPLATE_FIELDS[area]):
                raise ValueError('template contains forbidden fields')
            if any(len(value) > 30000 for value in fields.values()):
                raise ValueError('template text exceeds 30000 characters')
        if len(canonical(self)) > MAX_JSON:
            raise ValueError('template exceeds 2 MiB')
        return self

    @model_validator(mode='wrap')
    @classmethod
    def exact(cls, value, handler):
        result = handler(value)
        if isinstance(value, dict) and canonical(value) != canonical(result):
            raise ValueError('template requires exact fields without coercion')
        return result


def _form(doc: dict, area: str) -> dict:
    return doc['workspace']['draft']['form'] if area == 'shot' else doc[area]['form']


def create_template(doc: Studio | dict, name: str, *, notes: str = '',
                    areas: list[str] | None = None, omit_blank: bool = True) -> CreativeTemplate:
    raw = _value(doc)
    areas = selected_sections(list(TEMPLATE_FIELDS) if areas is None else areas, TEMPLATE_FIELDS)
    fields = {}
    for area in areas:
        form = _form(raw, area)
        values = {k: form[k] for k in TEMPLATE_FIELDS[area] if not omit_blank or form[k].strip()}
        if values:
            fields[area] = values
    return CreativeTemplate.model_validate({
        'schema_id': 'manju.creative-template/v1', 'name': name, 'notes': notes,
        'fields': fields, 'quality_only_on_apply': True, 'contains_media': False,
        'contains_approvals': False, 'automatic_execution': False,
    })


def apply_template(doc: Studio | dict, template: CreativeTemplate | dict, *,
                   mode: Literal['fill_empty', 'replace'] = 'fill_empty') -> Studio:
    if mode not in ('fill_empty', 'replace'):
        raise AuthoringError('template mode must be fill_empty or replace')
    raw = _value(doc)
    t = CreativeTemplate.model_validate(
        template.model_dump(mode='json') if isinstance(template, CreativeTemplate) else template)
    shot_changed = False
    for area, fields in t.fields.items():
        form = _form(raw, area)
        for key, text in fields.items():
            if mode == 'replace' or not form[key].strip():
                shot_changed |= area == 'shot' and form[key] != text
                form[key] = text
    if shot_changed:
        # Reviews remain historical; a modified task cannot inherit promotion
        # or an origin-project context that describes a different creative task.
        raw['workspace']['draft'].update(stage='draft', draftHash=None, sourceContext=None)
    return Studio.model_validate(raw)


def _parts(doc: dict) -> dict:
    w = deepcopy(doc['workspace'])
    w.pop('catalog'); w.pop('media')
    return {'shot': {'workspace': w, 'auxiliary_form': doc['auxiliary_form']},
            'repair': doc['repair'], 'director': doc['director'],
            'catalog': doc['workspace']['catalog']}


def difference(before: Studio | dict, after: Studio | dict) -> dict:
    """Explicit section/field effects; reports never become production inputs."""
    a, b = _value(before), _value(after)
    ap, bp = _parts(a), _parts(b)
    fields = []
    for area in TEMPLATE_FIELDS:
        old, new = _form(a, area), _form(b, area)
        for key in sorted(old):
            if old[key] != new[key]:
                fields.append({'area': area, 'field': key, 'before': old[key], 'after': new[key]})
    am = {m['sha256']: m['bytes'] for m in a['media']}
    bm = {m['sha256']: m['bytes'] for m in b['media']}
    return {
        'schema_id': 'manju.studio-difference/v1',
        'before_sha256': digest(a), 'after_sha256': digest(b),
        'changed_sections': [k for k in SECTIONS if canonical(ap[k]) != canonical(bp[k])],
        'retained_sections': [k for k in SECTIONS if canonical(ap[k]) == canonical(bp[k])],
        'field_changes': fields,
        'added_media': sorted(bm.keys() - am.keys()),
        'removed_media': sorted(am.keys() - bm.keys()),
        'result_media_files': len(bm), 'result_media_bytes': sum(bm.values()),
        'quality_only_on_apply': True, 'confirmations_restored': False,
        'automatic_execution': False, 'project_modified': False,
    }


def read_verified(path: Path) -> tuple[Studio, dict]:
    receipt = verify_studio(path)
    with zipfile.ZipFile(path) as archive:
        doc = Studio.model_validate(json_value(archive.read('STUDIO.json')))
    if digest(doc) != receipt['studio_sha256'] or file_digest(path) != receipt['archive_sha256']:
        raise AuthoringError('source changed while reading; use a stable backup')
    return doc, receipt


def compose_archives(target: Path, donor: Path, take: list[str], output: Path) -> dict:
    """Verify two real archives, then publish a new Studio/v1 file exclusively."""
    if output.exists() or output.is_symlink():
        raise FileExistsError('output already exists; original backups are never overwritten')
    a, ar = read_verified(target)
    b, br = read_verified(donor)
    result = compose_studios(a, b, take)
    report = difference(a, result)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.manju-compose-', dir=output.parent) as td:
        stage = Path(td) / 'composed.zip'
        with zipfile.ZipFile(target) as za, zipfile.ZipFile(donor) as zb:
            with zipfile.ZipFile(stage, 'w', compression=zipfile.ZIP_STORED) as dest:
                data = canonical(result)
                dest.writestr('STUDIO.json', data)
                sums = {'STUDIO.json': sha256(data).hexdigest()}
                available = set(za.namelist())
                for media in result.media:
                    name = 'media/' + media.sha256
                    source = za if name in available else zb
                    info = source.getinfo(name)
                    if (info.file_size != media.bytes or info.compress_size != media.bytes or
                            info.compress_type != zipfile.ZIP_STORED):
                        raise AuthoringError('source member changed before composition')
                    # The input can be replaced after initial verification. Bound
                    # each copy by the validated record, not the new ZIP header.
                    h = sha256()
                    remaining = media.bytes
                    with source.open(name) as src, dest.open(name, 'w') as out:
                        while remaining:
                            block = src.read(min(1024 * 1024, remaining))
                            if not block:
                                raise AuthoringError('source member truncated during composition')
                            out.write(block); h.update(block); remaining -= len(block)
                        if src.read(1) or h.hexdigest() != media.sha256:
                            raise AuthoringError('source media changed during composition')
                    sums[name] = media.sha256
                dest.writestr('MANIFEST.json', canonical({'schema_id': 'manju.studio-manifest/v1', 'files': sums}))
        verified = verify_studio(stage)
        if file_digest(target) != ar['archive_sha256'] or file_digest(donor) != br['archive_sha256']:
            raise AuthoringError('an input changed while composing; no output published')
        owned = False
        try:
            with output.open('xb') as out:
                owned = True
                with stage.open('rb') as src:
                    shutil.copyfileobj(src, out)
            if file_digest(output) != verified['archive_sha256']:
                raise AuthoringError('output copy did not match the verified archive')
        except BaseException:
            if owned:
                output.unlink(missing_ok=True)
            raise
    return {**verified, 'output': str(output), 'take': selected_sections(take),
            'target_archive_sha256': ar['archive_sha256'], 'donor_archive_sha256': br['archive_sha256'],
            'difference': report, 'sources_unchanged': True}
