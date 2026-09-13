"""Actual media and download rehearsal for scoped story workspaces.

Exact shipped HTML in isolated Chromium. Not native Windows/file navigation.
"""
from __future__ import annotations
import argparse
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
import json
from pathlib import Path
import shutil
import zipfile
from playwright.sync_api import sync_playwright, expect
import manju
from manju.authoring.core import canonical,digest
from manju.authoring.desk import verify_desk,extract_studio
from manju.authoring.story import new_story,compile_brief,Story,verify_brief_kit


def sha(p):return sha256(p.read_bytes()).hexdigest()
def media(p):
    with zipfile.ZipFile(p) as d,zipfile.ZipFile(BytesIO(d.read('STUDIO.zip'))) as z:
        return {n:sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith('media/')}


def example_story(checkout):
    with zipfile.ZipFile(checkout) as d,zipfile.ZipFile(BytesIO(d.read('STUDIO.zip'))) as z:
        asset=json.loads(z.read('STUDIO.json'))['workspace']['draft']['assets'][0]
    doc=new_story('RAIN_PORT_DEMO');doc.update(title='雨港来信 · 合成演练',form='limited_series',intent='用三场逐步改变人物间的信任。素材仅用于技术演练，不冒充剧情成片。',ending='盒中地图最终揭示为空白。仅供作者保留，不发给观众视角。')
    def source(id,kind,name,text,scope='audience',at=None,knower=None):
        return dict(id=id,kind=kind,name=name,text=text,scope=scope,from_scene_id=at,knower_id=knower,provenance='本包原创虚构演练资料；非用户真实创作')
    doc['sources']=[source('C01','character','林岚','修船匠；想护住箱子，又不愿失去周泊的信任。'),source('C02','character','周泊','摆渡人；需要拿回信物证明自己。'),source('REL01','relationship','互相信任的代价','林岚需要周泊隐瞒来历；周泊需要她亲手交还信物。'),source('SECRET','fact','作者尚未公开的真相','盒中地图是空白。','author','S30'),source('KEY','fact','周泊单独掌握的信息','备用钥匙在左抽屉。','character','S02','C02')]
    def scene(id,title,action):
        return dict(id=id,episode='E01',title=title,purpose='改变人物能够采取的下一步行动。',action=action,dialogue='',before='',after='',source_ids=[],references=[],policy={'scope':'audience','character_id':None,'history_count':0})
    a=scene('S10','渡口 · 护箱','林岚看见周泊伸手，迅速按住箱盖，阻止他拿走。')
    b=scene('S02','修船铺 · 隐瞒','周泊独自翻找抽屉，停在备用钥匙前。')
    c=scene('S30','空船 · 结局','林岚打开盒子，停下了原本准备说的话。')
    a['source_ids']=['C01','C02','REL01','SECRET','KEY'];b['source_ids']=['C02','KEY'];c['source_ids']=['C01','SECRET']
    a['references']=[dict(id='REF_MOTION',sha256=asset['sha256'],bytes=asset['bytes'],filename=asset['path'],role='performance',inherit='仅用于验证文件交接；真实制作时负责动作与时序。',exclude='不要继承测试图形的身份、服装或最终画面。',time_range='0–3秒，非认证帧编号'),dict(id='REF_CAMERA',sha256=asset['sha256'],bytes=asset['bytes'],filename=asset['path'],role='camera',inherit='只记录这一份原片还承担镜头参考用途。',exclude='不替代人物资料或原声音轨。',time_range='由使用者另行确认')]
    b['policy']['history_count']=1;doc['scenes']=[a,b,c]
    content=compile_brief(doc,'S10')
    doc['briefs']=[dict(id='BRIEF_BASE',scene_id='S10',content=content,content_sha256=digest(content),observation=dict(candidate_sha256=None,candidate_bytes=None,seen='',heard='',deviation='',layer='unreviewed'))]
    return Story.model_validate(doc).model_dump(mode='json')


