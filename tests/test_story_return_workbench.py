from copy import deepcopy
from pathlib import Path
import json
import pytest
from playwright.sync_api import expect
from tests.test_rebuilt_r5_workbench import browser
from tests.test_story_return import scenario
from tests.test_story_sources import sample,pin,scene,source
from manju.authoring.core import canonical
from manju.authoring.story_return import create_return,preview_return,apply_return
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def page(browser):
    c=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1080});p=c.new_page();p.set_default_timeout(10000)
    errors=[];p.on('pageerror',lambda e:errors.append(str(e)))
    p.set_content((ROOT/'tools/model_workbench.html').read_text(encoding='utf-8'),wait_until='load')
    yield p
    assert not errors,errors
    c.close()


def variants(name):
    a,b,c=scenario()
    if name=='same':b=deepcopy(a);c=deepcopy(a)
    if name=='unicode':b['scenes'][1]['action']='  \n</script>中文 😀 e\u0301\n  '
    if name=='ending':b['ending']=' 外部新结局 '
    if name=='add':b['sources'].append(source('NEW'));b['scenes'][1]['source_ids'].append('NEW')
    if name=='move':b['scenes'].reverse()
    if name=='rename':b['sources'][0]['name']='外部改名';c['sources'][0]['text']='本地补的人物正文'
    if name=='history':pin(b,'S02','NEW_BRIEF')
    if name=='local-history':pin(c,'S02','LOCAL_BRIEF')
    if name=='structure-conflict':b['scenes'].reverse();c['scenes'].append(scene('LOCAL'))
    if name=='delete':a['scenes'].append(scene('EXTRA','old'));b=deepcopy(a);b['scenes'].pop();c=deepcopy(a);c['scenes'][-1]['action']='local'
    if name=='prototype':a['scenes'].append(scene('constructor','old'));b=deepcopy(a);c=deepcopy(a);b['scenes'][-1]['action']='new'
    return a,b,c


@pytest.mark.parametrize('name',['normal','same','unicode','ending','add','move','rename','history','local-history','structure-conflict','delete','prototype'])
def test_python_js_exact_parity(page,name):
    a,b,c=variants(name);packet=create_return(a,b)
    assert page.evaluate('([a,b])=>ManjuStoryReturnCore.create(a,b)',[a,b])==packet
    p=page.evaluate('([c,p])=>ManjuStoryReturnCore.preview(c,p)',[c,packet]);assert p==preview_return(c,packet)
    if p['ready_keys']:
        result=page.evaluate('([c,p,k,h])=>ManjuStoryReturnCore.apply(c,p,k,h)',[c,packet,p['ready_keys'],p['preview_sha256']])
        assert result==apply_return(c,packet,p['ready_keys'],expected_preview=p['preview_sha256'])


def setup(p):
    a,b,c=scenario();p.evaluate('d=>ManjuStory.install(d)',c);p.locator('#ux-navigation [data-view=story]').click();p.locator('#story-return-section>summary').click();return a,b,c


def load(p,a,b):
    p.locator('#story-return-file').set_input_files({'name':'改稿.json','mimeType':'application/json','buffer':canonical(create_return(a,b))});expect(p.locator('#story-return-results')).to_be_visible()


def test_real_preview_select_apply_undo_keeps_other_work(page):
    p=page;a,b,c=setup(p);original=p.evaluate('ManjuDesk.fingerprint()');load(p,a,b)
    assert p.evaluate('ManjuDesk.fingerprint()')==original
    expect(p.locator('[data-return-key="scene/S02/action"]')).to_be_checked();expect(p.locator('[data-return-key="scene/S02/dialogue"]')).to_be_disabled()
    expect(p.locator('#story-return-rows')).to_contain_text(c['scenes'][1]['dialogue'])
    p.locator('#story-return-apply').click();r=p.evaluate('ManjuStory.view()')
    assert r['scenes'][1]['action']==b['scenes'][1]['action'] and r['scenes'][1]['dialogue']==c['scenes'][1]['dialogue']
    assert r['scenes'][0]==c['scenes'][0] and r['briefs']==c['briefs']
    p.locator('#story-return-undo').click();assert p.evaluate('ManjuStory.view()')==c


