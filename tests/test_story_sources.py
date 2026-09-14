from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import zipfile

import pytest
from typer.testing import CliRunner
from manju.authoring.story import Story, Scene, Source, FORMS, new_story, compile_brief, brief_status, report, read_story, from_series
from manju.authoring.core import canonical, digest
from manju.authoring.desk import StoryDesk, parse_desk, verify_desk, extract_studio
from manju.authoring.cli import app
from tests.test_rebuilt_r16_user_desk import write_desk


def scene(id, action='', **changes):
    return dict(id=id,episode='E01',title=id,purpose='',action=action,dialogue='',before='',after='',source_ids=[],references=[],policy={'scope':'audience','character_id':None,'history_count':0},**changes)


def source(id, **changes):
    return dict({'id':id,'kind':'character','name':id,'text':'','scope':'author','from_scene_id':None,'knower_id':None,'provenance':''},**changes)


def sample():
    d=new_story('RAIN_HARBOR');d.update(title='雨港',form='limited_series',intent='守信与代价',ending='盒子里的地图是空白的。')
    a=scene('S10','林岚按住箱盖，阻止周泊拿走。');b=scene('S02','周泊在暗室找到钥匙。');c=scene('S30','两人回到港口。')
    a['title']='渡口托付';b['episode']='E02';c['episode']='E03'
    d['scenes']=[a,b,c]
    d['sources']=[source('C01',name='林岚',text='码头修船匠',scope='audience'),source('C02',name='周泊',text='前航海员',scope='audience'),source('F01',kind='fact',name='最终秘密',text='地图是空白的',scope='audience',from_scene_id='S30'),source('K01',kind='fact',name='钥匙藏处',text='钥匙在左抽屉',scope='character',knower_id='C02',from_scene_id='S02')]
    a['source_ids']=['C01','C02','F01','K01'];b['source_ids']=['C02','K01']
    return d


def pin(d,sid='S10',bid='BRIEF1'):
    c=compile_brief(d,sid)
    b=dict(id=bid,scene_id=sid,content=c,content_sha256=digest(c),observation=dict(candidate_sha256=None,candidate_bytes=None,seen='',heard='',deviation='',layer='unreviewed'))
    d['briefs'].append(b);return b


def test_sample_valid_and_route_not_short_drama():
    d=sample();assert Story.model_validate(d).model_dump(mode='json')==d
    assert '限定' not in FORMS['film'] and '付费' not in FORMS['limited_series']
    assert report(d)['semantic_validation'] is False


def test_same_id_reversed_action_marks_only_affected_brief():
    d=sample();a=pin(d);pin(d,'S02','BRIEF2');before=deepcopy(a)
    d['scenes'][0]['action']='林岚主动把箱子递给周泊并松手。'
    assert brief_status(d,'BRIEF1')['status']=='needs_review'
    assert 'scene' in brief_status(d,'BRIEF1')['changes']
    assert brief_status(d,'BRIEF2')['status']=='current'
    assert d['briefs'][0]==before


def test_order_is_not_lexical_and_reorder_marks_prefix():
    d=sample();d['scenes'][1]['policy']['history_count']=1
    b=pin(d,'S02');assert b['content']['history'][0]['id']=='S10'
    d['scenes'][:2]=list(reversed(d['scenes'][:2]))
    assert 'order_prefix' in brief_status(d,b['id'])['changes']


def test_unreferenced_and_future_action_do_not_stale():
    d=sample();pin(d);d['scenes'][2]['action']='未来新动作';d['sources'].append(source('UNUSED',text='新人物'))
    assert brief_status(d,'BRIEF1')['status']=='current'


def test_source_rename_or_content_marks_dependency_not_all_library():
    d=sample();pin(d);d['sources'][0]['name']='林岚（雨衣版）'
    assert brief_status(d,'BRIEF1')['changes']==['sources']


def test_hidden_future_and_other_knowledge_not_leaked():
    d=sample();c=compile_brief(d,'S10');raw=canonical(c).decode()
    assert '地图是空白' not in raw and '最终秘密' not in raw and '左抽屉' not in raw
    assert c['ending'] is None and {x['id'] for x in c['withheld']}=={'F01','K01'}


def test_character_does_not_inherit_other_scenes_or_other_minds():
    d=sample();p={'scope':'character','character_id':'C01','history_count':20}
    c=compile_brief(d,'S02',p);assert c['history']==[] and c['history_omitted']==1
    assert '左抽屉' not in canonical(c).decode()
    c=compile_brief(d,'S02',{'scope':'character','character_id':'C02','history_count':20})
    assert '左抽屉' in canonical(c).decode()


def test_explicit_author_scope_can_see_marked_plan():
    c=compile_brief(sample(),'S10',{'scope':'author','character_id':None,'history_count':0})
    assert c['ending']=='盒子里的地图是空白的。' and len(c['sources'])==4
    assert c['creative_approval'] is False and c['automatic_execution'] is False


def test_missing_scene_keeps_old_brief():
    d=sample();b=pin(d);d['scenes']=[d['scenes'][2]]
    # sources still reference existing S30, K01 visibility must be moved explicitly.
    d['sources'][3]['from_scene_id']=None
    assert brief_status(d,b['id'])['status']=='needs_review'
    assert d['briefs'][0]['content']['scene']['id']=='S10'


