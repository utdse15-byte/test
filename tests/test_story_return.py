from copy import deepcopy
from pathlib import Path
import json
import pytest
from manju.authoring.core import AuthoringError, canonical, digest
from manju.authoring.story import Story, brief_status
from tests.test_story_sources import sample, pin, scene, source


def api():
    from manju.authoring.story_return import create_return, preview_return, apply_return, parse_return
    return create_return, preview_return, apply_return, parse_return


def scenario():
    a=sample();pin(a);b=deepcopy(a);c=deepcopy(a)
    b['scenes'][1]['action']='他先放下怀表，才问她为何隐瞒。'
    b['scenes'][1]['dialogue']='你可以晚一点说。\n但别替我决定。'
    c['scenes'][0]['action']='我后来补写的第一场。'
    c['scenes'][1]['dialogue']='本地后来写的对白。'
    return a,b,c


def test_three_way_selected_only_and_original_immutable():
    create,preview,apply,_=api();a,b,c=scenario();original=deepcopy(c);p=create(a,b);r=preview(c,p)
    assert {x['key']:x['status'] for x in r['rows']}=={'scene/S02/action':'ready','scene/S02/dialogue':'conflict'}
    out=apply(c,p,['scene/S02/action'],expected_preview=r['preview_sha256'])
    assert out['scenes'][1]['action']==b['scenes'][1]['action']
    assert out['scenes'][1]['dialogue']==c['scenes'][1]['dialogue'] and out['scenes'][0]==c['scenes'][0]
    assert out['briefs']==c['briefs'] and c==original and out['ending']==a['ending']
    assert preview(out,p)['rows'][0]['status']=='already_applied'


@pytest.mark.parametrize('selection',[[],['missing'],['scene/S02/dialogue'],['scene/S02/action']*2])
def test_reject_invalid_selection_without_mutation(selection):
    create,preview,apply,_=api();a,b,c=scenario();p=create(a,b);r=preview(c,p);before=deepcopy(c)
    with pytest.raises(AuthoringError):apply(c,p,selection,expected_preview=r['preview_sha256'])
    assert c==before


def test_stale_and_idempotence_and_second_batch():
    create,preview,apply,_=api();a,b,c=scenario();c=deepcopy(a);p=create(a,b);r=preview(c,p)
    x=apply(c,p,['scene/S02/action'],expected_preview=r['preview_sha256'])
    with pytest.raises(AuthoringError,match='stale'):apply(x,p,['scene/S02/dialogue'],expected_preview=r['preview_sha256'])
    newer=preview(x,p);assert newer['rows'][0]['status']=='already_applied'
    with pytest.raises(AuthoringError):apply(x,p,['scene/S02/action'],expected_preview=newer['preview_sha256'])
    assert apply(x,p,['scene/S02/dialogue'],expected_preview=newer['preview_sha256'])==b


def test_structure_whole_group_preserves_surviving_local_text():
    create,preview,apply,_=api();a=sample();pin(a);b=deepcopy(a);b['sources'].append(source('NEW',name='阿芙'))
    b['scenes'][1]['source_ids'].append('NEW');b['scenes'].reverse();c=deepcopy(a);c['scenes'][0]['dialogue']='用户自己的新对白'
    p=create(a,b);r=preview(c,p);assert [x['key'] for x in r['rows']]==['structure']
    out=apply(c,p,['structure'],expected_preview=r['preview_sha256'])
    assert out['scenes'][-1]['id']=='S10' and out['scenes'][-1]['dialogue']=='用户自己的新对白'
    assert out['sources'][-1]['id']=='NEW' and out['briefs']==a['briefs'];Story.model_validate(out)


def test_local_structural_change_blocks_only_structure():
    create,preview,apply,_=api();a,b,c=scenario();b['sources'].append(source('NEW'));c['scenes'].append(scene('LOCAL'))
    p=create(a,b);r=preview(c,p);assert r['rows'][-1]['status']=='conflict'
    out=apply(c,p,['scene/S02/action'],expected_preview=r['preview_sha256'])
    assert out['scenes'][-1]['id']=='LOCAL' and out['sources']==c['sources']


def test_delete_changed_object_is_conflict():
    create,preview,_,_=api();a=sample();a['scenes'].append(scene('EXTRA','old'));b=deepcopy(a);b['scenes'].pop();c=deepcopy(a);c['scenes'][-1]['action']='later'
    assert preview(c,create(a,b))['rows'][0]['status']=='conflict'


