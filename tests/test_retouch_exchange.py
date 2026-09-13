from copy import deepcopy
from hashlib import sha256
import json
import zipfile

import pytest
from typer.testing import CliRunner

from manju.authoring.core import digest, AuthoringError
from manju.authoring.retouch import (create_task,normalize_task,check_return,prompt_for,
                                     image_name,verify_kit)
from manju.authoring.cli import app
from tests.test_rebuilt_r12_director import fake_plan,png,recompute
from tests.test_rebuilt_r5_workbench import browser,page


def baseline():
    p,*_=fake_plan();p=p.model_dump(mode='json')
    a=deepcopy(p['anchors'][0]);a['id']='A02';a['source_time_ms']=1500
    p['anchors'].append(a)
    return recompute(p)


def raster(name='out.png',w=1920,h=1080,value=13):
    data=png(w,h,value)
    return dict(sha256=sha256(data).hexdigest(),bytes=len(data),width=w,height=h,filename=name)


@pytest.mark.parametrize('route',['manual-top-quality','openai-sunburst-20260908'])
def test_route_explicit_and_task_parity(page,route):
    p=baseline();task=create_task(p,route)
    actual=page.evaluate('async x=>ManjuRetouch.task(x.p,x.route)',dict(p=p,route=route))
    assert actual==task and not task['execution_authorized']
    for a in p['anchors']:
        assert page.evaluate('x=>ManjuRetouch.prompt(x.t,x.a)',dict(t=task,a=a))==prompt_for(task,a)
    if route!='manual-top-quality':assert task['route']['quality']=='max'


@pytest.mark.parametrize('mutate',[
 lambda t:t.update(execution_authorized=True),lambda t:t.update(automatic_selection=True),
 lambda t:t.update(extra=1),lambda t:t.update(task_sha256='f'*64),
 lambda t:t['route'].update(quality='low'),lambda t:t['route'].update(id='invented'),
 lambda t:t['jobs'][0].update(output_name='../evil.png'),lambda t:t['jobs'].reverse(),
 lambda t:t.update(checked_on='wrong'),lambda t:t['director_plan'].update(shot_id='elsewhere'),
])
def test_changed_task_is_rejected(mutate):
    t=create_task(baseline(),'manual-top-quality');mutate(t)
    with pytest.raises((ValueError,AuthoringError)):normalize_task(t)


def test_partial_return_allows_other_anchor_edits_and_idempotence():
    p=baseline();t=create_task(p,'manual-top-quality');n=t['jobs'][0]['output_name']
    current=deepcopy(p);current['anchors'][1]['target']='另一时刻继续写的新要求'
    recompute(current);r=raster(n);rows=check_return(t,current,{n:r});assert len(rows)==1
    current['anchors'][0]['guide']=r;recompute(current)
    assert check_return(t,current,{n:r})[0]['unchanged']
    second=t['jobs'][1]['output_name']
    with pytest.raises(AuthoringError,match='target changed'):check_return(t,current,{second:raster(second)})


@pytest.mark.parametrize('change',[
 lambda p:p.update(shot_id='新镜头'),lambda p:p.update(preserve=['不同构图']),
 lambda p:p['anchors'][0].update(target='新意图'),
 lambda p:p['anchors'][0].update(source_time_ms=600),
 lambda p:p['anchors'][0].update(guide=raster('changed.png',value=222)),
 lambda p:p['anchors'].pop(0),
])
def test_context_or_corresponding_target_changed_blocks_return(change):
    p=baseline();t=create_task(p,'manual-top-quality');name=t['jobs'][0]['output_name']
    change(p);recompute(p)
    with pytest.raises((ValueError,AuthoringError)):check_return(t,p,{name:raster(name)})


@pytest.mark.parametrize('wrong',['unknown.png','../A01.png','A01.png'])
def test_unknown_output_names_rejected(wrong):
    p=baseline();t=create_task(p,'manual-top-quality')
    with pytest.raises(AuthoringError):check_return(t,p,{wrong:raster(wrong)})


def test_aspect_and_empty_outputs_rejected():
    p=baseline();t=create_task(p,'manual-top-quality');n=t['jobs'][0]['output_name']
    with pytest.raises(AuthoringError):check_return(t,p,{n:raster(n,100,100)})
    with pytest.raises(AuthoringError):check_return(t,p,{})


def kit(tmp_path):
    p,_,f,g=fake_plan();t=create_task(p,'manual-top-quality')
    data={'TASK.json':json.dumps(t,ensure_ascii=False).encode(),'READ_FIRST.txt':b'non-executable instructions'}
    for a in t['director_plan']['anchors']:
        data['PROMPTS/'+a['id']+'.txt']=prompt_for(t,a).encode()
        data[image_name(a['frame'])]=f;data[image_name(a['guide'])]=g
    return data


def writezip(path,data):
    with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as z:
        for n,b in data.items():z.writestr(n,b)
    return path


def test_independent_verifier_cli_readonly(tmp_path):
    path=writezip(tmp_path/'kit.zip',kit(tmp_path));before=path.read_bytes()
    result=verify_kit(path);assert result['ok'] and not result['contains_video']
    cli=CliRunner().invoke(app,['retouch-verify',str(path)])
    assert cli.exit_code==0,cli.output
    assert path.read_bytes()==before


@pytest.mark.parametrize('kind',['extra','traversal','image','prompt','missing','duplicate'])
def test_archive_tampering_rejected(tmp_path,kind):
    data=kit(tmp_path)
    if kind=='extra':data['run.exe']=b'no'
    if kind=='traversal':data['../no.txt']=b'no'
    if kind=='image':data[next(n for n in data if n.startswith('INPUTS'))]=b'bad'
    if kind=='prompt':data['PROMPTS/A01.txt']=b'substituted'
    if kind=='missing':data.pop('TASK.json')
    path=writezip(tmp_path/'bad.zip',data)
    if kind=='duplicate':
        with zipfile.ZipFile(path,'a') as z:z.writestr('READ_FIRST.txt',b'duplicate')
    with pytest.raises((ValueError,AuthoringError)):verify_kit(path)