def test_reveal_change_changes_availability():
    d=sample();pin(d);d['sources'][2]['from_scene_id']='S10'
    assert 'sources' in brief_status(d,'BRIEF1')['changes']


def test_observation_is_not_canon_or_approval():
    d=sample();b=pin(d);saved=deepcopy(b['content']);b['observation'].update(seen='演员交出了箱子',heard='未听清',deviation='与原计划相反',layer='story')
    assert b['content']==saved and brief_status(d,b['id'])['status']=='current'
    assert b['content']['creative_approval'] is False


@pytest.mark.parametrize('change',[
 lambda d:d.update(schema_id='unknown'),lambda d:d.update(extra=True),lambda d:d.pop('ending'),
 lambda d:d.update(form='cheapest_model'),lambda d:d.update(title=4),lambda d:d.update(title='x'*241),
 lambda d:d['sources'].append(deepcopy(d['sources'][0])),lambda d:d['scenes'].append(deepcopy(d['scenes'][0])),
 lambda d:d['sources'][0].update(from_scene_id='NOT_EXIST'),lambda d:d['sources'][0].update(knower_id='C01'),
 lambda d:d['sources'][0].update(scope='character',knower_id='NOT_EXIST'),
 lambda d:d['scenes'][0].update(source_ids=['NOT_EXIST']),lambda d:d['scenes'][0].update(source_ids=['C01','C01']),
 lambda d:d['scenes'][0]['policy'].update(history_count=True),lambda d:d['scenes'][0]['policy'].update(history_count=21),
 lambda d:d['scenes'][0]['policy'].update(scope='character',character_id=None),
 lambda d:d['scenes'][0]['policy'].update(character_id='C01'),
 lambda d:d['sources'][0].update(id='../C01'),lambda d:d.update(title='bad\ud800'),
 lambda d:d.update(link={'brief_id':'MISSING','shot_id':'S10','prompt_sha256':'a'*64}),
])
def test_strict_rejects_malformed_graph(change):
    d=sample();change(d)
    with pytest.raises((ValueError,UnicodeError)):Story.model_validate(d)


@pytest.mark.parametrize('field,value',[('content_sha256','a'*64),('scene_id','S02')])
def test_tampered_pin_rejected(field,value):
    d=sample();b=pin(d);b[field]=value
    with pytest.raises(ValueError):Story.model_validate(d)


def test_desk_v2_preserves_inner_and_old_v1_still_exact(tmp_path):
    story=sample();pin(story)
    path,meta,inner=write_desk(tmp_path,edit=lambda d:d.update(schema_id='manju.desk-session/v2',story=story))
    assert isinstance(parse_desk(meta),StoryDesk);assert verify_desk(path)['ok']
    out=tmp_path/'inner.zip';extract_studio(path,out);assert out.read_bytes()==inner
    assert json.loads(zipfile.ZipFile(path).read('DESK.json'))['story']==story
    old,doc,oldinner=write_desk(tmp_path,'old.zip');assert parse_desk(doc).schema_id.endswith('/v1') and verify_desk(old)['ok']


def test_cli_readonly_and_no_overwrite(tmp_path):
    d=sample();pin(d);p=tmp_path/'story.json';p.write_bytes(canonical(d));before=p.read_bytes();r=CliRunner()
    result=r.invoke(app,['story-check',str(p)]);assert result.exit_code==0,result.output
    assert json.loads(result.output)['briefs'][0]['status']=='current'
    output=tmp_path/'brief.json';assert r.invoke(app,['story-brief',str(p),'S10','--output',str(output)]).exit_code==0
    assert r.invoke(app,['story-brief',str(p),'S10','--output',str(output)]).exit_code!=0
    assert p.read_bytes()==before and json.loads(output.read_text(encoding='utf-8'))==compile_brief(d,'S10')


@pytest.mark.parametrize('raw',[b'{"title":"a","title":"b"}',b'{"n":NaN}',b'[]',b'\xff'])
def test_read_rejects_bad_json(tmp_path,raw):
    p=tmp_path/'bad.json';p.write_bytes(raw)
    with pytest.raises((ValueError,TypeError)):read_story(p)


def test_series_bible_import_is_readonly_default_author(tmp_path):
    from manju.core.series import Series
    from manju.core.yamlio import write_yaml
    series=Series.create(tmp_path/'series',name='雨港')
    write_yaml(series.bible_dir/'characters.yaml',{'C01':{'name':'林岚','identity':'修船匠','unknown':'尚未确定'}})
    before={str(p.relative_to(series.root)):sha256(p.read_bytes()).hexdigest() for p in series.root.rglob('*') if p.is_file()}
    doc=from_series(series.root)
    after={str(p.relative_to(series.root)):sha256(p.read_bytes()).hexdigest() for p in series.root.rglob('*') if p.is_file()}
    assert before==after and doc['form']=='undecided' and doc['scenes']==[]
    c=next(x for x in doc['sources'] if x['kind']=='character');assert c['scope']=='author' and '尚未确定' in c['text']
    prior=c['id'];write_yaml(series.bible_dir/'characters.yaml',{'C01':{'name':'改名','identity':'修船匠'}})
    assert next(x for x in from_series(series.root)['sources'] if x['kind']=='character')['id']==prior