def test_missing_object_not_matched_by_same_name_or_text():
    create,preview,_,_=api();a,b,c=scenario();c['scenes'][1]['id']='RENAMED_ID'
    c['sources'][3]['from_scene_id']='RENAMED_ID'
    assert all(x['status']=='missing' for x in preview(c,create(a,b))['rows'])


def test_local_history_link_and_new_observation_survive_text():
    create,preview,apply,_=api();a,b,c=scenario();pin(c,'S02','LOCAL_BRIEF');c['briefs'][0]['observation']['seen']='新的人工观察'
    c['link']={'brief_id':'LOCAL_BRIEF','shot_id':'SHOT','prompt_sha256':'a'*64};p=create(a,b);r=preview(c,p)
    out=apply(c,p,['scene/S02/action'],expected_preview=r['preview_sha256'])
    assert out['briefs']==c['briefs'] and out['link']==c['link'] and brief_status(out,'LOCAL_BRIEF')['status']=='needs_review'


@pytest.mark.parametrize('attack',['identity','old_brief','observation','link','history_order','bad_field','cr','duplicate'])
def test_candidate_history_identity_and_settable_checks(attack):
    create,_,_,_=api();a=sample();pin(a);pin(a,'S02','BRIEF2');b=deepcopy(a)
    if attack=='identity':b['project_id']='OTHER'
    if attack=='old_brief':b['briefs'].pop()
    if attack=='observation':b['briefs'][0]['observation']['seen']='rewritten'
    if attack=='link':b['link']={'brief_id':'BRIEF1','shot_id':'X','prompt_sha256':'f'*64}
    if attack=='history_order':b['briefs'].reverse()
    if attack=='bad_field':b['scenes'][0]['title']='one\ntwo'
    if attack=='cr':b['scenes'][0]['action']='one\r\ntwo'
    if attack=='duplicate':b['scenes'].append(deepcopy(b['scenes'][0]))
    with pytest.raises(ValueError):create(a,b)


@pytest.mark.parametrize('attack',['hash','body','version','extra','automatic','surrogate'])
def test_packet_bad_data_refused(attack):
    create,preview,_,_=api();a,b,c=scenario();p=create(a,b)
    if attack=='hash':p['base_sha256']='f'*64
    if attack=='body':p['candidate']['scenes'][1]['action']='tampered'
    if attack=='version':p['schema_id']='future'
    if attack=='extra':p['execute']='command'
    if attack=='automatic':p['automatic_execution']=True
    if attack=='surrogate':p['candidate']['scenes'][1]['action']='\ud800'
    with pytest.raises((ValueError,UnicodeError)):preview(c,p)


def test_capacity_combination_fails_atomically():
    create,preview,apply,_=api();a=sample();a['sources'] += [source(f'BIG{i}',text='字'*6500) for i in range(50)]
    b=deepcopy(a);b['scenes'][0]['action']='甲'*12000
    c=deepcopy(a);c['scenes'][1]['action']='乙'*12000;Story.model_validate(c)
    p=create(a,b);r=preview(c,p);old=deepcopy(c)
    with pytest.raises(ValueError,match='1 MiB'):apply(c,p,['scene/S10/action'],expected_preview=r['preview_sha256'])
    assert c==old


def test_prototype_id_is_plain_identity():
    create,preview,apply,_=api();a=sample();a['scenes'].append(scene('constructor','old'));b=deepcopy(a);b['scenes'][-1]['action']='</script> 😀\n'
    p=create(a,b);r=preview(a,p);out=apply(a,p,['scene/constructor/action'],expected_preview=r['preview_sha256']);assert out==b


def test_noop_and_explicit_ending_change():
    create,preview,apply,_=api();a=sample();assert preview(a,create(a,a))['rows']==[]
    b=deepcopy(a);b['ending']='新结局';p=create(a,b);r=preview(a,p);assert r['rows'][0]['key']=='project/ending'
    assert apply(a,p,['project/ending'],expected_preview=r['preview_sha256'])==b


def test_mutated_model_rechecked():
    create,_,_,_=api();a=sample();b=Story.model_validate(a);b.scenes.append(b.scenes[0])
    with pytest.raises(ValueError):create(a,b)
