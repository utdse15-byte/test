from copy import deepcopy
from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from manju.authoring.cli import app
from manju.authoring.core import AuthoringError, canonical
from manju.authoring.ide import open_workspace, preview_workspace, export_story_return
from manju.authoring.story_return import create_return, read_return, preview_files, apply_files
from tests.test_rebuilt_r16_user_desk import write_desk
from tests.test_story_sources import sample, pin
from tests.test_story_return import scenario


def test_real_cli_selective_roundtrip_and_no_overwrite(tmp_path):
    d=sample();pin(d);base,_,_=write_desk(tmp_path,rich=True,edit=lambda x:x.update(schema_id='manju.desk-session/v2',story=d))
    original=base.read_bytes();w=tmp_path/'AI工作目录';open_workspace(base,w);b=deepcopy(d);b['scenes'][1]['action']='AI新的动作';(w/'STORY.json').write_bytes(canonical(b))
    edit=json.loads((w/'EDIT.json').read_text(encoding='utf-8'));edit['values']['shot/prompt']='另一条镜头修改';(w/'EDIT.json').write_bytes(canonical(edit))
    preview=preview_workspace(w);packet=tmp_path/'RETURN.json';cli=CliRunner()
    r=cli.invoke(app,['ide-story-return',str(w),'--expected-preview',preview['preview_sha256'],'--output',str(packet)])
    assert r.exit_code==0,r.stdout;assert json.loads(r.stdout)['excluded_edit_fields']==['shot/prompt']
    assert read_return(packet)==create_return(d,b)
    c=deepcopy(d);c['scenes'][0]['action']='本地新稿';current=tmp_path/'CURRENT.json';current.write_bytes(canonical(c));raw=current.read_bytes()
    r=cli.invoke(app,['story-return-preview',str(current),str(packet)]);assert r.exit_code==0;r=json.loads(r.stdout)
    out=tmp_path/'NEW.json';args=['story-return-apply',str(current),str(packet),'--take','scene/S02/action','--expected-preview',r['preview_sha256'],'--output',str(out)]
    q=cli.invoke(app,args);assert q.exit_code==0,q.stdout;res=json.loads(out.read_text(encoding='utf-8'))
    assert res['scenes'][0]['action']=='本地新稿' and res['scenes'][1]['action']=='AI新的动作'
    assert current.read_bytes()==raw and base.read_bytes()==original and (w/'BASE_CHECKOUT.zip').read_bytes()==original
    assert cli.invoke(app,args).exit_code==2


def test_stale_export_creates_nothing(tmp_path):
    d=sample();base,_,_=write_desk(tmp_path,edit=lambda x:x.update(schema_id='manju.desk-session/v2',story=d));w=tmp_path/'work';open_workspace(base,w);p=preview_workspace(w);d['title']='later';(w/'STORY.json').write_bytes(canonical(d))
    with pytest.raises(AuthoringError,match='stale'):export_story_return(w,tmp_path/'none.json',expected_preview=p['preview_sha256'])
    assert not (tmp_path/'none.json').exists()


@pytest.mark.parametrize('raw',[b'{"schema_id":1,"schema_id":2}',b'\xff',b'['*90+b'0'+b']'*90,b'x'*(2*1024*1024+4097)])
def test_malformed_and_oversized_file(tmp_path,raw):
    p=tmp_path/'bad.json';p.write_bytes(raw)
    with pytest.raises((ValueError,UnicodeError)):read_return(p)


def test_empty_old_checkout_needs_explicit_new_story_import(tmp_path):
    base,_,_=write_desk(tmp_path);w=tmp_path/'work';open_workspace(base,w);(w/'STORY.json').write_bytes(canonical(sample()));p=preview_workspace(w)
    with pytest.raises(AuthoringError,match='existing story'):export_story_return(w,tmp_path/'out.json',expected_preview=p['preview_sha256'])


def test_input_changes_during_file_build(tmp_path,monkeypatch):
    import manju.authoring.story_return as m
    a,b,c=scenario();current=tmp_path/'c.json';packet=tmp_path/'p.json';current.write_bytes(canonical(c));packet.write_bytes(canonical(create_return(a,b)));p=preview_files(current,packet);original=m.apply_return
    def changing(*args,**kw):
        result=original(*args,**kw);current.write_bytes(b'other editor');return result
    monkeypatch.setattr(m,'apply_return',changing)
    with pytest.raises(AuthoringError,match='input changed'):apply_files(current,packet,tmp_path/'out.json',['scene/S02/action'],expected_preview=p['preview_sha256'])
    assert not (tmp_path/'out.json').exists()


def test_symlink_rejected(tmp_path):
    p=tmp_path/'target';p.write_bytes(b'{}');link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises(AuthoringError):read_return(link)


def test_utf8_bom_return_supported(tmp_path):
    a,b,c=scenario();p=tmp_path/'packet.json';packet=create_return(a,b);p.write_bytes(b'\xef\xbb\xbf'+canonical(packet))
    assert read_return(p)==packet


def test_two_large_valid_stories_fit_own_envelope(tmp_path):
    from tests.test_story_sources import source
    a=sample();a['sources']=[]
    for scene in a['scenes']:scene['source_ids']=[]
    while len(canonical(a))<1024*1024-24:
        a['sources'].append(source('A'+str(len(a['sources'])),text='x'*12000))
    excess=len(canonical(a))-(1024*1024-24)
    a['sources'][-1]['text']=a['sources'][-1]['text'][:-excess] if excess else a['sources'][-1]['text']
    packet=create_return(a,a);raw=canonical(packet)
    assert 2*1024*1024<len(raw)<2*1024*1024+4096
    p=tmp_path/'large.json';p.write_bytes(raw);assert read_return(p)==packet
