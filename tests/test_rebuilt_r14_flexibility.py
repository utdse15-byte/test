"""Section composition and templates must not become transferable approval."""
from copy import deepcopy
from hashlib import sha256
import itertools
import json
from pathlib import Path
import zipfile

import pytest
from playwright.sync_api import expect
from typer.testing import CliRunner

from manju.authoring.cli import app
from manju.authoring.core import canonical, digest
from manju.authoring.flexibility import (
    CreativeTemplate, TEMPLATE_FIELDS, apply_template, compose_archives,
    compose_studios, create_template, difference, read_verified,
)
from manju.authoring.studio import Studio, verify_studio
from tests.test_rebuilt_r13_studio import empty_document, rich_document, write_archive, download_studio
from tests.test_rebuilt_r5_workbench import browser, page


def donor_document():
    value = empty_document()
    value['workspace']['draft']['form'].update(prompt='  B方案：冷光\n 保持连贯 ', **{'shot-id':'镜头B'})
    value['repair']['form']['repair-change'] = '  改衣服，不动背景\n'
    value['director']['form']['director-change'] = ' 改为蓝色 '
    value['workspace']['catalog']['revision'] += '-custom'
    value['auxiliary_form']['return-width'] = '1584'
    return value


def sample_template():
    return {'schema_id':'manju.creative-template/v1', 'name':'冷光模板', 'notes':'保留原始运动',
            'fields':{'shot':{'prompt':'  模板提示词\n','preserve':' 原运动 '},
                      'director':{'director-change':' 蓝色衣服 '}},
            'quality_only_on_apply':True, 'contains_media':False,
            'contains_approvals':False, 'automatic_execution':False}


COMBINATIONS = [list(c) for n in range(1, 5) for c in itertools.combinations(['shot','repair','director','catalog'], n)]


@pytest.mark.parametrize('take', COMBINATIONS)
def test_all_section_combinations_preserve_unselected_inputs(take):
    a, b = empty_document(), donor_document()
    original = deepcopy((a,b))
    out = compose_studios(a,b,take).model_dump(mode='json')
    assert out['workspace']['draft']==(b if 'shot' in take else a)['workspace']['draft']
    assert out['workspace']['catalog']==(b if 'catalog' in take else a)['workspace']['catalog']
    assert out['auxiliary_form']==(b if 'shot' in take else a)['auxiliary_form']
    for area in ['repair','director']:
        assert out[area]==(b if area in take else a)[area]
    assert (a,b)==original
    assert not out['automatic_execution'] and not out['confirmations_restored']
    assert out['quality_only_on_restore']


@pytest.mark.parametrize('take',[[],['unknown'],['repair','repair'],['shot',1],'shot'])
def test_ambiguous_section_selection_refused(take):
    with pytest.raises(ValueError):compose_studios(empty_document(), donor_document(), take)


def test_selected_director_carries_real_closure_without_other_review():
    donor, media = rich_document()
    result = compose_studios(empty_document(), donor, ['director'])
    assert len(result.media)==3
    assert result.workspace.pending==[] and result.repair.source is None
    assert result.workspace.catalog.model_dump()==Studio.model_validate(empty_document()).workspace.catalog.model_dump()
    assert set(m.sha256 for m in result.media)==set(media)


def test_composing_removal_drops_unreferenced_media_not_source_file(tmp_path):
    doc,media=rich_document();original=write_archive(tmp_path/'original.zip',doc,media)
    donor=write_archive(tmp_path/'empty.zip')
    before=original.read_bytes()
    receipt=compose_archives(original,donor,['shot','repair','director'],tmp_path/'fresh.zip')
    assert receipt['media_files']==0 and original.read_bytes()==before
    assert verify_studio(tmp_path/'fresh.zip')['ok']


def test_conflicting_media_geometry_rejected():
    a,_=rich_document();b=deepcopy(a)
    b['repair']['source']['width']=900
    b['director'] = empty_document()['director']
    # The donor keeps only its repair source, so the conflict is cross-document.
    b['workspace'] = empty_document()['workspace']
    h=b['repair']['source']['sha256'];b['media']=[m for m in b['media'] if m['sha256']==h]
    Studio.model_validate(b)
    with pytest.raises(ValueError):compose_studios(a,b,['repair'])


