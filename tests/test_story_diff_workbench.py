from copy import deepcopy
from pathlib import Path
import json
import pytest
from playwright.sync_api import expect
from tests.test_rebuilt_r5_workbench import browser
from tests.test_story_diff import variants, CASES
from tests.test_story_sources import sample,pin
from manju.authoring.story_diff import compare_stories
from manju.authoring.core import canonical
ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def diffpage(browser):
    c=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1050});p=c.new_page()
    p.set_content((ROOT/'tools/model_workbench.html').read_text(encoding='utf-8'),wait_until='load')
    yield p
    c.close()

@pytest.mark.parametrize('case',CASES)
def test_browser_python_diff_parity(diffpage,case):
    a,b=variants(case)
    actual=diffpage.evaluate('([a,b])=>ManjuStoryDiff.compare(a,b)',[a,b])
    assert actual==compare_stories(a,b)


def load_candidate(p,b):
    p.locator('#story-file').set_input_files({'name':'返回故事.json','mimeType':'application/json','buffer':canonical(b)})
    expect(p.locator('#story-import-preview')).to_be_visible()


def setup(p):
    a,b=variants('action');p.evaluate('d=>ManjuStory.install(d)',a)
    p.locator('#ux-navigation [data-view=story]').click()
    return a,b


def test_preview_is_visible_without_replacing_text_or_fingerprint(diffpage):
    p=diffpage;a,b=setup(p);finger=p.evaluate('ManjuDesk.fingerprint()');load_candidate(p,b)
    expect(p.locator('#story-import-diff')).to_contain_text(a['scenes'][0]['action'])
    expect(p.locator('#story-import-diff')).to_contain_text(b['scenes'][0]['action'])
    expect(p.locator('#story-import-diff')).to_contain_text('作者结局字段未改')
    assert p.evaluate('ManjuStory.view()')==a
    assert p.evaluate('ManjuDesk.fingerprint()')==finger
    assert p.locator('#story-import-diff button').count()==0
    assert p.locator('#story-import-diff input').count()==0


def test_new_edit_removes_stale_diff_and_cannot_restore(diffpage):
    p=diffpage;a,b=setup(p);load_candidate(p,b)
    p.locator('#story-import-confirmed').check()
    p.locator('#story-action').fill('这里是后来写的新稿')
    expect(p.locator('#story-import-preview')).not_to_be_visible()
    assert p.locator('#story-import-diff').inner_text()==''
    assert p.evaluate('ManjuStory.view().scenes[0].action')=='这里是后来写的新稿'
    assert not p.evaluate('ManjuStory.state.pending')


def test_invalid_new_file_clears_old_compare_not_current_story(diffpage):
    p=diffpage;a,b=setup(p);load_candidate(p,b);p.locator('#story-import-confirmed').check()
    p.locator('#story-file').set_input_files({'name':'坏.json','mimeType':'application/json','buffer':b'{bad}'})
    expect(p.locator('#story-import-preview')).not_to_be_visible()
    assert p.evaluate('ManjuStory.view()')==a and not p.evaluate('ManjuStory.state.pending')


@pytest.mark.parametrize('width',[360,390,768,1440])
def test_responsive_compare_no_horizontal_overflow(diffpage,width):
    p=diffpage;a,b=setup(p);p.set_viewport_size({'width':width,'height':1000});load_candidate(p,b)
    assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
    n=p.locator('#story-import-diff .diff-pair').first.evaluate('e=>getComputedStyle(e).gridTemplateColumns.split(" ").length')
    assert n==(1 if width<=820 else 2)


def test_hostile_text_remains_text(diffpage):
    p=diffpage;a,b=variants('html');p.evaluate('d=>ManjuStory.install(d)',a);p.locator('#ux-navigation [data-view=story]').click();load_candidate(p,b)
    assert p.locator('#story-import-diff script,#story-import-diff img').count()==0
    assert b['scenes'][0]['action'] in p.locator('#story-import-diff').inner_text()


def test_opening_comparison_details_does_not_dirty_backup(diffpage):
    p=diffpage;a,b=setup(p);load_candidate(p,b);old=p.evaluate('ManjuDesk.fingerprint()')
    p.locator('#story-import-diff>details>summary').click()
    p.locator('#story-import-diff>details>summary').click()
    assert p.evaluate('ManjuDesk.fingerprint()')==old


def test_returned_value_different_from_current_is_not_claimed_ai_delta(diffpage):
    p=diffpage;a,b=setup(p);p.locator('#story-action').fill('本地后来改过的内容');load_candidate(p,b)
    expect(p.locator('#story-import-diff')).to_contain_text('本地后来改过的内容')
    expect(p.locator('#story-import-diff')).to_contain_text('不代表原导出基线')
    assert p.locator('#story-action').input_value()=='本地后来改过的内容'


def test_inline_preview_does_not_create_redundant_toast_but_errors_still_show(diffpage):
    p=diffpage
    p.evaluate("document.getElementById('ux-notification').hidden=true")
    p.evaluate("async()=>{const built=await ManjuDesk.build();await ManjuDesk.preview(built.blob)}")
    expect(p.locator('#desk-restore-preview')).to_be_visible()
    expect(p.locator('#ux-notification')).not_to_be_visible()
    p.locator('#intake-files').set_input_files({'name':'坏.json','mimeType':'application/json','buffer':b'NOT JSON'})
    expect(p.locator('#ux-notification')).to_be_visible()
    assert p.locator('#ux-notification').inner_text().strip()
