from pathlib import Path
from playwright.sync_api import expect
import pytest
from tests.test_rebuilt_r5_workbench import browser
from tests.test_story_sources import sample,pin
from manju.authoring.core import canonical

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def comfortable(browser):
    c=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':1000});p=c.new_page()
    p.set_content((ROOT/'tools/model_workbench.html').read_text(encoding='utf-8'),wait_until='load')
    yield p;c.close()


def loaded(p):
    d=sample();pin(d);p.evaluate('d=>ManjuStory.install(d)',d)
    p.locator('#ux-navigation [data-view=story]').click()
    return d


def test_story_is_separate_and_shot_is_single_column(comfortable):
    p=comfortable;loaded(p)
    expect(p.locator('#story-section')).to_be_visible();expect(p.locator('#prompt')).not_to_be_visible()
    p.locator('#ux-navigation [data-view=shot]').click()
    expect(p.locator('#story-section')).not_to_be_visible();expect(p.locator('#prompt')).to_be_visible()
    assert p.locator('#ux-shot').evaluate('e=>getComputedStyle(e).gridTemplateColumns.split(" ").length')==1


def test_step_switch_keeps_all_data_and_backup_fingerprint(comfortable):
    p=comfortable;loaded(p)
    original=p.evaluate('ManjuDesk.fingerprint()')
    expect(p.locator('#story-action')).to_be_visible();expect(p.locator('#story-freeze')).not_to_be_visible()
    p.locator('#scene-step-refs').click()
    expect(p.locator('#story-action')).not_to_be_visible();expect(p.locator('#story-source-picks')).to_be_visible()
    p.locator('#scene-step-brief').click();expect(p.locator('#story-freeze')).to_be_visible()
    p.locator('#scene-step-write').click()
    assert p.evaluate('ManjuDesk.fingerprint()')==original
    p.locator('#story-action').fill('在这里继续写\n没有丢失')
    p.locator('#scene-step-refs').click();p.locator('#scene-step-write').click()
    assert p.locator('#story-action').input_value()=='在这里继续写\n没有丢失'


def test_reveal_opens_hidden_step_and_full_mode_keeps_all(comfortable):
    p=comfortable;loaded(p)
    p.evaluate('ManjuExperience.reveal("story-freeze")');expect(p.locator('#story-freeze')).to_be_visible()
    assert p.locator('#scene-step-brief').get_attribute('aria-selected')=='true'
    p.evaluate('ManjuExperience.showAll({scroll:false})')
    expect(p.locator('#story-action')).to_be_visible();expect(p.locator('#story-freeze')).to_be_visible()


def test_step_keyboard_and_no_false_dirty(comfortable):
    p=comfortable;loaded(p);before=p.evaluate('ManjuDesk.fingerprint()')
    p.locator('#scene-step-write').focus();p.keyboard.press('ArrowRight')
    expect(p.locator('#scene-step-refs')).to_be_focused()
    p.keyboard.press('End');expect(p.locator('#story-freeze')).to_be_visible()
    p.keyboard.press('Home');expect(p.locator('#story-action')).to_be_visible()
    assert p.evaluate('ManjuDesk.fingerprint()')==before


@pytest.mark.parametrize('width',[360,390,768,1440])
def test_views_no_horizontal_overflow_and_readable_inputs(comfortable,width):
    p=comfortable;p.set_viewport_size({'width':width,'height':960});loaded(p)
    for view in ['home','story','shot','review','director','repair','exchange','flex']:
        p.evaluate('v=>ManjuExperience.navigate(v)',view)
        assert p.evaluate('document.documentElement.scrollWidth<=innerWidth'),view
    p.evaluate('ManjuExperience.navigate("story")')
    assert p.locator('#story-action').evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')>=16
    for step in ['write','refs','brief']:
        p.locator(f'#scene-step-{step}').click()
        assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')


def test_original_controls_exactly_once(comfortable):
    p=comfortable
    ids=p.evaluate('[...document.querySelectorAll("[id]")].map(e=>e.id)')
    assert len(ids)==len(set(ids))
    import json
    for name in json.loads((ROOT/'tests/fixtures/ide_legacy_controls.json').read_text(encoding='utf-8')):
        assert ids.count(name)==1


def test_empty_story_can_be_created_and_imported(comfortable):
    p=comfortable;p.evaluate('ManjuExperience.navigate("story")')
    expect(p.locator('#story-create')).to_be_visible()
    p.locator('#story-create').click();expect(p.locator('#story-title')).to_be_visible()
    p.locator('#story-title').fill('新的作品')
    assert p.evaluate('ManjuStory.view().title')=='新的作品'


def test_ai_guide_search_locates_without_dispatch(comfortable):
    p=comfortable;p.keyboard.press('Control+k');p.locator('#ux-search-input').fill('IDE')
    p.locator('#ux-search-results button').first.click()
    expect(p.locator('#ide-guide')).to_be_visible()
    assert p.locator('#ide-guide').get_attribute('open') is not None