def test_later_edit_disarms_preview_and_undo(page):
    p=page;a,b,c=setup(p);load(p,a,b);p.locator('#story-action').fill('预览之后的新输入')
    expect(p.locator('#story-return-apply')).to_be_disabled();expect(p.locator('#story-return-summary')).to_contain_text('已变化')
    p.locator('#story-return-preview').click();p.locator('#story-return-apply').click()
    assert p.evaluate('ManjuStory.view().scenes[0].action')=='预览之后的新输入'
    p.locator('#story-action').fill('又写的新稿');expect(p.locator('#story-return-undo')).to_be_disabled()


@pytest.mark.parametrize('bad',[b'{bad}',b'{}',b'{"schema_id":1,"schema_id":2}',b'x'*(2*1024*1024+4097)])
def test_bad_next_file_disarms_but_retains_original_and_prior_packet(page,bad):
    p=page;a,b,c=setup(p);load(p,a,b);packet=p.evaluate('ManjuStoryReturn.state.packet')
    p.locator('#story-return-file').set_input_files({'name':'坏.json','mimeType':'application/json','buffer':bad})
    expect(p.locator('#story-return-results')).not_to_be_visible();expect(p.locator('#story-return-apply')).to_be_disabled()
    assert p.evaluate('ManjuStoryReturn.state.packet')==packet and p.evaluate('ManjuStory.view()')==c
    p.locator('#story-return-preview').click();expect(p.locator('#story-return-results')).to_be_visible()


@pytest.mark.parametrize('width',[360,390,768,1440])
def test_readable_responsive(page,width):
    p=page;a,b,c=setup(p);p.set_viewport_size({'width':width,'height':1080});load(p,a,b)
    assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
    n=p.locator('#story-return-rows .return-pair').first.evaluate('e=>getComputedStyle(e).gridTemplateColumns.split(" ").length')
    assert n==(1 if width<=900 else 2)
    assert p.locator('#story-return-rows pre').first.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')>=16


def test_structure_default_off_preserves_local_text_on_selected_apply(page):
    p=page;a=sample();pin(a);b=deepcopy(a);b['sources'].append(source('NEW'));b['scenes'][1]['source_ids'].append('NEW');b['scenes'].reverse();c=deepcopy(a);c['scenes'][0]['dialogue']='local'
    p.evaluate('d=>ManjuStory.install(d)',c);p.locator('#ux-navigation [data-view=story]').click();p.locator('#story-return-section>summary').click();load(p,a,b)
    expect(p.locator('[data-return-key=structure]')).not_to_be_checked();expect(p.locator('#story-return-apply')).to_be_disabled()
    p.locator('[data-return-key=structure]').check();p.locator('#story-return-apply').click();out=p.evaluate('ManjuStory.view()')
    assert out['scenes'][-1]['dialogue']=='local' and out['sources'][-1]['id']=='NEW' and out['briefs']==a['briefs']


def test_intake_discovers_not_whole_backup_route(page):
    p=page;a,b,c=scenario();p.evaluate('d=>ManjuStory.install(d)',c)
    p.locator('#intake-files').set_input_files({'name':'回来.json','mimeType':'application/json','buffer':canonical(create_return(a,b))})
    expect(p.locator('#intake-preview')).to_be_visible();p.locator('#intake-open').click();expect(p.locator('#story-return-results')).to_be_visible()
    assert p.evaluate('ManjuStory.view()')==c