def test_archive_composition_independent_old_reader_and_exclusive(tmp_path):
    a=write_archive(tmp_path/'A.zip');b,media=rich_document()
    source=write_archive(tmp_path/'B.zip',b,media);out=tmp_path/'组合.zip'
    original={p.name:sha256(p.read_bytes()).hexdigest() for p in (a,source)}
    result=compose_archives(a,source,['director'],out)
    assert result['sources_unchanged'] and result['media_files']==3
    restored,receipt=read_verified(out)
    assert restored.workspace.pending==[] and restored.director.source
    assert receipt['ok'] and not receipt['video_decode_verified']
    assert original=={p.name:sha256(p.read_bytes()).hexdigest() for p in (a,source)}
    content=out.read_bytes()
    with pytest.raises(FileExistsError):compose_archives(a,source,['repair'],out)
    assert out.read_bytes()==content
    with pytest.raises(FileExistsError):compose_archives(a,source,['repair'],a)


def test_corrupt_donor_never_publishes(tmp_path):
    target=write_archive(tmp_path/'target.zip');donor=tmp_path/'bad.zip';donor.write_bytes(b'bad');out=tmp_path/'not-created.zip'
    with pytest.raises(ValueError):compose_archives(target,donor,['repair'],out)
    assert not out.exists()


def test_output_cleanup_on_changed_input(tmp_path, monkeypatch):
    import manju.authoring.flexibility as flex
    a=write_archive(tmp_path/'a.zip');b=write_archive(tmp_path/'b.zip');out=tmp_path/'out.zip'
    original=flex.file_digest;count=0
    def altered(path):
        nonlocal count
        count+=1
        if count==3:return '0'*64  # First post-build input stability check.
        return original(path)
    monkeypatch.setattr(flex,'file_digest',altered)
    with pytest.raises(ValueError):compose_archives(a,b,['repair'],out)
    assert not out.exists()


def test_template_extraction_never_exports_identity_media_reviews_or_catalog():
    d,_=rich_document();t=create_template(d,'个人长期方法').model_dump(mode='json')
    for area, fields in t['fields'].items():assert set(fields)<=set(TEMPLATE_FIELDS[area])
    text=canonical(t).decode()
    for forbidden in ('shot-id','reviewer','sourceContext','draftHash','session_sha256','assets','catalog'):
        assert '"'+forbidden+'"' not in text
    assert not t['contains_approvals'] and not t['contains_media']


@pytest.mark.parametrize('mutation',[
    lambda t:t.update(automatic_execution=True), lambda t:t.update(contains_media=True),
    lambda t:t.update(quality_only_on_apply=False), lambda t:t.update(contains_approvals=0),
    lambda t:t.update(name=' '), lambda t:t.update(name='bad\nname'),
    lambda t:t.update(notes='x'*4001),lambda t:t.update(extra='x'),
    lambda t:t['fields'].update(catalog={'revision':'mine'}),
    lambda t:t['fields']['shot'].update(**{'shot-id':'stolen'}),
    lambda t:t['fields']['shot'].update(assets='anything'),
    lambda t:t['fields']['shot'].update(prompt={'script':'alert(1)'}),
    lambda t:t['fields']['shot'].update(prompt='x'*30001),
    lambda t:t.update(fields={}),lambda t:t['fields'].update(director={}),
    lambda t:t.update(quality_only_on_apply=1),
])
def test_templates_strict_whitelist(mutation):
    t=sample_template();mutation(t)
    with pytest.raises(ValueError):CreativeTemplate.model_validate(t)


def test_template_fill_empty_keeps_existing_text_and_exact_whitespace():
    d=empty_document();d['workspace']['draft']['form'].update(prompt='我的原文',preserve='  ')
    after=apply_template(d,sample_template()).model_dump(mode='json')
    assert after['workspace']['draft']['form']['prompt']=='我的原文'
    assert after['workspace']['draft']['form']['preserve']==' 原运动 '
    assert after['director']['form']['director-change']==' 蓝色衣服 '
    assert d['workspace']['draft']['form']['preserve']=='  '


