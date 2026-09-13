from copy import deepcopy
import io
import json
from pathlib import Path
from hashlib import sha256
import zipfile
import pytest
from typer.testing import CliRunner
from manju.authoring.core import AuthoringError, canonical
from manju.authoring.ide import open_workspace, preview_workspace, return_workspace
from manju.authoring.desk import verify_desk
from manju.authoring.cli import app
from tests.test_rebuilt_r16_user_desk import write_desk
from tests.test_story_sources import sample, pin


def read(path): return json.loads(path.read_text(encoding='utf-8'))
def put(path, doc): path.write_bytes(canonical(doc))


@pytest.fixture
def workspace(tmp_path):
    d=sample();pin(d)
    base, meta, _=write_desk(tmp_path,rich=True,edit=lambda x:x.update(schema_id='manju.desk-session/v2',story=d))
    root=tmp_path/'AI 中文目录';open_workspace(base,root)
    return root, base, meta


def test_open_preserves_original_and_exposes_real_media(workspace):
    w,base,meta=workspace
    assert (w/'BASE_CHECKOUT.zip').read_bytes()==base.read_bytes()
    assert read(w/'STORY.json')==meta['story']
    assert read(w/'EDIT.json')['values']
    assert len(list((w/'REFERENCES/media').iterdir()))==3
    assert (w/'AGENTS.md').is_file()
    assert preview_workspace(w)['story_changed'] is False


def test_noop_returns_original_bytes(workspace,tmp_path):
    w,base,_=workspace;p=preview_workspace(w);out=tmp_path/'no-op.zip'
    return_workspace(w,out,expected_preview=p['preview_sha256'])
    assert out.read_bytes()==base.read_bytes()


def test_story_and_text_roundtrip_preserves_pending_and_media(workspace,tmp_path):
    w,base,meta=workspace;before=base.read_bytes()
    d=read(w/'STORY.json');d['scenes'][0]['action']='主动交出箱子。';put(w/'STORY.json',d)
    e=read(w/'EDIT.json');e['values']['shot/prompt']='  AI 拟稿\n等待人审 ';put(w/'EDIT.json',e)
    p=preview_workspace(w);assert p['story_changed'] and p['changed_fields']==['shot/prompt']
    assert p['brief_status'][0]['status']=='needs_review'
    out=tmp_path/'候选返回.zip';r=return_workspace(w,out,expected_preview=p['preview_sha256'])
    assert r['ok'] and verify_desk(out)['schema_id']=='manju.desk-session/v2'
    with zipfile.ZipFile(out) as z,zipfile.ZipFile(base) as orig:
        result=json.loads(z.read('DESK.json'));assert result['story']==d
        assert result['external_edit']==meta['external_edit']
        assert result['personal_template']==meta['personal_template']
        assert result['scratch_form']==meta['scratch_form']
        with zipfile.ZipFile(io.BytesIO(z.read('STUDIO.zip'))) as changed,zipfile.ZipFile(io.BytesIO(orig.read('STUDIO.zip'))) as old:
            for n in old.namelist():
                if n.startswith('media/'):assert changed.read(n)==old.read(n)
            doc=json.loads(changed.read('STUDIO.json'))
            assert doc['workspace']['draft']['form']['prompt']=='  AI 拟稿\n等待人审 '
    assert base.read_bytes()==before


@pytest.mark.parametrize('target,change',[
 ('STORY.json',lambda d:d.update(project_id='OTHER')),
 ('STORY.json',lambda d:d['briefs'].clear()),
 ('STORY.json',lambda d:d['briefs'][0]['observation'].update(seen='假称看过')),
 ('STORY.json',lambda d:d.update(title='坏\n标题')),
 ('STORY.json',lambda d:d['scenes'][0].update(action='CR\rLF')),
 ('EDIT.json',lambda d:d['base'].update({'shot/prompt':'伪造基线'})),
 ('EDIT.json',lambda d:d['values'].update({'shot/duration':'not a number'})),
 ('EDIT.json',lambda d:d['values'].update({'injected/key':'x'})),
])
def test_invalid_mutation_rejected_without_output(workspace,tmp_path,target,change):
    w,_,_=workspace;d=read(w/target);change(d);put(w/target,d)
    with pytest.raises((AuthoringError,ValueError)):preview_workspace(w)
    assert not (tmp_path/'RETURN.zip').exists()


def test_stale_preview_rejected(workspace,tmp_path):
    w,_,_=workspace;p=preview_workspace(w);d=read(w/'STORY.json');d['title']='later';put(w/'STORY.json',d)
    with pytest.raises(AuthoringError,match='stale'):
        return_workspace(w,tmp_path/'never.zip',expected_preview=p['preview_sha256'])
    assert not (tmp_path/'never.zip').exists()


def test_original_media_tampering_rejected(workspace):
    w,_,_=workspace;f=next((w/'REFERENCES/media').iterdir());f.write_bytes(b'changed')
    with pytest.raises(AuthoringError,match='original file changed'):preview_workspace(w)


def test_symlink_rejected(workspace,tmp_path):
    w,_,_=workspace;p=w/'STORY.json';p.rename(tmp_path/'outside.json');p.symlink_to(tmp_path/'outside.json')
    with pytest.raises(AuthoringError,match='linked'):preview_workspace(w)


@pytest.mark.parametrize('key',['../oops','/tmp/oops','REFERENCES/../../oops','C:/oops'])
def test_manifest_traversal_rejected(workspace,key):
    w,_,_=workspace;d=read(w/'SESSION.json');d['immutable_files'][key]='a'*64;put(w/'SESSION.json',d)
    with pytest.raises(AuthoringError):preview_workspace(w)


def test_no_overwrite(workspace,tmp_path):
    w,base,_=workspace
    with pytest.raises(AuthoringError):open_workspace(base,w)
    p=preview_workspace(w);out=tmp_path/'keep.zip';out.write_bytes(b'keep')
    with pytest.raises(AuthoringError):return_workspace(w,out,expected_preview=p['preview_sha256'])
    assert out.read_bytes()==b'keep'


def test_v1_can_start_optional_story(tmp_path):
    base,_,_=write_desk(tmp_path);w=tmp_path/'new';open_workspace(base,w)
    assert read(w/'STORY.json') is None
    put(w/'STORY.json',sample());p=preview_workspace(w)
    out=tmp_path/'with-story.zip';return_workspace(w,out,expected_preview=p['preview_sha256'])
    assert verify_desk(out)['schema_id']=='manju.desk-session/v2'


def test_json_cli_and_help(workspace,tmp_path):
    w,_,_=workspace;r=CliRunner()
    for cmd in ['ide-open','ide-preview','ide-return']:
        assert r.invoke(app,[cmd,'--help']).exit_code==0
    p=r.invoke(app,['ide-preview',str(w)]);assert p.exit_code==0,p.output
    out=tmp_path/'cli.zip';a=r.invoke(app,['ide-return',str(w),'--output',str(out),'--expected-preview',json.loads(p.output)['preview_sha256']])
    assert a.exit_code==0,a.output
    err=r.invoke(app,['ide-preview',str(tmp_path/'missing')]);assert err.exit_code==2
    assert json.loads(err.output)['ok'] is False
