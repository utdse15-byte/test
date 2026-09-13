from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import zipfile

import pytest
from playwright.sync_api import expect
from manju.authoring.core import canonical,digest
from manju.authoring.story import Story,compile_brief,brief_status
from manju.authoring.desk import verify_desk, extract_studio
from tests.test_rebuilt_r5_workbench import browser,page
from tests.test_story_sources import sample,pin


def install(page,doc):
    page.evaluate('d=>ManjuStory.install(d)',doc)


def test_new_controls_dont_change_old_empty_backup(page):
    assert page.evaluate('ManjuStory.view()') is None
    r=page.evaluate('async()=>{const b=await ManjuDesk.build();return b.document;}')
    assert r['schema_id']=='manju.desk-session/v1' and 'story' not in r


@pytest.mark.parametrize('scope,character',[('audience',None),('author',None),('character','C01'),('character','C02')])
def test_context_and_hash_parity(page,scope,character):
    d=sample();d['scenes'][1]['policy']={'scope':scope,'character_id':character,'history_count':2};pin(d,'S02')
    assert page.evaluate('d=>ManjuStory.normalize(d)',d)==Story.model_validate(d).model_dump(mode='json')
    result=page.evaluate('d=>{const c=ManjuStory.compile(d,"S02");return {content:c,hash:ManjuStory.digest(c)}}',d)
    assert result['content']==compile_brief(d,'S02') and result['hash']==digest(result['content'])


@pytest.mark.parametrize('case',['action','source','order','unreferenced','observation'])
def test_impact_browser_python_parity(page,case):
    d=sample();pin(d)
    if case=='action':d['scenes'][0]['action']='主动交出箱子'
    elif case=='source':d['sources'][0]['text']='改过的人物'
    elif case=='order':d['scenes']=list(reversed(d['scenes']))
    elif case=='unreferenced':d['scenes'][2]['after']='之后的新故事'
    else:d['briefs'][0]['observation']['seen']='影片中演员交出了箱子'
    got=page.evaluate('d=>ManjuStory.status(d,d.briefs[0])',d)
    assert got==brief_status(d,'BRIEF1')


def test_story_json_preview_explicit_apply_and_stale_guard(page):
    page.locator('#prompt').fill('不要覆盖当前镜头')
    doc=sample()
    page.locator('#story-file').set_input_files({'name':'外部故事.json','mimeType':'application/json','buffer':canonical(doc)})
    expect(page.locator('#story-import-preview')).to_be_visible()
    assert page.evaluate('ManjuStory.view()') is None
    page.locator('#prompt').fill('读取后继续写的新稿')
    expect(page.locator('#story-import-apply')).to_be_disabled()
    page.locator('#story-file').set_input_files({'name':'外部故事.json','mimeType':'application/json','buffer':canonical(doc)})
    page.locator('#story-import-confirmed').check();page.locator('#story-import-apply').click()
    assert page.evaluate('ManjuStory.view()')==doc
    assert page.locator('#prompt').input_value()=='读取后继续写的新稿'


def test_desk_v2_download_verify_and_reopen(page,tmp_path):
    d=sample();pin(d);install(page,d)
    page.locator('#story-action').fill('  未完成的动作\n下一行仍在写 ')
    wanted=page.evaluate('ManjuStory.view()')
    with page.expect_download() as dl:page.locator('#desk-save').click()
    out=tmp_path/'STORY_DESK.zip';dl.value.save_as(out)
    assert verify_desk(out)['schema_id']=='manju.desk-session/v2'
    with zipfile.ZipFile(out) as z:
        got=json.loads(z.read('DESK.json'))['story'];assert got==wanted
        inner=z.read('STUDIO.zip')
    page.locator('#desk-verify-file').set_input_files(out)
    expect(page.locator('#desk-save-status')).to_contain_text('已核验你选回')
    assert page.evaluate('ManjuDesk.needsSave()') is False
    page.locator('#story-action').fill('新的修改')
    assert page.evaluate('ManjuDesk.needsSave()') is True
    # Only verified explicit restore can replace the new draft.
    # Use the actual saved file via unified entry, not a fabricated API response.
    page.locator('#intake-files').set_input_files(out)
    expect(page.locator('#intake-preview')).to_be_visible();page.locator('#intake-open').click()
    page.locator('#desk-restore-confirmed').check();page.locator('#desk-restore-apply').click()
    assert page.evaluate('ManjuStory.view()')==wanted
    with page.expect_download() as dl:page.locator('#desk-save').click()
    again=tmp_path/'again.zip';dl.value.save_as(again);assert out.read_bytes()==again.read_bytes()
    extract_studio(out,tmp_path/'legacy.zip');assert (tmp_path/'legacy.zip').read_bytes()==inner