def test_template_modified_final_returns_to_draft_but_keeps_historical_review():
    d,_=rich_document();d['workspace']['draft'].update(stage='final',draftHash='f'*64)
    before=deepcopy(d)
    after=apply_template(d,sample_template(),mode='replace').model_dump(mode='json')
    assert after['workspace']['draft']['stage']=='draft'
    assert after['workspace']['draft']['draftHash'] is None
    assert after['workspace']['draft']['form']['shot-id']==before['workspace']['draft']['form']['shot-id']
    assert after['workspace']['pending']==before['workspace']['pending']
    assert after['media']==before['media'] and after['workspace']['catalog']==before['workspace']['catalog']
    assert d==before


def test_director_only_template_does_not_downgrade_unmodified_shot():
    d=empty_document();d['workspace']['draft'].update(stage='final',draftHash='f'*64)
    t=sample_template();t['fields'].pop('shot')
    out=apply_template(d,t,mode='replace')
    assert out.workspace.draft.stage=='final' and out.workspace.draft.draftHash=='f'*64
    with pytest.raises(ValueError):apply_template(d,t,mode='auto')


def test_cli_composition_diff_template_roundtrip(tmp_path):
    runner=CliRunner();a=write_archive(tmp_path/'a.zip');b=write_archive(tmp_path/'b.zip',donor_document());out=tmp_path/'mix.zip'
    res=runner.invoke(app,['studio-compose',str(a),str(b),'--take','repair','--output',str(out)])
    assert res.exit_code==0,res.stdout
    assert json.loads(res.stdout)['sources_unchanged']
    res=runner.invoke(app,['studio-diff',str(a),str(out)]);assert res.exit_code==0,res.stdout
    assert json.loads(res.stdout)['changed_sections']==['repair']
    t=tmp_path/'template.json'
    res=runner.invoke(app,['template-create',str(b),'--name','方法集','--output',str(t)])
    assert res.exit_code==0,res.stdout
    res=runner.invoke(app,['template-verify',str(t)]);assert res.exit_code==0,res.stdout
    assert json.loads(res.stdout)['contains_approvals'] is False
    assert runner.invoke(app,['studio-compose',str(a),str(b),'--take','repair','--output',str(out)]).exit_code==2


@pytest.mark.parametrize('take',COMBINATIONS)
def test_browser_python_composition_and_diff_parity(page,take):
    a,b=empty_document(),donor_document()
    result=page.evaluate('([a,b,t])=>ManjuFlex.compose(a,b,t)',[a,b,take])
    py=compose_studios(a,b,take)
    assert result==py.model_dump(mode='json')
    assert page.evaluate('([a,b])=>ManjuFlex.difference(a,b)',[a,result])==difference(a,py)


@pytest.mark.parametrize('mode',['fill_empty','replace'])
def test_browser_python_template_parity(page,mode):
    d=empty_document();t=sample_template()
    assert page.evaluate('t=>ManjuFlex.normalizeTemplate(t)',t)==CreativeTemplate.model_validate(t).model_dump(mode='json')
    assert page.evaluate('([d,t,m])=>ManjuFlex.applyTemplate(d,t,m)',[d,t,mode])==apply_template(d,t,mode=mode).model_dump(mode='json')
    assert page.evaluate('d=>ManjuFlex.makeTemplate(d,"个人方法")',d)==create_template(d,'个人方法').model_dump(mode='json')


def test_real_template_download_does_not_backup_or_apply(page,tmp_path):
    page.locator('#prompt').fill('  用户原文\n')
    page.locator('#flex-template-box > summary').click()
    page.locator('#flex-template-name').fill('我的运镜要求')
    before=page.evaluate('ManjuStudio.view()')
    with page.expect_download() as d:page.locator('#flex-export-template').click()
    out=tmp_path/'mine.json';d.value.save_as(out)
    t=CreativeTemplate.model_validate_json(out.read_bytes())
    assert t.fields['shot']['prompt']=='  用户原文\n'
    assert page.evaluate('ManjuStudio.view()')==before
    assert page.evaluate('ManjuStudio.needsBackup()')