def test_data_not_executed_and_real_download_keeps_packet(page,tmp_path):
    p=page;a,b,c=setup(p);b['scenes'][1]['action']='<script>window.PWNED=1</script><img src=x onerror=alert(1)>\n😀';load(p,a,b)
    assert p.locator('#story-return-rows script,#story-return-rows img').count()==0 and p.evaluate('window.PWNED===undefined')
    p.locator('#story-return-files>summary').click()
    with p.expect_download() as dl:p.locator('#story-return-download').click()
    dest=tmp_path/'实际下载.json';dl.value.save_as(dest);assert json.loads(dest.read_text(encoding='utf-8'))==create_return(a,b)
    assert p.evaluate('ManjuStory.view()')==c


def test_large_review_is_paged_and_choices_stay_explicit(page):
    p=page;a=sample();a['sources']=[];a['briefs']=[];a['scenes']=[scene('S'+str(i),'old') for i in range(100)];b=deepcopy(a)
    for s in b['scenes']:s['action']='new'
    p.evaluate('d=>ManjuStory.install(d)',a);p.locator('#ux-navigation [data-view=story]').click();p.locator('#story-return-section>summary').click();load(p,a,b)
    assert p.locator('#story-return-rows input').count()==40;expect(p.locator('#story-return-apply')).to_contain_text('100')
    p.locator('#story-return-clear').click();p.locator('[data-return-key="scene/S0/action"]').check();p.locator('#story-return-next').click()
    assert p.locator('#story-return-rows input').count()==40
    p.locator('[data-return-key="scene/S40/action"]').check();p.locator('#story-return-apply').click();out=p.evaluate('ManjuStory.view()')
    assert sum(s['action']=='new' for s in out['scenes'])==2


def test_unchecked_ready_field_not_reselected_after_apply(page):
    p=page;a,b,c=scenario();c=deepcopy(a);p.evaluate('d=>ManjuStory.install(d)',c);p.locator('#ux-navigation [data-view=story]').click();p.locator('#story-return-section>summary').click();load(p,a,b)
    p.locator('[data-return-key="scene/S02/dialogue"]').uncheck();p.locator('#story-return-apply').click()
    expect(p.locator('#story-return-apply')).to_be_disabled()
    expect(p.locator('[data-return-key="scene/S02/dialogue"]')).not_to_be_checked()
    assert p.evaluate('ManjuStory.view().scenes[1].dialogue')==a['scenes'][1]['dialogue']


def test_file_read_race_does_not_replace_local_edit(page):
    p=page;a,b,c=setup(p);packet=create_return(a,b)
    p.evaluate('packet=>{window.releaseReturnRead=null;window.pendingRead=ManjuStoryReturn.load({size:2000,arrayBuffer:()=>new Promise(resolve=>{window.releaseReturnRead=()=>resolve(new TextEncoder().encode(JSON.stringify(packet)).buffer)})}).catch(e=>String(e.message));}',packet)
    p.locator('#story-action').fill('读取期间新写的稿');p.evaluate('releaseReturnRead()');p.evaluate('pendingRead')
    assert p.evaluate('ManjuStoryReturn.state.packet') is None
    assert p.evaluate('ManjuStory.view().scenes[0].action')=='读取期间新写的稿'
    expect(p.locator('#story-return-apply')).to_be_disabled()


def test_out_of_order_file_read_never_hides_newer_valid_result(page):
    p=page;a,b,c=setup(p);old=create_return(a,b);new=deepcopy(b);new['scenes'][1]['action']='最新的候选';newpacket=create_return(a,new)
    p.evaluate('old=>{window.resolveOld=null;window.oldReading=ManjuStoryReturn.load({name:"old.json",size:2000,arrayBuffer:()=>new Promise(resolve=>{window.resolveOld=()=>resolve(new TextEncoder().encode(JSON.stringify(old)).buffer)})});}',old)
    load(p,a,new);p.evaluate('resolveOld()');p.evaluate('oldReading')
    expect(p.locator('#story-return-results')).to_be_visible()
    assert p.evaluate('ManjuStoryReturn.state.packet')==newpacket
    expect(p.locator('#story-return-rows')).to_contain_text('最新的候选')