def test_explicit_story_link_blocks_stale_request_and_keeps_draft(page):
    d=sample();pin(d);install(page,d)
    page.locator('[data-story-tab=briefs]').click()
    page.get_by_role('button',name='预览带入当前镜头',exact=True).click()
    before=page.locator('#prompt').input_value()
    assert before!=page.locator('#story-new-prompt').input_value()
    page.locator('#story-link-confirmed').check();page.locator('#story-link-apply').click()
    expected=page.locator('#prompt').input_value()
    assert page.evaluate('ManjuStory.assertRequest()') is None
    page.locator('[data-story-tab=scenes]').click();page.locator('#story-action').fill('林岚主动把箱子递给对方并松手。')
    with pytest.raises(Exception,match='故事依据已变'):page.evaluate('ManjuStory.assertRequest()')
    assert page.locator('#prompt').input_value()==expected
    assert not page.locator('#human-confirmed').is_checked()


def test_link_preview_cannot_overwrite_later_prompt(page):
    d=sample();pin(d);install(page,d);page.locator('[data-story-tab=briefs]').click();page.get_by_role('button',name='预览带入当前镜头',exact=True).click()
    page.locator('#prompt').fill('后来写的新句子');expect(page.locator('#story-link-apply')).to_be_disabled()
    assert page.locator('#prompt').input_value()=='后来写的新句子'


def test_history_does_not_accept_fraction_silently(page):
    install(page,sample());page.locator('#story-history').fill('1.5');page.locator('#story-action').click()
    expect(page.locator('#story-status')).to_contain_text('保留上次有效值')
    assert page.locator('#story-history').input_value()=='0'
    assert page.evaluate('ManjuStory.view().scenes[0].policy.history_count')==0


def test_text_in_source_not_executed_or_interpreted_as_html(page):
    d=sample();payload='<img src=x onerror="window.storyPwned=1">';d['sources'][0]['name']=payload;d['sources'][0]['text']=payload
    install(page,d);page.locator('[data-story-tab=sources]').click()
    assert page.locator('#story-source-name').input_value()==payload
    assert not page.evaluate('Boolean(window.storyPwned)')
    assert page.locator('#story-section img').count()==0


@pytest.mark.parametrize('width',[360,390,768,1440])
def test_story_layout_no_horizontal_overflow(page,width):
    install(page,sample());page.set_viewport_size({'width':width,'height':900})
    for tab in ['scenes','sources','briefs']:
        page.locator('[data-story-tab='+tab+']').click()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),tab


def test_reference_not_in_shot_is_not_silently_given_to_model(page):
    d=sample();d['scenes'][0]['references']=[{'id':'R1','sha256':'a'*64,'bytes':10,'filename':'未绑定.png','role':'identity','inherit':'人物','exclude':'机位','time_range':''}];b=pin(d);install(page,d)
    prompt=page.evaluate('b=>ManjuStory.prompt(b.content)',b)
    d['link']={'brief_id':b['id'],'shot_id':b['scene_id'],'prompt_sha256':digest(prompt)}
    install(page,d);page.locator('#shot-id').fill(b['scene_id']);page.locator('#prompt').fill(prompt)
    with pytest.raises(Exception,match='参考尚未绑定'):page.evaluate('ManjuStory.assertRequest()')


def test_story_field_edit_dirties_verified_backup_immediately(page,tmp_path):
    install(page,sample())
    with page.expect_download() as dl:page.locator('#desk-save').click()
    out=tmp_path/'work.zip';dl.value.save_as(out);page.locator('#desk-verify-file').set_input_files(out)
    expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','verified')
    page.locator('#story-title').fill('新作品名')
    expect(page.locator('#ux-save-strip')).to_have_attribute('data-state','dirty')
