"""Exact original-content rebinding, real browser files, no synthetic approvals."""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
import struct
import zipfile
import zlib

import pytest
from playwright.sync_api import expect
from tests.test_rebuilt_r5_workbench import browser, page
from manju.authoring.desk import verify_desk

ROOT=Path(__file__).resolve().parents[1]

def png(color=b'\xff\x00\x00'):
    def chunk(k,v):return struct.pack('>I',len(v))+k+v+struct.pack('>I',zlib.crc32(k+v)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'+color))+chunk(b'IEND',b'')


def request(page, data, *, times=1):
    h=hashlib.sha256(data).hexdigest()
    refs=[dict(id=f'A{i+1}',role='first_frame' if i==0 else 'last_frame',path='原目录/原文件.png',sha256=h,bytes=len(data),duration_ms=None,origin_model=None) for i in range(times)]
    value=dict(schema_id='manju.model-request/v1',shot_id='用户镜头',task='animate',prompt='  继续写的新草稿，不应丢失  ',duration_s=8,resolution='720p',aspect_ratio='16:9',assets=refs,preserve=[],change=[],stage='draft',approved_draft_sha256=None)
    page.locator('#import-request').set_input_files({'name':'request.json','mimeType':'application/json','buffer':json.dumps(value,ensure_ascii=False).encode()})
    page.wait_for_function('() => ManjuWorkbench.state.assets.length>0')
    page.locator('#relink-panel').evaluate('(e)=>e.open=true')
    return h


def match(page, data, name='新名字.png'):
    page.locator('#relink-files').set_input_files({'name':name,'mimeType':'image/png','buffer':data})
    page.wait_for_function('() => !ManjuRelink.state.busy && !!ManjuRelink.state.pending')


def apply(page):
    page.locator('#relink-confirmed').check();page.locator('#relink-apply').click()
    expect(page.locator('#relink-status')).to_contain_text('已接回',timeout=20000)


def test_renamed_file_rebinds_two_roles_without_rewriting_task(page,tmp_path):
    data=png();h=request(page,data,times=2)
    before=page.evaluate('ManjuDesk.fingerprint()')
    match(page,data,'完全改名.bin')
    assert page.evaluate('ManjuWorkbench.state.files.size')==0
    expect(page.locator('#relink-summary')).to_contain_text('覆盖 2 个缺失用途')
    apply(page)
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    assert page.evaluate('ManjuWorkbench.state.assets.length')==2
    assert page.evaluate('ManjuWorkbench.state.files.size')==1
    assert page.evaluate('ManjuWorkbench.state.files.values().next().value.file.name')=='原文件.png'
    assert page.locator('#quality-only').is_checked()
    assert not page.locator('#human-confirmed').is_checked()
    assert page.evaluate('ManjuDesk.needsSave()')
    with page.expect_download() as d:page.locator('#desk-save').click()
    path=tmp_path/'relinked.zip';d.value.save_as(path)
    assert verify_desk(path)['ok']
    with zipfile.ZipFile(path) as outer,zipfile.ZipFile(io.BytesIO(outer.read('STUDIO.zip'))) as z:
        assert z.read('media/'+h)==data


@pytest.mark.parametrize('name',['原文件.png','另一份.png','<img src=x onerror=alert(1)>.png'])
def test_same_name_or_wrong_bytes_never_adds_new_asset(page,name):
    request(page,png());match(page,png(b'\x00\xff\x00'),name)
    assert page.evaluate('ManjuRelink.state.pending.matched.size')==0
    assert page.evaluate('ManjuWorkbench.state.files.size')==0
    assert page.evaluate('ManjuWorkbench.state.assets.length')==1
    assert not page.locator('#relink-apply').is_enabled()
    assert page.locator('#relink-rows img').count()==0


def test_partial_matches_leave_other_missing_file_and_refuse_empty_backup(page):
    data=png();request(page,data)
    page.evaluate('''async(data)=>{const q={...ManjuWorkbench.state.assets[0],id:'A2',sha256:await ManjuWorkbench.hash(new Uint8Array(data)),role:'last_frame',path:'other.png'};ManjuWorkbench.state.assets.push(q);ManjuWorkbench.invalidate();}''',list(png(b'\0\xff\0')))
    match(page,data);apply(page)
    assert page.evaluate('ManjuRelink.inventory().filter(r=>r.missing).length')==1
    page.locator('#desk-save').click()
    expect(page.locator('#desk-save-status')).to_contain_text('缺少原文件')
    assert page.evaluate('ManjuWorkbench.state.assets.length')==2


def test_typing_invalidates_preview_and_preserves_new_text(page):
    data=png();request(page,data);match(page,data)
    page.locator('#prompt').fill('预览之后继续写的文字')
    expect(page.locator('#relink-apply')).to_be_disabled()
    assert page.evaluate('ManjuRelink.state.pending') is None
    assert page.evaluate('ManjuWorkbench.state.files.size')==0
    assert page.locator('#prompt').input_value()=='预览之后继续写的文字'


def test_binding_change_invalidates_preview_even_with_same_content_metadata(page):
    data=png();h=request(page,data);match(page,data)
    page.evaluate('''async data=>{const f=new File([new Uint8Array(data)],'else.png');const e=await ManjuWorkbench.inspectFile(f);ManjuWorkbench.state.files.set(e.sha256,e);}''',list(data))
    page.locator('#relink-confirmed').click()
    expect(page.locator('#relink-apply')).to_be_disabled()
    assert page.evaluate('ManjuWorkbench.state.files.values().next().value.file.name')=='else.png'


def test_apply_rereads_file_and_failure_changes_no_binding(page):
    data=png();request(page,data);match(page,data)
    page.evaluate('''()=>{const e=ManjuRelink.state.pending.matched.values().next().value;e.file=new File([new Uint8Array(e.file.size)],'modified.png');}''')
    page.locator('#relink-confirmed').check();page.locator('#relink-apply').click()
    expect(page.locator('#relink-status')).to_contain_text('整次接回取消')
    assert page.evaluate('ManjuWorkbench.state.files.size')==0


def test_cancelled_preview_does_not_bind_or_clear_old_text(page):
    data=png();request(page,data);before=page.evaluate('ManjuDesk.fingerprint()');match(page,data)
    page.locator('#relink-cancel').click()
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    assert page.evaluate('ManjuRelink.state.pending') is None
    assert page.evaluate('ManjuWorkbench.state.files.size')==0


def test_reuse_existing_cross_workspace_original_without_file_selection(page):
    data=png();h=request(page,data)
    page.evaluate('''async data=>{const f=new File([new Uint8Array(data)],'current.png');const e=await ManjuWorkbench.inspectFile(f);ManjuReview.state.files.set(e.sha256,e);}''',list(data))
    page.locator('#relink-existing').click()
    page.wait_for_function('() => !!ManjuRelink.state.pending && !ManjuRelink.state.busy')
    apply(page)
    assert page.evaluate('ManjuWorkbench.state.files.size')==1
    assert page.evaluate('ManjuReview.state.files.size')==1
    assert page.evaluate('ManjuReview.state.pending.length')==0


def test_checklist_is_real_download_not_a_backup(page,tmp_path):
    data=png();h=request(page,data)
    with page.expect_download() as d:page.locator('#relink-report').click()
    path=tmp_path/'check.txt';d.value.save_as(path)
    text=path.read_text(encoding='utf-8')
    assert h in text and '原目录/原文件.png' in text and '不是备份' in text
    assert page.evaluate('ManjuDesk.needsSave()')


def test_narrow_layout_and_no_file_reads_on_inventory(page):
    request(page,png());page.set_viewport_size({'width':390,'height':844})
    page.locator('#relink-check').click()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
    assert page.evaluate('ManjuWorkbench.state.files.size')==0


def test_duplicate_selected_bytes_are_only_one_match(page):
    data=png();request(page,data)
    page.locator('#relink-files').set_input_files([{'name':n,'mimeType':'image/png','buffer':data} for n in ['a.png','b.png']])
    page.wait_for_function('() => !!ManjuRelink.state.pending && !ManjuRelink.state.busy')
    assert page.evaluate('ManjuRelink.state.pending.matched.size')==1
    apply(page)
    assert page.evaluate('ManjuWorkbench.state.assets.length')==1


def test_no_required_media_does_not_create_preview(page):
    page.locator('#relink-panel').evaluate('(e)=>e.open=true')
    page.locator('#relink-existing').click()
    expect(page.locator('#relink-status')).to_contain_text('没有缺失绑定')
    assert page.evaluate('ManjuRelink.state.pending') is None


def test_more_than_256_selections_refused_without_work_change(page):
    request(page,png());before=page.evaluate('ManjuDesk.fingerprint()')
    msg=page.evaluate('''async()=>{try{await ManjuRelink.prepare(Array.from({length:257},()=>new File(['x'],'x.png')));return '';}catch(e){return e.message;}}''')
    assert '256' in msg
    assert page.evaluate('ManjuDesk.fingerprint()')==before


def test_total_reference_limit_includes_already_bound_media(page):
    request(page,png())
    page.evaluate('''()=>{const a=ManjuWorkbench.state.assets[0];for(let i=1;i<=5;i++)ManjuWorkbench.state.assets.push({...a,id:'BIG'+i,path:'big'+i+'.png',sha256:i.toString(16).repeat(64),bytes:128*1024*1024});}''')
    msg=page.evaluate("async()=>{try{await ManjuRelink.prepare([]);return '';}catch(e){return e.message;}}")
    assert '512' in msg
    assert page.evaluate('ManjuWorkbench.state.files.size')==0


def test_exact_reconnect_preserves_final_stage_and_historical_approval(page):
    data=png();request(page,data)
    page.evaluate("()=>{ManjuWorkbench.state.stage='final';ManjuWorkbench.state.draftHash='d'.repeat(64);}")
    before=page.evaluate('ManjuDesk.fingerprint()');match(page,data);apply(page)
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    assert page.evaluate('ManjuWorkbench.state.stage')=='final'
    assert not page.locator('#human-confirmed').is_checked()

from tests.test_rebuilt_r12_director import video, load_video, capture_at, png as real_png


def seed_three_areas(page,video):
    page.locator('#prompt').fill('合成测试：保持原稿')
    load_video(page,video);capture_at(page,0.5)
    page.locator('#repair-source').set_input_files(video)
    page.wait_for_function('()=>!!ManjuRepair.state.source&&!ManjuRepair.state.busy')
    page.locator('#review-files').set_input_files(video)
    page.wait_for_function('()=>ManjuReview.state.pending.length===1&&!ManjuReview.state.busy')
    page.locator('#relink-panel').evaluate('(e)=>e.open=true')


def test_real_video_reconnects_review_repair_director_without_new_candidates(page,video,tmp_path):
    seed_three_areas(page,video);before=page.evaluate('ManjuDesk.fingerprint()')
    page.evaluate('''()=>{ManjuReview.state.files.clear();ManjuRepair.state.entry=null;ManjuDirector.state.entry=null;}''')
    match(page,video.read_bytes(),'改了名字.mp4');apply(page)
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    assert page.evaluate('ManjuReview.state.pending.length')==1
    assert not page.evaluate('ManjuRelink.inventory().some(r=>r.missing)')
    assert page.evaluate('''async()=>{let n=0;for(const v of document.querySelectorAll('video'))if(v.src.startsWith('blob:')){await v.play();v.pause();n++;}return n;}''')>=3
    with page.expect_download() as d:page.locator('#desk-save').click()
    output=tmp_path/'three-area.zip';d.value.save_as(output)
    assert verify_desk(output)['ok']


def test_high_resolution_guide_relinked_with_pixels_and_bytes_preserved(page,video,tmp_path):
    seed_three_areas(page,video)
    raw=real_png(1920,1080,value=126);guide=tmp_path/'target.png';guide.write_bytes(raw)
    page.get_by_label('A01 目标 PNG',exact=True).set_input_files(guide)
    page.wait_for_function('()=>!!ManjuDirector.state.anchors[0].guide&&!ManjuDirector.state.busy')
    before=page.evaluate('ManjuDesk.fingerprint()')
    page.evaluate('''()=>{for(const k of ManjuDirector.state.files.keys())if(k.startsWith('guides/'))ManjuDirector.state.files.delete(k);}''')
    match(page,raw,'搬家新名.png');apply(page)
    assert page.evaluate('ManjuDesk.fingerprint()')==before
    entry=page.evaluate('''async()=>{const a=ManjuDirector.state.anchors[0].guide;const e=[...ManjuDirector.state.files.values()].find(e=>e.sha256===a.sha256);return {width:a.width,height:a.height,hash:(await ManjuWorkbench.inspectFile(e.file)).sha256};}''')
    assert entry==dict(width=1920,height=1080,hash=hashlib.sha256(raw).hexdigest())


def test_video_metadata_mismatch_refused_even_when_hash_matches(page,video):
    page.locator('#repair-source').set_input_files(video)
    page.wait_for_function('()=>!!ManjuRepair.state.source&&!ManjuRepair.state.busy')
    page.locator('#relink-panel').evaluate('(e)=>e.open=true')
    page.evaluate('''()=>{ManjuRepair.state.entry=null;ManjuRepair.state.source.width+=2;}''')
    match(page,video.read_bytes(),'same.mp4')
    assert page.evaluate('ManjuRelink.state.pending.matched.size')==0
    assert page.evaluate('ManjuRepair.state.entry') is None
    expect(page.locator('#relink-rows')).to_contain_text('尺寸或时长')


def test_cancel_during_file_read_does_not_bind_anything(page):
    data=png();request(page,data)
    page.evaluate('''data=>{const file=new File([new Uint8Array(data)],'held.png');const original=file.slice.bind(file);file.slice=(...args)=>{const part=original(...args);return {arrayBuffer:async()=>{window.readHeld=true;await new Promise(r=>window.finishRead=r);return await part.arrayBuffer();}};};window.pendingRelink=ManjuRelink.prepare([file]).catch(e=>window.relinkFailure=e.message);}''',list(data))
    page.wait_for_function('()=>window.readHeld===true')
    page.locator('#relink-cancel').click();page.evaluate('window.finishRead()')
    page.wait_for_function('()=>!!window.relinkFailure')
    assert '取消' in page.evaluate('window.relinkFailure')
    assert page.evaluate('ManjuWorkbench.state.files.size')==0