def test_selective_preview_apply_undo_and_protect_unselected(page,tmp_path):
    archive=write_archive(tmp_path/'donor.zip',donor_document())
    page.locator('#prompt').fill('我的新镜头，不要覆盖')
    page.locator('#director-change').fill('我的导演原文')
    before=page.evaluate('ManjuStudio.view()')
    page.locator('#flex-donor-file').set_input_files(archive)
    expect(page.locator('#flex-preview-compose')).to_be_enabled()
    page.locator('#flex-take-repair').check();page.locator('#flex-preview-compose').click()
    expect(page.locator('#flex-preview')).to_be_visible()
    assert page.evaluate('ManjuStudio.view()')==before
    page.locator('#flex-confirmed').check();page.locator('#flex-apply').click()
    expect(page.locator('#flex-status')).to_contain_text('变更完成')
    after=page.evaluate('ManjuStudio.view()')
    assert after['workspace']==before['workspace']
    assert after['director']==before['director']
    assert after['repair']['form']['repair-change']==donor_document()['repair']['form']['repair-change']
    assert page.locator('#quality-only').is_checked()
    assert not page.locator('#human-confirmed').is_checked()
    assert page.evaluate('ManjuStudio.needsBackup()')
    page.locator('#flex-undo-confirmed').check();page.locator('#flex-undo').click()
    expect(page.locator('#flex-status')).to_contain_text('已撤回')
    assert page.evaluate('ManjuStudio.view()')==before


@pytest.mark.parametrize('edit',['prompt','director-change','selection'])
def test_selective_stale_preview_is_not_applicable(page,tmp_path,edit):
    archive=write_archive(tmp_path/'donor.zip',donor_document())
    page.locator('#flex-donor-file').set_input_files(archive)
    expect(page.locator('#flex-preview-compose')).to_be_enabled()
    page.locator('#flex-take-repair').check();page.locator('#flex-preview-compose').click()
    expect(page.locator('#flex-preview')).to_be_visible()
    if edit=='selection':page.locator('#flex-take-director').check()
    else:page.locator('#'+edit).fill('不要覆盖新内容')
    expect(page.locator('#flex-apply')).to_be_disabled()
    assert page.evaluate('ManjuFlex.state.pending') is None


def test_undo_refuses_to_overwrite_new_edits(page,tmp_path):
    archive=write_archive(tmp_path/'donor.zip',donor_document())
    page.locator('#flex-donor-file').set_input_files(archive)
    expect(page.locator('#flex-preview-compose')).to_be_enabled()
    page.locator('#flex-take-repair').check();page.locator('#flex-preview-compose').click()
    expect(page.locator('#flex-preview')).to_be_visible()
    page.locator('#flex-confirmed').check();page.locator('#flex-apply').click()
    expect(page.locator('#flex-status')).to_contain_text('变更完成')
    page.locator('#prompt').fill('应用后继续写的新文字')
    expect(page.locator('#flex-undo-confirmed')).to_be_disabled()
    expect(page.locator('#flex-undo')).to_be_disabled()


def test_template_preview_is_explicit_and_defaults_non_destructive(page,tmp_path):
    path=tmp_path/'template.json';path.write_bytes(canonical(sample_template()))
    page.locator('#prompt').fill('我的镜头原文')
    page.locator('#flex-template-box > summary').click()
    page.locator('#flex-template-file').set_input_files(path)
    expect(page.locator('#flex-preview-template')).to_be_enabled()
    assert page.locator('#flex-template-mode').input_value()=='fill_empty'
    page.locator('#flex-preview-template').click()
    expect(page.locator('#flex-preview')).to_be_visible()
    assert page.locator('#prompt').input_value()=='我的镜头原文'
    page.locator('#flex-confirmed').check();page.locator('#flex-apply').click()
    expect(page.locator('#flex-status')).to_contain_text('变更完成')
    assert page.locator('#prompt').input_value()=='我的镜头原文'
    assert page.locator('#director-change').input_value()==' 蓝色衣服 '


