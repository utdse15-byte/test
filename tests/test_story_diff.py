"""Read-only story diffs must explain changes, not claim semantic approval."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from tests.test_ide_workspace import workspace, read, put
from tests.test_story_sources import sample, scene, source, pin
from manju.authoring.ide import preview_workspace


def test_ide_preview_explains_story_only_change(workspace):
    root, base, _ = workspace
    old = read(root / 'STORY.json')
    changed = deepcopy(old)
    changed['scenes'][0]['action'] = '主动交出箱子，并松开双手。'
    put(root / 'STORY.json', changed)
    report = preview_workspace(root)
    rows = report['story_diff']['scenes']
    row = next(x for x in rows if x['id'] == 'S10')
    action = next(x for x in row['changes'] if x['field'] == 'action')
    assert action['before'] == old['scenes'][0]['action']
    assert action['after'] == changed['scenes'][0]['action']
    assert report['changed_fields'] == []
    assert base.read_bytes() == (root / 'BASE_CHECKOUT.zip').read_bytes()

from manju.authoring.story_diff import compare_stories, render_preview_html, write_preview_html, FIELDS
from manju.authoring.core import canonical, digest, AuthoringError
from manju.authoring.story import Story
from typer.testing import CliRunner
from manju.authoring.cli import app


def variants(case):
    a=sample(); pin(a); b=deepcopy(a)
    if case=='action': b['scenes'][0]['action']='  放开箱子\n再退后一步。 '
    elif case=='repeated':
        for d in (a,b):
            d['scenes'][0]['dialogue']='不。\n不。';d['scenes'][1]['dialogue']='不。\n不。'
        b['scenes'][1]['dialogue']='不。\n好。'
    elif case=='rename': b['sources'][0]['name']='林岚（新名称）'
    elif case=='reorder': b['scenes']=list(reversed(b['scenes']))
    elif case=='insert': b['scenes'].insert(0,scene('NEW','新开场'))
    elif case=='delete': b['scenes'].pop(0)
    elif case=='policy': b['scenes'][1]['policy']['history_count']=2
    elif case=='ending': b['ending']='重新选择离开。'
    elif case=='observation': b['briefs'][0]['observation']['seen']='片中所见（明确人工记录）'
    elif case=='add-brief': pin(b,'S02','NEW_BRIEF')
    elif case=='sources-order': b['sources'].reverse()
    elif case=='add-source': b['sources'].append(source('NEW_PERSON',name='新人物'))
    elif case=='unicode': b['scenes'][1]['action']='👨‍👩‍👧‍👦 雨\n\n雨 \t'
    elif case=='html': b['scenes'][0]['action']='</pre><img src=x onerror=alert(1)><script>alert(2)</script>'
    elif case=='different': b['project_id']='OTHER'
    elif case=='empty': a=b=None
    elif case=='created': a=None
    elif case=='removed': b=None
    elif case=='unchanged': pass
    else: raise AssertionError(case)
    return a,b

CASES=['action','repeated','rename','reorder','insert','delete','policy','ending','observation',
       'add-brief','sources-order','add-source','unicode','html','different','empty','created','removed','unchanged']

@pytest.mark.parametrize('case',CASES)
def test_diff_deterministic_and_read_only(case):
    a,b=variants(case); olda,oldb=deepcopy(a),deepcopy(b)
    r=compare_stories(a,b)
    assert a==olda and b==oldb
    assert r==compare_stories(a,b)
    assert r['before_sha256']==digest(a) and r['after_sha256']==digest(b)
    assert r['read_only'] and not r['semantic_validation'] and not r['automatic_execution']
    assert not r['merges_later_browser_edits']
    assert r['changed']==(canonical(a)!=canonical(b))


def test_addition_does_not_mislabel_shift_as_rewrite():
    r=compare_stories(*variants('insert'))
    assert r['orders']['scenes']['changed'] and not r['orders']['scenes']['shared_order_changed']
    shifted=[x for x in r['scenes'] if x['id']!='NEW']
    assert len(shifted)==3 and all(x['status']=='unchanged' and x['position_changed'] and not x['changes'] for x in shifted)


def test_reordered_content_not_changed():
    r=compare_stories(*variants('reorder'))
    assert r['orders']['scenes']['shared_order_changed']
    assert all(not x['changes'] for x in r['scenes'])
    assert set(r['unchanged_scene_ids'])=={'S10','S02','S30'}


def test_same_text_in_two_scenes_never_uses_text_as_identity():
    r=compare_stories(*variants('repeated'))
    assert [s['id'] for s in r['scenes']]==['S02']
    assert r['scenes'][0]['before_position']==2


def test_same_name_new_id_is_not_rename():
    a=sample();b=deepcopy(a);b['scenes'][0]['id']='NEW'
    r=compare_stories(a,b)
    assert {x['id']:x['status'] for x in r['scenes']}=={'NEW':'added','S10':'removed'}


def test_different_project_collision_is_not_diff():
    r=compare_stories(*variants('different'))
    assert r['relationship']=='different_project' and not r['scenes'] and not r['story_fields']
    assert r['ending_field_unchanged'] is None


def test_ending_fact_is_literal_not_semantic_approval():
    r=compare_stories(*variants('action'))
    assert r['ending_field_unchanged'] and r['last_scene_content_unchanged']
    assert r['last_scene_id']=='S30'
    assert not r['semantic_validation']
    assert r['brief_status_changes'][0]['after']['status']=='needs_review'


@pytest.mark.parametrize('bad',[lambda d:d['scenes'].append(deepcopy(d['scenes'][0])),
    lambda d:d['scenes'][0].update(source_ids=['NOT_REAL']),lambda d:d.update(extra='no'),
    lambda d:d['scenes'][0]['policy'].update(history_count=True)])
def test_invalid_input_cannot_produce_trustworthy_report(bad):
    a=sample();b=deepcopy(a);bad(b)
    with pytest.raises(ValueError):compare_stories(a,b)


def test_mutated_model_revalidated():
    model=Story.model_validate(sample());model.scenes[0].source_ids.append('NOT_REAL')
    with pytest.raises(ValueError):compare_stories(model,sample())


def test_html_is_escaped_has_no_script_or_action_controls(workspace,tmp_path):
    w,_,_=workspace;d=read(w/'STORY.json');d['scenes'][0]['action']='</pre><script>evil()</script>'
    put(w/'STORY.json',d);report=preview_workspace(w);text=render_preview_html(report)
    assert '<script>' not in text and '&lt;script&gt;evil()' in text
    assert '<button' not in text and '<form' not in text and '<input' not in text
    assert "default-src 'none'" in text
    p=tmp_path/'READ.html';write_preview_html(report,p)
    with pytest.raises(AuthoringError):write_preview_html(report,p)
    assert p.read_text(encoding='utf-8')==text


def test_html_cli_does_not_change_preview_hash_or_workspace(workspace,tmp_path):
    w,_,_=workspace;before={str(p.relative_to(w)):p.read_bytes() for p in w.rglob('*') if p.is_file()}
    expected=preview_workspace(w);out=tmp_path/'这次改了什么.html'
    got=CliRunner().invoke(app,['ide-preview',str(w),'--html',str(out)])
    assert got.exit_code==0,got.output
    assert json.loads(got.output)==expected
    assert out.is_file()
    assert before=={str(p.relative_to(w)):p.read_bytes() for p in w.rglob('*') if p.is_file()}
    no=CliRunner().invoke(app,['ide-preview',str(w),'--html',str(out)])
    assert no.exit_code==2 and json.loads(no.output)['ok'] is False


def test_all_current_model_fields_are_accounted_for():
    # A future field cannot silently disappear from a readable diff.
    from manju.authoring.story import Scene,Source,Brief
    assert set(FIELDS['story'])==set(Story.model_fields)-{'schema_id','project_id','sources','scenes','briefs'}
    for kind,model in [('scenes',Scene),('sources',Source),('briefs',Brief)]:
        assert set(FIELDS[kind])==set(model.model_fields)-{'id'}