def run(output:Path,example:Path,legacy_html:Path):
    output.mkdir(parents=True,exist_ok=False)
    html=files('manju.authoring').joinpath('data/workbench.html').read_text(encoding='utf-8')
    doc=example_story(example);(output/'STORY.json').write_bytes(canonical(doc));shutil.copy2(example,output/'BASE_CHECKOUT.zip')
    errors=[];network=[];original=media(example)
    with sync_playwright() as pw:
        browser=pw.chromium.launch(executable_path=shutil.which('chromium'),args=['--no-sandbox'])
        ctx=browser.new_context(accept_downloads=True,viewport={'width':1440,'height':960})
        def fresh(text=html):
            p=ctx.new_page();p.set_default_timeout(18000);p.on('pageerror',lambda e:errors.append(str(e)))
            p.on('request',lambda r:network.append(r.url) if r.url.startswith(('http:','https:')) else None)
            p.set_content(text,wait_until='load');return p
        def nav(p,key):p.locator('#ux-navigation [data-view='+key+']').click()
        def quiet(p):
            if p.locator('#ux-notification').is_visible():p.locator('#ux-notification button').click()
        def restore(p,path):
            p.locator('#intake-files').set_input_files(path);expect(p.locator('#intake-preview')).to_be_visible();p.locator('#intake-open').click();p.locator('#desk-restore-confirmed').check();p.locator('#desk-restore-apply').click();expect(p.locator('#desk-save-status')).to_contain_text('收工包已恢复')
        p=fresh();restore(p,example);before_prompt=p.locator('#prompt').input_value();nav(p,'shot')
        p.locator('#story-file').set_input_files(output/'STORY.json');expect(p.locator('#story-import-preview')).to_be_visible()
        assert p.evaluate('ManjuStory.view()') is None
        p.locator('#story-import-confirmed').check();p.locator('#story-import-apply').click()
        assert p.locator('#prompt').input_value()==before_prompt
        # See the source data, scoped facts, and real media responsibilities.
        p.locator('[data-story-tab=sources]').click();p.evaluate('ManjuExperience.reveal("story-section")');quiet(p)
        p.screenshot(path=str(output/'characters-desktop.png'))
        p.locator('[data-story-tab=scenes]').click()
        p.locator('#story-scene-editor details').filter(has=p.locator('#story-reference-file')).locator('summary').click()
        p.locator('#story-reference-file').select_option(doc['scenes'][0]['references'][0]['sha256'])
        assert p.locator('#story-references .story-reference').count()==2
        p.evaluate('ManjuExperience.reveal("story-section")');quiet(p);p.screenshot(path=str(output/'story-desktop.png'))
        p.locator('[data-story-tab=briefs]').click();first=p.locator('#story-briefs article').first
        assert '地图是空白' not in p.evaluate('ManjuStory.prompt(ManjuStory.view().briefs[0].content)')
        with p.expect_download() as dl:first.get_by_role('button',name='下载简报与参考 ZIP',exact=True).click()
        kit=output/'STORY_BRIEF.zip';dl.value.save_as(kit);kitcheck=verify_brief_kit(kit);assert kitcheck['media_files']==1 and kitcheck['reference_roles']==2
        with zipfile.ZipFile(kit) as z:
            for n in z.namelist():
                if n.startswith('media/'):assert sha256(z.read(n)).hexdigest() in original.values()
        first.get_by_role('button',name='预览带入当前镜头',exact=True).click();assert p.locator('#prompt').input_value()==before_prompt
        p.locator('#story-link-confirmed').check();p.locator('#story-link-apply').click()
        linked_prompt=p.locator('#prompt').input_value();assert p.evaluate('ManjuStory.assertRequest()') is None
        p.locator('[data-story-tab=scenes]').click();p.locator('#story-action').fill('林岚主动把箱子递给周泊，并松开双手。')
        p.locator('[data-story-tab=briefs]').click();assert p.locator('#story-briefs article').first.get_attribute('data-stale')=='true'
        assert p.locator('#prompt').input_value()==linked_prompt
        blocked=p.evaluate('()=>{try{ManjuStory.assertRequest();return false;}catch(e){return e.message;}}');assert '故事依据已变' in blocked
        p.evaluate('ManjuExperience.reveal("story-section")');quiet(p);p.screenshot(path=str(output/'stale-desktop.png'))
        # Explicit new version, not rewriting the old snapshot or relabelling it.
        p.locator('[data-story-tab=scenes]').click();p.locator('#story-freeze').click()
        newest=p.locator('#story-briefs article').first;assert newest.get_attribute('data-stale')=='false'
        newest.get_by_role('button',name='预览带入当前镜头',exact=True).click();p.locator('#story-link-confirmed').check();p.locator('#story-link-apply').click()
        assert p.evaluate('ManjuStory.assertRequest()') is None
        p.locator('[data-story-tab=briefs]').click();newest=p.locator('#story-briefs article').first
        newest.locator('details').nth(1).locator('summary').click()
        newest.locator('textarea').nth(0).fill('演练只看到合成图形，不能宣称演员已完成剧情。')
        newest.locator('textarea').nth(1).fill('未听取商业样本音轨。')
        newest.locator('textarea').nth(2).fill('需要真实生成后再回看片段；不是自动画质判断。')
        assert p.evaluate('ManjuStory.view().briefs[0].content.scene.action')==doc['scenes'][0]['action']
        p.set_viewport_size({'width':390,'height':844});p.locator('[data-story-tab=scenes]').click();p.evaluate('ManjuExperience.reveal("story-section")');quiet(p);p.screenshot(path=str(output/'story-narrow.png'))
        assert p.evaluate('document.documentElement.scrollWidth<=innerWidth')
        p.locator('[data-story-tab=sources]').click();p.evaluate('ManjuExperience.reveal("story-section")');p.screenshot(path=str(output/'characters-narrow.png'))
        p.set_viewport_size({'width':1440,'height':960})
        wanted=p.evaluate('ManjuDesk.fingerprint()');story=p.evaluate('ManjuStory.view()')
        assert len(story['briefs'])==2 and story['briefs'][0]['id']=='BRIEF_BASE'
        with p.expect_download() as dl:p.keyboard.press('Control+s')
        saved=output/'STORY_CHECKOUT.zip';dl.value.save_as(saved);assert verify_desk(saved)['schema_id']=='manju.desk-session/v2' and media(saved)==original
        p.locator('#ux-save-detail').click();p.locator('#desk-verify-file').set_input_files(saved);expect(p.locator('#ux-save-strip')).to_have_attribute('data-state','verified')
        nav(p,'director');p.get_by_role('button',name='查看 A01 精修目标图大图',exact=True).click();expect(p.locator('#ux-image-meta')).to_contain_text('1920 × 1080');p.keyboard.press('Escape')
        fresh_page=fresh();restore(fresh_page,saved);assert fresh_page.evaluate('ManjuDesk.fingerprint()')==wanted
        nav(fresh_page,'director');fresh_page.locator('#director-video').evaluate('(v)=>v.play()');fresh_page.wait_for_function('()=>document.querySelector("#director-video").currentTime>.1')
        with fresh_page.expect_download() as dl:fresh_page.locator('#ux-save').click()
        reopened=output/'STORY_REOPENED.zip';dl.value.save_as(reopened);assert reopened.read_bytes()==saved.read_bytes()
        inner=output/'LEGACY_STUDIO.zip';extract_studio(saved,inner)
        old=fresh(legacy_html.read_text(encoding='utf-8'));old.locator('#import-studio').set_input_files(inner)
        old.locator('#studio-restore-confirmed').check();old.locator('#apply-studio').click()
        nav(old,'director');old.locator('#director-video').evaluate('(v)=>v.play()');old.wait_for_function('()=>document.querySelector("#director-video").currentTime>.1')
        nav(old,'home')
        with old.expect_download() as dl:old.locator('#export-studio').click()
        oldout=output/'LEGACY_REEXPORTED.zip';dl.value.save_as(oldout);assert oldout.read_bytes()==inner.read_bytes()
        # File labels are human names; no claim of real generated footage or approval.
        ctx.close();browser.close()
    assert not errors,errors;assert not network,network
    result=dict(ok=True,version=manju.__version__,runtime=str(Path(manju.__file__).resolve()),html_sha256=sha256(html.encode()).hexdigest(),input_sha256=sha(example),actual_browser_download=True,story_scope_exclusion=True,stale_scene_blocks_link=True,old_brief_not_overwritten=True,fresh_checkout_reopen_identical=True,legacy_inner_reexport_identical=True,unchanged_media_files=len(original),media_sha256=original,brief_kit=kitcheck,brief_content_hashes=[b['content_sha256'] for b in story['briefs']],story_checkout_sha256=sha(saved),high_resolution_png_preserved=[1920,1080],javascript_errors=errors,network_requests=network,transport='isolated_document',native_windows_acceptance=False,commercial_generation=False,client_download_confirmed=False)
    (output/'RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2));return result

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--example',type=Path,required=True);p.add_argument('--legacy-html',type=Path,required=True);a=p.parse_args();run(a.output.absolute(),a.example.absolute(),a.legacy_html.absolute())