def test_template_text_not_executed_and_unknown_keys_rejected(page,tmp_path):
    t=sample_template();t['name']='<img src=x onerror="window.injected=1">';t['fields']['shot']['prompt']='</script><script>window.injected=1</script>'
    file=tmp_path/'text.json';file.write_bytes(canonical(t))
    page.locator('#flex-template-box > summary').click();page.locator('#flex-template-file').set_input_files(file)
    expect(page.locator('#flex-preview-template')).to_be_enabled()
    assert page.evaluate('window.injected') is None
    previous = json.loads(json.dumps(t))
    t['fields']['shot']['assets']=[];file.write_bytes(canonical(t))
    page.locator('#flex-template-file').set_input_files(file)
    expect(page.locator('#flex-template-status')).to_contain_text('未通过')
    # R16: malformed replacement cannot evict the last valid template.
    expect(page.locator('#flex-preview-template')).to_be_enabled()
    assert page.evaluate('ManjuFlex.state.template') == previous
    assert page.evaluate('ManjuFlex.state.pending') is None
    assert not page.locator('#flex-confirmed').is_checked()
    assert page.evaluate('window.injected') is None


def test_named_branch_is_old_studio_format_and_not_false_saved(page,tmp_path):
    page.locator('#prompt').fill('B方案')
    page.locator('#flex-branch-name').fill('B方案/冷光')
    with page.expect_download() as dl:page.locator('#flex-save-branch').click()
    assert dl.value.suggested_filename.startswith('MANJU_STUDIO_B方案_冷光_')
    out=tmp_path/'branch.zip';dl.value.save_as(out)
    assert verify_studio(out)['schema_id']=='manju.studio-session/v1'
    assert page.evaluate('ManjuStudio.needsBackup()')
    page.locator('#verify-studio-download').set_input_files(out)
    expect(page.locator('#studio-save-status')).to_contain_text('与当前三个工作区一致')
    assert not page.evaluate('ManjuStudio.needsBackup()')


def test_small_viewport_and_no_model_quality_change(page):
    page.set_viewport_size({'width':390,'height':844})
    page.locator('#flex-template-box > summary').click()
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    assert page.locator('#quality-only').is_checked()
    assert page.evaluate('ManjuWorkbench.catalog().profiles.length')==12


def test_text_template_can_apply_and_undo_before_media_rebinding(page,tmp_path):
    page.evaluate("""()=>{ManjuWorkbench.state.assets=[{id:'A1',role:'first_frame',path:'lost.png',sha256:'a'.repeat(64),bytes:4,duration_ms:null,origin_model:null}];ManjuWorkbench.state.stage='final';ManjuWorkbench.state.draftHash='f'.repeat(64);ManjuWorkbench.invalidate();}""")
    before=page.evaluate('ManjuStudio.view()')
    template=tmp_path/'method.json';template.write_bytes(canonical(sample_template()))
    page.locator('#flex-template-box > summary').click();page.locator('#flex-template-file').set_input_files(template)
    expect(page.locator('#flex-preview-template')).to_be_enabled()
    page.locator('#flex-template-mode').select_option('replace')
    page.locator('#flex-preview-template').click();expect(page.locator('#flex-preview')).to_be_visible()
    expect(page.locator('#flex-summary')).to_contain_text('不重读或要求绑定媒体')
    page.locator('#flex-confirmed').check();page.locator('#flex-apply').click()
    expect(page.locator('#flex-status')).to_contain_text('变更完成')
    after=page.evaluate('ManjuStudio.view()')
    assert after['workspace']['draft']['assets']==before['workspace']['draft']['assets']
    assert after['workspace']['draft']['stage']=='draft'
    assert page.evaluate('ManjuWorkbench.state.files.size')==0
    assert page.evaluate('ManjuStudio.needsBackup()')
    page.locator('#flex-undo-confirmed').check();page.locator('#flex-undo').click()
    expect(page.locator('#flex-status')).to_contain_text('已撤回')
    assert page.evaluate('ManjuStudio.view()')==before
    # Text-only flexibility must not fabricate a full backup of missing bytes.
    page.locator('#export-studio').click()
    expect(page.locator('#studio-save-status')).to_contain_text('缺少原文件')


