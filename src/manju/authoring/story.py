"""Story sources and version-bound production briefs. Pure data, no model calls.

Notes remain author claims. Visibility is explicit, not inferred character knowledge.
Existing series Bibles remain authoritative; the optional notebook is a working copy.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal
import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .core import AuthoringError, canonical, digest
from .workspace import json_value

FORMS = {
    'undecided': '先决定作品形态，不套用固定镜长、集数或付费卡点。',
    'film': '围绕单片的核心冲突与结束条件组织场景，不要求可无限续集。',
    'limited_series': '围绕跨集因果与明确收束组织；不以能否续写一百集作门槛。',
    'ongoing_series': '检查能持续产生故事的关系与压力，同时保留每集造成的后果。',
    'vertical_serial': '可使用短剧方法，但集数、镜长和付费结构均由你另行决定。',
    'music_visual': '以实际音乐、动作与视觉组织为主，不强制三幕或人物成长。',
    'custom': '按自定创作意图组织，不继承其他体裁的硬限制。',
}
MAX_BYTES = 1024 * 1024


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    @model_validator(mode='wrap')
    @classmethod
    def exact(cls, value, handler):
        result = handler(value)
        if isinstance(value, dict) and canonical(value) != canonical(result):
            raise ValueError('story fields must be exact; no implicit values')
        return result


class Source(Strict):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    kind: Literal['character', 'relationship', 'setting', 'fact', 'consequence']
    name: str = Field(max_length=240)
    text: str = Field(max_length=12000)
    scope: Literal['author', 'audience', 'character']
    from_scene_id: str | None
    knower_id: str | None
    provenance: str = Field(max_length=2000)


class Reference(Strict):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    bytes: int = Field(ge=1, le=128*1024*1024)
    filename: str = Field(min_length=1, max_length=240)
    role: Literal['identity', 'space', 'performance', 'camera', 'voice', 'music', 'look', 'other']
    inherit: str = Field(max_length=4000)
    exclude: str = Field(max_length=4000)
    time_range: str = Field(max_length=500)


class Policy(Strict):
    scope: Literal['author', 'audience', 'character']
    character_id: str | None
    history_count: int = Field(ge=0, le=20)


class Scene(Strict):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    episode: str = Field(max_length=120)
    title: str = Field(max_length=240)
    purpose: str = Field(max_length=4000)
    action: str = Field(max_length=12000)
    dialogue: str = Field(max_length=12000)
    before: str = Field(max_length=4000)
    after: str = Field(max_length=4000)
    source_ids: list[str] = Field(max_length=64)
    references: list[Reference] = Field(max_length=32)
    policy: Policy


class Observation(Strict):
    candidate_sha256: str | None
    candidate_bytes: int | None
    seen: str = Field(max_length=8000)
    heard: str = Field(max_length=8000)
    deviation: str = Field(max_length=8000)
    layer: Literal['unreviewed', 'story', 'references', 'space', 'performance', 'camera', 'sound', 'edit', 'none_observed']

    @model_validator(mode='after')
    def identity(self):
        if (self.candidate_sha256 is None) != (self.candidate_bytes is None):
            raise ValueError('candidate identity must be complete or explicitly absent')
        if self.candidate_sha256 is not None:
            import re
            if not re.fullmatch('[0-9a-f]{64}', self.candidate_sha256) or not 1 <= self.candidate_bytes <= 128*1024*1024:
                raise ValueError('invalid candidate identity')
        return self


class History(Strict):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    episode: str = Field(max_length=120)
    title: str = Field(max_length=240)
    action: str = Field(max_length=12000)
    dialogue: str = Field(max_length=12000)
    after: str = Field(max_length=4000)


class Withheld(Strict):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    reason: Literal['future', 'outside_scope']


class Compiled(Strict):
    schema_id: Literal['manju.story-brief/v1']
    project_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    project_title: str = Field(max_length=240)
    form: Literal['undecided','film','limited_series','ongoing_series','vertical_serial','music_visual','custom']
    intent: str = Field(max_length=12000)
    ending: str | None = Field(max_length=12000)
    scene: Scene
    order_prefix: list[str] = Field(max_length=512)
    history_omitted: int = Field(ge=0, le=512)
    history: list[History] = Field(max_length=20)
    sources: list[Source] = Field(max_length=64)
    withheld: list[Withheld] = Field(max_length=64)
    policy: Policy
    automatic_execution: Literal[False]
    creative_approval: Literal[False]

    @model_validator(mode='after')
    def closed_context(self):
        import re
        if (not self.order_prefix or self.order_prefix[-1] != self.scene.id
                or len(set(self.order_prefix)) != len(self.order_prefix)
                or any(not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}', x) for x in self.order_prefix)):
            raise ValueError('invalid scene order prefix')
        if self.history_omitted + len(self.history) != len(self.order_prefix) - 1:
            raise ValueError('history coverage does not match scene order')
        expected = self.order_prefix[-1-len(self.history):-1] if self.history else []
        if [h.id for h in self.history] != expected:
            raise ValueError('history must follow the actual preceding order')
        if self.policy.scope == 'character' and (self.policy.character_id is None or self.history):
            raise ValueError('character context needs explicit identity and no inherited history')
        if self.policy.scope != 'character' and self.policy.character_id is not None:
            raise ValueError('unexpected context character')
        if self.policy.scope != 'author' and self.ending is not None:
            raise ValueError('author ending is forbidden outside author scope')
        claimed = [x.id for x in self.sources] + [x.id for x in self.withheld]
        if len(claimed) != len(set(claimed)) or set(claimed) != set(self.scene.source_ids):
            raise ValueError('source coverage must be exact')
        for x in self.sources:
            if self.policy.scope == 'author':
                continue
            if x.from_scene_id is not None and x.from_scene_id not in self.order_prefix:
                raise ValueError('future source cannot enter scoped context')
            if x.scope != 'audience' and not (self.policy.scope == 'character' and x.scope == 'character' and x.knower_id == self.policy.character_id):
                raise ValueError('source outside chosen scope')
        return self


class Brief(Strict):
    id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    scene_id: str
    content: Compiled
    content_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    observation: Observation

    @model_validator(mode='after')
    def pinned(self):
        if self.scene_id != self.content.scene.id or self.content_sha256 != digest(self.content):
            raise ValueError('brief content does not match its pinned hash or scene')
        return self


class Link(Strict):
    brief_id: str
    shot_id: str
    prompt_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class Story(Strict):
    schema_id: Literal['manju.story-notebook/v1']
    project_id: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
    title: str = Field(max_length=240)
    form: Literal['undecided','film','limited_series','ongoing_series','vertical_serial','music_visual','custom']
    intent: str = Field(max_length=12000)
    ending: str = Field(max_length=12000)
    sources: list[Source] = Field(max_length=512)
    scenes: list[Scene] = Field(max_length=512)
    briefs: list[Brief] = Field(max_length=128)
    link: Link | None

    @model_validator(mode='after')
    def graph(self):
        entities = {s.id: s for s in self.sources}
        scenes = {s.id: s for s in self.scenes}
        briefs = {b.id: b for b in self.briefs}
        for seq, mapping in ((self.sources, entities),(self.scenes, scenes),(self.briefs, briefs)):
            if len(seq) != len(mapping):
                raise ValueError('duplicate stable story ID')
        characters = {s.id for s in self.sources if s.kind == 'character'}
        for source in self.sources:
            if source.from_scene_id is not None and source.from_scene_id not in scenes:
                raise ValueError('source visibility references missing scene')
            if source.scope == 'character':
                if source.knower_id not in characters:
                    raise ValueError('character knowledge requires an existing character')
            elif source.knower_id is not None:
                raise ValueError('only character knowledge has a knower')
        for scene in self.scenes:
            if len(set(scene.source_ids)) != len(scene.source_ids) or set(scene.source_ids)-entities.keys():
                raise ValueError('missing or duplicate scene source')
            if scene.policy.scope == 'character':
                if scene.policy.character_id not in characters:
                    raise ValueError('character context requires explicit character')
            elif scene.policy.character_id is not None:
                raise ValueError('unexpected context character')
            if len({r.id for r in scene.references}) != len(scene.references):
                raise ValueError('duplicate reference ID')
        if self.link and self.link.brief_id not in briefs:
            raise ValueError('linked brief missing')
        # Strict UTF-8, no surrogate text or ambiguous scalar lengths across runtimes.
        raw = canonical(self)
        if len(raw) > MAX_BYTES:
            raise ValueError('story notebook exceeds 1 MiB')
        return self


def new_story(project_id: str = 'PROJECT') -> dict:
    return Story.model_validate(dict(schema_id='manju.story-notebook/v1',project_id=project_id,
        title='',form='undecided',intent='',ending='',sources=[],scenes=[],briefs=[],link=None)).model_dump(mode='json')


def compile_brief(value: Story | dict, scene_id: str, policy: dict | Policy | None = None) -> dict:
    story = value if isinstance(value, Story) else Story.model_validate(value)
    order = [s.id for s in story.scenes]
    if scene_id not in order:
        raise AuthoringError('场景已不存在，请保留旧简报并手动复核')
    index = order.index(scene_id)
    scene = story.scenes[index]
    chosen = policy if isinstance(policy, Policy) else Policy.model_validate(policy) if policy else scene.policy
    if chosen.scope == 'character':
        if chosen.character_id not in {x.id for x in story.sources if x.kind == 'character'}:
            raise AuthoringError('指定人物已不存在；请重新核对知情范围')
    elif chosen.character_id is not None:
        raise AuthoringError('此范围不能指定知情人物')
    sources, withheld = [], []
    by_id = {s.id:s for s in story.sources}
    for sid in scene.source_ids:
        item = by_id[sid]
        future = item.from_scene_id is not None and order.index(item.from_scene_id) > index
        allowed = chosen.scope == 'author' or (not future and (item.scope == 'audience' or
            chosen.scope == 'character' and item.scope == 'character' and item.knower_id == chosen.character_id))
        if allowed:
            sources.append(item.model_dump(mode='json'))
        else:
            # No title or text from withheld author/future sources is leaked into context.
            withheld.append({'id':sid,'reason':'future' if future else 'outside_scope'})
    previous = story.scenes[max(0,index-chosen.history_count):index]
    # A character does NOT automatically know everything in previous scenes.
    history = [] if chosen.scope == 'character' else [dict(id=s.id,episode=s.episode,title=s.title,action=s.action,dialogue=s.dialogue,after=s.after) for s in previous]
    data=dict(schema_id='manju.story-brief/v1',project_id=story.project_id,project_title=story.title,
        form=story.form,intent=story.intent,ending=story.ending if chosen.scope=='author' else None,
        scene=scene.model_dump(mode='json'),order_prefix=order[:index+1],history=history,history_omitted=index-len(history),sources=sources,
        withheld=withheld,policy=chosen.model_dump(mode='json'),automatic_execution=False,creative_approval=False)
    return Compiled.model_validate(data).model_dump(mode='json')


def brief_status(value: Story | dict, brief_id: str) -> dict:
    story = value if isinstance(value, Story) else Story.model_validate(value)
    brief = next((b for b in story.briefs if b.id == brief_id), None)
    if brief is None:
        raise AuthoringError('brief missing')
    try:
        current = compile_brief(story, brief.scene_id, brief.content.policy)
    except AuthoringError as exc:
        return {'id':brief.id,'status':'needs_review','changes':['scene_missing'],'detail':str(exc)}
    old=brief.content.model_dump(mode='json')
    changes=[k for k in old if canonical(old[k]) != canonical(current[k])]
    return {'id':brief.id,'status':'current' if not changes else 'needs_review','changes':changes,
        'recorded_sha256':brief.content_sha256,'current_sha256':digest(current),
        'meaning_verified':False,'creative_approval':False}


def read_story(path: Path) -> Story:
    p=Path(path)
    if p.is_symlink() or not p.is_file() or not 1 <= p.stat().st_size <= 2*1024*1024:
        raise AuthoringError('story file missing, linked or oversized')
    with p.open('rb') as stream:
        raw = stream.read(2*1024*1024+1)
    if len(raw) > 2*1024*1024:
        raise AuthoringError('story file grew beyond limit during read')
    return Story.model_validate(json_value(raw))


def write_new(path: Path, value: dict) -> None:
    p=Path(path)
    # Caller validates first. Never modify an existing file or follow a symlink.
    with p.open('xb') as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')+b'\n')


def report(value: Story | dict) -> dict:
    story = value if isinstance(value, Story) else Story.model_validate(value)
    return {'ok':True,'schema_id':story.schema_id,'form':story.form,'route':FORMS[story.form],
            'scenes':len(story.scenes),'sources':len(story.sources),
            'briefs':[brief_status(story,b.id) for b in story.briefs],
            'automatic_execution':False,'semantic_validation':False,'project_modified':False}


def from_series(path: Path) -> dict:
    """Read only the existing registered series Bible. Do not invent a screenplay.

    Stable source IDs use the source file/category/key, never character display names.
    All imported entries remain author-only until the user explicitly scopes them.
    """
    from ..core.series import Series
    from ..core.container import BIBLE_FILES
    from ..core.yamlio import read_yaml
    series = Series(path)
    series.verify_manju_identity()
    config = series.load_config()
    result = new_story('series_'+digest({'name':config.name,'created_at':config.created_at})[:24])
    result['title'] = config.name
    result['intent'] = config.description
    for kind in BIBLE_FILES:
        file = series.bible_dir / (kind+'.yaml')
        if not file.exists():
            continue
        if file.is_symlink() or not file.is_file() or file.stat().st_size > MAX_BYTES:
            raise AuthoringError('Bible file missing, linked or too large: '+kind)
        before = file.read_bytes()
        entries = read_yaml(file)
        if not isinstance(entries, dict):
            raise AuthoringError('Bible must map stable IDs to entries: '+kind)
        for key, item in entries.items():
            if not isinstance(item, dict):
                raise AuthoringError('Bible entry is not an object: '+str(key))
            result['sources'].append({
                'id':'bible_'+digest({'file':kind,'id':str(key)})[:24],
                'kind':'character' if kind=='characters' else 'setting',
                'name':str(item.get('name') or key),
                'text':json.dumps(item,ensure_ascii=False,indent=2,allow_nan=False),
                'scope':'author','from_scene_id':None,'knower_id':None,
                'provenance':f'bible/{kind}.yaml#{key} | entry-sha256:{digest(item)} | file-sha256:{sha256(before).hexdigest()}',
            })
        if file.read_bytes() != before:
            raise AuthoringError('Bible changed during read; try again: '+kind)
    return Story.model_validate(result).model_dump(mode='json')


def verify_brief_kit(path: Path) -> dict:
    """Read-only closed-set hashes and reference identity; never a provenance signature.

    Does not execute, extract, decode media, or compare with a newer notebook.
    """
    import re
    import stat
    import zipfile
    from .workspace import MAX_TOTAL
    p = Path(path)
    if p.is_symlink() or not p.is_file() or p.stat().st_size > MAX_TOTAL + 4*MAX_BYTES:
        raise AuthoringError('invalid story brief archive or size')
    try:
        with zipfile.ZipFile(p) as z:
            members = z.infolist()
            names = [i.filename for i in members]
            if len(names) != len(set(names)) or not 3 <= len(names) <= 35:
                raise AuthoringError('unexpected or duplicate archive entries')
            if not {'BRIEF.json','README.md','MANIFEST.json'} <= set(names):
                raise AuthoringError('brief archive lacks required documents')
            for info in members:
                if info.filename not in {'BRIEF.json','README.md','MANIFEST.json'} and not re.fullmatch(r'media/[0-9a-f]{64}\.(png|jpg|jpeg|webp|mp4|mov|m4v|wav|mp3|m4a|flac|ogg|bin)',info.filename):
                    raise AuthoringError('unexpected or unsafe brief archive path')
                if info.is_dir() or info.compress_type != zipfile.ZIP_STORED or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                    raise AuthoringError('brief archive must be plain stored files')
                limit = 128*1024*1024 if info.filename.startswith('media/') else MAX_BYTES
                if not 0 < info.file_size <= limit:
                    raise AuthoringError('brief member empty or oversized')
            if sum(i.file_size for i in members if i.filename.startswith('media/')) > MAX_TOTAL:
                raise AuthoringError('brief media total exceeds bound')
            manifest = json_value(z.read('MANIFEST.json'))
            if not isinstance(manifest,dict) or set(manifest) != {'schema_id','files'} or manifest['schema_id'] != 'manju.story-brief-manifest/v1':
                raise AuthoringError('invalid story brief inventory')
            files = manifest['files']
            if not isinstance(files,dict) or set(files) != set(names)-{'MANIFEST.json'}:
                raise AuthoringError('brief inventory does not match archive')
            for name, expected in files.items():
                if not isinstance(expected,str) or not re.fullmatch('[0-9a-f]{64}',expected):
                    raise AuthoringError('invalid inventory hash')
                with z.open(name) as f:
                    if sha256_stream(f) != expected:
                        raise AuthoringError('brief member content changed: '+name)
            brief = Brief.model_validate(json_value(z.read('BRIEF.json')))
            media = {}
            for name in names:
                if name.startswith('media/'):
                    h = name.split('/')[1].split('.')[0]
                    if h in media or files[name] != h:
                        raise AuthoringError('duplicate or non-content-addressed reference')
                    media[h] = z.getinfo(name).file_size
            required = {}
            for ref in brief.content.scene.references:
                if ref.sha256 in required and required[ref.sha256] != ref.bytes:
                    raise AuthoringError('same reference identity has inconsistent size')
                required[ref.sha256] = ref.bytes
            if media != required:
                raise AuthoringError('reference originals missing, extra or size differs')
            return {'ok':True,'brief_id':brief.id,'content_sha256':brief.content_sha256,
                    'reference_roles':len(brief.content.scene.references),'media_files':len(media),
                    'current_notebook_checked':False,'media_decoded':False,'meaning_verified':False,
                    'creative_approval':False,'automatic_execution':False}
    except (zipfile.BadZipFile,RuntimeError) as e:
        raise AuthoringError('unreadable story brief archive') from e


def sha256_stream(stream) -> str:
    h=sha256()
    for chunk in iter(lambda:stream.read(1024*1024),b''):
        h.update(chunk)
    return h.hexdigest()


def assert_settable_story(story: Story) -> None:
    """Reject browser-sanitized scalar fields before publishing a restore candidate."""
    singles = [story.title]
    singles += [x.name for x in story.sources] + [x.provenance for x in story.sources]
    singles += [x.title for x in story.scenes] + [x.episode for x in story.scenes]
    if any('\n' in x or '\r' in x for x in singles):
        raise AuthoringError('single-line story fields cannot preserve line breaks')
    def check(value):
        if isinstance(value, str) and '\r' in value:
            raise AuthoringError('story text must use LF line endings')
        if isinstance(value, dict):
            for item in value.values(): check(item)
        elif isinstance(value, list):
            for item in value: check(item)
    check(story.model_dump(mode='json'))
