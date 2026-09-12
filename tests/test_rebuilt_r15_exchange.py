"""External data roundtrip, three-way conflicts and browser/CLI parity."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import zipfile

import pytest
from playwright.sync_api import expect
from typer.testing import CliRunner

from manju.authoring.core import AuthoringError, canonical, digest
from manju.authoring.cli import app
from manju.authoring.exchange import (ExternalEdit, create_edit, preview_edit, apply_edit,
    text_members, overlay_texts, media_index, export_kit, apply_archive, read_edit, field_state)
from manju.authoring.studio import Studio, verify_studio
from tests.test_rebuilt_r13_studio import empty_document, rich_document, write_archive, download_studio
from tests.test_rebuilt_r5_workbench import browser, page


def edited(doc=None, **changes):
    doc = empty_document() if doc is None else doc
    edit = create_edit(doc).model_dump(mode='json')
    edit['values'].update(changes or {'shot/prompt':'  外部新文字\n保留空白 '})
    return edit


def statuses(doc, edit):
    return {r['key']:r['status'] for r in preview_edit(doc, edit)['rows']}


@pytest.mark.parametrize('sections', [['shot'],['review'],['repair'],['director'],['settings'],None])
def test_export_areas_exact_and_not_an_approval(sections):
    doc, _ = rich_document(); old=deepcopy(doc)
    edit = create_edit(doc, sections)
    assert all(k.split('/')[0] in edit.sections for k in edit.base)
    assert edit.base == edit.values and not edit.transfers_approval and not edit.automatic_execution
    assert doc == old
    if sections==['director']:assert 'director/anchors/A01/target' in edit.base


@pytest.mark.parametrize('mutate',[
    lambda e:e.update(automatic_execution=True),lambda e:e.update(transfers_approval=0),
    lambda e:e.update(extra='ignored'),lambda e:e.update(base_sha256='0'*64),
    lambda e:e['values'].update(**{'shot/approved':'yes'}),
    lambda e:e['values'].pop('shot/prompt'),lambda e:e['base'].update(**{'shot/prompt':'changed baseline'}),
    lambda e:e['values'].update(**{'shot/prompt':{'code':'evil'}}),
    lambda e:e['values'].update(**{'shot/prompt':'x'*30001}),
    lambda e:e['values'].update(**{'review/notes':'x'*6001}),
    lambda e:e['values'].update(**{'shot/prompt':'\ud800'}),
    lambda e:e['contexts'].update(**{'shot/prompt':'bad'}),
    lambda e:e.update(sections=['director','shot']),lambda e:e.update(sections=['shot','shot']),
])
def test_invalid_input_never_coerced_or_ignored(mutate):
    e=edited();mutate(e)
    with pytest.raises((ValueError,UnicodeError)):ExternalEdit.model_validate(e)


def test_mutated_model_instance_is_revalidated():
    e=create_edit(empty_document());e.values['shot/prompt']=[]
    with pytest.raises(ValueError):preview_edit(empty_document(),e)


def test_three_way_keeps_new_local_text_and_reports_conflict():
    doc=empty_document();edit=edited(doc,**{'shot/prompt':'外部改','shot/change':'外部增加'})
    doc['workspace']['draft']['form']['prompt']='本地新稿'
    doc['workspace']['draft']['form']['preserve']='本地新增，不应清空'
    s=statuses(doc,edit);assert s['shot/prompt']=='conflict' and s['shot/change']=='ready'
    assert s['shot/preserve']=='unchanged'
    after,report=apply_edit(doc,edit,['shot/change'])
    assert after['workspace']['draft']['form']['prompt']=='本地新稿'
    assert after['workspace']['draft']['form']['preserve']=='本地新增，不应清空'
    assert report['not_applied']==['shot/prompt']
    forced,_=apply_edit(doc,edit,['shot/prompt'])
    assert forced['workspace']['draft']['form']['prompt']=='外部改'


def test_same_edit_reapplied_is_noop_not_conflict():
    doc=empty_document();e=edited(doc);fresh,_=apply_edit(doc,e,['shot/prompt'])
    assert statuses(fresh,e)['shot/prompt']=='already_applied'


@pytest.mark.parametrize('area,change,key',[
    ('shot',lambda d:d['workspace']['draft']['form'].update({'shot-id':'other'}),'shot/prompt'),
    ('repair',lambda d:d['repair']['source'].update(sha256='f'*64),'repair/repair-change'),
    ('director',lambda d:d['director']['source'].update(sha256='f'*64),'director/director-change'),
    ('anchor',lambda d:d['director']['anchors'][0].update(source_time_ms=2),'director/anchors/A01/target'),
    ('anchor',lambda d:d['director']['anchors'][0]['guide'].update(sha256='f'*64),'director/anchors/A01/target'),
    ('review',lambda d:d['workspace']['pending'].clear(),'review/notes'),
])
def test_changed_media_or_identity_blocks_wrong_context(area,change,key):
    doc,_=rich_document();e=edited(doc,**{key:'new'});change(doc)
    assert statuses(doc,e)[key]=='context_changed'
    with pytest.raises(ValueError):apply_edit(doc,e,[key])


def test_removed_anchor_cannot_return_target_text():
    doc,_=rich_document();e=edited(doc,**{'director/anchors/A01/target':'new'})
    doc['director']['anchors'].clear()
    assert statuses(doc,e)['director/anchors/A01/target']=='context_changed'


def test_shot_change_demotes_final_but_director_change_does_not():
    d,_=rich_document();d['workspace']['draft'].update(stage='final',draftHash=d['director']['source']['sha256'])
    e=edited(d,**{'shot/prompt':'new','director/director-change':'blue'})
    a,_=apply_edit(d,e,['director/director-change']);assert a['workspace']['draft']['stage']=='final'
    a,_=apply_edit(d,e,['shot/prompt']);assert a['workspace']['draft']['stage']=='draft' and a['workspace']['draft']['draftHash'] is None
    assert a['workspace']['review']==d['workspace']['review'] and a['media']==d['media']


@pytest.mark.parametrize('key,value',[
 ('shot/duration','banana'),('shot/duration','NaN'),('shot/duration','1e999'),
 ('repair/repair-start',' 2'),('settings/return-width','+8'),('shot/shot-id','hello\nworld'),
 ('director/director-change','new\r\nwords')])
def test_unrepresentable_edits_never_silently_cleaned(key,value):
    d=empty_document();e=edited(d,**{key:value})
    with pytest.raises(ValueError):apply_edit(d,e,[key])


def test_blank_is_explicit_clear_not_missing():
    d=empty_document();d['workspace']['draft']['form']['preserve']='something'
    e=edited(d,**{'shot/preserve':''});a,_=apply_edit(d,e,['shot/preserve'])
    assert a['workspace']['draft']['form']['preserve']==''


def test_txt_overlay_bom_crlf_and_empty_roundtrip():
    d=empty_document();e=create_edit(d);names={v:k for k,v in text_members(e).items()}
    out=overlay_texts(e,{names['shot/prompt']:b'\xef\xbb\xbf  line1\r\nline2\r',names['review/notes']:b''})
    assert out.values['shot/prompt']=='  line1\nline2\n' and out.values['review/notes']==''
    assert e.base==e.values


@pytest.mark.parametrize('files',[{'wrong.txt':b'x'},{'text-001.txt':b'\xff'}, {}, {'text-001.txt':b'x'*120004}])
def test_bad_text_selection_atomic(files):
    e=create_edit(empty_document());old=e.model_dump()
    with pytest.raises(ValueError):overlay_texts(e,files)
    assert e.model_dump()==old


def test_read_bom_duplicate_keys_deep_json_and_bound(tmp_path):
    p=tmp_path/'e.json';e=create_edit(empty_document())
    p.write_bytes(b'\xef\xbb\xbf'+canonical(e));assert read_edit(p)==e
    p.write_text('{"values":{},"values":{}}',encoding='utf8')
    with pytest.raises(ValueError):read_edit(p)
    p.write_bytes(b'['*4000+b'0'+b']'*4000)
    with pytest.raises(ValueError):read_edit(p)
    p.write_bytes(b' '* (2*1024*1024+1))
    with pytest.raises(ValueError):read_edit(p)


def test_media_map_dedup_names_roles_and_no_quality_changes():
    d,files=rich_document();m=media_index(d,['review','repair','director'],True)
    assert len(m['files'])==3 and {x['sha256'] for x in m['files']}==set(files)
    assert any(len(x['uses'])>=2 for x in m['files'])
    assert all(Path(x['path']).suffix in ('.png','.mp4') for x in m['files'])
    assert not media_index(d,['settings'],True)['files']


def test_real_archive_export_apply_and_old_reader(tmp_path):
    d,media=rich_document();src=write_archive(tmp_path/'source.zip',d,media);before=src.read_bytes()
    kit=tmp_path/'kit.zip';receipt=export_kit(src,kit,['repair','director'])
    assert receipt['media_files']==3
    with zipfile.ZipFile(kit) as z:
        m=json.loads(z.read('MEDIA_MAP.json'));e=json.loads(z.read('EDIT.json'))
        assert all(z.read(x['path'])==media[x['sha256']] for x in m['files'])
        assert len([n for n in z.namelist() if n.startswith('texts/')])==len(e['base'])
    e['values']['director/anchors/A01/target']='  externally edited\n'
    out=tmp_path/'fresh.zip';p=preview_edit(d,e)
    res=apply_archive(src,e,['director/anchors/A01/target'],out,expected_preview=digest(p))
    assert res['ok'] and verify_studio(out)['ok'] and src.read_bytes()==before
    with zipfile.ZipFile(out) as z:
        new=json.loads(z.read('STUDIO.json'))
        assert new['director']['anchors'][0]['target']=='  externally edited\n'
        assert all(z.read('media/'+h)==data for h,data in media.items())
    with pytest.raises(FileExistsError):export_kit(src,kit)
    with pytest.raises(FileExistsError):apply_archive(src,e,['director/anchors/A01/target'],out)


def test_stale_preview_no_output_and_no_source_changes(tmp_path):
    src=write_archive(tmp_path/'source.zip');e=edited();out=tmp_path/'absent.zip'
    with pytest.raises(ValueError):apply_archive(src,e,['shot/prompt'],out,expected_preview='0'*64)
    assert not out.exists()


def test_cli_end_to_end_explicit_preview_new_output(tmp_path):
    runner=CliRunner();src=write_archive(tmp_path/'source.zip');p=tmp_path/'EDIT.json'
    r=runner.invoke(app,['external-create',str(src),'--output',str(p)]);assert r.exit_code==0,r.output
    e=json.loads(p.read_text(encoding='utf8'));e['values']['shot/prompt']='外部新稿';p.write_text(json.dumps(e,ensure_ascii=False),encoding='utf8')
    r=runner.invoke(app,['external-preview',str(src),str(p)]);assert r.exit_code==0,r.output
    token=json.loads(r.output)['preview_sha256'];out=tmp_path/'new.zip'
    args=['external-apply',str(src),str(p),'--take','shot/prompt','--preview-sha256',token,'--output',str(out)]
    assert runner.invoke(app,args).exit_code==2 and not out.exists()
    r=runner.invoke(app,args+['--confirm']);assert r.exit_code==0,r.output
    assert verify_studio(out)['ok']
    assert runner.invoke(app,args+['--confirm']).exit_code==2


@pytest.mark.parametrize('sections',[['shot'],['review'],['repair'],['director'],['settings'],None])
def test_python_browser_export_parity(page,sections):
    d,_=rich_document();e=create_edit(d,sections).model_dump(mode='json')
    actual=page.evaluate('async ([doc,sections])=>ManjuExchange.make(doc,sections||undefined)',[d,sections])
    assert actual==e


def test_python_browser_preview_apply_parity(page):
    d,_=rich_document();e=edited(d,**{'shot/prompt':'external','director/anchors/A01/target':'new target'})
    d['workspace']['draft']['form']['prompt']='local'
    p=page.evaluate('async ([d,e])=>ManjuExchange.preview(d,e)',[d,e]);assert p==preview_edit(d,e)
    a,r=apply_edit(d,e,['director/anchors/A01/target'])
    actual=page.evaluate('async ([d,e])=>ManjuExchange.apply(d,e,["director/anchors/A01/target"])',[d,e])
    assert actual=={'document':a,'report':r}


def export_ui_edit(page,tmp_path):
    page.locator('#shot-id').fill('外部工作镜头');page.locator('#prompt').fill('导出原文')
    path=tmp_path/'EDIT.json'
    with page.expect_download() as got:page.locator('#exchange-export-json').click()
    got.value.save_as(path);return path,json.loads(path.read_text(encoding='utf8'))


def load_ui_edit(page,path,e):
    path.write_text(json.dumps(e,ensure_ascii=False),encoding='utf8');page.locator('#exchange-import-json').set_input_files(path)
    expect(page.locator('#exchange-preview')).to_be_enabled();page.locator('#exchange-preview').click()
    expect(page.locator('#exchange-preview-box')).to_be_visible()


def test_ui_conflict_default_kept_and_explicit_partial_apply_undo(page,tmp_path):
    p,e=export_ui_edit(page,tmp_path);e['values']['shot/prompt']='外部新稿';e['values']['repair/repair-change']='外部改衣服'
    page.locator('#prompt').fill('本地新稿');load_ui_edit(page,p,e)
    expect(page.get_by_label('带回 shot/prompt',exact=True)).not_to_be_checked()
    expect(page.get_by_label('带回 repair/repair-change',exact=True)).to_be_checked()
    page.locator('#exchange-confirmed').check();page.locator('#exchange-apply').click()
    expect(page.locator('#repair-change')).to_have_value('外部改衣服');expect(page.locator('#prompt')).to_have_value('本地新稿')
    expect(page.locator('#quality-only')).to_be_checked();expect(page.locator('#exchange-report')).to_be_visible()
    page.locator('#exchange-undo-confirmed').check();page.locator('#exchange-undo').click()
    expect(page.locator('#repair-change')).to_have_value('');expect(page.locator('#prompt')).to_have_value('本地新稿')


def test_ui_stale_preview_invalidated_by_other_area_edit(page,tmp_path):
    p,e=export_ui_edit(page,tmp_path);e['values']['shot/prompt']='external';load_ui_edit(page,p,e)
    page.locator('#director-change').fill('new local director')
    expect(page.locator('#exchange-preview-box')).to_be_hidden();expect(page.locator('#exchange-apply')).to_be_disabled()
    expect(page.locator('#prompt')).to_have_value('导出原文')


def test_ui_plain_txt_return_and_invalid_number_atomic(page,tmp_path):
    p,e=export_ui_edit(page,tmp_path);e['values']['shot/duration']='not-a-number';e['values']['shot/prompt']='must not land'
    load_ui_edit(page,p,e);page.locator('#exchange-confirmed').check();page.locator('#exchange-apply').click()
    expect(page.locator('#exchange-status')).to_contain_text('不能原样');expect(page.locator('#prompt')).to_have_value('导出原文')
    e['values']['shot/duration']=e['base']['shot/duration'];e['values']['shot/prompt']=e['base']['shot/prompt'];load_ui_edit(page,p,e)
    name={v:k for k,v in text_members(e).items()}['shot/prompt'];txt=tmp_path/name;txt.write_bytes(b'\xef\xbb\xbf new\r\nline\r')
    page.locator('#exchange-import-texts').set_input_files(txt);expect(page.locator('#exchange-import-status')).to_contain_text('CRLF')
    page.locator('#exchange-preview').click();page.locator('#exchange-confirmed').check();page.locator('#exchange-apply').click()
    expect(page.locator('#prompt')).to_have_value(' new\nline\n')


def test_ui_missing_media_still_exports_text_not_fake_kit(page,tmp_path):
    page.evaluate('()=>{ManjuWorkbench.state.assets.push({id:"A1",role:"first_frame",path:"missing.png",sha256:"a".repeat(64),bytes:4,duration_ms:null,origin_model:null});}')
    p,e=export_ui_edit(page,tmp_path);assert e['base']['shot/prompt']=='导出原文'
    page.locator('#exchange-export-kit').click();expect(page.locator('#exchange-status')).to_contain_text('缺少实际文件')


def test_ui_untrusted_text_does_not_run_html(page,tmp_path):
    p,e=export_ui_edit(page,tmp_path);evil='<img src=x onerror="window.XSS=true"><script>window.XSS=true</script>'
    e['values']['shot/prompt']=evil;load_ui_edit(page,p,e)
    assert page.evaluate('window.XSS===undefined')
    assert page.locator('#exchange-rows img').count()==0


def test_browser_media_index_parity(page):
    d,_=rich_document();sections=['review','repair','director']
    actual=page.evaluate('([d,s])=>ManjuExchange.mediaIndex(d,s,true)',[d,sections]);assert actual==media_index(d,sections,True)


def test_ui_narrow_preview_has_no_horizontal_overflow(page,tmp_path):
    page.set_viewport_size({'width':390,'height':844});p,e=export_ui_edit(page,tmp_path)
    e['values']['shot/prompt']='非常长但需要完整保存的要求'*150;load_ui_edit(page,p,e)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')


def test_txt_names_bind_export_baseline_no_cross_kit_mix():
    a=empty_document();b=empty_document();b['workspace']['draft']['form']['shot-id']='different shot'
    ea=create_edit(a);eb=create_edit(b)
    filename=next(iter(text_members(ea)))
    assert filename not in text_members(eb)
    with pytest.raises(ValueError):overlay_texts(eb,{filename:b'wrong shot'})


def test_all_31_export_scope_combinations_have_closed_fields_and_media():
    import itertools
    from manju.authoring.exchange import SECTIONS,media_uses
    doc,files=rich_document()
    for length in range(1,6):
        for selected in itertools.combinations(SECTIONS,length):
            e=create_edit(doc,list(selected));m=media_index(doc,list(selected),True)
            assert all(k.split('/')[0] in selected for k in e.base)
            assert {r['sha256'] for r in m['files']}=={r['sha256'] for r in media_uses(doc,list(selected))}
            assert all(r['sha256'] in files for r in m['files'])


def test_active_unknown_extension_exported_as_bin_not_executable():
    d,files=rich_document();d['workspace']['pending'][0]['filename']='untrusted.html'
    index=media_index(d,['review'],True)
    assert index['files'][0]['path'].endswith('.bin')
    assert index['files'][0]['original_names']==['untrusted.html']


def test_kit_without_media_is_honest_and_source_not_overwritten(tmp_path):
    d,files=rich_document();src=write_archive(tmp_path/'src.zip',d,files);out=tmp_path/'text-only.zip'
    report=export_kit(src,out,include_media=False);assert report['media_files']==0
    with zipfile.ZipFile(out) as z:
        index=json.loads(z.read('MEDIA_MAP.json'));assert index['files'] and not any(r['included'] for r in index['files'])
        assert not any(n.startswith('media/') for n in z.namelist())
    before=src.read_bytes()
    with pytest.raises(FileExistsError):export_kit(src,src)
    assert src.read_bytes()==before


def test_source_race_during_kit_and_apply_never_publishes(tmp_path,monkeypatch):
    import manju.authoring.exchange as m
    d,files=rich_document();src=write_archive(tmp_path/'src.zip',d,files);e=edited(d)
    original=m.file_digest
    def moved(path):
        return 'f'*64 if path==src else original(path)
    monkeypatch.setattr(m,'file_digest',moved)
    for operation,out in [(lambda out:m.export_kit(src,out),tmp_path/'kit.zip'),
                           (lambda out:m.apply_archive(src,e,['shot/prompt'],out),tmp_path/'new.zip')]:
        with pytest.raises(ValueError):operation(out)
        assert not out.exists()


def test_cli_text_overlay_origin_then_kit(tmp_path):
    src=write_archive(tmp_path/'src.zip');e=create_edit(empty_document());p=tmp_path/'EDIT.json';p.write_bytes(canonical(e))
    name={v:k for k,v in text_members(e).items()}['shot/prompt'];text=tmp_path/name;text.write_text('新的外部文字',encoding='utf8')
    runner=CliRunner();out=tmp_path/'returned.json';r=runner.invoke(app,['external-texts',str(p),'--text',str(text),'--output',str(out)])
    assert r.exit_code==0,r.output
    assert read_edit(out).values['shot/prompt']=='新的外部文字'
    r=runner.invoke(app,['external-kit',str(src),'--section','shot','--output',str(tmp_path/'kit.zip')])
    assert r.exit_code==0,r.output


def test_cli_preview_token_covers_external_changes(tmp_path):
    d=empty_document();src=write_archive(tmp_path/'src.zip',d);e=edited(d);token=digest(preview_edit(d,e))
    e['values']['shot/prompt']='changed after preview'
    with pytest.raises(ValueError):apply_archive(src,e,['shot/prompt'],tmp_path/'no.zip',expected_preview=token)
    assert not (tmp_path/'no.zip').exists()


def test_ui_racing_change_during_apply_is_refused(page,tmp_path):
    p,e=export_ui_edit(page,tmp_path);e['values']['shot/prompt']='external';load_ui_edit(page,p,e)
    # A deterministic digest barrier works in native WebCrypto and fallback-only documents.
    page.evaluate("""()=>{window._delayNextDigest=true;
      Object.defineProperty(crypto,'subtle',{configurable:true,value:{digest:async(_algo,bytes)=>{
        if(window._delayNextDigest){window._delayNextDigest=false;window._digestStarted=true;
          await new Promise(resolve=>window._releaseDigest=resolve);}
        const hex=new ManjuWorkbench.SHA256().update(new Uint8Array(bytes)).hex();
        return Uint8Array.from(hex.match(/../g),h=>parseInt(h,16)).buffer;
      }}});
    }""")
    page.locator('#exchange-confirmed').check();page.locator('#exchange-apply').click()
    page.wait_for_function('window._digestStarted === true')
    page.locator('#prompt').fill('newest local text while verifying')
    page.evaluate('window._releaseDigest()')
    expect(page.locator('#exchange-status')).to_contain_text('未覆盖新工作',timeout=5000)
    expect(page.locator('#prompt')).to_have_value('newest local text while verifying')


def test_ui_conflict_explicit_overwrite_and_stale_undo_lock(page,tmp_path):
    p,e=export_ui_edit(page,tmp_path);e['values']['shot/prompt']='external';page.locator('#prompt').fill('local');load_ui_edit(page,p,e)
    page.get_by_label('带回 shot/prompt',exact=True).check();page.locator('#exchange-confirmed').check();page.locator('#exchange-apply').click()
    expect(page.locator('#prompt')).to_have_value('external');page.locator('#director-change').fill('keep newer idea')
    expect(page.locator('#exchange-undo-confirmed')).to_be_disabled()
    expect(page.locator('#exchange-undo')).to_be_disabled()


def test_utf16_text_bounds_parity_and_non_bmp(page):
    d=empty_document();e=edited(d,**{'shot/prompt':'🪄'*15000});ExternalEdit.model_validate(e)
    assert page.evaluate('async e=>(await ManjuExchange.normalize(e)).values["shot/prompt"].length',e)==30000
    e['values']['shot/prompt']+='x'
    with pytest.raises(ValueError):ExternalEdit.model_validate(e)
    assert page.evaluate('async e=>{try{await ManjuExchange.normalize(e);return false;}catch{return true;}}',e)


def test_pending_returned_text_can_be_saved_without_touching_workspace(page,tmp_path):
    p,e=export_ui_edit(page,tmp_path)
    names={v:k for k,v in text_members(ExternalEdit.model_validate(e)).items()}
    text=tmp_path/names['shot/prompt'];text.write_text('  外部尚未批准的修改\n ',encoding='utf-8')
    page.locator('#exchange-import-json').set_input_files(p)
    expect(page.locator('#exchange-preview')).to_be_enabled()
    before=page.evaluate('ManjuStudio.view()')
    page.locator('#exchange-import-texts').set_input_files(text)
    expect(page.locator('#exchange-import-status')).to_contain_text('已载入 1 个外部 TXT')
    with page.expect_download() as item:page.locator('#exchange-save-returned').click()
    saved=tmp_path/'returned.json';item.value.save_as(saved)
    assert read_edit(saved).values['shot/prompt']=='  外部尚未批准的修改\n '
    assert page.evaluate('ManjuStudio.view()')==before
    page.locator('#exchange-clear').click()
    expect(page.locator('#exchange-save-returned')).to_be_disabled()
    page.locator('#exchange-import-json').set_input_files(saved)
    page.locator('#exchange-preview').click()
    expect(page.locator('#exchange-preview-box')).to_be_visible()
    assert page.evaluate('ManjuExchange.state.pending.report.rows.find(r=>r.key==="shot/prompt").status')=='ready'


def test_invalid_reload_clears_ready_label_but_retains_valid_buffer(page,tmp_path):
    p,e=export_ui_edit(page,tmp_path);load_ui_edit(page,p,e)
    p.write_text('{"broken":true}',encoding='utf-8')
    page.locator('#exchange-import-json').set_input_files(p)
    expect(page.locator('#exchange-status')).to_contain_text('未完成')
    expect(page.locator('#exchange-import-status')).not_to_contain_text('已加载')
    # R16 keeps rescuable text, not the failed input or an old approval.
    expect(page.locator('#exchange-save-returned')).to_be_enabled()
    expect(page.locator('#exchange-import-status')).to_contain_text('保留上一份有效改稿')
    assert page.evaluate('ManjuExchange.state.edit') == e
    assert page.evaluate('ManjuExchange.state.pending') is None
    assert not page.locator('#exchange-confirmed').is_checked()