def test_template_mode_switch_cancels_old_preview(page,tmp_path):
    t=tmp_path/'method.json';t.write_bytes(canonical(sample_template()))
    page.locator('#flex-template-box > summary').click();page.locator('#flex-template-file').set_input_files(t)
    expect(page.locator('#flex-preview-template')).to_be_enabled()
    page.locator('#flex-preview-template').click();expect(page.locator('#flex-preview')).to_be_visible()
    page.locator('#flex-template-mode').select_option('replace')
    expect(page.locator('#flex-apply')).to_be_disabled()
    assert page.evaluate('ManjuFlex.state.pending') is None


def test_async_source_read_rejects_stale_view(page,tmp_path):
    file=write_archive(tmp_path/'donor.zip',donor_document())
    page.evaluate('''()=>{const read=Blob.prototype.arrayBuffer;let once=true;
      Blob.prototype.arrayBuffer=function(){if(once){once=false;return new Promise(resolve=>{window.finishFlexRead=()=>read.call(this).then(resolve);});}return read.call(this);};}''')
    page.locator('#flex-donor-file').set_input_files(file)
    page.wait_for_function('typeof window.finishFlexRead==="function"')
    page.locator('#prompt').fill('读取时继续写的新内容')
    page.evaluate('window.finishFlexRead()')
    expect(page.locator('#flex-status')).to_contain_text('当前工作变化')
    assert page.locator('#prompt').input_value()=='读取时继续写的新内容'
    assert page.evaluate('ManjuFlex.state.donor') is None
    assert not page.evaluate('ManjuFlex.state.busy')


def test_no_restore_or_export_can_race_a_source_read(page,tmp_path):
    file=write_archive(tmp_path/'donor.zip',donor_document())
    page.evaluate('''()=>{const read=Blob.prototype.arrayBuffer;let once=true;
      Blob.prototype.arrayBuffer=function(){if(once){once=false;return new Promise(resolve=>{window.finishFlexRead=()=>read.call(this).then(resolve);});}return read.call(this);};}''')
    page.locator('#flex-donor-file').set_input_files(file)
    page.wait_for_function('typeof window.finishFlexRead==="function"')
    page.locator('#export-studio').click()
    expect(page.locator('#studio-save-status')).to_contain_text('其他文件操作仍在进行')
    page.evaluate('window.finishFlexRead()')
    expect(page.locator('#flex-preview-compose')).to_be_enabled()
    assert not page.evaluate('ManjuFlex.state.busy')


@pytest.mark.parametrize('raw',[b'{"schema_id":"x","schema_id":"y"}',b'['*70+b'0'+b']'*70,b'{} trailing'])
def test_template_import_strict_json_not_code(page,tmp_path,raw):
    file=tmp_path/'bad.json';file.write_bytes(raw)
    page.locator('#flex-template-box > summary').click();page.locator('#flex-template-file').set_input_files(file)
    expect(page.locator('#flex-template-status')).to_contain_text('未通过')
    assert page.evaluate('ManjuFlex.state.template') is None
    assert page.locator('#flex-preview-template').is_disabled()


def test_composition_enforces_combined_media_limit():
    a,b=empty_document(),empty_document();size=128*1024*1024
    for i in range(3):
        h=str(i+1)*64
        a['workspace']['draft']['assets'].append(dict(id='A'+str(i),role='source_video',path=f'{i}.mp4',sha256=h,bytes=size,duration_ms=1000,origin_model=None))
        record=dict(sha256=h,bytes=size,filename=f'{i}.mp4',mime_type='video/mp4')
        a['workspace']['media'].append(record);a['media'].append(record)
    for i,area in enumerate(['repair','director'],4):
        h=str(i)*64;b[area]['source']=dict(sha256=h,bytes=size,filename=f'{i}.mp4',duration_ms=1000,width=640,height=360,media_check='browser_metadata')
        b['media'].append(dict(sha256=h,bytes=size,filename=f'{i}.mp4',mime_type='video/mp4'))
    Studio.model_validate(a);Studio.model_validate(b)
    with pytest.raises(ValueError,match='512 MiB'):compose_studios(a,b,['repair','director'])


def test_input_replacement_cannot_expand_unbounded_zip_during_compose(tmp_path,monkeypatch):
    import manju.authoring.flexibility as flex
    target=write_archive(tmp_path/'target.zip');d,media=rich_document();donor=write_archive(tmp_path/'donor.zip',d,media);out=tmp_path/'out.zip'
    real=flex.read_verified
    def replace_after_read(path):
        value=real(path)
        if path==donor:
            with zipfile.ZipFile(donor) as z:entries={n:z.read(n) for n in z.namelist()}
            media_name=next(n for n in entries if n.startswith('media/'))
            entries[media_name]=b'changed source'*100000
            with zipfile.ZipFile(donor,'w') as z:
                for n,b in entries.items():z.writestr(n,b)
        return value
    monkeypatch.setattr(flex,'read_verified',replace_after_read)
    with pytest.raises(ValueError,match='source member changed'):compose_archives(target,donor,['director'],out)
    assert not out.exists()
    assert not list(tmp_path.glob('.manju-compose-*'))


def test_media_binding_order_matches_existing_studio_export_and_aliases():
    donor,_=rich_document();target=empty_document()
    donor['media'].reverse()
    for m in donor['media']:m['filename']='generic.bin'
    out=compose_studios(target,donor,['director'])
    from manju.authoring.studio import studio_media
    assert [m.sha256 for m in out.media]==list(studio_media(out))
    assert next(m for m in out.media if m.sha256==out.director.source.sha256).filename==out.director.source.filename
    for anchor in out.director.anchors:
        for raster in (anchor.frame,anchor.guide):
            if raster:
                m=next(m for m in out.media if m.sha256==raster.sha256)
                assert m.filename==raster.filename and m.mime_type=='image/png'


@pytest.mark.parametrize('field,text',[('duration','not-a-number'),('prompt','first\r\nsecond'),('preserve','first\rsecond')])
def test_template_preview_refuses_browser_silent_text_sanitization(page,tmp_path,field,text):
    before=page.evaluate('ManjuStudio.view()')
    t=sample_template();t['fields']={'shot':{field:text}}
    path=tmp_path/'not-representable.json';path.write_bytes(canonical(t))
    page.locator('#flex-template-box > summary').click()
    page.locator('#flex-template-file').set_input_files(path)
    expect(page.locator('#flex-preview-template')).to_be_enabled()
    page.locator('#flex-template-mode').select_option('replace')
    page.locator('#flex-preview-template').click()
    expect(page.locator('#flex-status')).to_contain_text('无法原样写入浏览器')
    assert page.evaluate('ManjuStudio.view()')==before
    assert page.evaluate('ManjuFlex.state.pending') is None
    assert page.locator('#flex-apply').is_disabled()


def test_template_preview_exactly_matches_applied_fraction_and_newlines(page,tmp_path):
    t=sample_template();t['fields']={'shot':{'duration':'2.50','prompt':'  first\nsecond  '}}
    path=tmp_path/'exact.json';path.write_bytes(canonical(t))
    page.locator('#flex-template-box > summary').click()
    page.locator('#flex-template-file').set_input_files(path)
    expect(page.locator('#flex-preview-template')).to_be_enabled()
    page.locator('#flex-template-mode').select_option('replace')
    page.locator('#flex-preview-template').click()
    expect(page.locator('#flex-preview')).to_be_visible()
    preview=page.evaluate('ManjuFlex.state.pending.after.document')
    page.locator('#flex-confirmed').check();page.locator('#flex-apply').click()
    expect(page.locator('#flex-status')).to_contain_text('变更完成')
    assert page.evaluate('ManjuStudio.view()')==preview
    assert page.locator('#duration').input_value()=='2.50'
